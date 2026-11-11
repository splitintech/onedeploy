import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Obj
import pytest

from onedeploy.autodeploy.daytona import DaytonaSDKAdapter, SDKBindings, DaytonaContractError
from onedeploy.autodeploy.transport import DaytonaPoolTransport, RuntimeConfig, TOKEN

RID='a'*64
IMAGE='ghcr.io/example/worker@sha256:'+'b'*64
RUNTIME=dict(schema_version=1,model_volume_id='models',model_subpath='revision1',model_directory='model',
             model_manifest='manifest.json',synthetic_audio='test.wav',synthetic_text='Hello world')

class Missing(Exception): pass

class Ledger:
    def __init__(self,path): self.path=path;self.retired=[]
    def connect(self): return sqlite3.connect(self.path)
    def retire(self,allocation): self.retired.append(allocation)

class Client:
    def __init__(self):
        self.workers={};self.sessions=[];self.commands=[];self.files={};self.creates=[];self.deleted=[]
        self.volume=Obj(get=lambda *a,**k:Obj(organization_id='account',state='ready'))
        self.snapshot=Obj(get=lambda *a,**k:Obj(organization_id='account',state='ready',image_name=IMAGE,cpu=2,mem=8,disk=20,gpu=1))
        self.evidence=dict(authenticated=True,cuda_available=True,model_on_gpu=True,checksum_verified=True,
                           synthetic_inference_passed=True,release_id=RID,model_digest='c'*64,gpu_type='h100')
        self.qualified=False;self.qualifying=False;self.active=0
    def get(self,name,request_timeout=None):
        if name not in self.workers: raise Missing()
        return self.workers[name]
    def exec(self,command,timeout):
        self.commands.append(command)
        if command.startswith('chmod '): return Obj(exit_code=0,result='')
        if 'qualify-status' in command:
            result={'completed':self.qualified,'in_progress':self.qualifying,'evidence':self.evidence if self.qualified else None}
        elif 'qualify ' in command:
            self.qualified=True;result=self.evidence
        elif 'drain' in command: result={'accepting_jobs':False,'active_jobs':self.active}
        elif 'infer' in command:
            path=command.split('--input ')[1].split(' ')[0]
            payload=json.loads(self.files[path]);result={'job_id':payload['job_id'],'text':'Hello world','release_id':RID}
        else: raise AssertionError(command)
        return Obj(exit_code=0,result=json.dumps(result))
    def create(self,params,timeout):
        self.creates.append(params)
        worker=Obj(id='worker1',name=params['name'],snapshot=params['snapshot'],organization_id='account',
                   labels=params['labels'],public=False,state='started',
                   auto_destroy_at=datetime.now(timezone.utc)+timedelta(minutes=params['ttl_minutes']))
        worker.process=Obj(exec=self.exec,create_session=lambda *a,**k:None,execute_session_command=lambda *a,**k:self.sessions.append(a[1]))
        worker.fs=Obj(upload_file=lambda data,path,**k:self.files.__setitem__(path,data),delete_file=lambda path,**k:self.files.pop(path,None))
        self.workers[worker.id]=self.workers[worker.name]=worker
        return worker
    def delete(self,worker,timeout,wait=False):
        self.deleted.append(worker.id);self.workers.pop(worker.id);self.workers.pop(worker.name)


def fixture(tmp_path):
    client=Client()
    adapter=DaytonaSDKAdapter(client,SDKBindings(lambda **kw:kw,lambda **kw:kw,Missing,'0.231.0'),
        account='account',deployment='demo',resources=dict(cpu=2,memory=8,disk=20,gpu=1,gpu_type='h100'),
        probe=lambda w:client.evidence,drainer=lambda w:{'accepting_jobs':False,'active_jobs':0},authorize_operation=lambda r:True)
    ledger=Ledger(tmp_path/'ledger.sqlite')
    transport=DaytonaPoolTransport(adapter,RUNTIME,worker_ttl_seconds=600,ledger=ledger,
        volume_type=lambda **kw:kw,session_request_type=lambda **kw:kw)
    release=dict(release_id=RID,deployment_id='demo',snapshot_id='snapshot',config=dict(target='account',resources=dict(cpu=2,memory_gib=8,disk_gib=20,gpu_count=1,gpu_type='h100'),candidate_timeout_seconds=10),
                 spec=dict(image_digest=IMAGE,model_digest='c'*64))
    return transport,client,release,ledger


