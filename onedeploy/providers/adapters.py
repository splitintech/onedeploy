"""Versioned, fail-closed official CLI/SDK adapters.

Only the core transport runs commands. CLI exit zero is never readiness evidence.
"""
import json
import copy
import hashlib
import hmac
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from ..errors import OnedeployError

IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,254}$')
RESERVED = ('RAILWAY_', 'DAYTONA_', 'MODAL_', 'PYTHON', 'LD_', 'DYLD_')


def fail(message, code='unsupported'):
    raise OnedeployError(code, message)


def step(operation, argv, mutating=False, env_keys=(), **extra):
    return {'id': operation, 'argv': argv, 'mutating': mutating,
            'env_keys': list(env_keys), **extra}


def safe_json(text):
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        fail('Provider returned malformed JSON', 'provider_failed')
    if not isinstance(data, (dict, list)):
        fail('Provider returned an unexpected response shape', 'provider_failed')
    return data


def pick(data, fields):
    return {key: data[key] for key in fields if key in data and isinstance(data[key], (str, int, float, bool, type(None)))}


def nodes(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [x.get('node', x) for x in value.get('edges', []) if isinstance(x, dict)]
    return []


class BaseAdapter:
    version = '1'
    qualification = 'experimental/offline-tested'
    target_fields = set()
    required_fields = set()
    profiles = set()

    def validate(self, manifest):
        if manifest.get('provider') != self.name:
            fail('Provider does not match adapter', 'invalid_config')
        if manifest.get('profile') not in self.profiles:
            fail('This provider/profile combination is not implemented')
        if self.name != 'modal' and 'entrypoint' in manifest.get('source',{}):
            fail('source.entrypoint is supported only by Modal; container startup belongs in the artifact','invalid_config')
        if 'image' in manifest.get('build',{}):
            fail('build.image is unsupported; specify a reviewed Dockerfile or existing snapshot','invalid_config')
        target = manifest.get('target', {})
        if set(target) - self.target_fields or not self.required_fields <= set(target):
            fail('Target fields do not match provider contract', 'invalid_config')
        for key, value in target.items():
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                fail('Target must contain explicit safe identifiers', 'invalid_config')
        if manifest.get('storage') and self.name not in {'daytona','railway'}:
            fail('Managed storage changes are not implemented')
        for key in manifest.get('variables', {}):
            if key.startswith(RESERVED) or key in {'PATH','HOME','SHELL','BASH_ENV','ENV'}:
                fail('Application variables cannot override control-plane/process configuration', 'invalid_config')
        if manifest.get('ingress', {}).get('mode', 'existing') not in {'private','existing'}:
            fail('Ingress changes are not implemented')
        self.readiness_endpoint(manifest)
        return []

    @property
    def capabilities(self):
        return {'adapter_version':self.version,'qualification':self.qualification,
                'profiles':sorted(self.profiles),'operations':['deploy','identity','inspect','status','reconcile'],
                'logs':True,'rollback':True,
                'readiness_verified':False,'managed_storage':self.name in {'railway','daytona'},'public_ingress_changes':False,
                'artifact_types':{'railway':['sanitized-source-bundle'],'daytona':['immutable-snapshot','sanitized-dockerfile-context'],'modal':['trusted-python-source']}[self.name],
                'resource_targeting':'explicit-account-and-resource-identifiers',
                'listener_port':'localhost probe destination' if self.name=='daytona' else 'Descriptive metadata only; does not configure or verify provider listener routing. Startup is owned by the Railway Dockerfile/PORT or Modal Python web function.',
                'ingress_modes':['existing'] + (['private'] if self.name=='daytona' else []),
                'persistent_storage':{'railway':'existing detached volume attach or verify current mount; no deletion/reassignment','daytona':'existing organization volumes shared read-write on new sandbox; no exclusivity guarantee','modal':'application-declared storage only'}[self.name],
                'secret_delivery':{'railway':'per-variable stdin','daytona':'runtime JSON stdin; never build inputs','modal':'trusted-source process environment'}[self.name],
                'lifecycle_assumptions':{'railway':'deploy consumes compute; attached volume storage persists','daytona':'snapshot build and storage incur ongoing costs; sandbox idle-stop is not deletion; shared volumes outlive sandbox','modal':'deployment imports trusted source; function compute and declared storage incur costs'}[self.name]}


    @property
    def supported_version(self):
        return {'railway':'5.71.0','modal':'1.6.2','daytona':'0.226.0'}[self.name]

    def version_command(self):
        if self.name == 'daytona':
            argv = [sys.executable,'-I',str(Path(__file__).with_name('daytona_bridge.py').resolve()),'version','{}']
        else:
            argv = [self.name,'--version']
        return step('version',argv)

    def parse_version(self, stdout):
        matches = re.findall(r'(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])',stdout)
        if len(matches)!=1 or matches[0] != self.supported_version:
            fail('Provider version differs from the offline-tested compatibility pin','unsupported')
        return {'provider':self.name,'version':matches[0],'qualification':self.qualification}

    def readiness_endpoint(self, manifest, remote=None):
        url = manifest.get('runtime',{}).get('ready_url')
        if not url:
            return None
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (ValueError,TypeError):
            fail('Invalid readiness URL','invalid_config')
        suffix = {'railway':'.up.railway.app','modal':'.modal.run'}.get(self.name)
        if (suffix is None or parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith(suffix)
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or port not in (None,443)):
            fail('Readiness URL must be a credential-free official provider HTTPS origin; use ready_path separately','invalid_config')
        return url.rstrip('/')

    def plan(self, manifest, release_tag=None):
        if release_tag is not None:
            if self.name != 'modal':
                fail('Release tags are supported only by Modal','invalid_config')
            return self.plan_steps(manifest,release_tag=release_tag)
        return self.plan_steps(manifest)

    def rollback_steps(self, manifest, release):
        fail('Rollback is not implemented for this provider; restore a reviewed prior artifact with a new deployment plan')

    def parse(self, operation, stdout):
        if operation == 'version':
            return self.parse_version(stdout)
        if operation == 'logs':
            return {'logs': stdout, 'redaction': 'best-effort; logs may contain sensitive application data'}
        if operation.startswith('set-variable:') or operation in {'deploy','rollback','start'}:
            return {'provider_command_completed': True, 'readiness_verified': False}
        return safe_json(stdout)


class RailwayAdapter(BaseAdapter):
    requires_binding_key = True
    name = 'railway'
    target_fields = required_fields = {'account','project','environment','service'}
    profiles = {'docker-http','asgi','worker'}
    credentials = ['RAILWAY_API_TOKEN']

    def validate(self, manifest):
        super().validate(manifest)
        if manifest.get('ingress',{}).get('mode') == 'private':
            fail('Railway private ingress must be independently configured; this adapter only preserves existing ingress')
        if manifest.get('runtime',{}).get('command'):
            fail('Railway startup command must be encoded in the reviewed Dockerfile or provider configuration')
        build = manifest.get('build',{})
        if 'snapshot_name' in build:
            fail('build.snapshot_name is supported only by Daytona','invalid_config')
        if build.get('context','.') != '.' or build.get('dockerfile','Dockerfile') != 'Dockerfile':
            fail('Railway currently supports root Dockerfile/context only')
        if manifest.get('resources'):
            fail('Railway resource changes must be configured outside this adapter')
        if len(manifest.get('storage',[])) > 1:
            fail('Railway supports one attached volume per service')
        for volume in manifest.get('storage',[]):
            if volume.get('attachment') not in {'attach','existing'}:
                fail('Railway storage requires explicit attachment: attach or existing','invalid_config')
            if volume.get('access') != 'read-write' or volume.get('single_writer') is not True:
                fail('Railway existing volume attachment requires read-write and single_writer true')
            if not IDENTIFIER.fullmatch(volume.get('resource_id','')) or not re.fullmatch(r'/[A-Za-z0-9_/-]+',volume.get('mount_path','')):
                fail('Invalid existing volume ID or mount path','invalid_config')
        return []

    def plan_steps(self, manifest):
        self.validate(manifest)
        t = manifest['target']
        steps = [step('set-variable:'+key,
                      ['railway','variable','set',key,'--stdin','--skip-deploys','--project',t['project'],'--service',t['service'],'--environment',t['environment']],
                      True, self.credentials, stdin_env_keys=[key], stdin_format='raw')
                 for key in sorted(manifest.get('variables', {}))]
        for volume in manifest.get('storage',[]):
            if volume['attachment'] == 'existing':
                continue
            steps.insert(0,step('attach-volume:'+volume['resource_id'],['railway','volume','--project',t['project'],'--environment',t['environment'],'--service',t['service'],'attach','--volume',volume['resource_id'],'--yes','--json'],True,self.credentials))
        steps.append(step('deploy', ['railway','up','.', '--project',t['project'], '--service',t['service'],
                                    '--environment',t['environment'],'--ci','--json'], True, self.credentials))
        return steps

    def command(self, manifest, operation):
        self.validate(manifest)
        t = manifest['target']
        if operation == 'identity':
            argv = ['railway','whoami','--json']
        elif operation in {'inspect','status','reconcile'}:
            argv = ['railway','status','--project',t['project'],'--environment',t['environment'],'--json']
        elif operation == 'logs':
            argv = ['railway','logs','--project',t['project'],'--service',t['service'],
                    '--environment',t['environment'],'--lines','100','--json']
        else:
            fail('Unsupported Railway operation')
        return step(operation, argv, env_keys=self.credentials)

    def parse(self, operation, stdout, binding_key=None):
        if operation == 'deploy':
            decoder = json.JSONDecoder()
            cursor, records = 0, []
            while cursor < len(stdout):
                while cursor < len(stdout) and stdout[cursor].isspace():
                    cursor += 1
                if cursor == len(stdout):
                    break
                try:
                    value, cursor = decoder.raw_decode(stdout,cursor)
                except ValueError:
                    fail('Railway deployment result did not match its JSON stream contract','provider_failed')
                if isinstance(value,dict):
                    records.append(value)
            required = {'deploymentId','projectId','environmentId','serviceId','status'}
            if not records or not required <= records[-1].keys():
                fail('Railway did not return a bound deployment identifier','provider_failed')
            result = records[-1]
            if any(not isinstance(result[k],str) or not IDENTIFIER.fullmatch(result[k]) for k in required):
                fail('Railway deployment identifiers were malformed','provider_failed')
            return {'provider_command_completed':True,'readiness_verified':False,
                    'deployment_id':result['deploymentId'],
                    'accepted_project_id':result['projectId'],'accepted_environment_id':result['environmentId'],
                    'accepted_service_id':result['serviceId'],'provider_status':result['status']}
        if operation.startswith('attach-volume:'):
            data = safe_json(stdout)
            if not isinstance(data,dict) or data.get('success') is not True:
                fail('Volume attachment did not return success','provider_failed')
            return {'volume_id':operation.split(':',1)[1],'provider_command_completed':True}
        if operation in {'identity','inspect','status','reconcile'}:
            data = safe_json(stdout)
            if not isinstance(data,dict):
                fail('Railway returned an unexpected response shape','provider_failed')
            if operation == 'identity':
                return pick(data, ['id','email'])
            result = pick(data,['id','name'])
            result['environments'] = [pick(n,['id','name']) for n in nodes(data.get('environments'))]
            result['volumes'] = []
            for environment in nodes(data.get('environments')):
                for volume in nodes(environment.get('volumeInstances')):
                    item = pick(volume,['serviceId','mountPath','environmentId','state','deletedAt','isPendingDeletion'])
                    item.update(pick(volume.get('volume') or {},['id','name']))
                    result['volumes'].append(item)
            result['services'] = []
            for service in nodes(data.get('services')):
                item = pick(service,['id','name'])
                item['instances'] = []
                for instance in nodes(service.get('serviceInstances')):
                    binding = self._settings_binding(instance,binding_key)
                    inst = pick(instance,['id','environmentId','serviceId','rootDirectory','region','numReplicas'])
                    inst['settings_binding'] = binding
                    latest = instance.get('latestDeployment') or {}
                    inst['deployment'] = pick(latest,['id','status','createdAt'])
                    item['instances'].append(inst)
                result['services'].append(item)
            # Railway 5.71 places instances under environments, not service nodes.
            services = {item['id']:item for item in result['services'] if 'id' in item}
            for environment in nodes(data.get('environments')):
                for instance in nodes(environment.get('serviceInstances')):
                    service = services.get(instance.get('serviceId'))
                    if service is None:
                        continue
                    binding = self._settings_binding(instance,binding_key)
                    inst = pick(instance,['id','environmentId','serviceId','rootDirectory','region','numReplicas'])
                    inst['settings_binding'] = binding
                    inst['deployment'] = pick(instance.get('latestDeployment') or {},['id','status','createdAt'])
                    domains = (instance.get('domains') or {}).get('serviceDomains') or []
                    inst['domains'] = [d['domain'] for d in domains if isinstance(d,dict) and isinstance(d.get('domain'),str)
                                       and re.fullmatch(r'[A-Za-z0-9-]+\.up\.railway\.app',d['domain'])]
                    service['instances'].append(inst)
            return result
        return super().parse(operation, stdout)

    def readiness_endpoint(self, manifest, remote=None):
        explicit = super().readiness_endpoint(manifest,remote)
        if remote is None:
            return None
        target = manifest['target']
        domains = []
        for service in remote.get('services',[]):
            if service.get('id') != target['service']:
                continue
            for instance in service.get('instances',[]):
                if instance.get('environmentId') == target['environment']:
                    domains.extend(instance.get('domains',[]))
        # An ambiguous set requires an explicit reviewed URL, never choose silently.
        if explicit:
            return explicit if urlsplit(explicit).hostname in domains else None
        if len(set(domains)) == 1:
            return 'https://' + domains[0]
        return None

    def assert_target(self, manifest, identity, inspection):
        t = manifest['target']
        if t['account'] not in {identity.get('id'),identity.get('email')}:
            fail('Railway authenticated account does not match target','authentication_failed')
        if inspection.get('id') != t['project']:
            fail('Observed Railway project differs from target','stale_plan')
        if not any(e.get('id') == t['environment'] for e in inspection.get('environments', [])):
            fail('Railway environment ID was not verified','stale_plan')
        service = next((s for s in inspection.get('services',[]) if s.get('id') == t['service']), None)
        if service is None or not any(i.get('environmentId') == t['environment'] for i in service.get('instances',[])):
            fail('Railway service instance in the target environment was not verified','stale_plan')
        for requested in manifest.get('storage',[]):
            instances = [i for i in service.get('instances',[]) if i.get('environmentId')==t['environment']]
            if len(instances)!=1 or instances[0].get('numReplicas')!=1:
                fail('Single-writer volume requires one verified service replica','stale_plan')
            matches = [v for v in inspection.get('volumes',[]) if v.get('id') == requested['resource_id'] and v.get('environmentId') == t['environment']]
            if len(matches)!=1 or matches[0].get('mountPath') != requested['mount_path'] or matches[0].get('isPendingDeletion') or matches[0].get('deletedAt'):
                fail('Existing Railway volume identity/mount is not verified','stale_plan')
            owner = matches[0].get('serviceId')
            if owner not in {None,t['service']} or (requested['attachment']=='existing' and owner!=t['service']):
                fail('Volume is attached to another service or expected attachment is absent','stale_plan')
        return {'identity':identity,'inspection':inspection}


    def rollback_steps(self, manifest, release, artifact=None):
        # Core restores the reviewed historical bundle/configuration before execution.
        if not isinstance(release,str) or not IDENTIFIER.fullmatch(release):
            fail('Rollback requires a known local deployment identifier','invalid_config')
        return self.plan_steps(manifest)

    @staticmethod
    def _settings_binding(instance, key):
        settings = {k:v for k,v in instance.items() if k not in {'latestDeployment','activeDeployments','domains','nextCronRunAt'}}
        if key is None:
            if any(settings.get(k) for k in ('startCommand','source','numReplicas','cronSchedule')):
                fail('Railway settings require keyed precondition binding','provider_failed')
            return None
        return hmac.new(key,json.dumps(settings,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()

    def assert_preconditions(self, manifest, planned, observed, completed_operations):
        expected = copy.deepcopy(planned)
        for volume in manifest.get('storage',[]):
            receipt = [r for r in completed_operations if r['id']=='attach-volume:'+volume['resource_id'] and r['state']=='succeeded']
            matches = [v for v in expected['inspection'].get('volumes',[]) if v.get('id')==volume['resource_id'] and v.get('environmentId')==manifest['target']['environment']]
            if volume['attachment']=='attach':
                if not matches or matches[0].get('serviceId') is not None:
                    fail('Attach plan requires an unmounted existing volume','stale_plan')
                if receipt:
                    matches[0]['serviceId'] = manifest['target']['service']
        if expected != observed:
            fail('Railway remote preconditions changed','stale_plan')


class ModalAdapter(BaseAdapter):
    name = 'modal'
    target_fields = {'account','environment','app','mode','function'}
    required_fields = {'account','environment','app'}
    profiles = {'asgi'}

    def validate(self, manifest):
        super().validate(manifest)
        if manifest.get('ingress',{}).get('mode') == 'private':
            fail('Modal proxy authentication is not private network ingress; use existing and review source authentication')
        if manifest['target'].get('mode','existing') not in {'existing','create'}:
            fail('Modal target.mode must be existing or create','invalid_config')
        runtime = manifest.get('runtime',{})
        if runtime.get('command'):
            fail('Modal startup must be declared in reviewed Python source')
        if (manifest.get('checks') or any(runtime.get(k) for k in ('health_path','ready_path','ready_url'))) and not manifest['target'].get('function'):
            fail('Modal HTTP verification requires target.function','invalid_config')
        source = manifest.get('source', {})
        entry = source.get('entrypoint','')
        if not isinstance(entry,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*\.py',entry) or '..' in Path(entry).parts:
            fail('Modal requires a relative Python source.entrypoint','invalid_config')
        if source.get('trust_code') is not True:
            fail('Modal imports and executes local deployment code: set source.trust_code only after reviewing it')
        if manifest.get('resources') or manifest.get('build'):
            fail('Modal image/resources must be declared in reviewed Python source, not generic container settings')
        return []

    def plan_steps(self, manifest, release_tag=None):
        self.validate(manifest)
        t = manifest['target']
        argv = ['modal','deploy',manifest['source']['entrypoint'],'--name',t['app'],'--env',t['environment']]
        if release_tag is not None:
            if not isinstance(release_tag,str) or not re.fullmatch(r'od-[a-f0-9]{32,64}',release_tag):
                fail('Modal release tag must be a plan-generated digest','invalid_config')
            argv += ['--tag',release_tag]
        return [step('deploy',argv,True,runtime_env_keys=sorted(manifest.get('variables',{})))]

    def command(self, manifest, operation, release_tag=None):
        self.validate(manifest)
        t = manifest['target']
        if operation == 'identity':
            argv = ['modal','profile','list','--json']
        elif operation == 'inspect' and t.get('mode') == 'create':
            argv = ['modal','app','list','--env',t['environment'],'--json']
        elif operation in {'inspect','status','reconcile'}:
            argv = ['modal','app','info',t['app'],'--env',t['environment'],'--json']
        elif operation == 'endpoint':
            if not t.get('function'):
                fail('Modal endpoint inspection requires target.function')
            argv = ['modal','function','info',t['app']+'/'+t['function'],'--env',t['environment'],'--json']
        elif operation == 'history':
            argv = ['modal','app','history',t['app'],'--env',t['environment'],'--json']
        elif operation == 'logs':
            argv = ['modal','app','logs',t['app'],'--env',t['environment'],'--tail','100']
        else:
            fail('Unsupported Modal operation')
        return step(operation,argv)

    def parse(self, operation, stdout):
        if operation == 'endpoint':
            data = safe_json(stdout)
            if not isinstance(data,dict) or not isinstance(data.get('web_info'),dict):
                fail('Modal function is not a verifiable HTTP endpoint','provider_failed')
            result = pick(data,['app_id','object_id','name'])
            result.update(pick(data['web_info'],['web_url','unauthenticated']))
            url = result.get('web_url','')
            if any(not isinstance(result.get(k),str) or not result[k] for k in ('app_id','object_id','name','web_url')):
                fail('Modal endpoint identity fields were missing','provider_failed')
            try:
                parsed = urlsplit(url)
                if (parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.modal.run')
                    or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.path not in ('','/') or parsed.query or parsed.fragment):
                    fail('Modal returned a non-official endpoint origin','provider_failed')
            except ValueError:
                fail('Modal returned a malformed endpoint','provider_failed')
            return result
        if operation == 'history':
            data = safe_json(stdout)
            if not isinstance(data,list) or any(not isinstance(r,dict) or not re.fullmatch(r'v[0-9]+',str(r.get('version',''))) for r in data):
                fail('Modal deployment history response shape changed','provider_failed')
            return {'releases':[{'version':r['version'],
                                 **({'tag':r['tag']} if isinstance(r.get('tag'),str) and re.fullmatch(r'od-[a-f0-9]{32,64}',r['tag']) else {})}
                                for r in data]}
        if operation in {'deploy','rollback'}:
            return {'provider_command_completed':True,'readiness_verified':False,'release_binding':'unavailable'}
        if operation == 'identity':
            data = safe_json(stdout)
            if not isinstance(data,list):
                fail('Modal profile response shape changed','provider_failed')
            active = [p for p in data if isinstance(p,dict) and p.get('active') is True]
            if len(active) != 1 or str(active[0].get('workspace','')).startswith('Unknown'):
                fail('A single authenticated saved Modal profile is required','authentication_failed')
            return pick(active[0],['name','workspace','active'])
        if operation in {'inspect','status','reconcile'}:
            data = safe_json(stdout)
            if isinstance(data,list) and operation == 'inspect':
                if any(not isinstance(x,dict) or not x.get('app_id') or 'description' not in x for x in data):
                    fail('Modal app list response shape changed','provider_failed')
                return {'apps':[pick(x,['app_id','description','state']) for x in data]}
            if not isinstance(data,dict) or not data.get('app_id'):
                fail('Modal app response shape changed','provider_failed')
            result = pick(data,['app_id','description','environment_name','state','version'])
            if not isinstance(data.get('functions',{}),dict):
                fail('Modal function map response shape changed','provider_failed')
            result['functions'] = {name:entry['id'] for name,entry in data.get('functions',{}).items()
                                   if isinstance(entry,dict) and isinstance(entry.get('id'),str)}
            if isinstance(data.get('lifecycle'),dict):
                result['lifecycle'] = pick(data['lifecycle'],['state','version','deployed_at','stopped_at'])
            return result
        return super().parse(operation,stdout)

    def assert_target(self, manifest, identity, inspection):
        t = manifest['target']
        if identity.get('workspace') != t['account']:
            fail('Modal authenticated workspace does not match target','authentication_failed')
        if t.get('mode') == 'create':
            if 'apps' not in inspection or any(a.get('description') == t['app'] for a in inspection['apps']):
                fail('Modal app already exists or absence was not verified','stale_plan')
            return {'identity':identity,'inspection':{'exists':False,'app':t['app'],'environment':t['environment']}}
        if inspection.get('description') != t['app']:
            fail('Modal app name does not match target','stale_plan')
        # info is explicitly scoped by environment; IDs rather than names cannot bypass it.
        if t['app'].startswith('ap-'):
            fail('Modal deployment targets must be existing app names, not app IDs','invalid_config')
        return {'identity':identity,'inspection':inspection}

    def correlate_release(self, history, release_tag):
        matches = [r for r in history.get('releases',[]) if r.get('tag') == release_tag]
        if len(matches) != 1:
            return None
        return {'release_version':matches[0]['version'],'release_tag':release_tag}

    def readiness_endpoint(self, manifest, remote=None):
        explicit = super().readiness_endpoint(manifest,remote)
        if remote is None:
            return None
        function = manifest['target'].get('function')
        endpoint = remote.get('function_info',{})
        if (not function or endpoint.get('app_id') != remote.get('app_id')
            or endpoint.get('name') != function or not endpoint.get('object_id')
            or remote.get('functions',{}).get(function) != endpoint['object_id']):
            return None
        observed_url = endpoint.get('web_url')
        if explicit and explicit.rstrip('/') != str(observed_url).rstrip('/'):
            return None
        return observed_url

    def rollback_steps(self, manifest, release):
        self.validate(manifest)
        if not isinstance(release,str) or not re.fullmatch(r'v[0-9]+',release):
            fail('Modal rollback requires a specific vN release','invalid_config')
        t = manifest['target']
        return [step('rollback',['modal','app','rollback',t['app'],release,'--env',t['environment']],True)]


class DaytonaAdapter(BaseAdapter):
    name = 'daytona'
    target_fields = {'account','sandbox','snapshot','region','mode'}
    required_fields = {'account','sandbox'}
    profiles = {'docker-http','asgi','model'}

    def validate(self, manifest):
        super().validate(manifest)
        runtime = manifest.get('runtime',{})
        if manifest.get('checks') or runtime.get('ready_path') or runtime.get('health_path'):
            if type(runtime.get('port')) is not int or not 1 <= runtime['port'] <= 65535:
                fail('Daytona HTTP verification requires a valid runtime.port','invalid_config')
        t = manifest['target']
        mode = t.get('mode','existing')
        if mode not in {'existing','create'}:
            fail('Daytona target.mode must be existing or create','invalid_config')
        if mode == 'create' and not t.get('snapshot') and not manifest.get('build',{}).get('snapshot_name'):
            fail('Daytona create requires an existing reviewed snapshot','invalid_config')
        if mode == 'existing' and 'region' in t:
            fail('Daytona region selects new sandbox placement and cannot change an existing sandbox','invalid_config')
        if mode == 'existing' and manifest.get('resources'):
            fail('Existing Daytona sandbox resource changes are not implemented')
        if mode == 'existing' and manifest.get('variables'):
            fail('Existing Daytona sandbox variables cannot be changed safely; plan a new snapshot sandbox')
        if t.get('region','us') not in {'us','eu'}:
            fail('Daytona region must be us or eu','invalid_config')
        if set(manifest.get('resources',{})) - {'auto_stop_minutes'}:
            fail('Snapshot CPU/memory/disk are inherited, not overridden')
        build = manifest.get('build',{})
        if build:
            if mode != 'create' or t.get('snapshot') or not IDENTIFIER.fullmatch(build.get('snapshot_name','')):
                fail('Project build requires create mode, a new snapshot_name, and no target.snapshot','invalid_config')
            if build.get('context','.') != '.' or build.get('dockerfile','Dockerfile') != 'Dockerfile':
                fail('Daytona project builds require root Dockerfile/context')
        if manifest.get('runtime',{}).get('command'):
            fail('Encode startup in the reviewed Dockerfile')
        for volume in manifest.get('storage',[]):
            if 'attachment' in volume:
                fail('storage.attachment is supported only by Railway','invalid_config')
            if mode != 'create' or volume.get('access') != 'read-write' or volume.get('single_writer') is not False:
                fail('Daytona existing volumes support shared read-write mounts only on new sandboxes')
            if not IDENTIFIER.fullmatch(volume.get('resource_id','')) or not re.fullmatch(r'/[A-Za-z0-9_/-]+',volume.get('mount_path','')) or '..' in volume['mount_path'].split('/'):
                fail('Invalid existing volume name or mount path','invalid_config')
        return []

    def rollback_steps(self, manifest, release, artifact=None):
        self.validate(manifest)
        if manifest['target'].get('mode') != 'create' or manifest.get('build'):
            fail('Daytona rollback creates an explicitly named replacement from a retained snapshot','invalid_config')
        if not isinstance(artifact,dict) or not IDENTIFIER.fullmatch(artifact.get('snapshot_id','')) or not re.search(r'@sha256:[0-9a-f]{64}$',artifact.get('snapshot_image','')):
            fail('Rollback requires the prior immutable snapshot receipt','invalid_config')
        operation = self._step(manifest,'create',True)
        config = json.loads(operation['argv'][-1])
        config['snapshot_receipt'] = pick(artifact,{'snapshot_id','snapshot_image'})
        operation['argv'][-1] = json.dumps(config,sort_keys=True,separators=(',',':'))
        return [operation]

    def materialize_step(self, operation, completed_operations):
        result = copy.deepcopy(operation)
        if operation['id'] == 'create':
            config = json.loads(result['argv'][-1])
            if config.get('build'):
                receipts = [r['result'] for r in completed_operations if r['id']=='snapshot-create' and r['state']=='succeeded']
                if len(receipts)!=1 or not receipts[0].get('snapshot_id') or not re.search(r'@sha256:[0-9a-f]{64}$',receipts[0].get('snapshot_image','')):
                    fail('Project create requires exactly one immutable completed snapshot receipt','stale_plan')
                config['snapshot_receipt'] = pick(receipts[0],{'snapshot_id','snapshot_image'})
                result['argv'][-1] = json.dumps(config,sort_keys=True,separators=(',',':'))
        return result

    def assert_preconditions(self, manifest, planned, observed, completed_operations):
        expected = copy.deepcopy(planned)
        if manifest.get('build'):
            if not expected['inspection'].get('snapshot_pending'):
                fail('Project build requires a previously absent snapshot name','stale_plan')
            receipts = [r['result'] for r in completed_operations if r['id']=='snapshot-create' and r['state']=='succeeded']
            if receipts:
                if len(receipts)!=1:
                    fail('Ambiguous snapshot receipts','stale_plan')
                inspection = expected['inspection']
                inspection.pop('snapshot_pending',None)
                inspection.update(pick(receipts[0],{'snapshot_id','snapshot_image'}))
        if expected != observed:
            fail('Remote preconditions changed','stale_plan')

    def _step(self, manifest, operation, mutating=False):
        config = {'target':manifest['target'], 'build':manifest.get('build',{}), 'checks':manifest.get('checks',[]), 'storage':manifest.get('storage',[]), 'auto_stop_minutes':manifest.get('resources',{}).get('auto_stop_minutes',15),
                  'timeout':manifest.get('runtime',{}).get('timeout',120),
                  'port':manifest.get('runtime',{}).get('port'),
                  'health_path':manifest.get('runtime',{}).get('health_path'),
                  'ready_path':manifest.get('runtime',{}).get('ready_path')}
        argv = [sys.executable,'-I',str(Path(__file__).with_name('daytona_bridge.py').resolve()),
                operation,json.dumps(config,sort_keys=True,separators=(',',':'))]
        extra = {}
        if operation == 'create':
            extra = {'stdin_env_keys':sorted(manifest.get('variables',{})), 'stdin_format':'json'}
        if operation == 'verify-checks':
            extra = {'stdin_env_keys':sorted({c['auth_variable'] for c in manifest.get('checks',[]) if c.get('auth_variable')}),'stdin_format':'json'}
        return step(operation,argv,mutating,['DAYTONA_API_KEY'],**extra)

    def plan_steps(self, manifest):
        self.validate(manifest)
        return ([self._step(manifest,'snapshot-create',True)] if manifest.get('build') else []) + [self._step(manifest,'create' if manifest['target'].get('mode') == 'create' else 'start',True)]

    def command(self, manifest, operation):
        self.validate(manifest)
        if operation not in {'identity','inspect','status','reconcile','logs','verify','verify-checks'}:
            fail('Unsupported Daytona operation')
        if operation == 'verify-checks' and not manifest.get('runtime',{}).get('port'):
            fail('Daytona application checks require runtime.port','invalid_config')
        if operation == 'verify' and (not manifest.get('runtime',{}).get('port') or not (manifest.get('runtime',{}).get('ready_path') or manifest.get('runtime',{}).get('health_path'))):
            fail('Daytona verification requires runtime.port and a health or readiness path')
        return self._step(manifest,operation)

    def parse(self, operation, stdout):
        if operation == 'version':
            return self.parse_version(stdout)
        data = safe_json(stdout)
        if not isinstance(data,dict) or data.get('error'):
            fail('Daytona bridge returned an invalid result','provider_failed')
        # Bridge already emits a deliberately restricted nonsecret projection.
        allowed = {'account','id','name','state','snapshot','public','exists','snapshot_id','snapshot_image','snapshot_pending','auto_stop_minutes','readiness_verified','process_running','health_verified','logs','truncated'}
        result = pick(data,allowed)
        if operation == 'verify-checks':
            result.update(pick(data,{'application_checks_passed','ingress_verified','reason'}))
            result['checks'] = [pick(c,{'name','passed','status','unauthenticated_status'}) for c in data.get('checks',[]) if isinstance(c,dict)]
        if isinstance(data.get('volumes'),list):
            result['volumes'] = [pick(v,{'id','name','mount_path'}) for v in data['volumes'] if isinstance(v,dict)]
        if operation in {'create','start'} and result.get('id'):
            result['accepted_release_id'] = result['id']
        return result

    def assert_target(self, manifest, identity, inspection):
        t = manifest['target']
        if identity.get('account') != t['account']:
            fail('Daytona API key organization does not match target','authentication_failed')
        if inspection.get('exists') is False:
            if t.get('mode') != 'create' or not (inspection.get('snapshot_id') or (manifest.get('build') and inspection.get('snapshot_pending'))):
                fail('Daytona target/snapshot was not verified','stale_plan')
        elif inspection.get('account') != t['account'] or t['sandbox'] not in {inspection.get('id'),inspection.get('name')}:
            fail('Daytona sandbox identity does not match target','stale_plan')
        elif manifest.get('ingress',{}).get('mode') == 'private' and inspection.get('public') is not False:
            fail('Existing Daytona sandbox was not verified private','stale_plan')
        elif t.get('snapshot') and inspection.get('snapshot') != t['snapshot']:
            fail('Existing Daytona sandbox snapshot differs from target','stale_plan')
        elif t.get('mode') == 'create':
            fail('Daytona sandbox already exists; reconcile instead of creating again','stale_plan')
        return {'identity':identity,'inspection':inspection}
