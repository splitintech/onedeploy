import base64
import threading
import time

import pytest
from onedeploy.autodeploy import StateStore, Reconciler
from onedeploy.autodeploy.controller import Controller
from onedeploy.autodeploy.pool import PoolPolicy
from onedeploy.errors import OnedeployError
from test_autodeploy_core import config, spec, Fake


class Transport:
    def __init__(self):
        self.created = {}; self.deleted = []; self.calls=[]; self.fail_create=False; self.fail_infer=False; self.ready=True
    def create(self,r,op):
        self.calls.append('create'); self.created[op]='worker-'+op
        if self.fail_create: raise TimeoutError('SECRET')
        return self.created[op]
    def inspect_create(self,r,op): self.calls.append('inspect'); return self.created.get(op)
    def qualify(self,w,r): return self.ready
    def infer(self,w,r,p):
        self.calls.append('infer')
        if self.fail_infer: raise TimeoutError('SECRET')
        return dict(job_id=p['job_id'],text='hello',release_id=r['release_id'])
    def drain(self,w,r): self.calls.append('drain'); return True
    def delete(self,w,r): self.deleted.append(w)


@pytest.fixture
def setup(tmp_path):
    store=StateStore(tmp_path/'state.db'); engine=Reconciler(store,Fake())
    r=engine.plan(config(),spec()); r=engine.apply(r['release_id'],approved=True)
    store.save({**r,'worker_deleted':True})
    clock=[1000.0]; t=Transport()
    policy=PoolPolicy(max_workers=2,concurrency_per_worker=1,idle_timeout_seconds=5,worker_ttl_seconds=100,startup_timeout_seconds=20,job_timeout_seconds=10,total_hourly_rate=3.6)
    ctl=Controller(store,'speech',t,policy,1,clock=lambda:clock[0])
    yield ctl,t,clock,engine
    ctl.close()


def payload(id='job1',audio=b'audio'):
    return dict(job_id=id,audio_base64=base64.b64encode(audio).decode())


def wait(ctl,id='job1',state='done'):
    for _ in range(100):
        if ctl.get_job(id)['state']==state: return
        time.sleep(.01)
    assert ctl.get_job(id)['state']==state


def test_scale_from_zero_routes_and_idles_to_zero(setup):
    c,t,clock,e=setup
    assert c.submit(payload())['state']=='queued'
    c.tick(); assert t.calls==['create']
    c.tick(); wait(c)
    assert c.get_job('job1')['result']['text']=='hello'
    assert c.submit(payload())['state']=='done'
    assert t.calls.count('infer')==1
    clock[0]+=6; c.tick()
    assert len(t.deleted)==1


def test_job_id_conflict(setup):
    c,*_=setup; c.submit(payload())
    with pytest.raises(OnedeployError): c.submit(payload(audio=b'other'))


def test_create_timeout_inspects_no_duplicate(setup):
    c,t,*_=setup; t.fail_create=True
    c.submit(payload()); c.tick(); c.tick(); wait(c)
    assert t.calls.count('create')==1 and 'inspect' in t.calls


def test_promotion_sends_new_jobs_only_new_release(setup):
    c,t,clock,e=setup
    c.submit(payload()); c.tick(); c.tick(); wait(c)
    r=e.plan(config(),spec('d')); e.apply(r['release_id'],approved=True)
    c.submit(payload('job2')); c.tick(); c.tick(); wait(c,'job2')
    assert c.get_job('job2')['release_id']==r['release_id']
    assert len(t.deleted)==1


def test_expired_lease_is_uncertain_by_default(setup):
    c,t,clock,e=setup; t.fail_infer=True
    c.submit(payload()); c.tick(); c.tick()
    time.sleep(.03); clock[0]+=11; c.tick()
    assert c.get_job('job1')['state']=='uncertain'
    assert t.calls.count('infer')==1


