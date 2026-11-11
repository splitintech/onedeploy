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


def launcher_module():
    spec=importlib.util.spec_from_file_location('whisper_launch',Path(__file__).parents[1]/'examples/whisper-worker/launch.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_cuda_library_discovery_and_loader_env(tmp_path):
    launch=launcher_module()
    root=tmp_path/'nonstandard-python'/'site-packages'
    for name in ('cublas','cudnn','cuda_runtime'):(root/'nvidia'/name/'lib').mkdir(parents=True)
    directories=launch.library_dirs([root,root,tmp_path/'absent'])
    assert len(directories)==3
    env=launch.loader_environment(directories,{'LD_LIBRARY_PATH':':/usr/local/cuda/lib64:'+directories[0], 'KEEP':'yes'})
    assert env['LD_LIBRARY_PATH']==':'.join(directories+['/usr/local/cuda/lib64'])
    assert env['KEEP']=='yes'


def test_cuda_launch_execs_new_interpreter_before_loading(monkeypatch):
    launch=launcher_module();calls=[]
    monkeypatch.setattr(launch,'library_dirs',lambda:['/image/nvidia/cudnn/lib'])
    monkeypatch.setattr(launch.os,'execve',lambda exe,args,env:calls.append((exe,args,env)))
    launch.main([])
    assert calls[0][1][-1].endswith('/worker.py')
    assert calls[0][2]['LD_LIBRARY_PATH'].startswith('/image/nvidia/cudnn/lib')
    launch.main(['--smoke'])
    assert calls[1][1][-1]=='--smoke-child'


def test_missing_cuda_shared_library_fails_smoke(monkeypatch):
    launch=launcher_module()
    def missing(*args,**kwargs):raise OSError('missing library')
    monkeypatch.setattr(launch.ctypes,'CDLL',missing)
    with pytest.raises(OSError):launch.smoke()


def fake_audio_modules(monkeypatch, frames, duration=None):
    import sys
    from types import SimpleNamespace as NS
    class Array:
        def reshape(self, *_):return [0.1]
    class Frame:
        sample_rate=16000
        layout=NS(channels=[1])
        def __init__(self, seconds):self.samples=int(seconds*16000)
        def to_ndarray(self):return Array()
    class Container:
        streams=NS(audio=[NS(duration=duration,time_base=1)])
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def demux(self,_):
            for seconds in frames:
                yield NS(decode=lambda seconds=seconds:[Frame(seconds)])
    class Resampler:
        def __init__(self,**_):pass
        def resample(self, frame):return [] if frame is None else [frame]
    monkeypatch.setitem(sys.modules,'av',NS(open=lambda *a,**k:Container(),AudioResampler=Resampler))
    monkeypatch.setitem(sys.modules,'numpy',NS(concatenate=lambda chunks:sum(chunks,[])))


def test_bounded_audio_accepts_short_stream(monkeypatch):
    fake_audio_modules(monkeypatch,[1,2])
    assert w.decode_bounded_audio('fixture')==[0.1,0.1]


def test_bounded_audio_rejects_long_stream_without_trusting_metadata(monkeypatch):
    fake_audio_modules(monkeypatch,[60,61])
    with pytest.raises(ValueError,match='120 second'):w.decode_bounded_audio('compressed')


def test_bounded_audio_rejects_long_metadata_and_empty(monkeypatch):
    fake_audio_modules(monkeypatch,[],duration=121)
    with pytest.raises(ValueError,match='120 second'):w.decode_bounded_audio('long')
    fake_audio_modules(monkeypatch,[])
    with pytest.raises(ValueError,match='No decoded'):w.decode_bounded_audio('empty')


def test_non_ascii_auth_denied_cleanly(tmp_path):
    a=worker(tmp_path)
    cls=w.handler(a,'x'*32)
    request=cls.__new__(cls)
    request.path='/status';request.command='GET'
    request.headers={'Authorization':'Bearer '+chr(233)*32}
    captured=[]
    request.send_json=lambda code,obj:captured.append((code,obj))
    request.handle_request()
    assert captured==[(401,{'error':'unauthorized'})]
