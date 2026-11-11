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
    def cancel_timed_out(self,w,r,op): self.calls.append('terminate'); self.deleted.append(w)


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
    c.submit(payload()); c.tick()
    with c.store.connect() as db: db.execute('UPDATE ctl_budget SET reserved=cap')
    c.tick()
    assert t.calls.count('create')==1 and 'inspect' in t.calls
    assert t.calls.count('terminate')==1 and 'infer' not in t.calls


def test_promotion_sends_new_jobs_only_new_release(setup):
    c,t,clock,e=setup
    c.submit(payload()); c.tick(); c.tick(); wait(c)
    r=e.plan(config(),spec('d')); e.apply(r['release_id'],approved=True)
    c.submit(payload('job2')); c.tick(); c.tick(); wait(c,'job2')
    assert c.get_job('job2')['release_id']==r['release_id']
    assert len(t.deleted)==1


def test_timeout_immediately_terminates_without_replay(setup):
    c,t,clock,e=setup; t.fail_infer=True
    c.submit(payload()); c.tick(); c.tick()
    time.sleep(.03); clock[0]+=11; c.tick()
    assert c.get_job('job1')['state']=='timed_out'
    assert t.calls.count('infer')==1


def test_timeout_never_replays_even_legacy_retry_flag(setup):
    c,t,clock,e=setup; c.allow_retries=True; t.fail_infer=True
    c.submit(payload()); c.tick(); c.tick()
    wait(c,state='timed_out'); clock[0]+=11; t.fail_infer=False
    c.tick(); c.tick()
    assert c.get_job('job1')['attempts']==1
    assert t.calls.count('infer')==1 and t.calls.count('terminate')==1


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
        assert '401' in request('DELETE','/jobs/job1',auth=False).splitlines()[0]
        assert c.get_job('job1')['state']=='queued'
        assert '404' in request('DELETE','/jobs/missing').splitlines()[0]
        assert '404' in request('DELETE','/jobs/../job1').splitlines()[0]
        response=request('DELETE','/jobs/job1')
        assert '200' in response.splitlines()[0] and 'cancelled' in response
        assert 'audio_base64' not in response
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


def test_timeout_keeps_job_uncertain_until_owned_deletion_confirmed(setup):
    c,t,clock,e=setup; t.fail_infer=True
    cancellation=[]
    def unconfirmed(*args):
        cancellation.append(args)
        raise RuntimeError('provider timeout secret')
    t.cancel_timed_out=unconfirmed
    c.submit(payload()); c.tick(); c.tick(); wait(c,state='uncertain')
    assert c.get_job('job1')['attempts']==1
    with c.store.connect() as db:
        row=db.execute('SELECT state,pending FROM ctl_workers').fetchone()
    assert tuple(row)==('draining','terminate')
    assert not t.deleted
    def confirmed(w,r,op): t.deleted.append(w)
    t.cancel_timed_out=confirmed
    c.tick(); assert c.get_job('job1')['state']=='timed_out'
    assert len(t.deleted)==1 and t.calls.count('infer')==1


def test_lease_expiry_cancels_unknown_completion_without_replay(setup):
    c,t,clock,e=setup; c.allow_retries=True
    def unknown(*args):
        t.calls.append('infer'); raise RuntimeError('network result unknown')
    t.infer=unknown
    c.submit(payload()); c.tick(); c.tick(); time.sleep(.03)
    clock[0]+=11; c.tick()
    assert c.get_job('job1')['state']=='timed_out'
    assert t.calls.count('terminate')==1 and t.calls.count('infer')==1


def test_dedicated_timeout_policy_rejects_shared_worker(setup):
    from dataclasses import replace
    c,t,clock,e=setup
    with pytest.raises(OnedeployError,match='one job per dedicated worker'):
        Controller(c.store,'speech',t,replace(c.policy,concurrency_per_worker=2),1)


def test_startup_timeout_terminates_without_waiting_for_drain(setup):
    c,t,clock,e=setup; t.ready=False
    t.drain=lambda *args: False
    c.submit(payload()); c.tick()
    clock[0]+=21; c.tick()
    assert t.calls.count('terminate')==1
    assert len(t.deleted)==1 and c.get_job('job1')['state']=='queued'
    for _ in range(3): c.tick()
    assert t.calls.count('create')==1 and c.health()['status']=='provisioning_held'


