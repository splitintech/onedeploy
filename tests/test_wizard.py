"""Offline pilot coverage for the opt-in three-step wizard, not live qualification."""
import asyncio
from copy import deepcopy
import threading
from types import SimpleNamespace

import pytest
pytest.importorskip("textual")
from textual.widgets import Button, Checkbox, Collapsible, Input, Select, Static

import onedeploy
from onedeploy import core
from onedeploy.errors import OnedeployError
from onedeploy.wizard import DeploymentWizard, _set_path, _get_path, fingerprint, review_summary


MANIFEST = {"version": 1, "provider": "railway", "name": "fixture", "profile": "docker-http", "source": {"path": ".", "trust_code": True}, "target": {"account": "acct", "project": "project", "environment": "env", "service": "service", "mode": "existing"}, "variables": {"TOKEN": {"type": "string", "secret": True, "required": True}}}
PROFILE = {"id": "profile", "kind": "profile", "manifest": MANIFEST, "root": "/fixture"}
CANDIDATE = {"id": "candidate", "format": "dockerfile", "path": "Dockerfile", "manifest": MANIFEST, "missing": [{"field": "target.account", "type": "string", "reason": "Exact account"}, {"field": "runtime.port", "type": "integer", "reason": "Listener port"}], "provenance": [{"field": "build.dockerfile", "path": "Dockerfile", "explicit": True}]}
DISCOVERY = {"id": "discovery", "kind": "discovery", "candidates": [CANDIDATE]}


def plan(remote=True):
    return {"id": "reviewed-id", "manifest": deepcopy(MANIFEST), "source": {"root": "/fixture", "bundle_sha256": "bundle"}, "operations": [{"id": "deploy"}], "remote_preconditions": {"identity": "acct"} if remote else None}


async def press(app, ident):
    await app.on_button_pressed(Button.Pressed(app.query_one("#" + ident, Button)))


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def install_discovery(monkeypatch):
    calls = []
    def discover(path, provider):
        calls.append(("discover", path, provider))
        return deepcopy(DISCOVERY)
    def inspect_resolution(discovery, answers):
        calls.append(("resolve", deepcopy(answers)))
        return {"status": "resolved", "profile": deepcopy(PROFILE)}
    fake = SimpleNamespace(discover=discover, inspect_resolution=inspect_resolution, save_profile=lambda profile, path: {"path": path})
    monkeypatch.setattr(onedeploy, "discovery", fake, raising=False)
    return calls


async def prepare(app, pilot):
    await press(app, "provider-next")
    await press(app, "discover")
    await settle(app, pilot)
    app.query_one("#runtime-profile", Select).value = "docker-http"
    app.query_one("#answer-0", Input).value = "acct"
    app.query_one("#answer-1", Input).value = "8000"
    app.query_one("#trust-code", Checkbox).value = True
    await pilot.pause()
    await press(app, "resolve-project")
    await settle(app, pilot)


@pytest.mark.asyncio
async def test_complete_three_step_flow_uses_engine_and_click_not_hash(monkeypatch):
    calls = install_discovery(monkeypatch)
    planned, deployed = [], []
    def make(profile, **kwargs):
        planned.append((profile, kwargs))
        return plan()
    def deploy(exact, **kwargs):
        deployed.append((exact, kwargs))
        return {"deployment_id": exact["id"], "state": "succeeded", "layers": {"provider_accepted": True, "readiness_passed": False}}
    monkeypatch.setattr(core, "make_plan_from_profile", make, raising=False)
    monkeypatch.setattr(core, "deploy", deploy)
    app = DeploymentWizard(path="./existing")
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert app.step == 1 and app.query_one("#advanced", Collapsible).collapsed
        assert not app.query("#approval")
        await prepare(app, pilot)
        assert app.step == 2 and app.profile == PROFILE
        assert calls[0] == ("discover", "./existing", "railway")
        assert calls[1][1]["runtime"]["port"] == 8000
        assert calls[1][1]["trust_code"] is True
        secret = app.query_one("#variable-0", Input)
        assert secret.password
        secret.value = "in-memory-secret"
        app.query_one("#inspect-remote", Checkbox).value = True
        await pilot.pause()
        await press(app, "review-plan")
        await settle(app, pilot)
        assert app.step == 3 and app.plan["id"] == "reviewed-id"
        assert planned[0][1]["env_overrides"] == {"TOKEN": "in-memory-secret"}
        assert planned[0][1]["inspect_remote"] is True
        assert "in-memory-secret" not in str(app.query_one("#review", Static).render())
        await press(app, "deploy")
        await settle(app, pilot)
        assert len(deployed) == 1 and deployed[0][1]["approval"] == "reviewed-id"
        assert app.plan is None and app.deployment_id == "reviewed-id"
        assert "readiness passed: not verified" in str(app.query_one("#result", Static).render())
        assert app.query_one("#deploy", Button).disabled


