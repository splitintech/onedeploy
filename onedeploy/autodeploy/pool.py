"""Pure pool admission decisions and cost estimates, not a running autoscaler.

A persistent controller must revalidate and atomically reserve capacity/budget
before executing any decision. Rates include all reserved resources, not GPU only.
"""
from dataclasses import dataclass
import math

from onedeploy.errors import OnedeployError


def number(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise OnedeployError('invalid_config', f'{name} must be a finite nonnegative number' + (' greater than zero.' if positive else '.'))
    return float(value)


def integer(value, name, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise OnedeployError('invalid_config', f'{name} must be a valid integer.')
    return value


@dataclass(frozen=True)
class Worker:
    id: str
    state: str  # ready, starting, draining
    inflight: int
    idle_seconds: float
    age_seconds: float

    def validate(self):
        if not isinstance(self.id, str) or not self.id or self.state not in {'ready', 'starting', 'draining'}:
            raise OnedeployError('invalid_config', 'Invalid worker identity or state.')
        integer(self.inflight, 'inflight')
        number(self.idle_seconds, 'idle_seconds')
        number(self.age_seconds, 'age_seconds')


@dataclass(frozen=True)
class PoolPolicy:
    max_workers: int
    concurrency_per_worker: int
    idle_timeout_seconds: float
    worker_ttl_seconds: float
    startup_timeout_seconds: float
    job_timeout_seconds: float
    total_hourly_rate: float

    def validate(self):
        integer(self.max_workers, 'max_workers', positive=True)
        integer(self.concurrency_per_worker, 'concurrency_per_worker', positive=True)
        for name in ('idle_timeout_seconds', 'worker_ttl_seconds', 'startup_timeout_seconds', 'job_timeout_seconds', 'total_hourly_rate'):
            number(getattr(self, name), name, positive=True)
        if self.worker_ttl_seconds < self.startup_timeout_seconds + self.job_timeout_seconds:
            raise OnedeployError('invalid_config', 'Worker TTL must cover startup and one bounded job.')


def decide(workers, queued_jobs, budget_remaining, policy):
    """Return an advisory snapshot, never mutate workers or claim reservations.

    Existing in-flight jobs must finish even when TTL/budget is exhausted. A TTL
    or idle-expired worker is only marked for draining while busy, never deleted.
    Budget refers to *unreserved* remaining money after existing commitments.
    """
    policy.validate()
    integer(queued_jobs, 'queued_jobs')
    remaining = number(budget_remaining, 'budget_remaining')
    workers = sorted(workers, key=lambda worker: worker.id)
    seen = set()
    for worker in workers:
        worker.validate()
        if worker.id in seen or worker.inflight > policy.concurrency_per_worker:
            raise OnedeployError('invalid_config', 'Duplicate worker or excess in-flight concurrency.')
        seen.add(worker.id)
    rate = policy.total_hourly_rate / 3600
    drain, delete, assignments = [], [], []
    queue = queued_jobs
    reserved = 0.0
    # Do not assume deletes finish immediately: all extant workers consume quota.
    for worker in workers:
        expired = worker.age_seconds >= policy.worker_ttl_seconds
        startup_expired = worker.state == 'starting' and worker.age_seconds >= policy.startup_timeout_seconds
        if worker.state == 'draining' or expired or startup_expired:
            if worker.inflight:
                drain.append(worker.id)
            else:
                delete.append(worker.id)
            continue
        if worker.state != 'ready':
            continue
        if policy.worker_ttl_seconds - worker.age_seconds < policy.job_timeout_seconds:
            (drain if worker.inflight else delete).append(worker.id)
            continue
        slots = policy.concurrency_per_worker - worker.inflight
        # Conservative full job duration per admission; never predict overlap savings.
        job_cost = policy.job_timeout_seconds * rate
        admits = min(queue, slots, int(max(0.0, remaining - reserved) / job_cost))
        if admits:
            assignments.append({'worker_id': worker.id, 'jobs': admits})
            reserved += admits * job_cost
            queue -= admits
        elif not worker.inflight and not queue and worker.idle_seconds >= policy.idle_timeout_seconds:
            delete.append(worker.id)
    starting_capacity = sum(policy.concurrency_per_worker for w in workers
                            if w.state == 'starting' and w.id not in delete and w.id not in drain)
    unmet = max(0, queue - starting_capacity)
    available_workers = max(0, policy.max_workers - len(workers))
    # Reserve full lifetime, including startup and idle, for each new worker.
    creation_cost = policy.worker_ttl_seconds * rate
    count = min(available_workers, math.ceil(unmet / policy.concurrency_per_worker),
                int(max(0.0, remaining - reserved) / creation_cost))
    reserved += count * creation_cost
    return {'assignments': assignments, 'create_workers': count, 'drain_workers': drain,
            'delete_workers': delete, 'queued_jobs': queue, 'existing_starting_capacity': starting_capacity,
            'reserve_usd': reserved, 'requires_atomic_reservation': True,
            'reason': 'capacity_or_budget_limited' if unmet and not count else 'planned'}


def compare_costs(*, requests, pooled_starts, startup_seconds, model_load_seconds,
                  inference_seconds, cleanup_seconds, pooled_idle_seconds, total_hourly_rate):
    """Estimate aggregate billed worker time, not wall time or guaranteed savings.

    pooled_idle_seconds is the sum across workers. Requests are homogeneous;
    actual measurements and reserved CPU/RAM/disk rates are required inputs.
    """
    integer(requests, 'requests')
    integer(pooled_starts, 'pooled_starts')
    if pooled_starts > requests or (requests and not pooled_starts):
        raise OnedeployError('invalid_config', 'Pool starts must be between one and request count for nonempty traffic.')
    values = [number(v, name) for v, name in ((startup_seconds, 'startup_seconds'), (model_load_seconds, 'model_load_seconds'),
               (inference_seconds, 'inference_seconds'), (cleanup_seconds, 'cleanup_seconds'), (pooled_idle_seconds, 'pooled_idle_seconds'))]
    startup, model, inference, cleanup, idle = values
    rate = number(total_hourly_rate, 'total_hourly_rate', positive=True) / 3600
    per_turn_seconds = requests * (startup + model + inference + cleanup)
    pooled_seconds = pooled_starts * (startup + model + cleanup) + requests * inference + idle
    per_turn, pooled = per_turn_seconds * rate, pooled_seconds * rate
    return {'per_turn_usd': per_turn, 'pooled_usd': pooled, 'estimated_savings_usd': per_turn - pooled,
            'break_even_pooled_idle_seconds': (requests - pooled_starts) * (startup + model + cleanup),
            'lower_estimate': 'pooled' if pooled < per_turn else 'per_turn' if per_turn < pooled else 'equal',
            'basis': 'aggregate_reserved_worker_seconds', 'live_measurement': False}
