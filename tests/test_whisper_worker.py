import importlib.util
import hashlib
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('whisper_worker',Path(__file__).parents[1]/'examples/whisper-worker/worker.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
class Engine:
    def transcribe(self,path):return 'hello world'

def worker(tmp_path):
    return w.Worker(tmp_path/'db',Engine(),release_id='r1',model_digest='a'*64,gpu_type='h100',synthetic_audio='test.wav',synthetic_text='Hello world!')

def test_infer_idempotent_and_drain(tmp_path):
    a=worker(tmp_path)
    assert a.infer('job1',b'audio')['text']=='hello world'
    assert a.infer('job1',b'audio')['text']=='hello world'
    with pytest.raises(ValueError):a.infer('job1',b'changed')
    assert a.drain()['active_jobs']==0
    with pytest.raises(RuntimeError):a.infer('job2',b'audio')
    assert a.infer('job1',b'audio')['text']=='hello world'

def test_actual_synthetic_output_comparison(tmp_path):
    a=worker(tmp_path)
    assert a.qualify()['synthetic_inference_passed'] is True
    # Completed evidence is immutable even if a caller later changes context.
    a.synthetic_text='different text'
    assert a.qualify()['synthetic_inference_passed'] is True
    assert a.qualification_status()['completed'] is True

def test_checksum_missing_extra_and_corruption(tmp_path):
    root=tmp_path/'model';root.mkdir();(root/'model.bin').write_bytes(b'weights')
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'model.bin':hashlib.sha256(b'weights').hexdigest()}));digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
    w.verify_model(root,manifest,digest)
    (root/'model.bin').write_bytes(b'bad')
    with pytest.raises(ValueError):w.verify_model(root,manifest,digest)

def test_admission_counts_active_under_engine_call(tmp_path):
    a=worker(tmp_path)
    class Inspect:
        def transcribe(self,path):
            assert a.status()['active_jobs']==1
            assert a.drain()['active_jobs']==1
            return 'text'
    a.engine=Inspect();a.infer('job1',b'audio')
    assert a.status()=={'accepting_jobs':False,'active_jobs':0,'release_id':'r1'}


def test_gpu_normalization():
    assert w.normalized_gpu('RTX-4090') in w.normalized_gpu('NVIDIA GeForce RTX 4090')
    assert w.normalized_gpu('H100_SXM') == 'h100sxm'


def test_http_handler_concurrency_is_bounded():
    import socket
    server=w.BoundedHTTPServer(('127.0.0.1',0),w.handler(None,'x'*32),max_handlers=1)
    assert server.slots.acquire(blocking=False)
    left,right=socket.socketpair()
    server.process_request(left,('local',0))
    assert left.fileno()==-1
    right.close();server.slots.release();server.server_close()


def test_control_rejects_timeout_outside_bounds(tmp_path):
    import subprocess,sys,os,uuid
    token=tmp_path/'token';token.write_text('x'*32)
    payload=Path('/tmp/od-job-'+str(uuid.uuid4())+'.json');payload.write_text('{}')
    control=Path(__file__).parents[1]/'examples/whisper-worker/worker-control'
    try:
        for invalid in ['0','3601','not-a-number']:
            env={**os.environ,'ONEDEPLOY_WORKER_TOKEN_FILE':str(token),'ONEDEPLOY_REQUEST_TIMEOUT_SECONDS':invalid}
            result=subprocess.run([sys.executable,str(control),'infer','--input',str(payload),'--json'],env=env,capture_output=True,text=True)
            assert result.returncode==2
            assert result.stdout==''
    finally:payload.unlink()


def test_negative_qualification_runs_exactly_once(tmp_path):
    a=worker(tmp_path);a.synthetic_text='different'
    class Count:
        calls=0
        def transcribe(self,path):self.calls+=1;return 'hello world'
    engine=Count();a.engine=engine
    assert a.qualify()['synthetic_inference_passed'] is False
    assert a.qualify()['synthetic_inference_passed'] is False
    assert engine.calls==1
    assert a.qualification_status()['in_progress'] is False


def test_uncertain_qualification_never_replayed_even_after_restart(tmp_path):
    a=worker(tmp_path)
    class Timeout:
        calls=0
        def transcribe(self,path):self.calls+=1;raise TimeoutError('unknown')
    engine=Timeout();a.engine=engine
    with pytest.raises(TimeoutError):a.qualify()
    assert a.qualification_status()=={'completed':False,'in_progress':True,'evidence':None}
    with pytest.raises(RuntimeError):a.qualify()
    a.db.close()
    restarted=worker(tmp_path);restarted.engine=engine
    with pytest.raises(RuntimeError):restarted.qualify()
    assert engine.calls==1