@pytest.mark.asyncio
async def test_local_preview_never_executes_and_back_clears_review(monkeypatch):
    install_discovery(monkeypatch)
    monkeypatch.setattr(core, "make_plan_from_profile", lambda *a, **kw: plan(False), raising=False)
    writes = []
    monkeypatch.setattr(core, "deploy", lambda *a, **kw: writes.append(kw))
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        await prepare(app, pilot)
        await press(app, "review-plan")
        await settle(app, pilot)
        assert app.query_one("#deploy", Button).disabled
        await press(app, "deploy")
        assert not writes
        await pilot.press("escape")
        assert app.step == 2 and app.plan is None


@pytest.mark.asyncio
async def test_edits_provider_switch_and_runtime_type_safety(monkeypatch):
    install_discovery(monkeypatch)
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        await prepare(app, pilot)
        app.plan = plan()
        app.review_binding = fingerprint(app.plan)
        app.query_one("#variable-0", Input).value = "secret"
        await pilot.pause()
        assert app.plan is None
        app.plan = plan()
        app.review_binding = fingerprint(app.plan)
        app.query_one("#provider", Select).value = "modal"
        await pilot.pause()
        assert app.profile is None and app.discovery is None and app.answers == {}
        assert app.plan is None and not app.query("#variable-0")
        assert not app.query_one("#trust-code", Checkbox).value
        assert app.query_one("#env-file", Input).value == ""


@pytest.mark.asyncio
async def test_repeated_submit_and_cancel_preserve_uncertain_recovery(monkeypatch):
    started, release = threading.Event(), threading.Event()
    writes = []
    def deploy(exact, **kwargs):
        writes.append(kwargs)
        started.set()
        release.wait()
        return {"deployment_id": exact["id"], "state": "uncertain"}
    monkeypatch.setattr(core, "deploy", deploy)
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        app.plan = plan()
        app.review_binding = fingerprint(app.plan)
        app.show_step(3)
        app._refresh_controls()
        try:
            await press(app, "deploy")
            await pilot.pause()
            assert started.is_set()
            await press(app, "deploy")
            app.action_cancel_operation()
            assert writes[0]["cancel_event"].is_set() and app.busy
        finally:
            release.set()
        await settle(app, pilot)
        assert len(writes) == 1
        assert app.plan is None and not app.busy
        assert not app.query_one("#advanced", Collapsible).collapsed
        assert app.query_one("#deployment", Input).value == "reviewed-id"
        await press(app, "deploy")
        assert len(writes) == 1


@pytest.mark.asyncio
async def test_mutated_review_and_stale_worker_cannot_enable_deploy(monkeypatch):
    writes = []
    monkeypatch.setattr(core, "deploy", lambda *a, **kw: writes.append(kw))
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        app.plan = plan()
        app.review_binding = fingerprint(app.plan)
        app.plan["manifest"]["target"]["account"] = "other"
        await press(app, "deploy")
        assert not writes and app.plan is None
        revision = app._revision
        app.invalidate()
        await app.finished("review-plan", plan(), revision, threading.Event())
        assert app.plan is None


@pytest.mark.asyncio
async def test_runtime_validation_never_echoes_secret(monkeypatch):
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        app.profile = deepcopy(PROFILE)
        await app._render_variables({"variables": {"COUNT": {"type": "integer", "secret": True}}})
        app.query_one("#variable-0", Input).value = "not-an-int-secret"
        await pilot.pause()
        await press(app, "review-plan")
        assert not app.busy
        assert "not-an-int-secret" not in str(app.query_one("#status", Static).render())
        assert "integer" in str(app.query_one("#status", Static).render())


@pytest.mark.asyncio
async def test_recovery_requires_new_click_and_operator_consent(monkeypatch):
    calls = []
    def resume(deployment, **kwargs):
        calls.append((deployment, kwargs))
        return {"state": "approval_required", "plan": plan()}
    monkeypatch.setattr(core, "resume", resume)
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        app.query_one("#deployment", Input).value = "prior"
        app.query_one("#observation", Input).value = "evidence"
        app.query_one("#outcome", Select).value = "operator-confirmed-not-executed"
        await pilot.pause()
        await press(app, "operator-resolve")
        assert not calls
        app.query_one("#resolution-consent", Checkbox).value = True
        await pilot.pause()
        await press(app, "operator-resolve")
        await settle(app, pilot)
        assert calls[0][1]["resolve_with_observation"] == "evidence"
        assert app.plan and app.step == 3
        assert not app.query_one("#resolution-consent", Checkbox).value
        assert app.query_one("#observation", Input).value == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("action,method", [("resume", "resume"), ("rollback", "rollback"), ("advanced-status", "status"), ("logs", "logs")])
