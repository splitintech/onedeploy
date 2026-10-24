"""Bounded official Daytona CLI transport, with no SDK or credential-file access.

The CLI owns its saved authentication. Only its documented public output is read.
Raw CLI JSON, build output and errors never leave this process. No fallback to SDK.
Contract: daytona/clients v0.226.0. See docs/provider-contracts.md.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

CLI_VERSION = '0.226.0'
MAX_OUTPUT = 1024 * 1024
MAX_PAGES = 100
PAGE_SIZE = 100
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,254}$')
DIGEST = re.compile(r'@sha256:[0-9a-f]{64}$')


class Failure(Exception):
    def __init__(self, code='provider_failed'):
        self.code = code


class Transport:
    """Subprocesses remain in the outer runner's process group for cancellation."""
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.mutation_attempted = False

    def call(self, args, *, mutating=False):
        if self.deadline <= time.monotonic():
            raise Failure()
        # The inherited environment has already been allowlisted by CommandRunner.
        # Drop SDK/API overrides even if this module is invoked outside the runner.
        allowed = {'PATH', 'HOME', 'XDG_CONFIG_HOME', 'DAYTONA_CONFIG_DIR', 'APPDATA', 'LANG', 'LC_ALL'}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        try:
            p = subprocess.Popen(['daytona', *args], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env, shell=False)
        except OSError:
            raise Failure() from None
        if mutating:
            self.mutation_attempted = True
        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()
        def read(pipe, buffer):
            try:
                while True:
                    block = pipe.read(4096)
                    if not block:
                        break
                    remaining = MAX_OUTPUT - len(buffer)
                    buffer.extend(block[:max(0, remaining)])
                    if len(block) > remaining:
                        overflow.set()
                        break
            finally:
                pipe.close()
        workers = [threading.Thread(target=read, args=(pipe, buffer), daemon=True)
                   for pipe, buffer in zip((p.stdout, p.stderr), buffers)]
        for worker in workers:
            worker.start()
        try:
            while p.poll() is None:
                if overflow.is_set() or time.monotonic() >= self.deadline:
                    p.kill()
                    p.wait(timeout=5)
                    raise Failure()
                time.sleep(0.02)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
            for worker in workers:
                worker.join(timeout=5)
        if p.returncode or overflow.is_set() or any(worker.is_alive() for worker in workers):
            raise Failure()
        return buffers[0].decode('utf-8', errors='strict')


def json_call(transport, args, shape):
    try:
        data = json.loads(transport.call(args))
    except (ValueError, UnicodeError):
        raise Failure() from None
    if not isinstance(data, shape):
        raise Failure()
    return data


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Failure()
    return value


def identity(transport, expected):
    # Omitting the argument intentionally queries members of the CLI ACTIVE org.
    # Org list JSON has no active marker and is NOT sufficient identity evidence.
    try:
        members = json_call(transport, ['organization', 'members', '--format', 'json'], list)
    except Failure:
        raise Failure('authentication_failed') from None
    if not members or any(not isinstance(m, dict) for m in members):
        raise Failure('authentication_failed')
    organizations = {m.get('organizationId') for m in members if isinstance(m.get('organizationId'), str)}
    if len(organizations) != 1 or any(m.get('organizationId') != expected for m in members):
        raise Failure('authentication_failed')
    # Never emit member IDs, email addresses, names, roles, or credential material.
    return {'account': expected, 'transport': 'cli'}


def sandbox_list(transport, account):
    items, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        args = ['list', '--format', 'json', '--limit', str(PAGE_SIZE)]
        if cursor:
            args += ['--cursor', cursor]
        data = json_call(transport, args, dict)
        rows = data.get('items')
        if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get('organizationId') != account for row in rows):
            raise Failure()
        items.extend(rows)
        cursor = data.get('nextCursor')
        if cursor is None or cursor == '':
            return items
        if not isinstance(cursor, str) or len(cursor) > 4096 or '\x00' in cursor or cursor in seen:
            raise Failure()
        seen.add(cursor)
    raise Failure()  # A bounded incomplete list is never absence evidence.


def snapshots(transport, account):
    items, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        rows = json_call(transport, ['snapshot', 'list', '--format', 'json', '--page', str(page), '--limit', str(PAGE_SIZE)], list)
        for row in rows:
            if not isinstance(row, dict) or row.get('organizationId') not in (None, account):
                raise Failure()
            key = identifier(row.get('id'))
            if key in seen:
                raise Failure()
            seen.add(key)
            items.append(row)
        if len(rows) < PAGE_SIZE:
            return items
    raise Failure()


