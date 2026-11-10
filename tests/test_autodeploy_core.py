import copy
import pytest

from onedeploy.autodeploy import DeploymentConfig, ReleaseSpec, Reconciler, StateStore
from onedeploy.errors import OnedeployError


def config():
    return dict(deployment_id="speech", repository="splitintech/app", branch="deploy", target="daytona", resources=dict(cpu=2, memory_gib=8, disk_gib=20, gpu_count=1, gpu_type="rtx4090"), max_workers=2, budget_usd=10)


def spec(n="a"):
    return dict(source_commit=n*40, image_digest="ghcr.io/org/image@sha256:"+n*64, model_digest="b"*64, evidence_digest="c"*64)


class Fake:
    def __init__(self):
        self.calls = []; self.failure = None; self.resolution = None; self.drained = True
    def ensure_snapshot(self, r):
        self.calls.append("snapshot")
        if self.failure == "snapshot": raise TimeoutError("SECRET")
        return "snapshot-" + r["release_id"][:8]
    def ensure_candidate(self, r): self.calls.append("candidate"); return "worker-" + r["release_id"][:8]
    def qualify(self, r):
        self.calls.append("qualify")
        return dict(ready=True, gpu=self.failure != "gpu", model_verified=True, inference_passed=True)
    def drain(self, r): self.calls.append("drain"); return self.drained
    def delete_worker(self, r): self.calls.append("delete")
    def inspect_operation(self, r, op): self.calls.append("inspect"); return self.resolution


@pytest.fixture
def engine(tmp_path):
    return Reconciler(StateStore(tmp_path / "state.db"), Fake())


def test_rollout_is_durable_idempotent_and_approval_gated(engine):
    p = engine.plan(config(), spec())
    assert engine.plan(config(), spec())["generation"] == 1
    with pytest.raises(OnedeployError, match="authorization"):
        engine.apply(p["release_id"])
    assert engine.adapter.calls == []
    r = engine.apply(p["release_id"], approved=True)
    assert r["state"] == "complete"
    engine.apply(p["release_id"], approved=True)
    assert engine.adapter.calls == ["snapshot", "candidate", "qualify"]
    assert StateStore(engine.store.path).deployment("speech")["desired_release"] == p["release_id"]


def test_stale_plan_cannot_create_or_promote(engine):
    first = engine.plan(config(), spec())
    second = engine.plan(config(), spec("d"))
    with pytest.raises(OnedeployError) as e: engine.apply(first["release_id"], approved=True)
    assert e.value.code == "stale_plan"
    assert not engine.adapter.calls
    engine.apply(second["release_id"], approved=True)
    with pytest.raises(OnedeployError): engine.store.promote(first)
    assert engine.status("speech")["desired_release"] == second["release_id"]


def test_timeout_never_retries_write_and_can_reconcile(engine):
    p = engine.plan(config(), spec())
    engine.adapter.failure = "snapshot"
    with pytest.raises(OnedeployError) as error: engine.apply(p["release_id"], approved=True)
    assert "SECRET" not in str(error.value)
    assert engine.store.get(p["release_id"])["state"] == "needs_reconciliation"
    with pytest.raises(OnedeployError): engine.apply(p["release_id"], approved=True)
    assert engine.adapter.calls == ["snapshot", "inspect"]
    engine.adapter.resolution = {"resolved": True, "result": "snapshot-recovered"}
    assert engine.apply(p["release_id"], approved=True)["state"] == "complete"
    assert engine.adapter.calls.count("snapshot") == 1


def test_unqualified_candidate_does_not_replace_healthy_release(engine):
    first = engine.plan(config(), spec()); engine.apply(first["release_id"], approved=True)
    second = engine.plan(config(), spec("d")); engine.adapter.failure = "gpu"
    assert engine.apply(second["release_id"], approved=True)["state"] == "failed"
    assert engine.status("speech")["desired_release"] == first["release_id"]
    assert "delete" not in engine.adapter.calls


def test_drain_waits_and_preserves_active_version(engine):
    first = engine.plan(config(), spec()); engine.apply(first["release_id"], approved=True)
    second = engine.plan(config(), spec("d")); engine.adapter.drained = False
    assert engine.apply(second["release_id"], approved=True)["state"] == "draining"
    assert "delete" not in engine.adapter.calls
    engine.adapter.drained = True
    assert engine.apply(second["release_id"], approved=True)["state"] == "complete"
    assert engine.store.get(first["release_id"])["worker_deleted"] is True


def test_rollback_recreates_deleted_worker_as_new_generation(engine):
    first = engine.plan(config(), spec()); engine.apply(first["release_id"], approved=True)
    second = engine.plan(config(), spec("d")); engine.apply(second["release_id"], approved=True)
    result = engine.rollback("speech", first["release_id"], approved=True)
    assert result["generation"] == 3 and result["spec"] == first["spec"]
    assert result["worker_id"] != first.get("worker_id")
    assert result["state"] == "complete"
    assert engine.rollback("speech", first["release_id"], approved=True)["generation"] == 3


