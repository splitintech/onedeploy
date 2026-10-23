import json
from pathlib import Path
import subprocess
import sys
import jsonschema
from onedeploy.config import load_manifest
from onedeploy.providers import get_adapter
from onedeploy.cli import init_template

ROOT = Path(__file__).resolve().parents[1]


def test_all_templates_validate_and_plan_without_provider_calls():
    for path in (ROOT/'onedeploy/templates').glob('*/onedeploy.yaml'):
        manifest = load_manifest(path)
        adapter = get_adapter(manifest['provider'])
        adapter.validate(manifest)
        steps = adapter.plan(manifest)
        assert steps and all(isinstance(step['argv'],list) for step in steps)


def test_templates_include_placeholder_env_no_provider_credentials(tmp_path):
    for name in ['docker-http','asgi','worker','model']:
        result=init_template(name,tmp_path/name)
        assert '.env.example' in result['files']
        assert 'onedeploy.yaml' in result['files']
        assert not (tmp_path/name/'.env').exists()


def test_schema_cli_envelope_validates():
    result = subprocess.run([sys.executable,'-m','onedeploy','schema','--json'],capture_output=True,text=True,cwd=ROOT)
    assert result.returncode == 0
    jsonschema.validate(json.loads(result.stdout),json.loads((ROOT/'schemas/result-v1.schema.json').read_text()))


def test_init_does_not_copy_python_bytecode(tmp_path):
    # pip may compile packaged .py template files during wheel installation.
    source=ROOT/'onedeploy/templates/docker-http'
    cache=source/'__pycache__'
    cache.mkdir(exist_ok=True)
    generated=cache/'onedeploy_test_only.pyc'
    generated.write_bytes(b'synthetic-bytecode-fixture')
    try:
        result=init_template('docker-http',tmp_path/'copy')
        assert not any('__pycache__' in file or file.endswith('.pyc') for file in result['files'])
        assert not (tmp_path/'copy'/'__pycache__').exists()
    finally:
        generated.unlink()


def test_model_example_warmup_is_offline_and_uses_selected_cache(tmp_path, monkeypatch):
    import importlib.util
    monkeypatch.setenv('MODEL_CACHE', str(tmp_path/'cache'))
    monkeypatch.setenv('MODEL_CONCURRENCY', '2')
    path=ROOT/'onedeploy/templates/model/app.py'
    spec=importlib.util.spec_from_file_location('example_cpu_model',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not module.READY
    assert len(module.warmup()) == 64
    assert module.READY
    assert json.loads((tmp_path/'cache/example-model.json').read_text())['version'] == 1
    assert module.LIMIT.acquire(blocking=False)
    assert module.LIMIT.acquire(blocking=False)
    assert not module.LIMIT.acquire(blocking=False)
