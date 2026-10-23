import threading
import pytest
pytest.importorskip("textual")
from textual.widgets import Input, Button, Static
from onedeploy import core, config
from onedeploy.tui import DeploymentApp


@pytest.mark.asyncio
async def test_secret_masking_back_and_cancel(monkeypatch):
    monkeypatch.setattr(config, "load_manifest", lambda p: {"provider": "daytona", "variables": {"TOKEN": {"secret": True, "required": True}}})
    monkeypatch.setattr(core, "make_plan", lambda *a, **kw: {"id": "p1", "variables": ["TOKEN"]})
    app = DeploymentApp()
    async with app.run_test(size=(100, 55)) as pilot:
        await pilot.pause()
        await app.load_configuration()
        await pilot.pause()
        field = app.query_one("#var-0", Input)
        assert field.password
        field.value = "a-secret"
        await pilot.pause()
        await pilot.click("#plan")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.plan["id"] == "p1"
        app.query_one("#approval", Input).value = "p1"
        await pilot.click("#back")
        assert app.plan is None
        assert app.query_one("#approval", Input).value == ""
        assert app.query_one("#deploy", Button).disabled
        app.action_cancel_operation()
        assert field.value == ""
        await pilot.resize_terminal(40, 20)
        await pilot.pause()


@pytest.mark.asyncio
async def test_repeated_deploy_and_cancel_do_not_duplicate(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    def deploy(*a, **kw):
        calls.append(kw)
        started.set()
        release.wait()
        return {"deployment_id": "d1", "state": "uncertain"}
    monkeypatch.setattr(core, "deploy", deploy)
    app = DeploymentApp()
    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        app.plan = {"id": "p1"}
        app.query_one("#deploy", Button).disabled = False
        app.query_one("#approval", Input).value = "p1"
        try:
            await pilot.click("#deploy")
            await pilot.pause()
            assert started.is_set()
            await pilot.click("#deploy")
            app.action_cancel_operation()
            assert calls[0]["cancel_event"].is_set()
            assert app.busy
        finally:
            # The fake cannot finish due to elapsed wall-clock time. Always
            # release on assertion failure too, so thread teardown cannot hang.
            release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(calls) == 1
        assert app.plan is None
        assert not app.busy
        assert app.query_one("#deployment", Input).value == "d1"


@pytest.mark.asyncio
async def test_bad_approval_never_calls_core(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "deploy", lambda *a, **kw: calls.append(kw))
    app = DeploymentApp()
    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        app.plan = {"id": "reviewed"}
        app.query_one("#deploy", Button).disabled = False
        app.query_one("#approval", Input).value = "wrong"
        await pilot.click("#deploy")
        assert not calls
        assert not app.busy


@pytest.mark.asyncio
async def test_edit_invalidates_review_and_default_secrets_are_masked(monkeypatch):
    monkeypatch.setattr(config, "load_manifest", lambda p: {"provider": "daytona", "variables": {"TOKEN": {"required": True}}})
    app = DeploymentApp()
    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        await app.load_configuration()
        await pilot.pause()
        assert app.query_one("#var-0", Input).password
        app.plan = {"id": "p1"}
        app.query_one("#approval", Input).value = "p1"
        app.query_one("#deploy", Button).disabled = False
        app.query_one("#var-0", Input).value = "changed"
        await pilot.pause()
        assert app.plan is None
        assert app.query_one("#approval", Input).value == ""
        assert app.query_one("#deploy", Button).disabled


@pytest.mark.asyncio
async def test_recovery_plan_requires_fresh_approval(monkeypatch):
    recovery = {"id": "recovery-new", "recovery_of": "d1"}
    monkeypatch.setattr(core, "resume", lambda *a, **kw: {"plan": recovery, "state": "approval_required"})
    app = DeploymentApp()
    async with app.run_test(size=(100, 65)) as pilot:
        await pilot.pause()
        app.query_one("#deployment", Input).value = "d1"
        app.query_one("#approval", Input).value = "old-approval"
        await pilot.click("#resume")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.plan == recovery
        assert app.query_one("#approval", Input).value == ""
        assert not app.query_one("#deploy", Button).disabled


@pytest.mark.asyncio
async def test_reconciliation_without_plan_cannot_deploy(monkeypatch):
    monkeypatch.setattr(core, "resume", lambda *a, **kw: {"plan": None, "state": "reconciliation_required"})
    app = DeploymentApp()
    async with app.run_test(size=(100, 65)) as pilot:
        await pilot.pause()
        app.query_one("#deployment", Input).value = "d1"
        await pilot.click("#resume")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.plan is None
        assert app.query_one("#deploy", Button).disabled


@pytest.mark.asyncio
async def test_cli_tui_real_local_plan_equivalence(tmp_path, monkeypatch, capsys):
    import json
    import subprocess
    from onedeploy import cli
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--template", "docker-http", "--json"]) == 0
    capsys.readouterr()
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Offline Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    assert cli.main(["plan", "--json"]) == 0
    cli_plan = json.loads(capsys.readouterr().out)["result"]
    app = DeploymentApp()
    async with app.run_test(size=(100, 65)) as pilot:
        await pilot.pause()
        await app.load_configuration()
        await pilot.pause()
        await pilot.click("#plan")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.plan is not None, str(app.query_one("#status", Static).render())
        assert app.plan["id"] == cli_plan["id"]
        assert app.plan == cli_plan


@pytest.mark.asyncio
async def test_provider_switch_clears_incompatible_targets_and_approval():
    from textual.widgets import Select
    app = DeploymentApp()
    async with app.run_test(size=(100, 65)) as pilot:
        await pilot.pause()
        app.query_one("#target-project", Input).value = "old-project"
        app.query_one("#target-account", Input).value = "old-account"
        await pilot.pause()
        app.plan = {"id":"old"}
        app.query_one("#approval", Input).value = "old"
        app.query_one("#new-provider", Select).value = "modal"
        await pilot.pause()
        assert app.query_one("#new-template", Select).value == "asgi"
        assert app.query_one("#target-project", Input).value == ""
        assert not app.query_one("#target-project", Input).display
        assert app.query_one("#target-account", Input).value == ""
        assert app.plan is None
        assert app.query_one("#approval", Input).value == ""


def test_review_prominently_discloses_trust_storage_and_dependencies():
    from onedeploy.tui import review_text
    text = review_text({"id":"p", "kind":"rollback", "manifest": {"provider":"daytona", "variables":{"TOKEN":{"secret":True}}, "storage":[{"kind":"model-cache"}], "dependencies":[{"name":"database"}]}})
    assert "Runtime secrets sent to this provider: TOKEN" in text
    assert "single-writer" in text and "connectivity" in text
    assert "ROLLBACK DOES NOT UNDO DATA" in text


@pytest.mark.asyncio
async def test_guided_create_project_loads_real_provider_manifest(tmp_path):
    from textual.widgets import Select
    app = DeploymentApp()
    async with app.run_test(size=(100, 65)) as pilot:
        await pilot.pause()
        app.query_one("#new-provider", Select).value = "daytona"
        await pilot.pause()
        app.query_one("#new-directory", Input).value = str(tmp_path / "fresh-project")
        for key, value in {"account":"org-example", "sandbox":"new-example", "snapshot-name":"snapshot-example"}.items():
            app.query_one("#target-" + key, Input).value = value
        await pilot.pause()
        await app.on_button_pressed(Button.Pressed(app.query_one("#create-project", Button)))
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.loaded_manifest["provider"] == "daytona"
        assert app.loaded_manifest["target"]["sandbox"] == "new-example"
        assert "service" not in app.loaded_manifest["target"]
        assert app.plan is None


@pytest.mark.asyncio
async def test_typed_variables_reject_invalid_without_leak(monkeypatch):
    monkeypatch.setattr(config, "load_manifest", lambda p: {"provider":"daytona", "variables":{"COUNT":{"type":"integer", "secret":True}}})
    calls = []
    monkeypatch.setattr(core, "make_plan", lambda *a, **kw: calls.append(kw))
    app = DeploymentApp()
    async with app.run_test(size=(100,65)) as pilot:
        await pilot.pause()
        await app.load_configuration()
        await pilot.pause()
        app.query_one("#var-0", Input).value = "sensitive-noninteger"
        await pilot.pause()
        await app.on_button_pressed(Button.Pressed(app.query_one("#plan", Button)))
        assert not calls
        assert "sensitive-noninteger" not in str(app.query_one("#status", Static).render())


@pytest.mark.asyncio
async def test_operator_resolution_requires_explicit_consent(monkeypatch):
    from textual.widgets import Select, Checkbox
    calls = []
    def resume(*a, **kw):
        calls.append(kw)
        return {"plan":{"id":"new-plan"}, "state":"approval_required"}
    monkeypatch.setattr(core, "resume", resume)
    app = DeploymentApp()
    async with app.run_test(size=(100,65)) as pilot:
        await pilot.pause()
        app.query_one("#deployment", Input).value = "old-deployment"
        app.query_one("#observation", Input).value = "evidence"
        app.query_one("#outcome", Select).value = "operator-confirmed-not-executed"
        await app.on_button_pressed(Button.Pressed(app.query_one("#resolve", Button)))
        assert not calls
        app.query_one("#resolution-consent", Checkbox).value = True
        await app.on_button_pressed(Button.Pressed(app.query_one("#resolve", Button)))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls[0]["resolve_with_observation"] == "evidence"
        assert app.plan["id"] == "new-plan"
        assert app.query_one("#approval", Input).value == ""
        assert not app.query_one("#resolution-consent", Checkbox).value


@pytest.mark.asyncio
async def test_saved_plan_load_requires_new_approval(monkeypatch):
    plan = {"id":"saved-id", "manifest":{"provider":"railway"}}
    saved = []
    monkeypatch.setattr(core, "save_plan", lambda *args: saved.append(args))
    monkeypatch.setattr(core, "read_plan", lambda path: plan)
    app = DeploymentApp()
    async with app.run_test(size=(100,65)) as pilot:
        await pilot.pause()
        app.plan = plan
        await app.on_button_pressed(Button.Pressed(app.query_one("#save-plan", Button)))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert saved[0][0] == plan and app.plan == plan
        app.query_one("#approval", Input).value = "old-approval"
        await app.on_button_pressed(Button.Pressed(app.query_one("#load-plan", Button)))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.plan == plan
        assert app.query_one("#approval", Input).value == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["doctor", "validate", "logs"])
