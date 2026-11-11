import math
import pytest
from onedeploy.autodeploy.pool import Worker, PoolPolicy, decide, compare_costs
from onedeploy.errors import OnedeployError


def policy(**kwargs):
    values = dict(max_workers=2, concurrency_per_worker=2, idle_timeout_seconds=60,
                  worker_ttl_seconds=600, startup_timeout_seconds=120, job_timeout_seconds=30,
                  total_hourly_rate=3.6)
    values.update(kwargs)
    return PoolPolicy(**values)


def test_ready_reuse_before_create():
    result = decide([Worker('a', 'ready', 0, 10, 20)], 2, 10, policy())
    assert result['assignments'] == [{'worker_id': 'a', 'jobs': 2}]
    assert result['create_workers'] == 0
    assert result['reserve_usd'] == pytest.approx(.06)


def test_scale_zero_only_idle_and_not_inflight():
    result = decide([Worker('a', 'ready', 0, 70, 100), Worker('b', 'ready', 1, 70, 100)], 0, 10, policy())
    assert result['delete_workers'] == ['a']


def test_expired_busy_drain_never_delete():
    result = decide([Worker('a', 'ready', 1, 0, 700)], 5, 10, policy())
    assert result['drain_workers'] == ['a']
    assert result['delete_workers'] == []
    assert result['create_workers'] == 1


def test_near_ttl_does_not_admit():
    result = decide([Worker('a', 'ready', 0, 0, 590)], 1, 10, policy())
    assert not result['assignments']
    assert result['delete_workers'] == ['a']


def test_budget_and_starting_capacity():
    assert decide([], 4, .59, policy())['create_workers'] == 0
    assert decide([], 4, .61, policy())['create_workers'] == 1
    result = decide([Worker('a', 'starting', 0, 0, 10)], 2, 10, policy())
    assert result['create_workers'] == 0
    assert result['existing_starting_capacity'] == 2


def test_no_capacity_assumed_until_deletion_confirmed():
    result = decide([Worker('a', 'starting', 0, 0, 121), Worker('b', 'draining', 0, 0, 5)], 2, 10, policy())
    assert result['delete_workers'] == ['a', 'b']
    assert result['create_workers'] == 0


def test_stable_order_and_duplicate_rejection():
    workers = [Worker('b', 'ready', 0, 0, 1), Worker('a', 'ready', 0, 0, 1)]
    assert decide(workers, 1, 10, policy())['assignments'][0]['worker_id'] == 'a'
    with pytest.raises(OnedeployError):
        decide([workers[0], workers[0]], 1, 10, policy())


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, True])
def test_invalid_money(bad):
    with pytest.raises(OnedeployError):
        decide([], 1, bad, policy())


def costs(**kwargs):
    values = dict(requests=10, pooled_starts=1, startup_seconds=20, model_load_seconds=10,
                  inference_seconds=5, cleanup_seconds=2, pooled_idle_seconds=60, total_hourly_rate=3.6)
    values.update(kwargs)
    return compare_costs(**values)


def test_cost_comparison_both_directions():
    result = costs()
    assert result['per_turn_usd'] == pytest.approx(.37)
    assert result['pooled_usd'] == pytest.approx(.142)
    assert result['break_even_pooled_idle_seconds'] == 288
    assert costs(pooled_idle_seconds=500)['lower_estimate'] == 'per_turn'
    assert costs(pooled_idle_seconds=288)['lower_estimate'] == 'equal'


def test_invalid_cost_inputs():
    for options in ({'requests': True}, {'pooled_starts': 11}, {'total_hourly_rate': math.inf}, {'model_load_seconds': -1}):
        with pytest.raises(OnedeployError):
            costs(**options)
