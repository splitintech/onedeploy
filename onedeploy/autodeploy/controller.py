"""Single-host durable job router and bounded, scale-to-zero worker runtime.

The injected transport must verify provider ownership on every operation. HTTP
binds localhost by default; put a TLS reverse proxy in front for remote use.
Payloads/results are sensitive local data, never logs. Job leases are at-least-
once if retries are explicitly enabled; they are not an exactly-once promise.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import re
import secrets
import threading
import time
import uuid

from onedeploy.errors import OnedeployError, EXIT_CODES
from .pool import Worker, decide

MAX_BODY = 12 * 1024 * 1024
MAX_AUDIO = 8 * 1024 * 1024


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _error(code, message):
    raise OnedeployError(code, message)


class Controller:
    """Durable controller. ``tick`` may allocate via an explicitly supplied transport.

    Transport methods: create(release, operation_id), inspect_create(release,
    operation_id), qualify(worker_id, release), infer(worker_id, release,
    payload), drain(worker_id, release), delete(worker_id, release). Creation
    must be deterministic by operation_id. inspect_create is read-only. A
    transport must impose provider-side lifecycle bounds matching policy.
    """
    def __init__(self, store, deployment_id, transport, policy, budget_usd, *,
                 allow_retries=False, max_attempts=2, retention_seconds=86400,
                 max_queued_jobs=1000, clock=time.time):
        policy.validate()
        if type(budget_usd) not in (int, float) or not math.isfinite(budget_usd) or budget_usd <= 0:
            _error("invalid_config", "A finite positive controller budget is required.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5 or type(retention_seconds) is not int or retention_seconds < 60:
            _error("invalid_config", "Invalid lease retry or retention policy.")
        if type(max_queued_jobs) is not int or not 1 <= max_queued_jobs <= 10000:
            _error("invalid_config", "Invalid queue limit.")
        self.store, self.deployment_id, self.transport = store, deployment_id, transport
        self.policy, self.clock = policy, clock
        self.allow_retries, self.max_attempts = allow_retries, max_attempts
        self.retention_seconds, self.max_queued_jobs = retention_seconds, max_queued_jobs
        self._stop = threading.Event()
        self._health_window = 30.0
        self._pool = ThreadPoolExecutor(max_workers=policy.max_workers * policy.concurrency_per_worker)
        self._futures = set()
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS ctl_health(deployment TEXT PRIMARY KEY, last_tick REAL, last_success REAL, status TEXT NOT NULL, error_code TEXT);
            CREATE TABLE IF NOT EXISTS ctl_budget(deployment TEXT PRIMARY KEY, cap REAL NOT NULL, reserved REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ctl_workers(
              operation TEXT PRIMARY KEY, deployment TEXT NOT NULL, release TEXT NOT NULL,
              worker TEXT, state TEXT NOT NULL, created REAL NOT NULL, last_used REAL NOT NULL,
              reserved REAL NOT NULL, pending TEXT);
            CREATE TABLE IF NOT EXISTS ctl_jobs(
              id TEXT NOT NULL, deployment TEXT NOT NULL, hash TEXT NOT NULL, payload TEXT,
              state TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL,
              release TEXT, operation TEXT, lease TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(deployment,id));
            ''')
            db.execute("INSERT OR IGNORE INTO ctl_health VALUES (?,NULL,NULL,'starting',NULL)", (deployment_id,))
            db.execute('INSERT OR IGNORE INTO ctl_budget VALUES (?,?,0)', (deployment_id, budget_usd))
            row = db.execute('SELECT cap FROM ctl_budget WHERE deployment=?', (deployment_id,)).fetchone()
            if row[0] != budget_usd:
                _error("conflict", "Controller budget differs from persisted approval; explicit budget migration required.")

    def submit(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"job_id", "audio_base64"}:
            _error("invalid_config", "Expected job_id and audio_base64 only.")
        if not isinstance(payload['job_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', payload['job_id']):
            _error("invalid_config", "Invalid job identifier.")
        audio = payload['audio_base64']
        if not isinstance(audio, str) or not 0 < len(audio) <= MAX_BODY:
            _error("invalid_config", "Audio exceeds request limit.")
        try:
            decoded = base64.b64decode(audio, validate=True)
        except (ValueError, TypeError):
            _error("invalid_config", "Invalid base64 audio.")
        if not 0 < len(decoded) <= MAX_AUDIO:
            _error("invalid_config", "Audio exceeds decoded limit.")
        body = _json(payload)
        fingerprint = hashlib.sha256(body.encode()).hexdigest()
        now = self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT hash FROM ctl_jobs WHERE deployment=? AND id=?', (self.deployment_id, payload['job_id'])).fetchone()
            if old:
                if old[0] != fingerprint:
                    _error("conflict", "Job ID was already used for different input.")
            else:
                count = db.execute("SELECT COUNT(*) FROM ctl_jobs WHERE deployment=? AND state IN ('queued','running')", (self.deployment_id,)).fetchone()[0]
                if count >= self.max_queued_jobs:
                    _error("conflict", "Queue capacity reached.")
                db.execute("INSERT INTO ctl_jobs(id,deployment,hash,payload,state,created,updated) VALUES (?,?,?,?,'queued',?,?)", (payload['job_id'], self.deployment_id, fingerprint, body, now, now))
        return self.get_job(payload['job_id'])

    def get_job(self, job_id):
        with self.store.connect() as db:
            row = db.execute('SELECT id,state,result,release,attempts FROM ctl_jobs WHERE deployment=? AND id=?', (self.deployment_id, job_id)).fetchone()
        if not row:
            _error("invalid_config", "Unknown job.")
        out = {'job_id': row['id'], 'state': row['state'], 'release_id': row['release'], 'attempts': row['attempts']}
        if row['result']:
            out['result'] = json.loads(row['result'])
        return out

    def _workers(self, db):
        return db.execute("SELECT * FROM ctl_workers WHERE deployment=? AND state!='deleted' ORDER BY created", (self.deployment_id,)).fetchall()

    def _update_worker(self, operation, **values):
        allowed = {'state', 'worker', 'pending', 'last_used'}
        if set(values) - allowed:
            raise ValueError('Invalid worker update')
        with self.store.connect() as db:
            db.execute('UPDATE ctl_workers SET '+','.join(k+'=?' for k in values)+' WHERE operation=? AND deployment=?', (*values.values(), operation, self.deployment_id))

    def _recover_jobs(self, db, now):
        stale = db.execute("SELECT id,operation,attempts FROM ctl_jobs WHERE deployment=? AND state='running' AND lease_until<?", (self.deployment_id, now)).fetchall()
        for row in stale:
            retry = self.allow_retries and row['attempts'] < self.max_attempts
            db.execute('UPDATE ctl_jobs SET state=?,lease=NULL,lease_until=NULL,updated=? WHERE deployment=? AND id=?', ('queued' if retry else 'uncertain', now, self.deployment_id, row['id']))
            # A timed-out request may still run remotely. Drain, never reuse it.
            db.execute("UPDATE ctl_workers SET state='draining' WHERE operation=?", (row['operation'],))
        # Tombstones retain IDs/hashes so expired jobs cannot silently execute again.
        db.execute("UPDATE ctl_jobs SET state='expired',payload=NULL,result=NULL WHERE deployment=? AND state IN ('done','failed','uncertain') AND updated<?", (self.deployment_id, now-self.retention_seconds))
        db.execute("UPDATE ctl_jobs SET state='expired',payload=NULL,result=NULL,updated=? WHERE deployment=? AND state='queued' AND created<?", (now, self.deployment_id, now-self.retention_seconds))

    def _cleanup(self, row, release):
        if not row['worker']:
            return  # Unknown create remains held until provider inspection resolves it.
        self._update_worker(row['operation'], state='draining', pending='drain')
        try:
            if self.transport.drain(row['worker'], release) is not True:
                return
            self._update_worker(row['operation'], pending='delete')
            # delete must be exact-owned-resource idempotent and inspect before retry.
            self.transport.delete(row['worker'], release)
        except Exception:
            return  # Sanitized durable pending status, never leak provider text.
        self._update_worker(row['operation'], state='deleted', pending=None)
        if release.get('worker_id') == row['worker']:
            self.store.save({**release, 'worker_deleted': True})

    def _adopt_candidates(self, desired_id, now):
        # Rollout qualification workers already exist. Track them rather than
        # creating a second uncounted pool; old candidates are cleanup-only.
        releases = self.store.deployment(self.deployment_id)['releases']
        for release in releases:
            if not release.get('worker_id') or release.get('worker_deleted'):
                continue
            operation = 'candidate-' + release['release_id']
            created = release.get('worker_created_at', release.get('created_at', 0))
            if type(created) not in (int,float) or not math.isfinite(created):
                created = 0  # Missing/corrupt timestamps cannot renew a lifetime.
            created = min(created, now)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM ctl_workers WHERE operation=?', (operation,)).fetchone():
                    continue
                desired = release['release_id'] == desired_id
                cost = self.policy.worker_ttl_seconds * self.policy.total_hourly_rate / 3600 if desired else 0
                can_adopt = desired and db.execute('UPDATE ctl_budget SET reserved=reserved+? WHERE deployment=? AND cap-reserved>=?', (cost,self.deployment_id,cost)).rowcount == 1
                # If the configured budget cannot admit it, drain this already
                # allocated candidate; never leave it untracked or create more.
                db.execute('INSERT INTO ctl_workers VALUES (?,?,?,?,?,?,?,?,NULL)',
                    (operation,self.deployment_id,release['release_id'],release['worker_id'],
                     'starting' if can_adopt else 'draining',created,created,cost if can_adopt else 0))

    def _record_health(self, *, status, error_code=None, success=False):
        now = self.clock()
        with self.store.connect() as db:
            db.execute('UPDATE ctl_health SET last_tick=?,last_success=CASE WHEN ? THEN ? ELSE last_success END,status=?,error_code=? WHERE deployment=?',
                       (now,success,now,status,error_code,self.deployment_id))

    def health(self):
        """Sanitized durable reconciliation health, separate from HTTP liveness."""
        try:
            with self.store.connect() as db:
                row=db.execute('SELECT * FROM ctl_health WHERE deployment=?',(self.deployment_id,)).fetchone()
                jobs={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) AS n FROM ctl_jobs WHERE deployment=? GROUP BY state',(self.deployment_id,))}
                workers={r['state']:r['n'] for r in db.execute("SELECT state,COUNT(*) AS n FROM ctl_workers WHERE deployment=? AND state!='deleted' GROUP BY state",(self.deployment_id,))}
                budget=db.execute('SELECT cap,reserved FROM ctl_budget WHERE deployment=?',(self.deployment_id,)).fetchone()
                pending=db.execute("SELECT COUNT(*) FROM ctl_workers WHERE deployment=? AND state!='deleted' AND pending IS NOT NULL",(self.deployment_id,)).fetchone()[0]
                overdue=db.execute("SELECT COUNT(*) FROM ctl_workers WHERE deployment=? AND state!='deleted' AND pending IS NOT NULL AND created<?",(self.deployment_id,self.clock()-self.policy.startup_timeout_seconds)).fetchone()[0]
            if row is None:
                raise ValueError()
            fresh=row['last_success'] is not None and 0 <= self.clock()-row['last_success'] <= self._health_window
            status=row['status']
            error_code=row['error_code']
            if overdue:
                status,error_code='blocked','uncertain'
            if status=='running' and jobs.get('queued',0) and not workers.get('ready',0):
                status='warming' if workers.get('starting',0) else 'blocked'
            if not fresh and status=='running':
                status='stale'
            return {'alive':True,'ready':bool(fresh and status=='running' and error_code is None),
                    'status':status,'last_tick':row['last_tick'],'last_success':row['last_success'],
                    'error_code':error_code,'jobs':jobs,'workers':workers,'pending_operations':pending,
                    'budget_remaining_usd':max(0,budget['cap']-budget['reserved'])}
        except Exception:
            return {'alive':True,'ready':False,'status':'blocked','error_code':'state_unavailable'}

    def tick(self):
        """Reconcile once and persist only allowlisted, value-free health metadata."""
        try:
            result=self._tick()
        except Exception as error:
            code=error.code if isinstance(error,OnedeployError) and error.code in EXIT_CODES else 'internal_error'
            try:
                self._record_health(status='blocked',error_code=code)
            except Exception:
                pass  # Storage outage is reported separately by health().
            raise
        self._record_health(status=result['state'],success=True)
        return result

    def _tick(self):
        """Reconcile lifecycle and dispatch jobs once under exclusive local ownership."""
        with self.store.locked():
            now = self.clock()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self._recover_jobs(db, now)
                desired_row = db.execute('SELECT desired FROM deployments WHERE id=?', (self.deployment_id,)).fetchone()
                desired_id = desired_row[0] if desired_row else None
                rows = self._workers(db)
            if not desired_id:
                return {'state': 'waiting_for_release', 'workers': len(rows)}
            self._adopt_candidates(desired_id, now)
            with self.store.connect() as db:
                rows = self._workers(db)
            release = self.store.get(desired_id)
            if release.get('qualification') is None:
                _error('conflict', 'Desired release lacks qualification evidence.')
            for row in rows:
                old_release = self.store.get(row['release'])
                if not row['worker']:
                    try:
                        recovered = self.transport.inspect_create(old_release, row['operation'])
                    except Exception:
                        recovered = None
                    if recovered:
                        self._update_worker(row['operation'], worker=recovered, pending='qualify')
                        with self.store.connect() as db:
                            row = db.execute('SELECT * FROM ctl_workers WHERE operation=?', (row['operation'],)).fetchone()
                if row['release'] != desired_id or row['state'] == 'draining' or now-row['created'] >= self.policy.worker_ttl_seconds:
                    self._cleanup(row, old_release)
                    continue
                if row['state'] == 'starting':
                    worker_id = row['worker']
                    if not worker_id:
                        try:
                            worker_id = self.transport.inspect_create(old_release, row['operation'])
                        except Exception:
                            continue
                        if not worker_id:
                            continue  # no blind create retry
                        self._update_worker(row['operation'], worker=worker_id, pending='qualify')
                    try:
                        ready = self.transport.qualify(worker_id, old_release)
                    except Exception:
                        ready = False
                    if ready is True:
                        self._update_worker(row['operation'], state='ready', pending=None)
                    elif now-row['created'] >= self.policy.startup_timeout_seconds:
                        with self.store.connect() as db:
                            current = db.execute('SELECT * FROM ctl_workers WHERE operation=?', (row['operation'],)).fetchone()
                        self._cleanup(current, old_release)
            with self.store.connect() as db:
                rows = self._workers(db)
                counts = {r['operation']: db.execute("SELECT COUNT(*) FROM ctl_jobs WHERE operation=? AND state='running'", (r['operation'],)).fetchone()[0] for r in rows}
                queued = db.execute("SELECT COUNT(*) FROM ctl_jobs WHERE deployment=? AND state='queued'", (self.deployment_id,)).fetchone()[0]
                budget = db.execute('SELECT cap,reserved FROM ctl_budget WHERE deployment=?', (self.deployment_id,)).fetchone()
            prepaid = sum(max(0,self.policy.worker_ttl_seconds-(now-r['created'])) * self.policy.total_hourly_rate / 3600 for r in rows if r['state']=='ready')
            decision = decide([Worker(r['operation'], r['state'], counts[r['operation']], max(0,now-r['last_used']), max(0,now-r['created'])) for r in rows], queued, max(0,budget['cap']-budget['reserved']) + prepaid, self.policy)
            for operation in decision['delete_workers'] + decision['drain_workers']:
                row = next(r for r in rows if r['operation'] == operation)
                self._cleanup(row, self.store.get(row['release']))
            for _ in range(decision['create_workers']):
                operation = uuid.uuid4().hex
                cost = self.policy.worker_ttl_seconds * self.policy.total_hourly_rate / 3600
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    count = len(self._workers(db))
                    if count >= self.policy.max_workers:
                        break
                    changed = db.execute('UPDATE ctl_budget SET reserved=reserved+? WHERE deployment=? AND cap-reserved>=?', (cost,self.deployment_id,cost)).rowcount
                    if not changed:
                        break
                    db.execute("INSERT INTO ctl_workers VALUES (?,?,?,NULL,'starting',?,?,?,'create')", (operation,self.deployment_id,desired_id,now,now,cost))
                try:
                    worker = self.transport.create(release, operation)
                    if not isinstance(worker,str) or not worker or len(worker)>200:
                        continue
                    self._update_worker(operation, worker=worker, pending='qualify')
                except Exception:
                    pass  # intent remains durable, subsequent ticks inspect only
            # Claims use desired pointer in the same SQL transaction. A promotion
            # cannot race this read because it uses this same exclusive store lock.
            self._futures = {future for future in self._futures if not future.done()}
            for assignment in decision['assignments']:
                for _ in range(assignment['jobs']):
                    self._claim(assignment['worker_id'], desired_id)
            return {'state':'running', 'desired_release':desired_id, 'decision':decision}

    def _claim(self, operation, desired_id):
        if len(self._futures) >= self.policy.max_workers * self.policy.concurrency_per_worker:
            return  # Bound local executor queue even if a transport ignores timeouts.
        now = self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT desired FROM deployments WHERE id=?', (self.deployment_id,)).fetchone()
            worker = db.execute('SELECT * FROM ctl_workers WHERE operation=?', (operation,)).fetchone()
            if not current or current[0] != desired_id or not worker or worker['state'] != 'ready' or worker['release'] != desired_id:
                return
            inflight = db.execute("SELECT COUNT(*) FROM ctl_jobs WHERE operation=? AND state='running'", (operation,)).fetchone()[0]
            if inflight >= self.policy.concurrency_per_worker:
                return
            row = db.execute("SELECT id,payload FROM ctl_jobs WHERE deployment=? AND state='queued' ORDER BY created LIMIT 1", (self.deployment_id,)).fetchone()
            if not row:
                return
            lease = uuid.uuid4().hex
            db.execute("UPDATE ctl_jobs SET state='running',release=?,operation=?,lease=?,lease_until=?,attempts=attempts+1,updated=? WHERE deployment=? AND id=?", (desired_id,operation,lease,now+self.policy.job_timeout_seconds,now,self.deployment_id,row['id']))
            db.execute('UPDATE ctl_workers SET last_used=? WHERE operation=?', (now,operation))
        future = self._pool.submit(self._run_job, row['id'], json.loads(row['payload']), dict(worker), lease)
        self._futures.add(future)

    def _run_job(self, job_id, payload, worker, lease):
        try:
            release = self.store.get(worker['release'])
            result = self.transport.infer(worker['worker'], release, payload)
            if not isinstance(result,dict) or set(result) != {'job_id','text','release_id'} or result['job_id'] != job_id or result['release_id'] != worker['release'] or not isinstance(result['text'],str) or len(result['text']) > 100000:
                raise ValueError('Invalid worker output')
            output, state = _json(result), 'done'
        except Exception:
            # Unknown completion cannot be silently re-executed. Lease recovery
            # is the explicit, bounded at-least-once mechanism.
            return
        with self.store.connect() as db:
            db.execute("UPDATE ctl_jobs SET state=?,result=?,payload=NULL,lease=NULL,lease_until=NULL,updated=? WHERE deployment=? AND id=? AND state='running' AND lease=?", (state,output,self.clock(),self.deployment_id,job_id,lease))
            db.execute('UPDATE ctl_workers SET last_used=? WHERE operation=?', (self.clock(),worker['operation']))

    def run(self, interval=1.0):
        if not 0 < interval <= 60:
            _error('invalid_config','Invalid reconciliation interval.')
        self._health_window = max(30.0, interval * 3)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass  # state/health remains observable; no credential-bearing logs
            self._stop.wait(interval)

    def close(self):
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


def make_server(controller, *, host='127.0.0.1', port=8766, token=None):
    """Run authenticated HTTP API plus reconciliation loop until interrupted.

    POST /infer returns durable 202 job status; poll GET /jobs/<job_id>.
    Token defaults to ONEDEPLOY_CONTROLLER_TOKEN. No token is printed or saved.
    """
    token = token or os.environ.get('ONEDEPLOY_CONTROLLER_TOKEN')
    if not isinstance(token,str) or len(token) < 32:
        _error('invalid_config','A controller bearer token of at least 32 characters is required.')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def setup(self):
            super().setup()
            self.connection.settimeout(15)
        def _respond(self, status, body):
            raw = _json(body).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)
        def _authorized(self):
            auth = self.headers.get('Authorization','')
            return secrets.compare_digest(auth.encode('utf-8'),('Bearer '+token).encode('utf-8'))
        def do_GET(self):
            if not self._authorized():
                return self._respond(401,{'error':'unauthorized'})
            if self.path in {'/health','/ready'}:
                health=controller.health()
                return self._respond(503 if self.path=='/ready' and not health['ready'] else 200,health)
            if not re.fullmatch(r'/jobs/[A-Za-z0-9_-]{1,100}', self.path):
                return self._respond(404,{'error':'not_found'})
            try:
                self._respond(200,controller.get_job(self.path.rsplit('/',1)[1]))
            except OnedeployError:
                self._respond(404,{'error':'not_found'})
        def do_POST(self):
            if not self._authorized():
                return self._respond(401,{'error':'unauthorized'})
            if self.path != '/infer':
                return self._respond(404,{'error':'not_found'})
            try:
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[])) != 1:
                    raise ValueError()
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError()
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError()
                def unique(items):
                    result = {}
                    for key,value in items:
                        if key in result:
                            raise ValueError()
                        result[key] = value
                    return result
                payload = json.loads(raw,object_pairs_hook=unique)
                result = controller.submit(payload)
            except OnedeployError as error:
                return self._respond(409 if error.code=='conflict' else 400,{'error':error.code})
            except (ValueError, UnicodeError, TimeoutError):
                return self._respond(400,{'error':'invalid_request'})
            self._respond(202,result)
    class BoundedServer(ThreadingHTTPServer):
        daemon_threads = True
        def __init__(self,*args):
            self.slots = threading.BoundedSemaphore(32)
            super().__init__(*args)
        def process_request(self,request,address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request,address)
            except Exception:
                self.slots.release()
                raise
        def process_request_thread(self,request,address):
            try:
                super().process_request_thread(request,address)
            finally:
                self.slots.release()
    return BoundedServer((host,port),Handler)


def serve(controller, *, host='127.0.0.1', port=8766, token=None):
    """Serve the bounded authenticated API and run periodic reconciliation."""
    server = make_server(controller,host=host,port=port,token=token)
    thread = threading.Thread(target=controller.run,daemon=True)
    thread.start()
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
        controller.close()
        thread.join(timeout=5)