async def test_recovery_controls_share_engine(monkeypatch, action, method):
    calls = []
    monkeypatch.setattr(core, method, lambda *a, **kw: calls.append((a, kw)) or {"state": "reconciliation_required"})
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        app.query_one("#deployment", Input).value = "prior"
        app.query_one("#release", Input).value = "release"
        await pilot.pause()
        await press(app, action)
        await settle(app, pilot)
        assert len(calls) == 1 and calls[0][0] == ("prior",)
        assert app.plan is None
        if action == "logs":
            assert calls[0][1]["follow"] is False


@pytest.mark.asyncio
async def test_narrow_keyboard_navigation_resize_and_error_focus(monkeypatch):
    install_discovery(monkeypatch)
    app = DeploymentWizard()
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        await pilot.press("tab", "tab", "enter")
        await pilot.pause()
        assert app.step == 2
        await press(app, "discover")
        await settle(app, pilot)
        app.query_one("#runtime-profile", Select).value = "docker-http"
        app.query_one("#answer-1", Input).value = "wrong"
        app.query_one("#trust-code", Checkbox).value = True
        await pilot.pause()
        await press(app, "resolve-project")
        await pilot.pause()
        assert app.focused.id == "answer-1"
        assert not app.busy
        await pilot.resize_terminal(24, 12)
        await pilot.pause()
        await pilot.press("alt+left")
        assert app.step == 1


def test_review_exposes_consequences_and_nested_answer_paths():
    current = plan()
    current["kind"] = "rollback"
    text = review_summary(current)
    for phrase in ("Exact destination", "Authenticated identity", "Secret variable names: TOKEN", "COST UNCERTAINTY", "single-writer", "ROLLBACK DOES NOT UNDO DATA"):
        assert phrase in text
    answers = {}
    _set_path(answers, "storage.0.single_writer", True)
    assert _get_path(answers, "storage.0.single_writer") is True
    assert _get_path(answers, "missing", "fallback") == "fallback"


@pytest.mark.asyncio
async def test_narrow_keyboard_enter_approves_exact_reviewed_plan(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "deploy", lambda exact, **kwargs: calls.append((exact, kwargs)) or {"deployment_id": exact["id"], "state": "succeeded", "layers": {"provider_accepted": True, "application_qualified": False}})
    app = DeploymentWizard()
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        await app.finished("load-plan", plan(), app._revision, threading.Event())
        await pilot.pause()
        app.query_one("#deploy", Button).focus()
        await pilot.pause()
        await pilot.press("enter")
        await settle(app, pilot)
        assert len(calls) == 1
        assert calls[0][1]["approval"] == "reviewed-id"
        assert "application qualified: not verified" in str(app.query_one("#result", Static).render())


@pytest.mark.asyncio
async def test_saved_plan_new_target_clears_previous_runtime_inputs():
    app = DeploymentWizard(env_file="/private/previous.env")
    async with app.run_test() as pilot:
        await app._render_variables(MANIFEST)
        app.query_one("#variable-0", Input).value = "previous-secret"
        await pilot.pause()
        app._runtime_scope = app._runtime_identity("/other-project", MANIFEST)
        await app.finished("load-plan", plan(), app._revision, threading.Event())
        assert app.plan is not None
        assert app.query_one("#env-file", Input).value == ""
        assert app.query_one("#variable-0", Input).value == ""


@pytest.mark.asyncio
async def test_candidate_scope_env_clear_is_independent_of_resolution_answers():
    app = DeploymentWizard(env_file="/private/explicit-first.env")
    async with app.run_test() as pilot:
        first = deepcopy(CANDIDATE)
        second = deepcopy(CANDIDATE)
        second["id"] = "second-candidate"
        app.discovery = {"root": "/fixture", "candidates": [first, second]}
        app.query_one("#candidate", Select).set_options([("First", first["id"]), ("Second", second["id"])])
        app._assign("candidate", first["id"])
        await app._show_candidate(first["id"])
        assert app.query_one("#env-file", Input).value == "/private/explicit-first.env"
        # Discovery or unresolved-answer rendering may reset answers; scope must
        # remain authoritative independently of that temporary form state.
        app.answers = {}
        app.query_one("#candidate", Select).value = second["id"]
        await pilot.pause()
        assert app.query_one("#env-file", Input).value == ""
        assert not app.query_one("#trust-code", Checkbox).value


@pytest.mark.asyncio
async def test_profile_reload_new_source_scope_clears_env_even_with_empty_answers():
    app = DeploymentWizard(env_file="/private/first.env")
    async with app.run_test() as pilot:
        app._enter_source_scope("/first", "first-candidate", MANIFEST)
        assert app.query_one("#env-file", Input).value == "/private/first.env"
        app.answers = {}
        loaded = deepcopy(PROFILE)
        loaded["root"] = "/second"
        loaded["discovery"] = deepcopy(DISCOVERY)
        loaded["resolution"] = {"candidate": CANDIDATE["id"], "answers": {"target": MANIFEST["target"]}}
        await app.finished("load-profile", loaded, app._revision, threading.Event())
        assert app.query_one("#env-file", Input).value == ""
        assert app.profile is None