def test_timeout_fences_late_success_and_stops_active_worker(setup):
    c,t,clock,e=setup
    entered=threading.Event(); release=threading.Event()
    def active(w,r,p):
        t.calls.append('infer'); entered.set(); release.wait(2)
        return {'job_id':p['job_id'],'text':'late output','release_id':r['release_id']}
    def cancelled(w,r,op):
        t.calls.append('terminate');t.deleted.append(w);release.set()
    t.infer=active;t.cancel_timed_out=cancelled;t.drain=lambda *args:False
    c.submit(payload());c.tick();c.tick();assert entered.wait(1)
    clock[0]+=11;c.tick();time.sleep(.03)
    result=c.get_job('job1')
    assert result['state']=='timed_out' and 'result' not in result
    assert t.calls.count('terminate')==1 and t.calls.count('infer')==1


def test_identified_create_timeout_cancels_in_same_tick(setup):
    c,t,clock,e=setup
    def timeout(r,op):
        t.calls.append('create')
        error=TimeoutError('provider accepted but response timed out')
        error.worker_id='known-worker';error.operation_id=op
        raise error
    t.create=timeout
    c.submit(payload());c.tick()
    assert t.deleted==['known-worker'] and t.calls==['create','terminate']


def test_creation_timeout_persists_hold_no_automatic_new_allocations(setup):
    c,t,clock,e=setup;t.fail_create=True
    c.submit(payload());c.tick()
    for _ in range(4): c.tick()
    assert t.calls.count('create')==1 and t.calls.count('terminate')==1
    assert c.get_job('job1')['state']=='queued'
    assert c.health()['status']=='provisioning_held'
    assert c.health()['error_code']=='provisioning_timeout'
    # A new explicitly approved release has its own policy/hold identity.
    new=e.plan(config(),spec('d'));e.apply(new['release_id'],approved=True)
    t.fail_create=False;c.tick();wait(c)
    assert c.get_job('job1')['release_id']==new['release_id']


def test_provisioning_hold_does_not_cancel_existing_healthy_worker_jobs(setup):
    c,t,clock,e=setup
    c.submit(payload());c.tick();c.tick();wait(c)
    rid=c.store.deployment('speech')['desired_release']
    with c.store.connect() as db:
        db.execute('INSERT INTO ctl_provisioning_holds VALUES (?,?,?,?)',('speech',rid,'creation_timeout',clock[0]))
    c.submit(payload('job2'));c.tick();wait(c,'job2')
    assert c.get_job('job2')['state']=='done' and t.calls.count('create')==1


def test_cancel_queued_is_tombstone_without_provisioning(setup):
    c,t,clock,e=setup
    c.submit(payload())
    assert c.cancel('job1')['state']=='cancelled'
    assert c.cancel('job1')['state']=='cancelled'
    assert c.submit(payload())['state']=='cancelled'
    c.tick()
    assert t.calls==[]
    with c.store.connect() as db:
        assert db.execute("SELECT payload FROM ctl_jobs WHERE id='job1'").fetchone()[0] is None


def test_cancel_running_fences_late_result_and_terminates(setup):
    c,t,clock,e=setup
    started=threading.Event(); release=threading.Event()
    def infer(w,r,p):
        started.set(); release.wait(3)
        return dict(job_id=p['job_id'],text='late',release_id=r['release_id'])
    t.infer=infer
    c.submit(payload()); c.tick(); c.tick()
    assert started.wait(1)
    try:
        assert c.cancel('job1')['state']=='cancelled'
        assert t.calls.count('terminate')==1
    finally:
        release.set()
    for f in list(c._futures): f.result(timeout=2)
    assert c.get_job('job1')['state']=='cancelled'
    assert 'result' not in c.get_job('job1')
    assert c.submit(payload())['state']=='cancelled'


def test_cancel_unconfirmed_deletion_stays_pending(setup):
    c,t,clock,e=setup
    started=threading.Event(); release=threading.Event()
    def infer(w,r,p):
        started.set(); release.wait(3)
        return dict(job_id=p['job_id'],text='late',release_id=r['release_id'])
    def fail(*args): raise TimeoutError('private provider message')
    t.infer=infer; t.cancel_timed_out=fail
    c.submit(payload()); c.tick(); c.tick()
    assert started.wait(1)
    try:
        assert c.cancel('job1')['state']=='cancelling'
        assert c.submit(payload())['state']=='cancelling'
        with c.store.connect() as db:
            assert db.execute("SELECT payload,lease FROM ctl_jobs WHERE id='job1'").fetchone()[:] == (None,None)
        t.cancel_timed_out=lambda w,r,op:t.deleted.append(w)
        assert c.cancel('job1')['state']=='cancelled'
    finally: release.set()


def test_cancel_unknown_and_done_do_not_delete_worker(setup):
    c,t,clock,e=setup
    with pytest.raises(OnedeployError): c.cancel('unknown')
    c.submit(payload()); c.tick(); c.tick(); wait(c)
    assert c.cancel('job1')['state']=='done'
    assert not t.deleted
