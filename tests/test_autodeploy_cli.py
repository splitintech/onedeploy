import json
import subprocess
from pathlib import Path

import pytest

from onedeploy import cli
from onedeploy.autodeploy import planner
from onedeploy.errors import OnedeployError


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-b', 'deployment')
    git('config', 'user.name', 'test'); git('config', 'user.email', 'test@example.test')
    git('remote', 'add', 'origin', 'https://github.com/example/project.git')
    (repo / 'file').write_text('hello')
    git('add', '.'); git('commit', '-m', 'initial')
    config = {'deployment_id': 'demo', 'repository': 'example/project', 'branch': 'deployment', 'target': 'test-account',
              'resources': {'cpu': 2, 'memory_gib': 8, 'disk_gib': 20, 'gpu_count': 1, 'gpu_type': 'test'},
              'max_workers': 1, 'budget_usd': 1}
    return repo, git('rev-parse', 'HEAD'), config


def plan(checkout):
    repo, sha, config = checkout
    return planner.make_plan(config, repo, sha, 'ghcr.io/example/worker@sha256:' + 'a'*64, 'b'*64, 'c'*64)


def test_plan_roundtrip_and_tamper(checkout):
    p = plan(checkout)
    assert planner.verify_plan(p, checkout[0]) == p
    p['spec']['image_digest'] = 'ghcr.io/example/worker@sha256:' + 'd'*64
    with pytest.raises(OnedeployError, match='integrity'):
        planner.verify_plan(p, checkout[0])


def test_dirty_and_wrong_repository(checkout):
    checkout[2]['repository'] = 'other/repository'
    with pytest.raises(OnedeployError, match='origin'):
        plan(checkout)
    checkout[2]['repository'] = 'example/project'
    (checkout[0] / 'untracked').write_text('x')
    with pytest.raises(OnedeployError, match='clean'):
        plan(checkout)


def test_read_rejects_duplicate_and_secret_error(tmp_path, capsys):
    f = tmp_path / 'config.json'; f.write_text('{"secret":"NEVER_PRINT", "secret":1}')
    assert cli.main(['autodeploy', 'validate', '--config', str(f), '--json']) == 2
    out = capsys.readouterr()
    assert 'NEVER_PRINT' not in out.out + out.err
    assert json.loads(out.out)['command'] == 'autodeploy validate'


def test_init_exclusive(tmp_path, capsys):
    f = tmp_path / 'config.json'
    assert cli.main(['autodeploy', 'init', '--output', str(f), '--json']) == 0
    capsys.readouterr()
    assert cli.main(['autodeploy', 'init', '--output', str(f), '--json']) == 12
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'conflict'


def test_apply_approval_then_qualification_no_state(checkout, tmp_path, capsys):
    p = plan(checkout); f = tmp_path / 'plan.json'; planner.save_json(p, f)
    state = tmp_path / 'state.db'
    args = ['autodeploy', 'apply', '--plan', str(f), '--source', str(checkout[0]), '--state', str(state), '--json']
    assert cli.main(args) == 4
    capsys.readouterr()
    assert cli.main(args + ['--approve-plan', p['id']]) == 4
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'approval_required'
    assert not state.exists()


def test_status_does_not_create_state(tmp_path, capsys):
    state = tmp_path / 'no.db'
    assert cli.main(['autodeploy', 'status', '--state', str(state), '--deployment', 'demo', '--json']) == 2
    assert not state.exists()


def test_mutable_image_rejected(checkout):
    repo, sha, config = checkout
    with pytest.raises(OnedeployError):
        planner.make_plan(config, repo, sha, 'ghcr.io/example/worker:latest', 'b'*64, 'c'*64)


def test_unknown_flags_do_not_leak(capsys):
    assert cli.main(['autodeploy', 'apply', '--secret=NOPE', '--json']) == 2
    assert 'NOPE' not in capsys.readouterr().out