def matching(rows, name):
    found = [row for row in rows if name in (row.get('id'), row.get('name'))]
    if len(found) > 1:
        raise Failure()
    return found[0] if found else None


def summary(row, account):
    if row.get('organizationId') != account or type(row.get('public')) is not bool or not isinstance(row.get('state'),str) or not re.fullmatch(r'[A-Za-z_-]{1,64}',row['state']) or (row.get('snapshot') is not None and (not isinstance(row['snapshot'],str) or not ID.fullmatch(row['snapshot']))) or type(row.get('autoStopInterval')) is not int:
        raise Failure()
    return {'exists': True, 'id': identifier(row.get('id')), 'name': identifier(row.get('name')),
            'account': account, 'state': row.get('state'), 'snapshot': row.get('snapshot'),
            'public': row['public'], 'auto_stop_minutes': row.get('autoStopInterval'),
            'readiness_verified': False}


def info(transport, sandbox_id, account):
    row = json_call(transport, ['info', identifier(sandbox_id), '--format', 'json'], dict)
    if sandbox_id not in (row.get('id'), row.get('name')):
        raise Failure('stale_plan')
    return summary(row, account)  # row.env and any signed URL are intentionally discarded.


def snapshot_receipt(snapshot, account):
    if snapshot.get('organizationId') not in (None, account) or snapshot.get('state') != 'active':
        raise Failure()
    image = snapshot.get('imageName')
    if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}',image):
        raise Failure()
    return {'snapshot_id': identifier(snapshot.get('id')), 'snapshot_image': image}


def check_runtime(config, runtime):
    public = config.get('public_variable_names',[])
    if not isinstance(runtime,dict) or set(runtime) != set(public) or any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',k) or not isinstance(v,str) or '\x00' in v for k,v in runtime.items()):
        raise Failure('unsupported')
    if config.get('variables') or any(c.get('auth_variable') for c in config.get('checks', [])):
        raise Failure('unsupported')


def execute(operation, config, transport, runtime=None):
    if operation not in {'identity', 'inspect', 'status', 'reconcile', 'create', 'snapshot-create', 'start', 'verify', 'verify-checks'}:
        raise Failure('unsupported')
    target = config['target']
    account = identifier(target['account'])
    name = identifier(target['sandbox'])
    runtime = runtime or {}
    check_runtime(config, runtime)
    ident = identity(transport, account)
    if operation == 'identity':
        return ident
    row = matching(sandbox_list(transport, account), name)
    if row:
        if operation in {'create', 'snapshot-create'}:
            raise Failure('stale_plan')
        observed = info(transport, row['id'], account)
        if operation == 'start':
            approved = config.get('approved_inspection')
            if not isinstance(approved,dict) or approved != observed:
                raise Failure('stale_plan')
            identity(transport, account)  # Refresh just before the write; no profile switching.
            transport.call(['start', observed['id']], mutating=True)
            identity(transport, account)
            return info(transport, observed['id'], account)
        if operation in {'verify', 'verify-checks'}:
            return verify(transport, observed, config, checks_only=operation == 'verify-checks')
        return observed
    if target.get('mode') != 'create' or operation not in {'inspect', 'reconcile', 'create', 'snapshot-create'}:
        raise Failure('stale_plan')
    volumes = []
    for item in config.get('storage', []):
        volume = json_call(transport, ['volume', 'get', identifier(item['resource_id']), '--format', 'json'], dict)
        if item['resource_id'] not in (volume.get('id'),volume.get('name')) or volume.get('organizationId') != account or volume.get('state') != 'ready':
            raise Failure('stale_plan')
        volumes.append({'id': identifier(volume.get('id')), 'name': identifier(volume.get('name')), 'mount_path': item['mount_path']})
    if operation in {'create','snapshot-create'}:
        approved = config.get('approved_inspection')
        if not isinstance(approved,dict) or approved.get('exists') is not False or approved.get('volumes',[]) != volumes:
            raise Failure('stale_plan')
    build = config.get('build', {})
    snapshot_name = identifier(build.get('snapshot_name') or target.get('snapshot'))
    snapshot = matching(snapshots(transport, account), snapshot_name)
    if operation == 'snapshot-create':
        if snapshot:
            raise Failure('stale_plan')
        identity(transport, account)
        # Outer core has already materialized a hash-verified sanitized bundle as cwd.
        transport.call(['snapshot', 'create', snapshot_name, '--dockerfile', 'Dockerfile', '--context', '.'], mutating=True)
        identity(transport, account)
        snapshot = matching(snapshots(transport, account), snapshot_name)
        if not snapshot:
            raise Failure()
        return {**snapshot_receipt(snapshot, account), 'account': account}
    if not snapshot:
        if not build or operation == 'create':
            raise Failure('stale_plan')
        return {'exists': False, 'account': account, 'snapshot_pending': True, 'volumes': volumes}
    receipt = snapshot_receipt(snapshot, account)
    if operation != 'create':
        return {'exists': False, 'account': account, **receipt, 'volumes': volumes}
    pinned = config.get('snapshot_receipt')
    if not pinned or pinned != receipt:
        raise Failure('stale_plan')
    identity(transport, account)
    args = ['create', '--name', name, '--snapshot', receipt['snapshot_id'], '--auto-stop', str(config['auto_stop_minutes']),
            '--auto-delete', '-1', '--target', target.get('region', 'us'), '--label', 'onedeploy-managed=true']
    for key in sorted(runtime):
        args += ['--env', key + '=' + runtime[key]]
    for volume in volumes:
        args += ['--volume', volume['id'] + ':' + volume['mount_path']]
    transport.call(args, mutating=True)
    identity(transport, account)
    result = info(transport, name, account)
    if result['public'] or result.get('snapshot') not in (receipt['snapshot_id'], snapshot_name):
        raise Failure()
    return {**result, **receipt}


