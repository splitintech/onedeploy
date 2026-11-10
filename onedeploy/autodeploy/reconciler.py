"""Fail-closed, journaled rollout coordination; no provider is selected implicitly."""
from typing import Protocol
import time

from onedeploy.errors import OnedeployError
from .models import DeploymentConfig, ReleaseSpec, digest


class Adapter(Protocol):
    """Implementations reconcile deterministic release IDs and enforce cost limits.

    inspect_operation is read-only. Return {'resolved': True, 'result': ...} only
    with provider evidence of completion; otherwise None. Never retry an unknown
    write merely because a timeout occurred.
    """
    def ensure_snapshot(self, release: dict) -> str: ...
    def ensure_candidate(self, release: dict) -> str: ...
    def qualify(self, release: dict) -> dict: ...
    def drain(self, release: dict) -> bool: ...
    def delete_worker(self, release: dict) -> None: ...
    def inspect_operation(self, release: dict, operation: str) -> dict | None: ...


class Reconciler:
    def __init__(self, store, adapter=None):
        self.store, self.adapter = store, adapter

    def plan(self, config, spec):
        config = DeploymentConfig.from_dict(config.to_dict() if isinstance(config, DeploymentConfig) else config)
        spec = ReleaseSpec.from_dict(spec.to_dict() if isinstance(spec, ReleaseSpec) else spec)
        data = {"config": config.to_dict(), "spec": spec.to_dict()}
        record = {**data, "release_id": digest(data), "deployment_id": config.deployment_id,
                  "state": "planned", "snapshot_id": None, "worker_id": None,
                  "pending_operation": None, "created_at": time.time()}
        with self.store.locked():
            return self.store.add(record)

    def status(self, deployment_id):
        return self.store.deployment(deployment_id)

    def _approval(self, approved):
        if approved is not True:
            raise OnedeployError("approval_required", "Explicit authorization for this rollout is required.")
        if self.adapter is None:
            raise OnedeployError("unsupported_capability", "No qualified AutoDeploy adapter was selected.")

    def _current(self, release):
        if self.store.deployment(release["deployment_id"])["generation"] != release["generation"]:
            raise OnedeployError("stale_plan", "A newer release superseded this rollout.")

    def _operation(self, release, operation, *, current_check=True):
        if current_check:
            self._current(release)
        # Write intent first: a crash immediately after a remote effect is uncertain.
        pending = {**release, "pending_operation": operation, "pending_started_at": time.time(),
                   "resume_state": release["state"], "state": "needs_reconciliation"}
        self.store.save(pending)
        try:
            result = getattr(self.adapter, operation)(release)
        except Exception:
            # Never persist provider exception text, which can contain credentials.
            raise OnedeployError("uncertain", "Provider operation needs read-only reconciliation.", uncertain=True, mutation_possible=True) from None
        return self._finish(pending, result)

    def _finish(self, release, result):
        operation = release["pending_operation"]
        out = {**release, "pending_operation": None}
        out.pop("resume_state", None)
        out.pop("pending_started_at", None)
        if operation in ("ensure_snapshot", "ensure_candidate"):
            if not isinstance(result, str) or not result or len(result) > 200 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in result):
                raise OnedeployError("uncertain", "Provider returned an invalid resource identifier.", uncertain=True, mutation_possible=True)
            field, state = (("snapshot_id", "snapshot_ready") if operation == "ensure_snapshot" else ("worker_id", "candidate_ready"))
            out.update({field: result, "state": state})
            if operation == "ensure_candidate":
                # Conservatively count the entire provider call against worker TTL.
                # Read-only recovery preserves this original persisted intent time.
                out["worker_created_at"] = release.get("pending_started_at", release["created_at"])
        elif operation == "qualify":
            required = ("ready", "gpu", "model_verified", "inference_passed")
            if not isinstance(result, dict) or not all(result.get(k) is True for k in required):
                out.update(state="failed", failure_code="candidate_not_qualified")
            else:
                out.update(state="qualified", qualification={k: True for k in required})
        elif operation == "drain":
            if type(result) is not bool:
                raise OnedeployError("uncertain", "Invalid drain result.", uncertain=True)
            out.update(state=release["resume_state"], worker_drained=result)
        elif operation == "delete_worker":
            out.update(state=release["resume_state"], worker_deleted=True)
        else:
            raise OnedeployError("uncertain", "Unknown pending operation.", uncertain=True)
        return self.store.save(out)

    def _recover(self, release):
        if not release.get("pending_operation"):
            return release
        inspect = getattr(self.adapter, "inspect_operation", None)
        try:
            resolution = inspect(release, release["pending_operation"]) if inspect else None
        except Exception:
            raise OnedeployError(
                "uncertain", "Provider inspection failed; pending operation remains unresolved.",
                uncertain=True, mutation_possible=True,
            ) from None
        if not isinstance(resolution, dict) or resolution.get("resolved") is not True:
            raise OnedeployError("uncertain", "Pending provider operation has not been resolved; no write retried.", uncertain=True, mutation_possible=True)
        return self._finish(release, resolution.get("result"))

    def apply(self, release_id, approved=False):
        self._approval(approved)
        with self.store.locked():
            release = self.store.get(release_id)
            self._current(release)
            release = self._recover(release)
            if release["state"] == "failed":
                return release
            if release["state"] == "planned":
                release = self._operation(release, "ensure_snapshot")
            if release["state"] == "snapshot_ready":
                active = [r for r in self.store.deployment(release["deployment_id"])["releases"] if r.get("worker_id") and not r.get("worker_deleted") and r["release_id"] != release_id]
                if len(active) >= release["config"]["max_workers"]:
                    raise OnedeployError("conflict", "Worker limit prevents safe candidate overlap; preserve existing workers.")
                release = self._operation(release, "ensure_candidate")
            if release["state"] == "candidate_ready":
                release = self._operation(release, "qualify")
            if release["state"] == "qualified":
                self._current(release)
                previous = self.store.deployment(release["deployment_id"])["desired_release"]
                release = self.store.save({**release, "previous_release": previous})
                release = self.store.promote(release)
            if release["state"] in ("promoted", "draining"):
                release = self.store.save({**release, "state": "draining"})
                previous_id = release.get("previous_release")
                if previous_id:
                    previous = self.store.get(previous_id)
                    if not previous.get("worker_deleted"):
                        previous = self._recover(previous)
                        if not previous.get("worker_drained"):
                            previous = self._operation(previous, "drain", current_check=False)
                        if not previous.get("worker_drained"):
                            return release
                        if not previous.get("worker_deleted"):
                            self._operation(previous, "delete_worker", current_check=False)
                release = self.store.save({**release, "state": "complete"})
            return release

    def rollback(self, deployment_id, release_id, approved=False):
        self._approval(approved)
        with self.store.locked():
            prior = self.store.get(release_id)
            if prior["deployment_id"] != deployment_id or not prior.get("qualification"):
                raise OnedeployError("invalid_config", "Rollback requires a previously qualified release of this deployment.")
            current = self.store.deployment(deployment_id)
            if current["desired_release"] == release_id:
                return prior
            latest = current["releases"][0] if current["releases"] else None
            if latest and latest.get("rollback_to") == release_id:
                result = latest
            else:
                result = None
            # New generation and new candidate: old worker may already be deleted.
            identity = {"rollback_to": release_id, "from_generation": current["generation"]}
            record = {"config": prior["config"], "spec": prior["spec"], "deployment_id": deployment_id,
                      "release_id": digest(identity), "state": "planned", "snapshot_id": None,
                      "worker_id": None, "pending_operation": None, "created_at": time.time(), **identity}
            if result is None:
                result = self.store.add(record)
        return self.apply(result["release_id"], approved=True)

    def cleanup(self, release_id, approved=False):
        """Explicitly drain/delete a failed or superseded non-desired worker.

        Snapshots and shared model volumes are deliberately retained. This also
        recovers old workers left behind when a newer plan supersedes a rollout.
        """
        self._approval(approved)
        with self.store.locked():
            release = self.store.get(release_id)
            deployment = self.store.deployment(release["deployment_id"])
            if deployment["desired_release"] == release_id:
                raise OnedeployError("conflict", "Cannot clean up the desired release worker.")
            if release["generation"] == deployment["generation"] and release.get("resume_state", release["state"]) != "failed":
                raise OnedeployError("conflict", "Cleanup requires a failed or superseded release.")
            release = self._recover(release)
            if not release.get("worker_id") or release.get("worker_deleted"):
                return release
            if not release.get("worker_drained"):
                release = self._operation(release, "drain", current_check=False)
            if release.get("worker_drained"):
                release = self._operation(release, "delete_worker", current_check=False)
            return release