def test_apply_wires_real_engine_with_injected_boundary(checkout, tmp_path, capsys, monkeypatch):
    from onedeploy.autodeploy import cli as auto_cli
    class Adapter:
        from types import SimpleNamespace
        transport = SimpleNamespace(ledger=SimpleNamespace(bind_runtime=lambda release: None))
        def ensure_snapshot(self, release): return 'snapshot-test'
        def ensure_candidate(self, release): return 'worker-test'
        def qualify(self, release): return dict(ready=True, gpu=True, model_verified=True, inference_passed=True)
    monkeypatch.setattr(auto_cli, 'approved_adapter', lambda args, config: Adapter())
    p = plan(checkout); f = tmp_path / 'plan.json'; planner.save_json(p, f)
    state = tmp_path / 'state.db'
    assert cli.main(['autodeploy', 'apply', '--plan', str(f), '--source', str(checkout[0]), '--state', str(state), '--approve-plan', p['id'], '--json']) == 0
    result = json.loads(capsys.readouterr().out)['result']
    assert result['state'] == 'complete'
    assert cli.main(['autodeploy', 'status', '--deployment', 'demo', '--state', str(state), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['result']['desired_release'] == result['release_id']


def test_builtin_live_factory_bound_policy_without_cloud(tmp_path, monkeypatch):
    from onedeploy.autodeploy import cli as auto_cli, daytona
    from onedeploy.autodeploy.budget import BudgetPolicy
    from onedeploy.autodeploy.models import DeploymentConfig
    c = DeploymentConfig.from_dict({'deployment_id': 'demo', 'repository': 'example/project', 'branch': 'main', 'target': 'account', 'resources': {'cpu': 2, 'memory_gib': 8, 'disk_gib': 20, 'gpu_count': 1, 'gpu_type': 'test'}, 'max_workers': 2, 'budget_usd': 1})
    runtime = {'schema_version': 1, 'model_volume_id': 'vol', 'model_subpath': 'whisper-v1', 'model_directory': 'model', 'model_manifest': 'manifest.json', 'synthetic_audio': 'check.wav', 'synthetic_text': 'hello'}
    policy = BudgetPolicy.from_dict({'schema_version': 1, 'config_digest': planner.digest(c.to_dict()), 'runtime_digest': planner.digest(runtime), 'concurrency_per_worker': 1, 'job_timeout_seconds': 90, 'total_hourly_rate': 1, 'max_worker_seconds': 600, 'allocation_overhead_usd': .1, 'max_total_usd': 1}, c)
    rp, pp = tmp_path/'runtime.json', tmp_path/'policy.json'
    planner.save_json(runtime, rp); planner.save_json(policy.to_dict(), pp)
    calls = []
    monkeypatch.setenv('DAYTONA_API_KEY', 'TEST-SECRET')
    monkeypatch.setattr(daytona, 'sdk_adapter', lambda **kw: calls.append(kw) or object())
    transport, returned, ledger = auto_cli.live_adapter(c, state=tmp_path/'state.db', policy_path=pp, approve_policy=policy.id, runtime_path=rp, pool_transport=True)
    assert returned == policy
    assert calls[0]['worker_ttl_seconds'] == 600
    assert calls[0]['resources']['memory'] == 8
    assert transport.runtime.to_dict() == runtime
    assert ledger.remaining('demo') == 1
    with pytest.raises(OnedeployError, match='approval'):
        auto_cli.live_adapter(c, state=tmp_path/'state.db', policy_path=pp, approve_policy='wrong', runtime_path=rp)


def test_serve_rejects_nonlocal_binding_before_provider(tmp_path, capsys):
    c = {'deployment_id': 'demo', 'repository': 'example/project', 'branch': 'main', 'target': 'account', 'resources': {'cpu': 2, 'memory_gib': 8, 'disk_gib': 20, 'gpu_count': 1, 'gpu_type': 'test'}, 'max_workers': 2, 'budget_usd': 1}
    path = tmp_path/'config.json'; planner.save_json(c, path)
    assert cli.main(['autodeploy', 'serve', '--config', str(path), '--deployment', 'demo', '--state', str(tmp_path/'s.db'), '--host', '0.0.0.0', '--json']) == 2
    assert not (tmp_path/'s.db').exists()
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'invalid_config'


def test_cli_completed_release_cannot_silently_switch_runtime(checkout, tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace
    from onedeploy.autodeploy import cli as auto_cli
    from onedeploy.autodeploy.models import DeploymentConfig
    from onedeploy.autodeploy.budget import BudgetPolicy, BudgetLedger
    p = plan(checkout); f=tmp_path/'plan.json'; planner.save_json(p,f); state=tmp_path/'state.db'
    config = DeploymentConfig.from_dict(p['config'])
    data = {'schema_version':1, 'config_digest':planner.digest(config.to_dict()), 'runtime_digest':'d'*64,
            'concurrency_per_worker':1, 'job_timeout_seconds':90, 'total_hourly_rate':1,
            'max_worker_seconds':600, 'allocation_overhead_usd':0, 'max_total_usd':1}
    class Adapter:
        def __init__(self, ledger): self.transport=SimpleNamespace(ledger=ledger)
        def ensure_snapshot(self, release): return 'snapshot'
        def ensure_candidate(self, release): return 'worker'
        def qualify(self, release): return dict(ready=True,gpu=True,model_verified=True,inference_passed=True)
    adapter=Adapter(BudgetLedger(state,BudgetPolicy.from_dict(data,config)))
    monkeypatch.setattr(auto_cli,'approved_adapter',lambda args, config:adapter)
    args=['autodeploy','apply','--plan',str(f),'--source',str(checkout[0]),'--state',str(state),'--approve-plan',p['id'],'--json']
    assert cli.main(args)==0
    capsys.readouterr()
    adapter.transport.ledger=BudgetLedger(state,BudgetPolicy.from_dict({**data,'runtime_digest':'e'*64},config))
    assert cli.main(args)==9
    assert json.loads(capsys.readouterr().out)['error']['code']=='stale_plan'
