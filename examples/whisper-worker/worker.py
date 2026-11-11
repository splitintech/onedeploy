"""Authenticated, local-model-only Whisper worker. No startup model downloads."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_AUDIO = 10 * 1024 * 1024
MAX_BODY = 14 * 1024 * 1024
MAX_AUDIO_SECONDS = 120
AUDIO_SAMPLE_RATE = 16000


def normalized_gpu(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


class BoundedHTTPServer(ThreadingHTTPServer):
    """Bound simultaneous HTTP handlers; slow clients cannot create unlimited threads."""
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, handler_class, max_handlers=16):
        self.slots = threading.BoundedSemaphore(max_handlers)
        super().__init__(address, handler_class)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def verify_model(root, manifest_path, digest):
    root = Path(root).resolve()
    raw = Path(manifest_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Model manifest digest mismatch')
    files = json.loads(raw)
    if not isinstance(files, dict) or not files:
        raise ValueError('Empty model manifest')
    for name, expected in files.items():
        path = root / name
        if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe model path')
        if not re.fullmatch('[0-9a-f]{64}', expected) or not path.is_file():
            raise ValueError('Invalid model manifest entry')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Model file checksum mismatch')
    actual_files = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    if actual_files != set(files):
        raise ValueError('Every model file must be in the manifest')


def decode_bounded_audio(path):
    """Stream decode before inference; reject >120 s without allocating it all."""
    import av
    import numpy as np
    chunks = []
    total_samples = 0
    decoded_seconds = 0.0
    with av.open(str(path), mode='r') as container:
        streams = list(container.streams.audio)
        if len(streams) != 1:
            raise ValueError('Exactly one audio stream is required')
        stream = streams[0]
        if stream.duration is not None and stream.time_base is not None:
            if float(stream.duration * stream.time_base) > MAX_AUDIO_SECONDS:
                raise ValueError('Audio exceeds 120 second limit')
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=AUDIO_SAMPLE_RATE)
        def append(frame):
            nonlocal total_samples
            if frame.samples < 0 or total_samples + frame.samples > MAX_AUDIO_SECONDS * AUDIO_SAMPLE_RATE:
                raise ValueError('Audio exceeds decoded sample limit')
            total_samples += frame.samples
            chunks.append(frame.to_ndarray().reshape(-1))
        # Packet iteration bounds malformed containers that yield no useful audio.
        for count, packet in enumerate(container.demux(stream)):
            if count >= 100000:
                raise ValueError('Too many audio packets')
            for frame in packet.decode():
                rate = frame.sample_rate
                if not isinstance(rate, int) or not 8000 <= rate <= 192000 or len(frame.layout.channels) > 8:
                    raise ValueError('Unsupported audio format')
                decoded_seconds += frame.samples / rate
                if decoded_seconds > MAX_AUDIO_SECONDS:
                    raise ValueError('Audio exceeds 120 second limit')
                for output in resampler.resample(frame):
                    append(output)
        for output in resampler.resample(None):
            append(output)
    if total_samples == 0:
        raise ValueError('No decoded audio samples')
    return np.concatenate(chunks)


class CudaWhisper:
    def __init__(self, model_dir):
        import ctranslate2
        from faster_whisper import WhisperModel
        if ctranslate2.get_cuda_device_count() < 1:
            raise RuntimeError('CUDA GPU unavailable')
        self.model = WhisperModel(str(model_dir), device='cuda', compute_type='float16', local_files_only=True)
        if self.model.model.device != 'cuda':
            raise RuntimeError('Model is not on CUDA')

    def transcribe(self, path):
        audio = decode_bounded_audio(path)
        segments, _ = self.model.transcribe(audio, beam_size=5, vad_filter=False)
        return ' '.join(segment.text.strip() for segment in segments).strip()


class Worker:
    def __init__(self, db, engine, *, release_id, model_digest, gpu_type, synthetic_audio, synthetic_text):
        self.engine = engine
        self.release_id, self.model_digest, self.gpu_type = release_id, model_digest, gpu_type
        self.synthetic_audio, self.synthetic_text = synthetic_audio, synthetic_text
        self.lock = threading.RLock()
        self.engine_lock = threading.Lock()  # One request/GPU until explicitly benchmarked.
        self.db = sqlite3.connect(str(db), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, state TEXT NOT NULL, text TEXT)')
        self.db.execute("INSERT OR IGNORE INTO state VALUES ('accepting','true')")
        # Single process file lock held by main ensures no old process is running.
        self.db.execute("UPDATE jobs SET state='interrupted' WHERE state='active'")
        self.db.commit()

    def status(self):
        with self.lock:
            return {'accepting_jobs': self.db.execute("SELECT value FROM state WHERE key='accepting'").fetchone()[0] == 'true',
                    'active_jobs': self.db.execute("SELECT count(*) FROM jobs WHERE state='active'").fetchone()[0],
                    'release_id': self.release_id}

    def drain(self):
        with self.lock:
            self.db.execute("UPDATE state SET value='false' WHERE key='accepting'")
            self.db.commit()
            return self.status()

    def infer(self, job_id, audio):
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job_id):
            raise ValueError('Invalid job ID')
        if not audio or len(audio) > MAX_AUDIO:
            raise ValueError('Audio size invalid')
        digest = hashlib.sha256(audio).hexdigest()
        with self.lock:
            row = self.db.execute('SELECT input_hash,state,text FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError('Job ID reused with different input')
                if row[1] == 'complete':
                    return {'job_id': job_id, 'text': row[2], 'release_id': self.release_id}
                raise RuntimeError('Job already active, failed or interrupted; reconcile before retry')
            if not self.status()['accepting_jobs']:
                raise RuntimeError('Worker draining')
            if self.status()['active_jobs'] >= 1:
                raise RuntimeError('Worker at capacity')
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,NULL)', (job_id, digest, 'active'))
            self.db.commit()
        try:
            with tempfile.NamedTemporaryFile(suffix='.audio') as stream:
                stream.write(audio); stream.flush()
                with self.engine_lock:
                    text = self.engine.transcribe(stream.name)
            if not isinstance(text, str):
                raise RuntimeError('Invalid engine response')
            with self.lock:
                self.db.execute("UPDATE jobs SET state='complete',text=? WHERE id=?", (text, job_id)); self.db.commit()
            return {'job_id': job_id, 'text': text, 'release_id': self.release_id}
        except Exception:
            with self.lock:
                self.db.execute("UPDATE jobs SET state='failed' WHERE id=?", (job_id,)); self.db.commit()
            raise

    def qualify(self):
        # Persist the intent before invoking inference. A crash/timeout cannot
        # safely distinguish an unfinished call from a completed response loss.
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key='qualification'").fetchone()
            if row:
                return json.loads(row[0])
            pending = self.db.execute("SELECT value FROM state WHERE key='qualification_pending'").fetchone()
            if pending and pending[0] == 'true':
                raise RuntimeError('Qualification outcome pending; inspect without replay')
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('qualification_pending','true')")
            self.db.commit()
        # Any exception deliberately retains pending=true for operator/TTL
        # recovery. Do not turn an uncertain call into a retry permission.
        with self.engine_lock:
            text = self.engine.transcribe(self.synthetic_audio)
        normalize = lambda value: re.sub(r'[^a-z0-9]+', '', value.lower())
        passed = bool(normalize(self.synthetic_text)) and normalize(text) == normalize(self.synthetic_text)
        evidence = dict(authenticated=True, cuda_available=True, model_on_gpu=True, checksum_verified=True,
                        synthetic_inference_passed=passed, release_id=self.release_id,
                        model_digest=self.model_digest, gpu_type=self.gpu_type)
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('qualification',?)", (json.dumps(evidence),))
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('qualification_pending','false')")
            self.db.commit()
        return evidence

    def qualification_status(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key='qualification'").fetchone()
            pending = self.db.execute("SELECT value FROM state WHERE key='qualification_pending'").fetchone()
            return {'completed': row is not None, 'in_progress': bool(pending and pending[0] == 'true'),
                    'evidence': json.loads(row[0]) if row else None}


def handler(worker, token):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)
        def log_message(self, *args): pass  # Never log audio, tokens or transcript contents.
        def send_json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self): self.handle_request()
        def do_POST(self): self.handle_request()
        def handle_request(self):
            if self.path == '/health' and self.command == 'GET':
                return self.send_json(200, {'alive': True})
            if not hmac.compare_digest(self.headers.get('Authorization','').encode('utf-8'), ('Bearer '+token).encode('utf-8')):
                return self.send_json(401, {'error':'unauthorized'})
            try:
                if self.path == '/status' and self.command == 'GET': result = worker.status()
                elif self.path == '/qualification-status' and self.command == 'GET': result = worker.qualification_status()
                elif self.path == '/drain' and self.command == 'POST': result = worker.drain()
                elif self.path == '/qualify' and self.command == 'POST': result = worker.qualify()
                elif self.path == '/infer' and self.command == 'POST':
                    length = int(self.headers.get('Content-Length','0'))
                    if not 0 < length <= MAX_BODY: raise ValueError('Invalid body size')
                    payload = json.loads(self.rfile.read(length))
                    if set(payload) != {'job_id','audio_base64'}: raise ValueError('Invalid fields')
                    result = worker.infer(payload['job_id'], base64.b64decode(payload['audio_base64'], validate=True))
                else: return self.send_json(404, {'error':'not_found'})
                self.send_json(200, result)
            except (ValueError, TypeError): self.send_json(400, {'error':'invalid_request'})
            except RuntimeError: self.send_json(409, {'error':'not_available'})
            except Exception: self.send_json(500, {'error':'worker_failure'})
    return Handler


def main():
    import fcntl
    import subprocess
    token = Path(os.environ['ONEDEPLOY_WORKER_TOKEN_FILE']).read_text().strip()
    if len(token) < 32: raise ValueError('Worker token too short')
    state = Path(os.environ.get('ONEDEPLOY_STATE_DIR','/var/lib/onedeploy'))
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / 'worker.lock').open('w'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    root = os.environ['ONEDEPLOY_MODEL_DIR']
    digest = os.environ['ONEDEPLOY_MODEL_DIGEST']
    verify_model(root, os.environ['ONEDEPLOY_MODEL_MANIFEST'], digest)
    gpu_type = os.environ['ONEDEPLOY_GPU_TYPE']
    gpu_name = subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'], text=True, timeout=10).lower()
    if not normalized_gpu(gpu_type) or normalized_gpu(gpu_type) not in normalized_gpu(gpu_name):
        raise RuntimeError('GPU type does not match deployment')
    engine = CudaWhisper(root)
    worker = Worker(state/'jobs.sqlite3', engine, release_id=os.environ['ONEDEPLOY_RELEASE_ID'], model_digest=digest,
        gpu_type=gpu_type, synthetic_audio=os.environ['ONEDEPLOY_SYNTHETIC_AUDIO'], synthetic_text=os.environ['ONEDEPLOY_SYNTHETIC_TEXT'])
    server = BoundedHTTPServer(('0.0.0.0', int(os.environ.get('ONEDEPLOY_WORKER_PORT','8766'))), handler(worker, token))
    server.serve_forever()

if __name__ == '__main__': main()
