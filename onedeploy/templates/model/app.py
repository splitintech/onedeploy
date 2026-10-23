"""Harmless CPU scoring model example: no weights, network downloads or telemetry."""
import hashlib
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore

CACHE = Path(os.environ.get('MODEL_CACHE', '/tmp/onedeploy-model-cache'))
LIMIT = BoundedSemaphore(int(os.environ.get('MODEL_CONCURRENCY', '2')))
READY = False
WEIGHTS = (0.25, -0.5, 0.75)


def warmup():
    global READY
    CACHE.mkdir(parents=True, exist_ok=True)
    # Cache only public example model metadata, never request bodies or credentials.
    payload = json.dumps({'version': 1, 'weights': WEIGHTS}).encode()
    (CACHE / 'example-model.json').write_bytes(payload)
    sum(a * b for a, b in zip(WEIGHTS, (1, 1, 1)))
    READY = True
    return hashlib.sha256(payload).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Request paths/bodies can contain sensitive user data.

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'alive': True})
        elif self.path == '/ready':
            self.send_json(200 if READY else 503, {'ready': READY})
        else:
            self.send_json(404, {'error': 'not_found'})

    def do_POST(self):
        if self.path != '/score':
            self.send_json(404, {'error': 'not_found'})
            return
        if not READY or not LIMIT.acquire(blocking=False):
            self.send_json(503, {'error': 'not_ready_or_busy'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError()
            values = json.loads(self.rfile.read(length))['values']
            if not isinstance(values, list) or len(values) != len(WEIGHTS):
                raise ValueError()
            if not all(type(v) in (float, int) and abs(v) <= 1000000 for v in values):
                raise ValueError()
            self.send_json(200, {'score': sum(a * b for a, b in zip(WEIGHTS, values))})
        except (ValueError, TypeError, KeyError):
            self.send_json(400, {'error': 'expected_three_bounded_numbers'})
        finally:
            LIMIT.release()


if __name__ == '__main__':
    warmup()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8000'))), Handler).serve_forever()
