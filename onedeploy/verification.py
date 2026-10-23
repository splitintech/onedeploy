"""Bounded credential-free readiness probes against official provider endpoints.

No redirects, user-supplied arbitrary hosts, credentials or response-body logging.
A protected endpoint is not silently treated as a successful readiness check.
"""
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError, URLError
import ipaddress
import socket
import ssl

SUFFIXES = {"railway": (".up.railway.app",), "modal": (".modal.run",)}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def _origin(provider, value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or not any(host.endswith(suffix) and host != suffix[1:]
                           for suffix in SUFFIXES.get(provider, ()))):
            return None
        # Avoid private address probes even if DNS is misconfigured. Requests still
        # use normal certificate verification; no custom CA/security bypass.
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            return None
        return "https://" + host
    except (ValueError, OSError):
        return None

def verify_http(provider, endpoints, health_path=None, ready_path=None, timeout=10):
    """Return layered evidence, never endpoint URLs or response bodies.

    Only the first eligible endpoint is probed. Paths are explicit application
    contracts. No auth credential is sent by this helper.
    """
    result = {"process_running": False, "readiness_passed": False,
              "ingress_verified": False, "http_reachable": False, "checks": [], "reason": "not_requested"}
    if not health_path and not ready_path:
        return result
    if not isinstance(endpoints, (list, tuple)):
        result["reason"] = "no_verified_public_endpoint"
        return result
    timeout = max(0.1, min(float(timeout), 30.0))
    origin = next((candidate for url in endpoints if (candidate := _origin(provider, url))), None)
    if origin is None:
        result["reason"] = "no_verified_public_endpoint"
        return result
    opener = build_opener(NoRedirect(), ProxyHandler({}))
    for kind, path in (("health", health_path), ("ready", ready_path)):
        if not path:
            continue
        if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
                or any(ch in path for ch in ("?", "#", "\\", "\r", "\n")) or len(path) > 2048):
            result["reason"] = "invalid_probe_path"
            return result
        code = None
        try:
            request = Request(origin + path, headers={"User-Agent": "Onedeploy-readiness/0.1"}, method="GET")
            with opener.open(request, timeout=timeout) as response:
                code = response.status
                response.read(1)  # Never collect/log application bodies.
        except HTTPError as error:
            code = error.code
            error.close()
        except (URLError, TimeoutError, OSError, ssl.SSLError):
            result["checks"].append({"kind": kind, "passed": False, "status": None})
            result["reason"] = "probe_unavailable"
            continue
        passed = 200 <= code < 300
        result["checks"].append({"kind": kind, "passed": passed, "status": code})
        if passed:
            result["http_reachable"] = True  # Reachability is not authentication-policy verification.
            if kind == "health":
                result["process_running"] = True
            else:
                result["readiness_passed"] = True
                result["process_running"] = True
        elif code in (401, 403):
            result["reason"] = "authentication_required"
        else:
            result["reason"] = "probe_failed"
    if result["checks"] and all(check["passed"] for check in result["checks"]):
        result["reason"] = "requested_probes_passed"
    return result


def verify_checks(provider, endpoints, checks, values, timeout=10, cancel_event=None):
    """Explicit read-only application GET contracts on release-owned endpoints.

    An auth variable is a runtime secret reference, not a raw header in a plan.
    Authentication evidence requires BOTH unauthenticated denial and authenticated
    success. This checks the declared route, not every route or authorization rule.
    """
    result = {'application_checks_passed': False, 'ingress_verified': False,
              'checks': [], 'reason': 'not_requested'}
    if not checks:
        return result
    if not isinstance(checks, list) or len(checks) > 8:
        result['reason'] = 'invalid_checks'
        return result
    if not isinstance(endpoints, (list, tuple)):
        result['reason'] = 'no_verified_public_endpoint'
        return result
    origin = next((candidate for url in endpoints if (candidate := _origin(provider, url))), None)
    if not origin:
        result['reason'] = 'no_verified_public_endpoint'
        return result
    timeout = max(0.1, min(float(timeout), 30.0))
    opener = build_opener(NoRedirect(), ProxyHandler({}))

    def request_status(path, token=None):
        headers = {'User-Agent': 'Onedeploy-readonly-check/0.1'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        try:
            with opener.open(Request(origin + path, headers=headers, method='GET'), timeout=timeout) as response:
                response.read(1)
                return response.status
        except HTTPError as error:
            code = error.code
            error.close()
            return code
        except (URLError, TimeoutError, OSError, ssl.SSLError, ValueError):
            return None

    auth_evidence = []
    for check in checks:
        if cancel_event is not None and cancel_event.is_set():
            result['reason'] = 'cancelled'
            return result
        path = check.get('path')
        if (check.get('safety') != 'read-only' or not isinstance(path, str)
                or not path.startswith('/') or path.startswith('//') or len(path) > 2048
                or any(ch in path for ch in ('?', '#', '\\', '\r', '\n'))):
            result['reason'] = 'invalid_checks'
            return result
        token = None
        if check.get('auth_variable'):
            token = values.get(check['auth_variable'])
            if (not isinstance(token, str) or not token or len(token) > 8192
                    or any(ch.isspace() or not 33 <= ord(ch) <= 126 for ch in token)):
                result['reason'] = 'invalid_auth_reference'
                return result
        denied = None
        if 'unauthenticated_status' in check:
            if token is None or check['unauthenticated_status'] not in (401, 403):
                result['reason'] = 'invalid_checks'
                return result
            denied = request_status(path)
        if cancel_event is not None and cancel_event.is_set():
            result['reason'] = 'cancelled'
            return result
        status = request_status(path, token)
        passed = status == check.get('expected_status', 200)
        if 'unauthenticated_status' in check:
            passed = passed and denied == check['unauthenticated_status']
            auth_evidence.append(passed)
        result['checks'].append({'name': check['name'], 'passed': passed, 'status': status,
                                 'unauthenticated_status': denied})
    result['application_checks_passed'] = bool(result['checks']) and all(c['passed'] for c in result['checks'])
    result['ingress_verified'] = bool(auth_evidence) and all(auth_evidence) and result['application_checks_passed']
    result['reason'] = 'requested_checks_passed' if result['application_checks_passed'] else 'application_check_failed'
    return result
