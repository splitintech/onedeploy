from types import SimpleNamespace as Obj
import pytest
from onedeploy.autodeploy.daytona import DaytonaSDKAdapter, SDKBindings, DaytonaContractError

IMAGE = 'ghcr.io/example/worker@sha256:' + 'a' * 64

class Missing(Exception): pass

class Client:
    def __init__(self):
        self.snapshots = {}; self.workers = {}; self.created = 0; self.deleted = []
        self.snapshot = Obj(get=self.get_snapshot, create=self.create_snapshot)
    def get_snapshot(self, name):
        if name not in self.snapshots: raise Missing()
        return self.snapshots[name]
    def create_snapshot(self, p, timeout):
        self.created += 1
        s = Obj(id='s1', name=p['name'], organization_id='account', image_name=p['image'], state='active', cpu=2, mem=8, disk=20, gpu=1)
        self.snapshots[s.id] = self.snapshots[s.name] = s
        return s
    def get(self, name, request_timeout):
        if name not in self.workers: raise Missing()
        return self.workers[name]
    def create(self, p, timeout):
        w = Obj(id='w1', name=p['name'], organization_id='account', snapshot=p['snapshot'], labels=p['labels'], public=False, state='started')
        self.workers[w.id] = self.workers[w.name] = w
        return w
    def delete(self, worker, timeout): self.deleted.append(worker.id)


def fixture():
    c = Client()
    evidence = dict(gpu_type='h100',authenticated=True,cuda_available=True,model_on_gpu=True,checksum_verified=True,synthetic_inference_passed=True,release_id='r1',model_digest='b'*64, secret='not returned')
    jobs = dict(accepting_jobs=False, active_jobs=0)
    a = DaytonaSDKAdapter(c, SDKBindings(lambda **k:k,lambda **k:k,Missing,'0.231.0'), account='account',deployment='demo',resources=dict(cpu=2,memory=8,disk=20,gpu=1,gpu_type='h100'),probe=lambda w:evidence,drainer=lambda w:jobs)
    r = dict(release_id='r1', deployment_id='demo', spec=dict(image_digest=IMAGE,model_digest='b'*64))
    r['config'] = {'target': 'account', 'resources': dict(cpu=2,memory_gib=8,disk_gib=20,gpu_count=1,gpu_type='h100')}
    return a,c,r,evidence,jobs


def test_happy_path_is_idempotent_and_sanitized():
    a,c,r,e,j=fixture()
    r['snapshot_id']=a.ensure_snapshot(r)
    assert a.ensure_snapshot(r)==r['snapshot_id'] and c.created==1
    r['worker_id']=a.ensure_candidate(r)
    assert a.ensure_candidate(r)==r['worker_id']
    assert a.qualify(r)['gpu'] is True
    assert 'secret' not in repr(a.qualify(r))
    a.delete_worker(r)
    assert c.deleted==['w1']


def test_snapshot_failure_does_not_retry_writes():
    a,c,r,e,j=fixture()
    def fail(*args,**kwargs): raise TimeoutError('unknown outcome')
    c.snapshot.create=fail
    with pytest.raises(TimeoutError): a.ensure_snapshot(r)


def test_mutable_image_rejected_before_write():
    a,c,r,e,j=fixture();r['spec']['image_digest']='worker:latest'
    with pytest.raises(DaytonaContractError): a.ensure_snapshot(r)
    assert c.created==0


def test_foreign_worker_cannot_delete():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r)
    c.workers['w1'].organization_id='other'
    with pytest.raises(DaytonaContractError): a.delete_worker(r)
    assert c.deleted==[]


def test_active_job_cannot_delete():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r);j['active_jobs']=1
    assert a.drain(r) is False
    with pytest.raises(DaytonaContractError): a.delete_worker(r)
    assert c.deleted==[]


def test_http_success_alone_never_qualifies():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r);e['model_on_gpu']=False
    assert a.qualify(r)['gpu'] is False


def test_wrong_model_or_resources_rejected():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);c.snapshots['s1'].gpu=4
    with pytest.raises(DaytonaContractError): a.ensure_snapshot(r)


def test_uncertain_snapshot_reconciliation_is_read_only():
    a,c,r,e,j=fixture(); expected=a.ensure_snapshot(r)
    assert a.inspect_operation(r,'ensure_snapshot')=={'resolved':True,'result':expected}
    assert c.created==1


def test_plan_resource_mismatch_blocks_write():
    a,c,r,e,j=fixture();r['config']['resources']['gpu_count']=8
    with pytest.raises(DaytonaContractError): a.ensure_snapshot(r)
    assert c.created==0


def test_gpu_type_mismatch_blocks_qualification():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r);e['gpu_type']='a100'
    assert a.qualify(r)['gpu'] is False


def test_budget_denial_does_not_block_owned_cleanup_or_inspection():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r)
    a.authorize_operation=lambda release:False
    with pytest.raises(DaytonaContractError): a.ensure_candidate(r)
    assert a.inspect_operation(r,'ensure_candidate')['resolved'] is True
    assert a.drain(r) is True
    a.delete_worker(r)
    assert c.deleted==['w1']


def test_unknown_probe_is_held_and_never_replayed_by_inspection():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r)
    def fail(worker): raise TimeoutError('unknown')
    a.probe=fail;a.drainer=fail
    with pytest.raises(TimeoutError): a.qualify(r)
    assert a.inspect_operation(r,'qualify') is None
    assert a.inspect_operation(r,'drain') is None


def test_separate_readonly_callback_recovers_qualify_and_drain():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r)
    a.inspect_status=lambda worker, op: {'completed':True,'evidence':e if op=='qualify' else j}
    e['model_on_gpu']=False
    assert a.inspect_operation(r,'qualify')['result']['gpu'] is False
    assert a.inspect_operation(r,'drain')=={'resolved':True,'result':True}


def test_malformed_probe_evidence_is_unknown_not_negative():
    a,c,r,e,j=fixture();r['snapshot_id']=a.ensure_snapshot(r);r['worker_id']=a.ensure_candidate(r)
    e.pop('model_on_gpu')
    with pytest.raises(DaytonaContractError): a.qualify(r)