def test_resource_limit_blocks_overlap(engine):
    c = config(); c["max_workers"] = 1
    first = engine.plan(c, spec()); engine.apply(first["release_id"], approved=True)
    second = engine.plan(c, spec("d"))
    with pytest.raises(OnedeployError, match="Worker limit"):
        engine.apply(second["release_id"], approved=True)
    assert engine.status("speech")["desired_release"] == first["release_id"]


def test_concurrent_controller_fails_closed(engine):
    p = engine.plan(config(), spec())
    with engine.store.locked():
        with pytest.raises(OnedeployError) as e:
            Reconciler(StateStore(engine.store.path), Fake()).apply(p["release_id"], approved=True)
    assert e.value.code == "locked"


@pytest.mark.parametrize("field,value", [("budget_usd", float("nan")), ("budget_usd", True), ("max_workers", 0), ("branch", "../main"), ("target", "https://x"), ("unknown", 1)])
def test_config_rejects_unsafe_values(field, value):
    c = config(); c[field] = value
    with pytest.raises(OnedeployError): DeploymentConfig.from_dict(c)


@pytest.mark.parametrize("field,value", [("source_commit", "main"), ("image_digest", "image:latest"), ("model_digest", "abc"), ("api_key", "secret")])
def test_release_rejects_unbound_inputs(field, value):
    s = spec(); s[field] = value
    with pytest.raises(OnedeployError): ReleaseSpec.from_dict(s)


def test_failed_candidate_cleanup_is_explicit_and_never_deletes_desired(engine):
    first = engine.plan(config(), spec()); engine.apply(first["release_id"], approved=True)
    second = engine.plan(config(), spec("d")); engine.adapter.failure = "gpu"
    engine.apply(second["release_id"], approved=True)
    with pytest.raises(OnedeployError): engine.cleanup(second["release_id"])
    with pytest.raises(OnedeployError): engine.cleanup(first["release_id"], approved=True)
    assert engine.cleanup(second["release_id"], approved=True)["worker_deleted"]
    assert engine.status("speech")["desired_release"] == first["release_id"]


def test_cleanup_can_finish_superseded_rollout_old_worker(engine):
    first = engine.plan(config(), spec()); engine.apply(first["release_id"], approved=True)
    second = engine.plan(config(), spec("d")); engine.adapter.drained = False
    engine.apply(second["release_id"], approved=True)
    engine.plan(config(), spec("e"))
    engine.adapter.drained = True
    assert engine.cleanup(first["release_id"], approved=True)["worker_deleted"]
    assert engine.status("speech")["desired_release"] == second["release_id"]


def test_cleanup_timeout_inspects_instead_of_repeating_delete(engine):
    first = engine.plan(config(), spec()); engine.adapter.failure = "gpu"
    engine.apply(first["release_id"], approved=True)
    def uncertain_delete(r):
        engine.adapter.calls.append("delete")
        raise TimeoutError()
    engine.adapter.delete_worker = uncertain_delete
    with pytest.raises(OnedeployError): engine.cleanup(first["release_id"], approved=True)
    with pytest.raises(OnedeployError): engine.cleanup(first["release_id"], approved=True)
    assert engine.adapter.calls.count("delete") == 1
    engine.adapter.resolution = {"resolved": True, "result": None}
    assert engine.cleanup(first["release_id"], approved=True)["worker_deleted"]


def test_inspection_failure_is_sanitized_and_preserves_pending_mutation(engine):
    import traceback
    release = engine.plan(config(), spec())
    engine.adapter.failure = "snapshot"
    with pytest.raises(OnedeployError):
        engine.apply(release["release_id"], approved=True)
    def secret_inspection(record, operation):
        raise RuntimeError("SECRET-API-TOKEN example.invalid?token=private")
    engine.adapter.inspect_operation = secret_inspection
    with pytest.raises(OnedeployError) as exc:
        engine.apply(release["release_id"], approved=True)
    error = exc.value
    assert error.code == "uncertain" and error.uncertain and error.mutation_possible
    assert "SECRET-API-TOKEN" not in str(error)
    assert "SECRET-API-TOKEN" not in ''.join(traceback.format_exception(error))
    assert engine.store.get(release["release_id"])["pending_operation"] == "ensure_snapshot"
    assert engine.adapter.calls == ["snapshot"]


def test_candidate_created_time_is_intent_time_and_survives_recovery(engine, monkeypatch):
    import onedeploy.autodeploy.reconciler as module
    monkeypatch.setattr(module.time, 'time', lambda: 100.0)
    release = engine.plan(config(), spec())
    def uncertain_candidate(r):
        raise TimeoutError()
    engine.adapter.ensure_candidate = uncertain_candidate
    with pytest.raises(OnedeployError):
        engine.apply(release['release_id'], approved=True)
    pending=engine.store.get(release['release_id'])
    assert pending['pending_started_at']==100.0
    monkeypatch.setattr(module.time, 'time', lambda: 300.0)
    engine.adapter.resolution={'resolved':True,'result':'recovered-worker'}
    completed=engine.apply(release['release_id'],approved=True)
    assert completed['worker_created_at']==100.0
    assert 'pending_started_at' not in completed
