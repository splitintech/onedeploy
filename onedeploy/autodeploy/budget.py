"""Durable conservative release admission. Estimates are not provider billing caps."""
from dataclasses import dataclass, asdict
import math
import os
import re
from pathlib import Path
import sqlite3
import time

from onedeploy.errors import OnedeployError
from .models import DeploymentConfig, digest


@dataclass(frozen=True)
class BudgetPolicy:
    config_digest: str
    runtime_digest: str
    concurrency_per_worker: int
    job_timeout_seconds: int
    total_hourly_rate: float
    max_worker_seconds: int
    allocation_overhead_usd: float
    max_total_usd: float
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data, config):
        fields = {'concurrency_per_worker', 'job_timeout_seconds', 'runtime_digest', 'config_digest', 'total_hourly_rate', 'max_worker_seconds', 'allocation_overhead_usd', 'max_total_usd', 'schema_version'}
        if not isinstance(data, dict) or set(data) != fields or type(data['schema_version']) is not int or data['schema_version'] != 1:
            raise OnedeployError('invalid_config', 'Invalid budget policy fields or version.')
        if not isinstance(data['runtime_digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', data['runtime_digest']):
            raise OnedeployError('invalid_config', 'Runtime digest must be SHA-256.')
        config = DeploymentConfig.from_dict(config.to_dict() if isinstance(config, DeploymentConfig) else config)
        if data['config_digest'] != digest(config.to_dict()):
            raise OnedeployError('stale_plan', 'Budget policy is not bound to this deployment configuration.')
        for key in ('total_hourly_rate', 'allocation_overhead_usd', 'max_total_usd'):
            value = data[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (key != 'allocation_overhead_usd' and value == 0):
                raise OnedeployError('invalid_config', 'Budget policy requires finite explicit resource rates and limits.')
        for key, upper in (('concurrency_per_worker', 64), ('job_timeout_seconds', 3600)):
            if type(data[key]) is not int or not 1 <= data[key] <= upper:
                raise OnedeployError('invalid_config', 'Policy concurrency or job timeout exceeds bounds.')
        if type(data['max_worker_seconds']) is not int or (not config.candidate_timeout_seconds + data['job_timeout_seconds'] <= data['max_worker_seconds'] <= 86400 or data['max_worker_seconds'] % 60 != 0):
            raise OnedeployError('invalid_config', 'Worker lifetime must cover candidate qualification, use whole minutes, and be at most 24 hours.')
        if data['max_total_usd'] > config.budget_usd:
            raise OnedeployError('invalid_config', 'Policy budget exceeds the approved deployment budget.')
        return cls(**data)

    def to_dict(self):
        return asdict(self)

    @property
    def id(self):
        return digest(self.to_dict())

    @property
    def reservation(self):
        return self.total_hourly_rate * self.max_worker_seconds / 3600 + self.allocation_overhead_usd


class BudgetLedger:
    """Single-host transactional reservations. Never auto-refund uncertain work.

    Rates and overhead are operator estimates; cloud charges may differ. This
    ledger cannot substitute for provider billing alerts or a running watchdog.
    """
    def __init__(self, path, policy, clock=time.time):
        self.path = Path(path)
        if self.path.is_symlink():
            raise OnedeployError('invalid_config', 'Budget state must not be a symlink.')
        self.policy, self.clock = policy, clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS autodeploy_release_runtime (release_id TEXT PRIMARY KEY, runtime_digest TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS autodeploy_budget (release_id TEXT PRIMARY KEY, config_digest TEXT NOT NULL, deployment_id TEXT NOT NULL, amount REAL NOT NULL, expires REAL NOT NULL, policy_id TEXT NOT NULL, retired INTEGER NOT NULL DEFAULT 0)')

        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def bind_runtime(self, release):
        """Bind before any remote effect; old unbound effects need explicit migration.

        Runtime changes require a fresh release identity. Neither reapproval nor
        successful prior qualification silently substitutes a different runtime.
        """
        rid = release.get('release_id')
        if not isinstance(rid, str) or not re.fullmatch(r'[0-9a-f]{64}', rid):
            raise OnedeployError('invalid_config', 'Invalid release runtime identity.')
        if digest(release.get('config')) != self.policy.config_digest:
            raise OnedeployError('stale_plan', 'Release configuration differs from approved runtime policy.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT runtime_digest FROM autodeploy_release_runtime WHERE release_id=?', (rid,)).fetchone()
            if prior:
                if prior[0] != self.policy.runtime_digest:
                    raise OnedeployError('stale_plan', 'Release runtime is immutable; create a fresh release for changed runtime settings.')
                return
            effects = (release.get('snapshot_id') or release.get('worker_id') or release.get('pending_operation')
                       or release.get('state', 'planned') != 'planned')
            if effects:
                raise OnedeployError('conflict', 'Existing remote release has no runtime binding; explicit verified migration is required.')
            db.execute('INSERT INTO autodeploy_release_runtime VALUES (?,?)', (rid, self.policy.runtime_digest))

    def authorize(self, release):
        if digest(release.get('config')) != self.policy.config_digest:
            return False
        rid = release.get('release_id')
        if not isinstance(rid, str) or len(rid) != 64 or any(c not in '0123456789abcdef' for c in rid):
            return False
        allocation = release.get('allocation_id', rid)
        if not isinstance(allocation, str) or not re.fullmatch(r'[0-9a-f]{64}', allocation):
            return False
        self.bind_runtime(release)
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT policy_id,expires,retired FROM autodeploy_budget WHERE release_id=?', (allocation,)).fetchone()
            if prior:
                return prior[0] == self.policy.id and now < prior[1] and not prior[2]
            active = db.execute('SELECT COUNT(*) FROM autodeploy_budget WHERE deployment_id=? AND retired=0', (release['deployment_id'],)).fetchone()[0]
            if active >= release['config']['max_workers']:
                return False
            total = db.execute('SELECT COALESCE(SUM(amount),0) FROM autodeploy_budget WHERE deployment_id=?', (release['deployment_id'],)).fetchone()[0]
            if total + self.policy.reservation > self.policy.max_total_usd:
                return False
            db.execute('INSERT INTO autodeploy_budget(release_id,config_digest,deployment_id,amount,expires,policy_id) VALUES (?,?,?,?,?,?)',
                       (allocation, self.policy.config_digest, release['deployment_id'], self.policy.reservation, now + self.policy.max_worker_seconds, self.policy.id))
        return True

    def retire(self, allocation_id):
        """Call only after verified provider deletion. Never refund charged estimate."""
        with self.connect() as db:
            db.execute('UPDATE autodeploy_budget SET retired=1 WHERE release_id=?', (allocation_id,))

    def remaining(self, deployment_id):
        with self.connect() as db:
            total = db.execute('SELECT COALESCE(SUM(amount),0) FROM autodeploy_budget WHERE deployment_id=?', (deployment_id,)).fetchone()[0]
        return max(0.0, self.policy.max_total_usd - total)