def verify(transport, observed, config, *, checks_only=False):
    port = config.get('port')
    checks = config.get('checks', []) if checks_only else [
        {'name': name, 'path': config[key], 'expected_status': 200}
        for name, key in [('health', 'health_path'), ('ready', 'ready_path')] if config.get(key)]
    if type(port) is not int or not 1 <= port <= 65535 or not checks or len(checks) > 8:
        raise Failure()
    if any(c.get('auth_variable') or c.get('unauthenticated_status') for c in checks):
        raise Failure('unsupported')
    if any(not re.fullmatch(r'/[A-Za-z0-9_./-]*', c.get('path', '')) for c in checks):
        raise Failure()
    code = """import http.client,json,sys
port=int(sys.argv[1]);checks=json.loads(sys.argv[2]);result=[]
for check in checks:
 try:
  c=http.client.HTTPConnection('127.0.0.1',port,timeout=5);c.request('GET',check['path']);r=c.getresponse();status=r.status;c.close()
 except Exception:status=None
 result.append(status)
print(json.dumps(result))
"""
    raw = transport.call(['exec', observed['id'], '--timeout', '90', '--', 'python3', '-I', '-c', code, str(port), json.dumps(checks)])
    try:
        statuses = json.loads(raw)
    except ValueError:
        raise Failure() from None
    if not isinstance(statuses, list) or len(statuses) != len(checks) or any(s is not None and type(s) is not int for s in statuses):
        raise Failure()
    if checks_only:
        results = [{'name': check['name'], 'passed': status == check.get('expected_status', 200), 'status': status,
                    'unauthenticated_status': None} for check, status in zip(checks, statuses)]
        passed = all(c['passed'] for c in results)
        return {'application_checks_passed': passed, 'ingress_verified': False, 'checks': results,
                'reason': 'requested_checks_passed' if passed else 'application_check_failed'}
    ok = {check['name']: status is not None and 200 <= status < 300 for check, status in zip(checks, statuses)}
    return {'process_running': observed.get('state') == 'started', 'health_verified': ok.get('health', False), 'readiness_verified': ok.get('ready', False)}


def main():
    transport = None
    try:
        if len(sys.argv) != 3:
            raise Failure()
        operation, raw = sys.argv[1:]
        config = json.loads(raw)
        timeout = config.get('timeout', 120)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise Failure()
        transport = Transport(timeout)
        version = transport.call(['--version'])
        versions = re.findall(r'(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])', version)
        if versions != [CLI_VERSION]:
            raise Failure('unsupported')
        raw_runtime = sys.stdin.read(MAX_OUTPUT + 1) if operation == 'create' else '{}'
        if len(raw_runtime) > MAX_OUTPUT:
            raise Failure()
        runtime = json.loads(raw_runtime or '{}')
        result = execute(operation, config, transport, runtime)
        print(json.dumps(result))
        return 0
    except Exception as error:
        # Provider output/errors may contain application environment or signed URLs.
        # Never serialize exceptions. Outer runner also treats failed writes uncertain.
        mutation = transport is not None and transport.mutation_attempted
        code = 'uncertain' if mutation else (error.code if isinstance(error, Failure) else 'provider_failed')
        print(json.dumps({'error': code, 'mutation_possible': mutation}))
        return 7 if mutation else 0  # Read errors are safe typed data, interpreted by the adapter.


if __name__ == '__main__':
    sys.exit(main())