def test_create_mount_bootstrap_and_hard_ttl(tmp_path):
    transport,client,release,ledger=fixture(tmp_path)
    assert transport.create(release,'slot1')=='worker1'
    params=client.creates[0]
    assert params['ttl_minutes']==10 and params['auto_stop_interval']==0
    assert params['volumes'][0]['volume_id']=='models'
    assert params['env_vars']['ONEDEPLOY_MODEL_DIR'].endswith('/model')
    assert len(client.files[TOKEN])>=32
    assert all(client.files[TOKEN].decode() not in command for command in client.commands)
    assert transport.inspect_create(release,'slot1')=='worker1'
    assert transport.create(release,'slot1')=='worker1'
    assert len(client.creates)==1


def test_inference_payload_is_file_not_command_and_deleted(tmp_path):
    transport,client,release,_=fixture(tmp_path);worker=transport.create(release,'slot1')
    result=transport.infer(worker,release,{'job_id':'job1','audio_base64':'c2VjcmV0'})
    assert result['text']=='Hello world'
    assert all('c2VjcmV0' not in command for command in client.commands)
    assert list(client.files)==[TOKEN]


def test_qualification_receipt_prevents_repeat(tmp_path):
    transport,client,release,_=fixture(tmp_path);worker=transport.create(release,'slot1')
    assert transport.qualify(worker,release) is True
    assert transport.qualify(worker,release) is True
    assert sum('qualify --json' in command for command in client.commands)==1
    client.qualified=False;client.qualifying=True
    assert transport.qualify(worker,release) is False
    assert sum('qualify --json' in command for command in client.commands)==1


def test_confirmed_delete_retires_capacity_not_budget(tmp_path):
    transport,client,release,ledger=fixture(tmp_path);worker=transport.create(release,'slot1')
    allocation=client.workers[worker].labels['onedeploy-allocation']
    client.active=1
    with pytest.raises(DaytonaContractError):transport.delete(worker,release)
    assert not ledger.retired
    client.active=0;transport.delete(worker,release)
    assert ledger.retired==[allocation]
    transport.delete(worker,release)
    assert ledger.retired==[allocation,allocation]


def test_foreign_volume_and_runtime_block(tmp_path):
    transport,client,release,_=fixture(tmp_path)
    client.volume=Obj(get=lambda *a,**k:Obj(organization_id='other',state='ready'))
    with pytest.raises(DaytonaContractError):transport.create(release,'slot1')
    assert not client.creates
    with pytest.raises(DaytonaContractError):RuntimeConfig.from_dict({**RUNTIME,'model_manifest':'../secret'})
    with pytest.raises(DaytonaContractError):RuntimeConfig.from_dict({**RUNTIME,'model_manifest':'model/manifest.json'})


def test_wrong_runtime_cannot_infer_or_delete(tmp_path):
    transport,client,release,_=fixture(tmp_path);worker=transport.create(release,'slot1')
    client.workers[worker].labels['onedeploy-runtime']='wrong'
    with pytest.raises(DaytonaContractError):transport.infer(worker,release,{'job_id':'j','audio_base64':'eA=='})
    with pytest.raises(DaytonaContractError):transport.delete(worker,release)
    assert not client.deleted


def test_rollout_candidate_is_adopted_and_routes_job_end_to_end(tmp_path):
    import base64,time
    from onedeploy.autodeploy.state import StateStore
    from onedeploy.autodeploy.reconciler import Reconciler
    from onedeploy.autodeploy.controller import Controller
    from onedeploy.autodeploy.pool import PoolPolicy
    transport,client,_,_=fixture(tmp_path)
    transport.adapter.ensure_snapshot=lambda release:'snapshot'
    store=StateStore(tmp_path/'controller.sqlite')
    engine=Reconciler(store,transport.release_adapter())
    release=engine.plan(dict(deployment_id='demo',repository='example/app',branch='deployment',target='account',
        resources=dict(cpu=2,memory_gib=8,disk_gib=20,gpu_count=1,gpu_type='h100'),max_workers=2,budget_usd=10,candidate_timeout_seconds=10),
        dict(source_commit='d'*40,image_digest=IMAGE,model_digest='c'*64,evidence_digest='e'*64))
    client.evidence['release_id']=release['release_id']
    # Test fake worker response identity follows the actual generated release.
    original=client.exec
    def execute(command,timeout):
        reply=original(command,timeout)
        if 'infer' in command:
            result=json.loads(reply.result);result['release_id']=release['release_id'];reply.result=json.dumps(result)
        return reply
    client.exec=execute
    assert engine.apply(release['release_id'],approved=True)['state']=='complete'
    policy=PoolPolicy(2,1,60,600,10,30,1)
    controller=Controller(store,'demo',transport,policy,10)
    try:
        controller.submit({'job_id':'integration','audio_base64':base64.b64encode(b'audio').decode()})
        for _ in range(30):
            controller.tick()
            if controller.get_job('integration')['state']=='done':break
            time.sleep(.01)
        assert controller.get_job('integration')['result']['text']=='Hello world'
        assert len(client.creates)==1 # No duplicate pool candidate.
    finally:controller.close()


