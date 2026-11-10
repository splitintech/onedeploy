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
    assert cli.main(args + ['--approve-plan', p['id']]) == 13
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'unsupported_capability'
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
