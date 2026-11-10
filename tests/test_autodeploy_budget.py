import pytest
from onedeploy.autodeploy.budget import BudgetPolicy, BudgetLedger
from onedeploy.autodeploy.models import DeploymentConfig, digest
from onedeploy.errors import OnedeployError


def config():
    return DeploymentConfig.from_dict({'deployment_id': 'demo', 'repository': 'example/project', 'branch': 'main', 'target': 'account', 'resources': {'cpu': 2, 'memory_gib': 8, 'disk_gib': 20, 'gpu_count': 1, 'gpu_type': 'test'}, 'max_workers': 2, 'budget_usd': 1})


def data():
    return {'schema_version': 1, 'concurrency_per_worker': 1, 'job_timeout_seconds': 90, 'runtime_digest': 'd'*64, 'config_digest': digest(config().to_dict()), 'total_hourly_rate': 3.6, 'max_worker_seconds': 600, 'allocation_overhead_usd': .1, 'max_total_usd': 1}


def release(rid='a'):
    return {'config': config().to_dict(), 'deployment_id': 'demo', 'release_id': rid*64}


def test_transactional_reservation_retry_and_budget(tmp_path):
    ledger = BudgetLedger(tmp_path / 'state.db', BudgetPolicy.from_dict(data(), config()), clock=lambda: 100)
    assert ledger.authorize(release())
    assert ledger.authorize(release())
    assert not ledger.authorize(release('b'))
    assert BudgetLedger(tmp_path / 'state.db', ledger.policy, clock=lambda: 100).authorize(release())


def test_expiration_never_refunds(tmp_path):
    ledger = BudgetLedger(tmp_path / 'state.db', BudgetPolicy.from_dict(data(), config()), clock=lambda: 100)
    assert ledger.authorize(release())
    ledger.clock = lambda: 701
    assert not ledger.authorize(release())
    assert not ledger.authorize(release('b'))


@pytest.mark.parametrize('changes', [{'total_hourly_rate': float('nan')}, {'max_total_usd': 2}, {'max_worker_seconds': True}, {'extra': 1}, {'config_digest': 'x'}])
def test_policy_strict(changes):
    with pytest.raises(OnedeployError):
        BudgetPolicy.from_dict({**data(), **changes}, config())


def test_config_mismatch_denied(tmp_path):
    ledger = BudgetLedger(tmp_path / 'state.db', BudgetPolicy.from_dict(data(), config()))
    r = release(); r['config']['target'] = 'other'
    assert not ledger.authorize(r)


def test_allocation_slots_retire_no_refund(tmp_path):
    c = config().to_dict(); c['max_workers'] = 1; c['budget_usd'] = 10
    d = data(); d.update(config_digest=digest(c), max_total_usd=10)
    ledger = BudgetLedger(tmp_path / 'state.db', BudgetPolicy.from_dict(d, c), clock=lambda: 100)
    r = {'config': c, 'deployment_id': 'demo', 'release_id': 'a'*64}
    assert ledger.authorize(r)
    assert not ledger.authorize({**r, 'allocation_id': 'b'*64})
    remaining = ledger.remaining('demo')
    ledger.retire('a'*64)
    assert ledger.remaining('demo') == remaining
    assert not ledger.authorize(r)
    assert ledger.authorize({**r, 'allocation_id': 'b'*64})


def test_runtime_binding_prevents_reapply_changed_runtime(tmp_path):
    d = data(); ledger = BudgetLedger(tmp_path/'state.db', BudgetPolicy.from_dict(d, config()))
    r = {**release(), 'state': 'planned', 'snapshot_id': None, 'worker_id': None}
    ledger.bind_runtime(r)
    complete = {**r, 'state': 'complete', 'snapshot_id': 's1', 'worker_id': 'w1'}
    ledger.bind_runtime(complete)
    changed = BudgetLedger(tmp_path/'state.db', BudgetPolicy.from_dict({**d, 'runtime_digest': 'e'*64}, config()))
    with pytest.raises(OnedeployError, match='immutable'):
        changed.bind_runtime(complete)
    with pytest.raises(OnedeployError, match='immutable'):
        changed.authorize(r)


@pytest.mark.parametrize('effects', [{'state': 'complete'}, {'state': 'planned', 'worker_id': 'w1'}, {'state': 'planned', 'snapshot_id': 's1'}, {'state': 'needs_reconciliation', 'pending_operation': 'ensure_snapshot'}])
def test_existing_unbound_effects_require_migration(tmp_path, effects):
    ledger = BudgetLedger(tmp_path/'state.db', BudgetPolicy.from_dict(data(), config()))
    with pytest.raises(OnedeployError, match='migration'):
        ledger.bind_runtime({**release(), **effects})


def test_new_rollback_identity_binds_before_admission(tmp_path):
    ledger = BudgetLedger(tmp_path/'state.db', BudgetPolicy.from_dict(data(), config()))
    rollback = {**release('c'), 'state': 'planned', 'rollback_to': 'a'*64}
    assert ledger.authorize(rollback)
    with ledger.connect() as db:
        assert db.execute('SELECT runtime_digest FROM autodeploy_release_runtime WHERE release_id=?', ('c'*64,)).fetchone()[0] == data()['runtime_digest']