def test_resource_drift_blocks_pool_allocation(tmp_path):
    transport,client,release,_=fixture(tmp_path)
    client.snapshot=Obj(get=lambda *a,**k:Obj(organization_id='account',state='ready',image_name=IMAGE,cpu=64,mem=8,disk=20,gpu=1))
    with pytest.raises(DaytonaContractError):transport.create(release,'slot1')
    assert not client.creates


def test_recovery_rejects_changed_runtime(tmp_path):
    transport,client,release,_=fixture(tmp_path)
    worker=transport.create(release,'candidate')
    release['worker_id']=worker
    client.workers[worker].labels['onedeploy-runtime']='wrong'
    for operation in ('qualify','drain','delete_worker'):
        with pytest.raises(DaytonaContractError):
            transport.release_adapter().inspect_operation(release,operation)


def test_bootstrap_executes_cuda_library_launcher(tmp_path):
    transport,client,release,_=fixture(tmp_path)
    transport.create(release,'candidate')
    assert client.sessions == [{'command':'python3 /opt/onedeploy/launch.py','run_async':True}]


def test_slow_payload_upload_never_dispatches_after_deadline(tmp_path):
    transport,client,release,_=fixture(tmp_path)
    worker=transport.create(release,'candidate')
    now=[1000.0];transport.clock=lambda:now[0]
    old=client.workers[worker].fs.upload_file
    def upload(*args,**kwargs):
        old(*args,**kwargs);now[0]+=transport.job_timeout+1
    client.workers[worker].fs.upload_file=upload
    with pytest.raises(TimeoutError):
        transport.infer(worker,release,{'job_id':'late','audio_base64':'eA=='})
    assert not any('worker-control infer' in cmd for cmd in client.commands)


def test_timeout_termination_stops_active_job_and_confirms_absence(tmp_path):
    transport,client,release,ledger=fixture(tmp_path);worker=transport.create(release,'slot1')
    client.active=1
    assert transport.terminate(worker,release) is True
    assert client.deleted==[worker] and len(ledger.retired)==1
    assert not any('worker-control drain' in command for command in client.commands)
    assert transport.terminate(worker,release) is True
    assert client.deleted==[worker]


def test_uncertain_termination_is_held_and_not_replayed(tmp_path):
    transport,client,release,ledger=fixture(tmp_path);worker=transport.create(release,'slot1')
    calls=[]
    def timeout(*args,**kwargs):calls.append(1);raise TimeoutError('unknown delete')
    client.delete=timeout
    assert transport.terminate(worker,release) is False
    assert transport.terminate(worker,release) is False
    assert calls==[1] and not ledger.retired
    actual=client.workers[worker];client.workers.pop(actual.name);client.workers.pop(worker)
    assert transport.terminate(worker,release) is True
    assert len(ledger.retired)==1


def test_termination_checks_allocation_and_dedicated_worker(tmp_path):
    transport,client,release,ledger=fixture(tmp_path);worker=transport.create(release,'slot1')
    transport.concurrency_per_worker=2
    with pytest.raises(DaytonaContractError):transport.terminate(worker,release)
    transport.concurrency_per_worker=1
    client.workers[worker].labels['onedeploy-allocation']='d'*64
    with pytest.raises(DaytonaContractError):transport.terminate(worker,release)
    assert not client.deleted and not ledger.retired


def test_termination_never_retires_unknown_worker(tmp_path):
    transport,client,release,ledger=fixture(tmp_path)
    with pytest.raises(DaytonaContractError):transport.terminate('unknown',release)
    assert not ledger.retired


