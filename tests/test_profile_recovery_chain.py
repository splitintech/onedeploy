"""Real profile bindings survive explicitly reconciled, no-write recovery chains.

Only the read-only remote observation is synthetic. Discovery, resolution,
planning, private journals, source inventory and execution guards remain real.
Provider transports must never be reached by these tests.
"""
import copy
import json
from threading import Event

import pytest

from onedeploy import core, discovery
from onedeploy.errors import OnedeployError


@pytest.fixture
def recovery_chain(tmp_path, monkeypatch):
    source = tmp_path / "application"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM scratch\n")
    found = discovery.discover(source, "railway")
    profile = discovery.resolve(found, {
        "name": "recovery-fixture",
        "profile": "docker-http",
        "trust_code": True,
        "target": {
            "account": "fixture-account", "project": "fixture-project",
            "environment": "fixture-env", "service": "fixture-service",
        },
    })
    observation = {"identity": {"account": "fixture-account"}, "inspection": {}}
    monkeypatch.setattr(core, "_observe", lambda *args: copy.deepcopy(observation))

    def no_provider(*args, **kwargs):
        pytest.fail("Recovery-chain regression attempted a provider transport")

    monkeypatch.setattr(core, "_execute", no_provider)
    monkeypatch.setattr(core.runner, "run", no_provider)
    original = core.make_plan_from_profile(profile, allow_dirty=True, inspect_remote=True)
    resolved = copy.deepcopy(original)
    resolved["resolution_of"] = original["id"]
    resolved["resolution_observation"] = core.digest(observation)
    resolved["id"] = core._plan_id(resolved)
    state = core._state_dir(state_dir=tmp_path / "state")
    origin = {
        "deployment_id": original["id"], "plan": original,
        "state": "operator_resolved", "operations": [],
        "operator_resolution": {"observation_id": resolved["resolution_observation"]},
        "recovery_plan_id": resolved["id"],
    }
    cancelled = {
        "deployment_id": resolved["id"], "plan": resolved,
        "state": "cancelled", "operations": [],
    }
    core._atomic(core._journal_path(state, original["id"]), core.canonical(origin))
    core._atomic(core._journal_path(state, resolved["id"]), core.canonical(cancelled))
    return state, original, resolved, origin


def test_profile_operator_recovery_can_resume_after_prewrite_cancellation(recovery_chain):
    state, original, resolved, _ = recovery_chain
    first = core.resume(resolved["id"], state_dir=state)
    child = first["plan"]
    assert first["state"] == "approval_required"
    assert first["mutation_performed"] is False
    assert child["recovery_of"] == resolved["id"]
    assert child["resolution_of"] == original["id"]
    origin_path = core._journal_path(state, original["id"])
    assert core._read_journal(origin_path)["recovery_plan_id"] == child["id"]

    # Repeating the same read-only resume is deterministic and does not revoke
    # its already generated child or produce another execution.
    assert core.resume(resolved["id"], state_dir=state)["plan"] == child
    with pytest.raises(OnedeployError) as unapproved:
        core.deploy(child, state_dir=state)
    assert unapproved.value.code == "approval_required"

    # Cancellation is observed after the real source/binding/journal checks but
    # before the first provider operation. It must not fail as stale_plan merely
    # because a legitimately resumed child has a new immutable plan ID.
    cancelled = Event()
    cancelled.set()
    with pytest.raises(OnedeployError) as stopped:
        core.deploy(child, approval=child["id"], state_dir=state, cancel_event=cancelled)
    assert stopped.value.code == "cancelled"
    child_journal = core._read_journal(core._journal_path(state, child["id"]))
    assert child_journal["state"] == "cancelled"
    assert child_journal["operations"] == []

    # The same safety-preserving behavior applies to another no-write child.
    grandchild = core.resume(child["id"], state_dir=state)["plan"]
    assert grandchild["recovery_of"] == child["id"]
    assert core._read_journal(origin_path)["recovery_plan_id"] == grandchild["id"]
    assert core.resume(child["id"], state_dir=state)["plan"] == grandchild


@pytest.mark.parametrize("tamper", ["binding", "observation", "profile", "state"])
def test_profile_recovery_cannot_advance_unrelated_origin(recovery_chain, tamper):
    state, original, resolved, origin = recovery_chain
    if tamper == "binding":
        origin["recovery_plan_id"] = "f" * 64
    elif tamper == "observation":
        origin["operator_resolution"]["observation_id"] = "unrelated-observation"
    elif tamper == "profile":
        origin["plan"]["normalized_profile"]["resolution"]["answers"]["name"] = "another-application"
    else:
        origin["state"] = "uncertain"
    path = core._journal_path(state, original["id"])
    core._atomic(path, core.canonical(origin))
    before = path.read_bytes()
    with pytest.raises(OnedeployError) as rejected:
        core.resume(resolved["id"], state_dir=state)
    assert rejected.value.code == "stale_plan"
    assert path.read_bytes() == before
    assert json.loads(path.read_text())["recovery_plan_id"] == origin["recovery_plan_id"]
