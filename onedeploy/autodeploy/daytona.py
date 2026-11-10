"""Explicit, injectable Daytona SDK boundary for AutoDeploy.

No import creates a client, reads credentials, or calls a provider. GPU SDK
bindings must be qualified and supplied by the caller; the legacy CLI transport
is never silently upgraded to SDK. Provider timeouts propagate for reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable


class DaytonaContractError(ValueError):
    """The provider result does not satisfy the deployment contract."""


def _value(value: Any) -> Any:
    return getattr(value, 'value', value)


def _digest(image: str) -> str:
    if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}', image):
        raise DaytonaContractError('An immutable registry image digest is required')
    return image


@dataclass(frozen=True)
class SDKBindings:
    """Version-specific constructors, deliberately not guessed across SDKs.

    Both constructors receive keyword arguments shown below. Their wrappers must
    be qualified against the installed SDK before enabling a paid deployment.
    Snapshot creation must apply explicit GPU-compatible resource constraints.
    """
    snapshot_params: Callable[..., Any]
    sandbox_params: Callable[..., Any]
    missing_error: type[Exception]
    qualified_version: str


class DaytonaSDKAdapter:
    """SDK resource operations; release promotion belongs to the state engine.

    ``probe`` performs authenticated application checks and returns sanitized
    evidence, not provider objects. ``drainer`` stops admissions and returns an
    authoritative active-job count. Both are injected trusted application code.
    """

    def __init__(self, client: Any, bindings: SDKBindings, *, account: str,
                 deployment: str, resources: dict[str, Any], probe: Callable,
                 drainer: Callable, transport: str = 'sdk', timeout: int = 120,
                 idle_minutes: int = 5, authorize_operation: Callable | None = None,
                 inspect_status: Callable | None = None):
        if transport != 'sdk':
            raise DaytonaContractError('AutoDeploy requires explicitly selected SDK transport')
        for name, value in [('account', account), ('deployment', deployment)]:
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
                raise DaytonaContractError(f'Invalid {name}')
        required = {'cpu', 'memory', 'disk', 'gpu', 'gpu_type'}
        if set(resources) != required or any(type(resources[k]) is not int or resources[k] <= 0 for k in required - {'gpu_type'}):
            raise DaytonaContractError('Explicit positive CPU, RAM, disk and GPU resources are required')
        if not isinstance(resources['gpu_type'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', resources['gpu_type']):
            raise DaytonaContractError('An explicit GPU type is required')
        if type(timeout) is not int or not 1 <= timeout <= 3600 or type(idle_minutes) is not int or not 1 <= idle_minutes <= 60:
            raise DaytonaContractError('Timeout or idle policy outside allowed bounds')
        if not bindings.qualified_version:
            raise DaytonaContractError('A qualified SDK version is required')
        self.client, self.bindings = client, bindings
        self.account, self.deployment = account, deployment
        self.resources, self.probe, self.drainer = dict(resources), probe, drainer
        self.timeout, self.idle_minutes = timeout, idle_minutes
        self.authorize_operation = authorize_operation
        self.inspect_status = inspect_status

    def _name(self, release_id: str, kind: str) -> str:
        if not isinstance(release_id, str) or not release_id:
            raise DaytonaContractError('Release identity required')
        suffix = hashlib.sha256((self.account + '/' + self.deployment + '/' + release_id).encode()).hexdigest()[:24]
        return f'od-{kind}-{suffix}'

    def _labels(self, release_id: str) -> dict[str, str]:
        return {'onedeploy-managed': 'true', 'onedeploy-deployment': self.deployment,
                'onedeploy-release': release_id}

    def _owned_worker(self, worker_id: str, release_id: str) -> Any:
        worker = self.client.get(worker_id, request_timeout=self.timeout)
        if worker.organization_id != self.account or any(getattr(worker, 'labels', {}).get(k) != v for k, v in self._labels(release_id).items()):
            raise DaytonaContractError('Worker ownership mismatch')
        if worker.public is not False:
            raise DaytonaContractError('Worker must not have public unauthenticated ingress')
        return worker

    def _ensure_snapshot(self, release_id: str, image: str) -> str:
        image = _digest(image)
        name = self._name(release_id, 'snapshot')
        try:
            snapshot = self.client.snapshot.get(name)
        except self.bindings.missing_error:
            params = self.bindings.snapshot_params(name=name, image=image, resources=dict(self.resources))
            snapshot = self.client.snapshot.create(params, timeout=self.timeout)
        if snapshot.organization_id != self.account or snapshot.name != name or snapshot.image_name != image:
            raise DaytonaContractError('Snapshot identity, account or image mismatch')
        if _value(snapshot.state) not in {'active', 'ready'}:
            raise DaytonaContractError('Snapshot is not ready; reconcile rather than recreate')
        for field, attr in [('cpu', 'cpu'), ('memory', 'mem'), ('disk', 'disk'), ('gpu', 'gpu')]:
            if getattr(snapshot, attr, None) != self.resources[field]:
                raise DaytonaContractError('Snapshot resource allocation mismatch')
        return snapshot.id

    def _ensure_candidate(self, release_id: str, snapshot_id: str, model_digest: str) -> str:
        # Re-read exact snapshot before creating or trusting a worker.
        snapshot = self.client.snapshot.get(snapshot_id)
        if snapshot.organization_id != self.account or snapshot.name != self._name(release_id, 'snapshot') or _value(snapshot.state) not in {'active', 'ready'}:
            raise DaytonaContractError('Candidate snapshot ownership or readiness mismatch')
        _digest(snapshot.image_name)
        name = self._name(release_id, 'candidate')
        try:
            worker = self.client.get(name, request_timeout=self.timeout)
        except self.bindings.missing_error:
            params = self.bindings.sandbox_params(name=name, snapshot=snapshot_id,
                resources=dict(self.resources), labels=self._labels(release_id), public=False,
                env_vars={'ONEDEPLOY_RELEASE_ID': release_id, 'ONEDEPLOY_MODEL_DIGEST': model_digest},
                auto_stop_interval=self.idle_minutes, auto_delete_interval=0)
            worker = self.client.create(params, timeout=self.timeout)
        worker = self._owned_worker(worker.id, release_id)
        if worker.name != name or worker.snapshot != snapshot_id:
            raise DaytonaContractError('Candidate identity or snapshot mismatch')
        return worker.id

    def _qualification_result(self, evidence: Any, release_id: str, model_digest: str) -> dict[str, Any]:
        required = ('authenticated', 'cuda_available', 'model_on_gpu', 'checksum_verified', 'synthetic_inference_passed')
        if not isinstance(evidence, dict) or any(type(evidence.get(k)) is not bool for k in required):
            raise DaytonaContractError('Malformed GPU qualification evidence; outcome unknown')
        identity_matches = evidence.get('release_id') == release_id and evidence.get('model_digest') == model_digest
        ready = evidence['authenticated'] and identity_matches
        gpu = evidence['cuda_available'] and evidence['model_on_gpu'] and evidence.get('gpu_type') == self.resources['gpu_type']
        model = evidence['checksum_verified'] and identity_matches
        return {'ready': ready, 'gpu': gpu, 'model_verified': model,
                'inference_passed': evidence['synthetic_inference_passed'],
                'evidence': {**{k: evidence[k] for k in required}, 'release_id': release_id,
                    'model_digest': model_digest, 'sdk_version': self.bindings.qualified_version}}

    def _qualify(self, worker_id: str, release_id: str, model_digest: str) -> dict[str, Any]:
        worker = self._owned_worker(worker_id, release_id)
        if _value(worker.state) != 'started':
            return {'ready': False, 'gpu': False, 'model_verified': False, 'inference_passed': False}
        return self._qualification_result(self.probe(worker), release_id, model_digest)

    def _drain(self, worker_id: str, release_id: str) -> bool:
        worker = self._owned_worker(worker_id, release_id)
        evidence = self.drainer(worker)
        if not isinstance(evidence, dict) or evidence.get('accepting_jobs') is not False or type(evidence.get('active_jobs')) is not int or evidence['active_jobs'] < 0:
            raise DaytonaContractError('Invalid drain acknowledgement')
        complete = evidence['active_jobs'] == 0
        return complete

    def delete_drained(self, worker_id: str, release_id: str) -> None:
        # Re-probe after a restart; an old journal flag is insufficient.
        if not self._drain(worker_id, release_id):
            raise DaytonaContractError('Active jobs prevent worker deletion')
        worker = self._owned_worker(worker_id, release_id)
        self.client.delete(worker, timeout=self.timeout)

    def _release(self, release: dict[str, Any]) -> None:
        if release.get('deployment_id') != self.deployment:
            raise DaytonaContractError('Release deployment mismatch')
        config = release.get('config', {})
        expected = {'cpu': self.resources['cpu'], 'memory_gib': self.resources['memory'],
                    'disk_gib': self.resources['disk'], 'gpu_count': self.resources['gpu'],
                    'gpu_type': self.resources['gpu_type']}
        if config.get('target') != self.account or config.get('resources') != expected:
            raise DaytonaContractError('Approved account or resources do not match adapter')

    def _admission(self, release: dict[str, Any]) -> None:
        if self.authorize_operation is not None and self.authorize_operation(release) is not True:
            raise DaytonaContractError('Budget or deployment policy denied admission')

    def ensure_snapshot(self, release: dict[str, Any]) -> str:
        self._release(release)
        self._admission(release)
        return self._ensure_snapshot(release['release_id'], release['spec']['image_digest'])

    def ensure_candidate(self, release: dict[str, Any]) -> str:
        self._release(release)
        self._admission(release)
        return self._ensure_candidate(release['release_id'], release['snapshot_id'], release['spec']['model_digest'])

    def qualify(self, release: dict[str, Any]) -> dict[str, Any]:
        self._release(release)
        self._admission(release)
        return self._qualify(release['worker_id'], release['release_id'], release['spec']['model_digest'])

    def drain(self, release: dict[str, Any]) -> bool:
        self._release(release)
        return self._drain(release['worker_id'], release['release_id'])

    def delete_worker(self, release: dict[str, Any]) -> None:
        self._release(release)
        try:
            self.delete_drained(release['worker_id'], release['release_id'])
        except self.bindings.missing_error:
            return  # Previously deleted; never substitute another worker.


    def inspect_operation(self, release: dict[str, Any], operation: str) -> dict[str, Any] | None:
        """Read-only recovery of uncertain provider mutations; never recreate."""
        self._release(release)
        rid = release['release_id']
        if operation == 'ensure_snapshot':
            try:
                snapshot = self.client.snapshot.get(self._name(rid, 'snapshot'))
            except self.bindings.missing_error:
                return None
            if (snapshot.organization_id == self.account and snapshot.image_name == release['spec']['image_digest']
                    and _value(snapshot.state) in {'active', 'ready'}
                    and all(getattr(snapshot, attr, None) == self.resources[field] for field, attr in [('cpu','cpu'),('memory','mem'),('disk','disk'),('gpu','gpu')])):
                return {'resolved': True, 'result': snapshot.id}
            return None
        if operation == 'ensure_candidate':
            try:
                worker = self.client.get(self._name(rid, 'candidate'), request_timeout=self.timeout)
            except self.bindings.missing_error:
                return None
            self._owned_worker(worker.id, rid)
            if worker.snapshot == release['snapshot_id']:
                return {'resolved': True, 'result': worker.id}
        if operation in {'qualify', 'drain'}:
            # A different, explicitly read-only status callback is required.
            # Never replay synthetic inference or the mutating drain command.
            if self.inspect_status is None:
                return None
            worker = self._owned_worker(release['worker_id'], rid)
            status = self.inspect_status(worker, operation)
            if not isinstance(status, dict) or status.get('completed') is not True:
                return None
            if operation == 'qualify':
                result = self._qualification_result(status.get('evidence'), rid, release['spec']['model_digest'])
            else:
                evidence = status.get('evidence', {})
                if (not isinstance(evidence, dict) or evidence.get('accepting_jobs') is not False
                        or type(evidence.get('active_jobs')) is not int or evidence['active_jobs'] < 0):
                    return None
                result = evidence['active_jobs'] == 0
            return {'resolved': True, 'result': result}
        if operation in {'delete', 'delete_worker'}:
            try:
                self._owned_worker(release['worker_id'], rid)
            except self.bindings.missing_error:
                return {'resolved': True, 'result': None}
        return None


def sdk_adapter(*, account: str, deployment: str, resources: dict[str, Any],
                probe: Callable, drainer: Callable, api_key: str,
                timeout: int = 120, idle_minutes: int = 5,
                authorize_operation: Callable, inspect_status: Callable | None = None) -> DaytonaSDKAdapter:
    """Explicit live SDK factory. Caller must authorize the exact deployment.

    Requires a separate environment with daytona==0.231.0; do not upgrade the
    legacy CLI-first 0.226.0 environment implicitly. Application probes are
    mandatory trusted callbacks, never config module paths. No GPU is allocated
    by this function, but account identity is verified with the provider.
    """
    if not callable(authorize_operation):
        raise DaytonaContractError('Live deployment requires budget/authorization policy hook')
    import importlib.metadata
    if importlib.metadata.version('daytona') != '0.231.0':
        raise DaytonaContractError('AutoDeploy requires isolated daytona==0.231.0')
    from daytona import (Daytona, DaytonaConfig, Resources, GpuType,
                        CreateSnapshotParams, CreateSandboxFromSnapshotParams,
                        DaytonaNotFoundError)
    from onedeploy.providers.daytona_bridge import identity
    if not api_key or not isinstance(api_key, str):
        raise DaytonaContractError('Explicit API key required')
    # Existing bridge identity check prevents wrong-account writes.
    identity(api_key, account)
    def snapshot_params(**kwargs):
        raw = kwargs.pop('resources')
        try:
            gpu_type = GpuType(raw['gpu_type'])
        except ValueError as error:
            raise DaytonaContractError('Unsupported GPU type for pinned SDK') from error
        return CreateSnapshotParams(**kwargs, resources=Resources(**{**raw, 'gpu_type': gpu_type}))
    def sandbox_params(**kwargs):
        # Snapshot carries resource allocation; this model has no resources field.
        kwargs.pop('resources')
        return CreateSandboxFromSnapshotParams(**kwargs)
    bindings = SDKBindings(snapshot_params, sandbox_params, DaytonaNotFoundError, '0.231.0')
    client = Daytona(DaytonaConfig(api_key=api_key, api_url='https://app.daytona.io/api'))
    return DaytonaSDKAdapter(client, bindings, account=account, deployment=deployment,
        resources=resources, probe=probe, drainer=drainer, timeout=timeout, idle_minutes=idle_minutes, authorize_operation=authorize_operation, inspect_status=inspect_status)


def worker_control(worker: Any, action: str) -> dict[str, Any]:
    """Run the fixed control protocol inside a reviewed worker image.

    This uses Daytona's authenticated process API, not unauthenticated public
    ingress. The image must supply the executable; there is no shell/plugin path
    in user configuration. A missing/broken command is a qualification failure.
    """
    import json
    if action not in {'qualify', 'drain'}:
        raise DaytonaContractError('Unsupported worker control action')
    result = worker.process.exec('/opt/onedeploy/worker-control ' + action + ' --json', timeout=90)
    if result.exit_code != 0 or not isinstance(result.result, str) or len(result.result) > 16384:
        raise DaytonaContractError('Worker control command failed or exceeded response limit')
    try:
        evidence = json.loads(result.result)
    except (ValueError, TypeError) as error:
        raise DaytonaContractError('Invalid worker control response') from error
    if not isinstance(evidence, dict):
        raise DaytonaContractError('Worker control must return a JSON object')
    return evidence
