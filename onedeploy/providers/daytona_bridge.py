"""Isolated Daytona SDK transport. Invoke only through the core's fixed runner.

Runtime values arrive on stdin, the API key through the process environment.
No arbitrary URL, Python module, command, or credential path is accepted.
"""
import contextlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import urllib.request

API_URL = 'https://app.daytona.io/api'
SDK_VERSION = '0.226.0'
MAX_RESPONSE = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Credential-bearing redirects are forbidden')


def identity(api_key, expected):
    request = urllib.request.Request(API_URL+'/api-keys/current', headers={
        'Authorization':'Bearer '+api_key, 'X-Daytona-Organization-ID':expected})
    with urllib.request.build_opener(NoRedirect).open(request,timeout=30) as response:
        raw = response.read(MAX_RESPONSE+1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError('Oversized identity response')
    data = json.loads(raw)
    account = data.get('organizationId')
    if not isinstance(account,str) or account != expected:
        raise ValueError('Organization could not be verified')
    return {'account':account}


def scalar(value):
    return value.value if hasattr(value,'value') else value


def summary(sandbox):
    # Never dump SDK objects: sandbox.env contains application credentials.
    return {'exists':True,'id':sandbox.id,'name':sandbox.name,
            'account':sandbox.organization_id,'state':scalar(sandbox.state),
            'snapshot':sandbox.snapshot,'public':sandbox.public,
            'auto_stop_minutes':sandbox.auto_stop_interval,'readiness_verified':False}


def execute(operation, config, runtime, client, missing_error, params_type, image_type=None, snapshot_params_type=None, volume_type=None):
    """SDK boundary is injectable for offline contract tests, not runtime plugins."""
    target = config['target']
    name = target['sandbox']
    if operation not in {'inspect','status','reconcile','create','start','logs','verify','verify-checks','snapshot-create'}:
        raise ValueError('Unsupported bridge operation')
    try:
        sandbox = client.get(name, request_timeout=30)
    except missing_error:
        if operation not in {'inspect','reconcile','create','snapshot-create'} or target.get('mode') != 'create':
            raise
        sandbox = None
    if sandbox is not None:
        if sandbox.organization_id != target['account']:
            raise ValueError('Sandbox belongs to a different organization')
        if operation in {'create','snapshot-create'}:
            raise ValueError('Sandbox already exists; reconcile without repeating create')
        if operation == 'logs':
            logs = sandbox.process.get_entrypoint_logs(request_timeout=30)
            text = logs.output or ''
            return {'logs':text[-65536:],'truncated':len(text)>65536}
        if operation == 'verify':
            return verify(sandbox,config)
        if operation == 'verify-checks':
            return verify_checks(sandbox,config,runtime)
        if operation == 'start':
            sandbox.start(timeout=config['timeout'])
            sandbox.refresh_data()
        return summary(sandbox)
    volumes = []
    mounts = []
    for item in config.get('storage',[]):
        volume = client.volume.get(item['resource_id'],create=False)
        if volume.organization_id != target['account'] or scalar(volume.state) != 'ready':
            raise ValueError('Existing volume must be ready and belong to target organization')
        volumes.append({'id':volume.id,'name':volume.name,'mount_path':item['mount_path']})
        if volume_type:
            mounts.append(volume_type(volume_id=volume.id,mount_path=item['mount_path'],subpath=item.get('subpath')))
    build = config.get('build',{})
    snapshot_name = build.get('snapshot_name') or target.get('snapshot')
    try:
        snapshot = client.snapshot.get(snapshot_name)
    except missing_error:
        if not build:
            raise
        snapshot = None
    if operation == 'snapshot-create':
        if snapshot is not None:
            raise ValueError('Snapshot exists; never replay snapshot create')
        dockerfile = Path(config['bundle_path']) / 'Dockerfile'
        image = image_type.from_dockerfile(dockerfile,strict_context=True)
        snapshot = client.snapshot.create(snapshot_params_type(name=snapshot_name,image=image),timeout=config['timeout'])
        if snapshot.organization_id not in (None,target['account']):
            raise ValueError('Snapshot organization mismatch')
        if not re.search(r'@sha256:[0-9a-f]{64}$',snapshot.image_name or ''):
            raise ValueError('Created snapshot lacks immutable image digest')
        return {'snapshot_id':snapshot.id,'snapshot_image':snapshot.image_name,'account':target['account']}
    if snapshot is None:
        if operation == 'create':
            raise ValueError('Snapshot build has not completed')
        return {'exists':False,'account':target['account'],'snapshot_pending':True,'volumes':volumes}
    pinned = config.get('snapshot_receipt')
    if operation == 'create' and ((build and not pinned) or (pinned and (pinned.get('snapshot_id') != snapshot.id or pinned.get('snapshot_image') != snapshot.image_name))):
        raise ValueError('Create requires exact completed snapshot receipt')
    if snapshot.organization_id not in (None,target['account']):
        raise ValueError('Snapshot belongs to a different organization')
    if not isinstance(snapshot.image_name,str) or not re.search(r'@sha256:[0-9a-f]{64}$',snapshot.image_name):
        raise ValueError('Snapshot must resolve to an immutable sha256 image digest')
    if operation != 'create':
        return {'exists':False,'account':target['account'], 'snapshot_id':snapshot.id,
                'snapshot_image':snapshot.image_name,'volumes':volumes}
    params = params_type(name=name,snapshot=snapshot.id,env_vars=runtime,
                         public=False,auto_stop_interval=config['auto_stop_minutes'],
                         auto_delete_interval=-1, target=target.get('region','us'),
                         labels={'onedeploy-managed':'true'})
    if mounts:
        params.volumes = mounts
    sandbox = client.create(params,timeout=config['timeout'])
    if sandbox.organization_id != target['account']:
        raise ValueError('Created sandbox organization does not match')
    result = summary(sandbox)
    result.update({'snapshot_id':snapshot.id,'snapshot_image':snapshot.image_name})
    return result



def verify(sandbox, config):
    port = config.get('port')
    paths = [config.get('health_path'), config.get('ready_path')]
    if type(port) is not int or not 1 <= port <= 65535 or not any(paths):
        raise ValueError('Verification requires port and a health or readiness path')
    paths = [p for p in paths if p]
    if any(not isinstance(p,str) or not re.fullmatch(r'/[A-Za-z0-9_./-]*',p) for p in paths):
        raise ValueError('Probe paths must be simple absolute HTTP paths')
    # Constant probe program; manifest values are shell-quoted data, never code.
    code = """import http.client,json,sys
port=int(sys.argv[1]); paths=json.loads(sys.argv[2]); ok=[]
for path in paths:
 try:
  c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
  c.request('GET',path); r=c.getresponse(); ok.append(200<=r.status<300); c.close()
 except Exception: ok.append(False)
print(json.dumps(ok))
"""
    command = shlex.join(['python3','-I','-c',code,str(port),json.dumps(paths)])
    response = sandbox.process.exec(command,timeout=20)
    if response.exit_code != 0:
        return {'process_running':scalar(sandbox.state)=='started','health_verified':False,'readiness_verified':False}
    checks = json.loads(response.result)
    if not isinstance(checks,list) or len(checks)!=len(paths) or any(type(v) is not bool for v in checks):
        raise ValueError('Invalid probe response')
    return {'process_running':scalar(sandbox.state)=='started',
            'health_verified':checks[0] if config.get('health_path') else False,
            'readiness_verified':checks[-1] if config.get('ready_path') else False}


def verify_checks(sandbox, config, runtime):
    port = config.get('port')
    checks = config.get('checks',[])
    if type(port) is not int or not 1 <= port <= 65535 or not checks or len(checks)>8:
        raise ValueError('Invalid check bounds')
    for check in checks:
        if check.get('safety')!='read-only' or not re.fullmatch(r'/[A-Za-z0-9_./-]*',check.get('path','')) or type(check.get('expected_status',200)) is not int or not 200<=check.get('expected_status',200)<300:
            raise ValueError('Invalid check contract')
        token = runtime.get(check.get('auth_variable')) if check.get('auth_variable') else None
        if check.get('auth_variable') and (not isinstance(token,str) or not token or any(c.isspace() or ord(c)<32 for c in token)):
            raise ValueError('Invalid auth reference')
        if 'unauthenticated_status' in check and (token is None or check['unauthenticated_status'] not in (401,403)):
            raise ValueError('Invalid negative auth check')
    code = """import http.client,json,sys,os
port=int(sys.argv[1]); checks=json.loads(sys.argv[2]); values=json.loads(os.environ.pop('ONEDEPLOY_CHECK_VALUES')); results=[]; auth=[]
def request(path,token=None):
 try:
  c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
  h={'Authorization':'Bearer '+token} if token else {}
  c.request('GET',path,headers=h);r=c.getresponse();status=r.status;c.close();return status
 except Exception:return None
for check in checks:
 token=values.get(check.get('auth_variable')); denied=None
 if 'unauthenticated_status' in check:denied=request(check['path'])
 status=request(check['path'],token);passed=status==check.get('expected_status',200)
 if 'unauthenticated_status' in check:
  passed=passed and denied==check['unauthenticated_status'];auth.append(passed)
 results.append({'name':check['name'],'passed':passed,'status':status,'unauthenticated_status':denied})
ok=bool(results) and all(c['passed'] for c in results)
print(json.dumps({'application_checks_passed':ok,'ingress_verified':bool(auth) and all(auth) and ok,'checks':results,'reason':'requested_checks_passed' if ok else 'application_check_failed'}))
"""
    command = shlex.join(['python3','-I','-c',code,str(port),json.dumps(checks)])
    response = sandbox.process.exec(command,env={'ONEDEPLOY_CHECK_VALUES':json.dumps(runtime)},timeout=90)
    if response.exit_code != 0:
        return {'application_checks_passed':False,'ingress_verified':False,'checks':[],'reason':'probe_failed'}
    result = json.loads(response.result)
    if not isinstance(result,dict) or type(result.get('application_checks_passed')) is not bool or type(result.get('ingress_verified')) is not bool or not isinstance(result.get('checks'),list):
        raise ValueError('Invalid probe result')
    return result


def main():
    operation = sys.argv[1] if len(sys.argv)>1 else ''
    mutating = operation in {'create','start','snapshot-create'}
    mutation_attempted = False
    try:
        if len(sys.argv)!=3:
            raise ValueError('Invalid bridge arguments')
        if operation == 'version':
            print(json.dumps({'version':importlib.metadata.version('daytona')}))
            return 0
        config = json.loads(sys.argv[2])
        config['bundle_path'] = os.getcwd()
        api_key = os.environ.get('DAYTONA_API_KEY')
        if not api_key:
            raise ValueError('DAYTONA_API_KEY is required')
        ident = identity(api_key,config['target']['account'])
        if operation == 'identity':
            print(json.dumps(ident))
            return 0
        if importlib.metadata.version('daytona') != SDK_VERSION:
            raise ValueError('Install the documented exact Daytona SDK version')
        raw = sys.stdin.read(MAX_RESPONSE+1) if operation in {'create','verify-checks'} else '{}'
        if len(raw)>MAX_RESPONSE:
            raise ValueError('Oversized runtime input')
        runtime = json.loads(raw or '{}')
        if not isinstance(runtime,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in runtime.items()):
            raise ValueError('Runtime variables must be a string map')
        # A fresh cwd blocks implicit .env discovery in both SDK and dependencies.
        with tempfile.TemporaryDirectory(prefix='onedeploy-daytona-') as directory:
            old_cwd = os.getcwd()
            try:
                os.chdir(directory)
                with open(os.devnull,'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams, Image, CreateSnapshotParams, VolumeMount
                    from daytona.common.errors import DaytonaNotFoundError
                    client = Daytona(DaytonaConfig(api_key=api_key,api_url=API_URL,
                                                  target=config['target'].get('region','us'),otel_enabled=False))
                    # Conservative: any later exception during write workflow is uncertain.
                    mutation_attempted = mutating
                    result = execute(operation,config,runtime,client,DaytonaNotFoundError,CreateSandboxFromSnapshotParams,Image,CreateSnapshotParams,VolumeMount)
            finally:
                os.chdir(old_cwd)
        print(json.dumps(result))
        return 0
    except Exception:
        # Exceptions may carry bearer headers, env maps, or signed URLs. Never serialize them.
        print(json.dumps({'error':'uncertain' if mutation_attempted else 'provider_failed',
                          'mutation_possible':mutation_attempted}))
        return 7 if mutation_attempted else 6


if __name__ == '__main__':
    sys.exit(main())
