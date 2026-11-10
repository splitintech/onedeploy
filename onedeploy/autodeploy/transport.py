"""Concrete owner-checked Daytona worker-pool transport.

Payloads travel through the authenticated SDK filesystem/process channel, never
in shell arguments or public preview links. No client is created at import time.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
import time
import uuid

from .daytona import DaytonaContractError, worker_control, _value
from .models import digest

MOUNT = '/opt/onedeploy/models'
TOKEN = '/tmp/od-worker-token'


@dataclass(frozen=True)
class RuntimeConfig:
    model_volume_id: str
    model_subpath: str
    model_directory: str
    model_manifest: str
    synthetic_audio: str
    synthetic_text: str
    schema_version: int = 1

    @classmethod
    def from_dict(cls, raw):
        required = {'schema_version','model_volume_id','model_subpath','model_directory','model_manifest','synthetic_audio','synthetic_text'}
        if not isinstance(raw,dict) or set(raw)!=required or type(raw['schema_version']) is not int or raw['schema_version']!=1:
            raise DaytonaContractError('Invalid worker runtime configuration')
        if not isinstance(raw['model_volume_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',raw['model_volume_id']):
            raise DaytonaContractError('Invalid existing model volume identity')
        for key in ('model_subpath','model_directory','model_manifest','synthetic_audio'):
            value=raw[key]
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}',value) or PurePosixPath(value).is_absolute() or any(part in ('..','.') for part in value.split('/')) or '//' in value or value.endswith('/'):
                raise DaytonaContractError('Model paths must be bounded relative paths')
        if not isinstance(raw['synthetic_text'],str) or not 1<=len(raw['synthetic_text'])<=500 or '\x00' in raw['synthetic_text']:
            raise DaytonaContractError('A bounded synthetic qualification transcript is required')
        root=PurePosixPath(raw['model_directory'])
        if any(PurePosixPath(raw[k]).is_relative_to(root) for k in ('model_manifest','synthetic_audio')):
            raise DaytonaContractError('Manifest and qualification fixture must be outside the model directory')
        return cls(**raw)

    def to_dict(self):
        return asdict(self)


def _allocation(release,operation):
    return release['release_id'] if operation=='candidate' else hashlib.sha256((release['release_id']+'/'+operation).encode()).hexdigest()


class DaytonaPoolTransport:
    def __init__(self, adapter, runtime, *, worker_ttl_seconds, ledger=None, job_timeout_seconds=60,
                 volume_type=None, session_request_type=None, clock=time.monotonic, sleep=time.sleep):
        self.adapter=adapter
        self.runtime=RuntimeConfig.from_dict(runtime.to_dict() if isinstance(runtime,RuntimeConfig) else runtime)
        if type(worker_ttl_seconds) is not int or not 60<=worker_ttl_seconds<=86400 or worker_ttl_seconds%60:
            raise DaytonaContractError('Worker TTL must be whole minutes between one minute and 24 hours')
        self.ttl=worker_ttl_seconds
        if type(job_timeout_seconds) is not int or not 1<=job_timeout_seconds<=3600 or job_timeout_seconds>=worker_ttl_seconds:
            raise DaytonaContractError('Invalid bounded inference timeout')
        self.job_timeout=job_timeout_seconds
        self.ledger=ledger
        self.clock,self.sleep=clock,sleep
        self.volume_type,self.session_request_type=volume_type,session_request_type
        if ledger:
            with ledger.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS od_worker_allocations (worker TEXT PRIMARY KEY, release TEXT NOT NULL, allocation TEXT NOT NULL)')

    def _remember(self,worker,release):
        if self.ledger:
            with self.ledger.connect() as db:
                existing=db.execute('SELECT release,allocation FROM od_worker_allocations WHERE worker=?',(worker.id,)).fetchone()
                value=(release['release_id'],worker.labels['onedeploy-allocation'])
                if existing and tuple(existing)!=value:
                    raise DaytonaContractError('Persisted worker allocation mismatch')
                db.execute('INSERT OR IGNORE INTO od_worker_allocations VALUES (?,?,?)',(worker.id,*value))

    def _retire_absent(self,worker_id,release):
        if self.ledger:
            with self.ledger.connect() as db:
                found=db.execute('SELECT allocation FROM od_worker_allocations WHERE worker=? AND release=?',(worker_id,release['release_id'])).fetchone()
            if found:
                self.ledger.retire(found[0])

    def _constructors(self):
        if self.volume_type is None or self.session_request_type is None:
            from daytona import VolumeMount, SessionExecuteRequest
            self.volume_type,self.session_request_type=VolumeMount,SessionExecuteRequest

    def _labels(self,release,operation):
        if not isinstance(operation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',operation):
            raise DaytonaContractError('Invalid allocation operation identity')
        return {**self.adapter._labels(release['release_id']),
                'onedeploy-allocation':_allocation(release,operation),
                'onedeploy-runtime':digest(self.runtime.to_dict())}

    def _name(self,release,operation):
        return self.adapter._name(_allocation(release,operation),'pool')

    def _owned(self,worker_id,release):
        self.adapter._release(release)
        worker=self.adapter._owned_worker(worker_id,release['release_id'])
        labels=getattr(worker,'labels',{})
        if labels.get('onedeploy-runtime')!=digest(self.runtime.to_dict()) or not re.fullmatch(r'[0-9a-f]{64}',labels.get('onedeploy-allocation','')):
            raise DaytonaContractError('Worker runtime or allocation ownership mismatch')
        if worker.snapshot!=release['snapshot_id']:
            raise DaytonaContractError('Worker snapshot identity mismatch')
        return worker

    def _volume(self):
        volume=self.adapter.client.volume.get(self.runtime.model_volume_id,create=False)
        if volume.organization_id!=self.adapter.account or _value(volume.state)!='ready':
            raise DaytonaContractError('Existing model volume is not ready or belongs to another account')
        return volume

    def _env(self,release):
        r=self.runtime
        return {'ONEDEPLOY_WORKER_TOKEN_FILE':TOKEN,'ONEDEPLOY_STATE_DIR':'/var/lib/onedeploy',
                'ONEDEPLOY_MODEL_DIR':MOUNT+'/'+r.model_directory,
                'ONEDEPLOY_MODEL_MANIFEST':MOUNT+'/'+r.model_manifest,
                'ONEDEPLOY_SYNTHETIC_AUDIO':MOUNT+'/'+r.synthetic_audio,
                'ONEDEPLOY_SYNTHETIC_TEXT':r.synthetic_text,
                'ONEDEPLOY_RELEASE_ID':release['release_id'],
                'ONEDEPLOY_MODEL_DIGEST':release['spec']['model_digest'],
                'ONEDEPLOY_GPU_TYPE':self.adapter.resources['gpu_type'],
                'ONEDEPLOY_WORKER_PORT':'8766','ONEDEPLOY_REQUEST_TIMEOUT_SECONDS':str(self.job_timeout)}

    def create(self,release,operation_id):
        self.adapter._release(release)
        labels=self._labels(release,operation_id)
        allocation=labels['onedeploy-allocation']
        self.adapter._admission({**release,'allocation_id':allocation})
        self._constructors()
        self._volume()
        snapshot=self.adapter.client.snapshot.get(release['snapshot_id'])
        if snapshot.organization_id!=self.adapter.account or snapshot.image_name!=release['spec']['image_digest'] or _value(snapshot.state) not in ('ready','active'):
            raise DaytonaContractError('Snapshot no longer matches approved release')
        for field,attr in (('cpu','cpu'),('memory','mem'),('disk','disk'),('gpu','gpu')):
            if getattr(snapshot,attr,None)!=self.adapter.resources[field]:
                raise DaytonaContractError('Snapshot resources changed since approval')
        name=self._name(release,operation_id)
        try:
            existing=self.adapter.client.get(name,request_timeout=self.adapter.timeout)
        except self.adapter.bindings.missing_error:
            existing=None
        if existing is not None:
            # Never rotate credentials/restart an uncertain existing worker.
            self._owned(existing.id,release)
            if any(getattr(existing,'labels',{}).get(k)!=v for k,v in labels.items()):
                raise DaytonaContractError('Existing worker allocation mismatch')
            self._remember(existing,release)
            return existing.id
        params=self.adapter.bindings.sandbox_params(name=name,snapshot=release['snapshot_id'],
            resources=dict(self.adapter.resources),labels=labels,public=False,
            auto_stop_interval=0,auto_delete_interval=0,ttl_minutes=self.ttl//60,
            env_vars=self._env(release),
            volumes=[self.volume_type(volume_id=self.runtime.model_volume_id,mount_path=MOUNT,subpath=self.runtime.model_subpath)])
        worker=self.adapter.client.create(params,timeout=self.adapter.timeout)
        worker=self._owned(worker.id,release)
        self._remember(worker,release)
        # Fresh random token, never in env vars, command arguments or controller logs.
        worker.fs.upload_file(secrets.token_urlsafe(48).encode(),TOKEN,timeout=self.adapter.timeout)
        secured=worker.process.exec('chmod 600 '+TOKEN,timeout=30)
        if secured.exit_code!=0:
            raise DaytonaContractError('Cannot secure worker token')
        worker.process.create_session('onedeploy-worker',request_timeout=self.adapter.timeout)
        worker.process.execute_session_command('onedeploy-worker',
            self.session_request_type(command='python3 /opt/onedeploy/worker.py',run_async=True),timeout=self.adapter.timeout)
        return worker.id

    def inspect_create(self,release,operation_id):
        self.adapter._release(release)
        try:
            worker=self.adapter.client.get(self._name(release,operation_id),request_timeout=self.adapter.timeout)
        except self.adapter.bindings.missing_error:
            return None
        self._owned(worker.id,release)
        if getattr(worker,'labels',{}).get('onedeploy-allocation')!=_allocation(release,operation_id):
            raise DaytonaContractError('Allocation inspection mismatch')
        self._remember(worker,release)
        return worker.id

    def qualify(self,worker_id,release):
        worker=self._owned(worker_id,release)
        if _value(worker.state)!='started':
            return False
        status=worker_control(worker,'qualify-status')
        if status.get('completed') is True:
            evidence=status.get('evidence')
        elif status.get('in_progress') is True:
            return False  # Unknown/in-flight qualification must never be replayed.
        elif status.get('completed') is False and status.get('in_progress') is False:
            evidence=worker_control(worker,'qualify')
        else:
            raise DaytonaContractError('Qualification receipt is malformed or unknown')
        result=self.adapter._qualification_result(evidence,release['release_id'],release['spec']['model_digest'])
        return all(result.get(k) is True for k in ('ready','gpu','model_verified','inference_passed'))

    def infer(self,worker_id,release,payload):
        worker=self._owned(worker_id,release)
        if not isinstance(payload,dict) or set(payload)!={'job_id','audio_base64'}:
            raise DaytonaContractError('Invalid inference payload')
        raw=json.dumps(payload,allow_nan=False,separators=(',',':')).encode()
        if len(raw)>14*1024*1024:
            raise DaytonaContractError('Inference request too large')
        path='/tmp/od-job-'+str(uuid.uuid4())+'.json'
        try:
            worker.fs.upload_file(raw,path,timeout=self.adapter.timeout)
            permission=worker.process.exec('chmod 600 '+path,timeout=30)
            if permission.exit_code!=0:
                raise DaytonaContractError('Cannot secure inference payload')
            response=worker.process.exec('/opt/onedeploy/worker-control infer --input '+path+' --json',timeout=self.job_timeout)
            if response.exit_code!=0 or not isinstance(response.result,str) or len(response.result)>1024*1024:
                raise DaytonaContractError('Inference did not return bounded success')
            result=json.loads(response.result)
            if not isinstance(result,dict) or set(result)!={'job_id','text','release_id'} or result['job_id']!=payload['job_id'] or result['release_id']!=release['release_id'] or not isinstance(result['text'],str):
                raise DaytonaContractError('Inference result identity mismatch')
            return result
        finally:
            try:
                worker.fs.delete_file(path,request_timeout=self.adapter.timeout)
            except Exception:
                # Bounded worker TTL is the final retention bound; do not mask
                # uncertain inference with another automatic inference attempt.
                pass

    def drain(self,worker_id,release):
        try:
            worker=self._owned(worker_id,release)
        except self.adapter.bindings.missing_error:
            return True
        result=worker_control(worker,'drain')
        if not isinstance(result,dict) or result.get('accepting_jobs') is not False or type(result.get('active_jobs')) is not int or result['active_jobs']<0:
            raise DaytonaContractError('Invalid worker drain acknowledgement')
        return result['active_jobs']==0

    def delete(self,worker_id,release):
        try:
            worker=self._owned(worker_id,release)
        except self.adapter.bindings.missing_error:
            # Retire only a previously persisted exact worker/release mapping.
            self._retire_absent(worker_id,release)
            return
        allocation=worker.labels['onedeploy-allocation']
        if not self.drain(worker_id,release):
            raise DaytonaContractError('Active work prevents deletion')
        self.adapter.client.delete(worker,timeout=self.adapter.timeout)
        try:
            self.adapter.client.get(worker_id,request_timeout=self.adapter.timeout)
        except self.adapter.bindings.missing_error:
            if self.ledger:
                self.ledger.retire(allocation)
            return
        raise DaytonaContractError('Deletion not yet confirmed')

    def release_adapter(self):
        return LiveReleaseAdapter(self)


class LiveReleaseAdapter:
    def __init__(self,transport):
        self.transport=transport
        self.adapter=transport.adapter

    def ensure_snapshot(self,release):
        return self.adapter.ensure_snapshot(release)

    def ensure_candidate(self,release):
        return self.transport.create(release,'candidate')

    def qualify(self,release):
        # Loading is allowed to finish within the approved candidate timeout.
        deadline=self.transport.clock()+release['config']['candidate_timeout_seconds']
        while True:
            try:
                ok=self.transport.qualify(release['worker_id'],release)
                if ok:
                    return {k:True for k in ('ready','gpu','model_verified','inference_passed')}
                worker=self.transport._owned(release['worker_id'],release)
                status=worker_control(worker,'qualify-status')
                if status.get('completed') is True:
                    return {k:False for k in ('ready','gpu','model_verified','inference_passed')}
                if self.transport.clock()>=deadline:
                    raise DaytonaContractError('Candidate qualification remains uncertain')
                self.transport.sleep(1)
            except Exception:
                if self.transport.clock()>=deadline:
                    raise DaytonaContractError('Candidate qualification remains uncertain') from None
                self.transport.sleep(1)

    def drain(self,release):
        return self.transport.drain(release['worker_id'],release)

    def delete_worker(self,release):
        result=self.transport.delete(release['worker_id'],release)
        if self.transport.ledger:
            # release adapter only owns deterministic candidate allocation.
            try:
                self.adapter.client.get(release['worker_id'],request_timeout=self.adapter.timeout)
            except self.adapter.bindings.missing_error:
                self.transport.ledger.retire(release['release_id'])
        return result

    def inspect_operation(self,release,operation):
        if operation=='ensure_snapshot':
            return self.adapter.inspect_operation(release,operation)
        if operation=='ensure_candidate':
            found=self.transport.inspect_create(release,'candidate')
            return {'resolved':True,'result':found} if found else None
        if operation in {'qualify','drain','delete','delete_worker'}:
            try:
                self.transport._owned(release['worker_id'],release)
            except self.adapter.bindings.missing_error:
                if operation not in {'delete','delete_worker'}:
                    return None
        return self.adapter.inspect_operation(release,operation)