def test_opt_in_retry_uses_lease_fencing(setup):
    c,t,clock,e=setup; c.allow_retries=True; t.fail_infer=True
    c.submit(payload()); c.tick(); c.tick()
    time.sleep(.03); clock[0]+=11; t.fail_infer=False
    c.tick(); c.tick(); wait(c)
    assert c.get_job('job1')['attempts']==2


def test_budget_is_durable_and_blocks_new_workers(setup):
    c,t,clock,e=setup
    with c.store.connect() as db: db.execute('UPDATE ctl_budget SET reserved=cap')
    c.submit(payload()); c.tick(); assert not t.created
    with pytest.raises(OnedeployError): Controller(c.store,'speech',t,c.policy,2)


def test_retention_removes_payload_and_result_preserves_tombstone(setup):
    c,t,clock,e=setup
    c.submit(payload()); c.tick(); c.tick(); wait(c)
    clock[0]+=86401; c.tick()
    assert c.get_job('job1')['state']=='expired'
    assert c.submit(payload())['state']=='expired'
    with c.store.connect() as db:
        row=db.execute('SELECT payload,result FROM ctl_jobs').fetchone()
    assert tuple(row)==(None,None)


def test_prepaid_worker_can_execute_when_remaining_budget_zero(setup):
    c,t,clock,e=setup
    c.submit(payload()); c.tick()
    with c.store.connect() as db: db.execute('UPDATE ctl_budget SET reserved=cap')
    c.tick(); wait(c)
    assert c.get_job('job1')['state']=='done'


def test_candidate_is_adopted_not_duplicated_and_deleted_flag_saved(setup):
    c,t,clock,e=setup
    desired=c.store.deployment('speech')['desired_release']
    release=c.store.get(desired)
    c.store.save({**release,'worker_deleted':False})
    c.submit(payload()); c.tick(); wait(c)
    assert not t.created
    with c.store.connect() as db:
        row=db.execute('SELECT * FROM ctl_workers').fetchone()
    assert row['worker']==release['worker_id'] and row['reserved']>0
    clock[0]+=6; c.tick()
    assert c.store.get(desired)['worker_deleted'] is True
    c.submit(payload('job2')); c.tick(); c.tick(); wait(c,'job2')
    assert len(t.created)==1


def test_http_auth_bounded_input_and_async_job_contract(setup):
    import io
    import json
    from onedeploy.autodeploy.controller import make_server
    c,*_=setup
    server=make_server(c,port=0,token='s'*32)
    class Socket:
        def __init__(self,data): self.input=io.BytesIO(data); self.output=io.BytesIO()
        def makefile(self,*args): return self.input
        def sendall(self,data): self.output.write(data)
        def settimeout(self,*args): pass
    def request(method,path,body='',auth=True):
        headers=('Authorization: Bearer '+'s'*32+'\r\n') if auth else ''
        raw=(method+' '+path+' HTTP/1.0\r\n'+headers+'Content-Length: '+str(len(body.encode()))+'\r\n\r\n'+body).encode()
        sock=Socket(raw)
        server.RequestHandlerClass(sock,('127.0.0.1',12345),server)
        return sock.output.getvalue().decode()
    try:
        assert '401' in request('GET','/health',auth=False).splitlines()[0]
        response=request('POST','/infer',json.dumps(payload()))
        assert '202' in response.splitlines()[0] and 'queued' in response
        response=request('GET','/jobs/job1')
        assert '200' in response.splitlines()[0] and 'audio_base64' not in response
        response=request('POST','/infer','{"job_id":"a","job_id":"b","audio_base64":"YQ=="}')
        assert '400' in response.splitlines()[0]
    finally:
        server.server_close()


def test_aged_candidate_adoption_does_not_renew_worker_lifetime(setup):
    c,t,clock,e=setup
    desired=c.store.deployment('speech')['desired_release']
    release=c.store.get(desired)
    c.store.save({**release,'worker_deleted':False,'worker_created_at':clock[0]-95})
    c.submit(payload()); c.tick()
    assert 'infer' not in t.calls  # Five seconds left cannot cover a ten-second job.
    assert release['worker_id'] in t.deleted
    with c.store.connect() as db:
        row=db.execute("SELECT created FROM ctl_workers WHERE operation LIKE 'candidate-%'").fetchone()
    assert row['created']==clock[0]-95