async def test_diagnostic_controls_use_shared_engine(monkeypatch, action):
    calls = []
    monkeypatch.setattr(config, "load_manifest", lambda path: {"provider":"railway"})
    def capture(*args, **kwargs):
        calls.append((args,kwargs))
        return {"checked": True}
    if action == "validate": monkeypatch.setattr(config, "validate_config", capture)
    else: monkeypatch.setattr(core, action, capture)
    app = DeploymentApp()
    async with app.run_test(size=(100,65)) as pilot:
        await pilot.pause()
        app.query_one("#deployment", Input).value = "recorded-id"
        await app.on_button_pressed(Button.Pressed(app.query_one("#" + action, Button)))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(calls) == 1
        assert app.plan is None
        if action == "logs": assert calls[0][1]["follow"] is False


@pytest.mark.asyncio
async def test_empty_resolution_outcome_is_blocked_before_engine(monkeypatch):
    from textual.widgets import Checkbox, Select
    calls=[]
    monkeypatch.setattr(core,'resume',lambda *a,**kw:calls.append(kw))
    app=DeploymentApp()
    async with app.run_test(size=(100,60)) as pilot:
        await pilot.pause()
        app.query_one('#resolution-consent',Checkbox).value=True
        app.query_one('#observation',Input).value='fresh-observation'
        assert app.query_one('#outcome',Select).value is Select.NULL
        await app.on_button_pressed(Button.Pressed(app.query_one('#resolve',Button)))
        await pilot.pause()
        assert calls==[]
        assert not app.busy