def test_timeout_skips_payload_cleanup_so_cancellation_is_immediate(tmp_path):
    transport,client,release,_=fixture(tmp_path);worker=transport.create(release,'slot1')
    original=client.exec
    def execute(command,timeout):
        if 'worker-control infer' in command:raise TimeoutError('execution')
        return original(command,timeout)
    client.workers[worker].process.exec=execute
    def no_cleanup(*a,**k):raise AssertionError('cleanup must not delay cancellation')
    client.workers[worker].fs.delete_file=no_cleanup
    with pytest.raises(TimeoutError):transport.infer(worker,release,{'job_id':'j','audio_base64':'eA=='})
    assert transport.terminate(worker,release) is True


def test_cancel_operation_mismatch_cannot_stop_worker(tmp_path):
    transport,client,release,_=fixture(tmp_path);worker=transport.create(release,'slot1')
    with pytest.raises(DaytonaContractError):transport.cancel_timed_out(worker,release,'slot2')
    assert not client.deleted
    transport.cancel_timed_out(worker,release,'slot1')
    assert client.deleted==[worker]


def test_only_typed_sdk_timeouts_trigger_timeout_classification(monkeypatch):
    import sys
    from onedeploy.autodeploy.transport import _is_timeout
    class SDKTimeout(Exception):pass
    monkeypatch.setitem(sys.modules,'daytona',Obj(DaytonaTimeoutError=SDKTimeout))
    assert _is_timeout(SDKTimeout('deadline')) is True
    assert _is_timeout(RuntimeError('timeout text without timeout type')) is False


def test_create_sdk_timeout_inspects_and_identifies_without_replay(tmp_path,monkeypatch):
    import sys
    class SDKTimeout(Exception):pass
    monkeypatch.setitem(sys.modules,'daytona',Obj(DaytonaTimeoutError=SDKTimeout))
    transport,client,release,ledger=fixture(tmp_path)
    original=client.create
    def accepted_then_timeout(params,timeout):
        original(params,timeout)
        raise SDKTimeout('accepted response lost')
    client.create=accepted_then_timeout
    with pytest.raises(TimeoutError) as caught:transport.create(release,'slot1')
    assert caught.value.worker_id=='worker1'
    assert len(client.creates)==1 and client.sessions==[]
    transport.cancel_timed_out(caught.value.worker_id,release,'slot1')
    assert client.deleted==['worker1']


def test_unknown_create_timeout_never_invents_worker_identity(tmp_path):
    transport,client,release,ledger=fixture(tmp_path)
    def timeout(*a,**k):raise TimeoutError('no known outcome')
    client.create=timeout
    with pytest.raises(TimeoutError) as caught:transport.create(release,'slot1')
    assert caught.value.worker_id is None
    assert client.deleted==[] and ledger.retired==[]


def test_bootstrap_timeout_recovers_existing_exact_worker(tmp_path):
    transport,client,release,ledger=fixture(tmp_path)
    original=client.create
    def create(*args,**kwargs):
        worker=original(*args,**kwargs)
        def timeout(*a,**k):raise TimeoutError('bootstrap response lost')
        worker.process.execute_session_command=timeout
        return worker
    client.create=create
    with pytest.raises(TimeoutError) as caught:transport.create(release,'slot1')
    assert caught.value.worker_id=='worker1'
    assert len(client.creates)==1


@pytest.mark.parametrize('expiry',[None,'not-a-date','2099-01-01T00:00:00Z','2000-01-01T00:00:00Z',datetime(2030,1,1)])
def test_unconfirmed_or_unbounded_expiry_identified_for_cancellation(tmp_path,expiry):
    transport,client,release,ledger=fixture(tmp_path)
    original=client.create
    def ignored_ttl(*args,**kwargs):
        worker=original(*args,**kwargs);worker.auto_destroy_at=expiry;return worker
    client.create=ignored_ttl
    with pytest.raises(TimeoutError) as error:transport.create(release,'slot1')
    assert error.value.worker_id=='worker1'
    assert client.sessions==[] and TOKEN not in client.files
    transport.cancel_timed_out(error.value.worker_id,release,'slot1')
    assert client.deleted==['worker1'] and ledger.retired


def test_iso_expiry_accepted_and_reuse_rechecks_deadline(tmp_path):
    transport,client,release,ledger=fixture(tmp_path)
    original=client.create
    def iso(*args,**kwargs):
        worker=original(*args,**kwargs);worker.auto_destroy_at=worker.auto_destroy_at.isoformat();return worker
    client.create=iso
    assert transport.create(release,'slot1')=='worker1'
    client.workers['worker1'].auto_destroy_at=None
    with pytest.raises(TimeoutError) as error:transport.create(release,'slot1')
    assert error.value.worker_id=='worker1'
    assert len(client.creates)==1