def test_runtime_health_persists_sanitized_error_and_recovers(setup):
    import json
    c,t,clock,e=setup
    assert c.health()['ready'] is False
    c.tick(); assert c.health()['ready'] is True
    success=c.health()['last_success']
    original=c._tick
    def fail(): raise OnedeployError('SECRET-TOKEN','secret provider address and token')
    c._tick=fail; clock[0]+=1
    with pytest.raises(OnedeployError): c.tick()
    health=c.health()
    assert health['alive'] and not health['ready'] and health['status']=='blocked'
    assert health['error_code']=='internal_error' and health['last_success']==success
    assert 'secret' not in json.dumps(health).lower()
    with c.store.connect() as db:
        record=dict(db.execute('SELECT * FROM ctl_health').fetchone())
    assert 'SECRET' not in json.dumps(record)
    c._tick=original; clock[0]+=1; c.tick()
    assert c.health()['ready'] is True and c.health()['error_code'] is None
    clock[0]+=31
    assert c.health()['status']=='stale' and not c.health()['ready']


def test_health_exposes_budget_blocked_queue_counts(setup):
    c,t,clock,e=setup
    with c.store.connect() as db: db.execute('UPDATE ctl_budget SET reserved=cap')
    c.submit(payload()); c.tick()
    health=c.health()
    assert health['status']=='blocked' and health['jobs']['queued']==1
    assert health['budget_remaining_usd']==0 and not health['ready']


def test_slow_qualification_cannot_admit_past_worker_deadline(setup):
    c,t,clock,e=setup
    rid=c.store.deployment('speech')['desired_release']; r=c.store.get(rid)
    c.store.save({**r,'worker_deleted':False,'worker_created_at':clock[0]-80})
    def slow_qualification(*args):
        clock[0]+=30
        return True
    t.qualify=slow_qualification
    c.submit(payload()); c.tick()
    assert 'infer' not in t.calls
    assert c.get_job('job1')['state']=='queued'


def test_delayed_executor_rechecks_ttl_before_transmitting(setup,monkeypatch):
    from concurrent.futures import Future
    c,t,clock,e=setup
    rid=c.store.deployment('speech')['desired_release']; r=c.store.get(rid)
    c.store.save({**r,'worker_deleted':False,'worker_created_at':clock[0]-80})
    captured=[]
    def delayed(fn,*args):
        captured.append((fn,args)); return Future()
    monkeypatch.setattr(c._pool,'submit',delayed)
    c.submit(payload()); c.tick()
    assert c.get_job('job1')['state']=='running'
    clock[0]+=15
    fn,args=captured[0];fn(*args)
    assert 'infer' not in t.calls
    assert c.get_job('job1')['state']=='queued'
    assert c.get_job('job1')['attempts']==0


@pytest.mark.parametrize('change',['promote','drain','delete'])
def test_delayed_executor_rechecks_desired_release_and_worker_state(setup,monkeypatch,change):
    from concurrent.futures import Future
    c,t,clock,e=setup
    rid=c.store.deployment('speech')['desired_release']; r=c.store.get(rid)
    c.store.save({**r,'worker_deleted':False,'worker_created_at':clock[0]})
    captured=[]
    def delayed(fn,*args):
        captured.append((fn,args)); return Future()
    monkeypatch.setattr(c._pool,'submit',delayed)
    c.submit(payload()); c.tick()
    assert c.get_job('job1')['state']=='running'
    if change=='promote':
        next_release=e.plan(config(),spec('d')); e.apply(next_release['release_id'],approved=True)
    else:
        with c.store.connect() as db:
            db.execute('UPDATE ctl_workers SET state=?', ('draining' if change=='drain' else 'deleted',))
    fn,args=captured[0];fn(*args)
    assert 'infer' not in t.calls
    job=c.get_job('job1')
    assert job['state']=='queued' and job['attempts']==0 and job['release_id'] is None
