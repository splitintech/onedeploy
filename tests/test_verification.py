from unittest.mock import patch
from onedeploy.verification import verify_http, _origin

PUBLIC = [(2, 1, 6, '', ('8.8.8.8', 443))]

class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, count): assert count == 1; return b'x'


def test_no_probe_without_requested_paths():
    with patch('socket.getaddrinfo') as dns:
        assert verify_http('railway', ['https://x.up.railway.app'])['reason'] == 'not_requested'
        dns.assert_not_called()


def test_invalid_origins_rejected_without_network():
    for url in ['http://x.up.railway.app','https://a:b@x.up.railway.app','https://x.up.railway.app?token=a',
                'https://example.com','https://x.up.railway.app.evil.test','https://127.0.0.1',
                'https://x.up.railway.app:444','https://x.up.railway.app/private']:
        with patch('socket.getaddrinfo') as dns:
            assert _origin('railway', url) is None
            dns.assert_not_called()


def test_private_dns_fails_closed():
    with patch('socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
        assert _origin('railway','https://x.up.railway.app') is None


def test_success_no_url_or_body_in_result():
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        build.return_value.open.return_value=Response()
        result=verify_http('railway',['https://x.up.railway.app'],'/health','/ready')
        assert result['readiness_passed'] and result['http_reachable']
        assert not result['ingress_verified']
        assert 'railway.app' not in str(result)
        assert build.return_value.open.call_count == 2


def test_invalid_path_not_requested():
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        result=verify_http('modal',['https://x.modal.run'],ready_path='//evil.test')
        assert result['reason']=='invalid_probe_path'
        build.return_value.open.assert_not_called()


def test_auth_required_is_not_readiness():
    from urllib.error import HTTPError
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        build.return_value.open.side_effect=HTTPError('https://x.modal.run',401,'Unauthorized',{},None)
        result=verify_http('modal',['https://x.modal.run'],ready_path='/ready')
        assert result['reason']=='authentication_required'
        assert not result['readiness_passed']
        assert not result['ingress_verified']


def test_redirect_is_not_followed_or_success():
    from urllib.error import HTTPError
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        build.return_value.open.side_effect=HTTPError('https://x.modal.run',302,'Found',{'Location':'http://127.0.0.1'},None)
        result=verify_http('modal',['https://x.modal.run'],ready_path='/ready')
        assert result['reason']=='probe_failed'
        assert not result['readiness_passed']
        assert build.return_value.open.call_count==1


def test_declared_auth_check_requires_denial_and_success():
    from urllib.error import HTTPError
    from onedeploy.verification import verify_checks
    checks=[{'name':'auth','path':'/identity','safety':'read-only','auth_variable':'APP_TOKEN','unauthenticated_status':401}]
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        build.return_value.open.side_effect=[HTTPError('https://x.modal.run',401,'Unauthorized',{},None),Response()]
        result=verify_checks('modal',['https://x.modal.run'],checks,{'APP_TOKEN':'synthetic-sensitive-token'})
        assert result['ingress_verified'] and result['application_checks_passed']
        calls=build.return_value.open.call_args_list
        assert calls[0].args[0].get_header('Authorization') is None
        assert calls[1].args[0].get_header('Authorization') == 'Bearer synthetic-sensitive-token'
        assert 'synthetic-sensitive-token' not in str(result)
        assert 'modal.run' not in str(result)


def test_public_auth_route_is_not_verified():
    from onedeploy.verification import verify_checks
    checks=[{'name':'auth','path':'/identity','safety':'read-only','auth_variable':'TOKEN','unauthenticated_status':401}]
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        build.return_value.open.return_value=Response()
        result=verify_checks('railway',['https://x.up.railway.app'],checks,{'TOKEN':'test'})
        assert not result['ingress_verified'] and not result['application_checks_passed']


def test_check_rejects_header_injection_or_unsafe_contract():
    from onedeploy.verification import verify_checks
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        for check,values in [({'name':'x','path':'/','safety':'write'},{}),
                             ({'name':'x','path':'/','safety':'read-only','auth_variable':'TOKEN'},{'TOKEN':'a\r\nX: b'})]:
            result=verify_checks('modal',['https://x.modal.run'],[check],values)
            assert not result['application_checks_passed']
        build.return_value.open.assert_not_called()


def test_auth_check_cancellation_before_credential_request():
    from threading import Event
    from urllib.error import HTTPError
    from onedeploy.verification import verify_checks
    event=Event()
    def deny_and_cancel(*args,**kwargs):
        event.set()
        raise HTTPError('https://x.modal.run',401,'Unauthorized',{},None)
    checks=[{'name':'auth','path':'/identity','safety':'read-only','auth_variable':'TOKEN','unauthenticated_status':401}]
    with patch('socket.getaddrinfo',return_value=PUBLIC), patch('onedeploy.verification.build_opener') as build:
        build.return_value.open.side_effect=deny_and_cancel
        result=verify_checks('modal',['https://x.modal.run'],checks,{'TOKEN':'synthetic'},cancel_event=event)
        assert result['reason']=='cancelled'
        assert not result['ingress_verified']
        assert build.return_value.open.call_count == 1
