"""Real discovery/profile/planner paths with no provider calls or handwritten manifests."""
from copy import deepcopy
import json
from pathlib import Path
import threading

import pytest
pytest.importorskip("textual")
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea
from onedeploy import core, discovery
from onedeploy.wizard import DeploymentWizard


async def press(app, ident):
    await app.on_button_pressed(Button.Pressed(app.query_one("#" + ident, Button)))


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def fill(app, pilot, answers):
    if app._profile_inferred:
        app.query_one("#runtime-profile", Select).value = answers.get("profile", "docker-http")
    for index, question in enumerate(app.question_fields):
        if "fixed" in question:
            continue
        field = question["field"]
        if field not in answers:
            continue
        control = app.query_one(f"#answer-{index}")
        value = answers[field]
        if isinstance(control, TextArea):
            control.load_text("\n".join(value))
        else:
            control.value = str(value).lower() if isinstance(value, bool) else str(value)
    app.query_one("#trust-code", Checkbox).value = True
    await pilot.pause()


@pytest.mark.asyncio
async def test_real_compose_dependency_storage_to_identical_plan_without_yaml(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM python:3.12-slim\nEXPOSE 8000\nCMD [\"python\", \"app.py\"]\n")
    (project / "app.py").write_text("print('fixture')\n")
    compose = project / "compose.yaml"
    compose.write_text("services:\n  api:\n    build: .\n    ports: [\"8000\"]\n    depends_on: [database]\n    volumes: [\"data:/data\"]\n  database:\n    image: postgres:16\nvolumes:\n  data: {}\n")
    originals = {item.name: item.read_bytes() for item in project.iterdir()}
    app = DeploymentWizard(path=compose, allow_dirty=True)
    values = {"name": "fixture", "runtime.port": 8000, "target.account": "acct", "target.project": "project", "target.environment": "env", "target.service": "service", "dependencies.0.kind": "postgres", "dependencies.0.variable": "DATABASE_URL", "dependencies.0.tls": True, "dependencies.0.required": True, "storage.0.resource_id": "existing-volume", "storage.0.kind": "volume", "storage.0.single_writer": True, "storage.0.attachment": "attach"}
    async with app.run_test(size=(60, 24)) as pilot:
        await press(app, "provider-next")
        await press(app, "discover")
        await settle(app, pilot)
        candidate = next(item for item in app.discovery["candidates"] if item["service"] == "api")
        app.query_one("#candidate", Select).value = candidate["id"]
        await pilot.pause()
        assert any(q["field"] == "dependencies.0.variable" for q in app.question_fields)
        assert any(q["field"] == "storage.0.resource_id" for q in app.question_fields)
        await fill(app, pilot, values)
        await press(app, "resolve-project")
        await settle(app, pilot)
        if app.profile is None:
            await fill(app, pilot, values)
            await press(app, "resolve-project")
            await settle(app, pilot)
        assert app.profile is not None, str(app.query_one("#status", Static).render())
        manifest = app.profile["manifest"]
        assert manifest["dependencies"][0]["name"] == "database"
        assert manifest["storage"][0]["mount_path"] == "/data"
        assert manifest["variables"]["DATABASE_URL"]["source"] == "env:DATABASE_URL"
        assert manifest["variables"]["DATABASE_URL"]["secret"]
        runtime = app.query_one("#variable-0", Input)
        assert runtime.password
        runtime.value = "postgresql://fixture:synthetic@database.invalid/db?sslmode=require"
        await pilot.pause()
        expected = core.make_plan_from_profile(app.profile, allow_dirty=True, env_overrides={"DATABASE_URL": runtime.value}, plan_output=app.query_one("#plan-file", Input).value)
        await press(app, "review-plan")
        await settle(app, pilot)
        assert app.plan == expected
        assert app.plan["remote_preconditions"] is None
        assert app.query_one("#deploy", Button).disabled
        assert "synthetic" not in str(app.query_one("#review", Static).render())
        assert not (project / "onedeploy.yaml").exists()
        assert {name: (project / name).read_bytes() for name in originals} == originals


@pytest.mark.asyncio
async def test_saved_profile_load_requires_fresh_source_trust_and_reuses_settings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    docker = project / "Dockerfile"
    docker.write_text("FROM scratch\nEXPOSE 8000\n")
    found = discovery.discover(docker, "railway")
    answers = {"name": "fixture", "trust_code": True, "runtime": {"port": 8000}, "target": {"account": "acct", "project": "project", "environment": "env", "service": "service"}}
    profile = discovery.resolve(found, answers)
    saved = tmp_path / "saved-profile.json"
    discovery.save_profile(profile, saved)
    app = DeploymentWizard(path=saved, allow_dirty=True)
    async with app.run_test() as pilot:
        await press(app, "provider-next")
        # Discover automatically recognizes the explicit saved-profile path too.
        await press(app, "discover")
        await settle(app, pilot)
        assert app.discovery == profile["discovery"]
        assert app.profile is None
        assert not app.query_one("#trust-code", Checkbox).value
        assert app.answers["target"]["account"] == "acct"
        await press(app, "resolve-project")
        assert app.profile is None
        app.query_one("#trust-code", Checkbox).value = True
        await pilot.pause()
        await press(app, "resolve-project")
        await settle(app, pilot)
        assert app.profile is not None
        assert app.profile["manifest"] == profile["manifest"]
        assert app.plan is None


@pytest.mark.asyncio
async def test_cleared_answers_and_changed_source_scope_never_reuse_trust_or_secrets(tmp_path):
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        app.answers = {"target": {"account": "old-account"}}
        await app._render_questions({"missing": [{"field": "target.account", "type": "string", "reason": "Exact account"}]})
        app.query_one("#answer-0", Input).value = ""
        await pilot.pause()
        assert "account" not in app._collect_answers().get("target", {})
        await app._render_variables({"variables": {"TOKEN": {"secret": True}}})
        app.query_one("#variable-0", Input).value = "old-project-secret"
        app.query_one("#trust-code", Checkbox).value = True
        await pilot.pause()
        app.query_one("#project-path", Input).value = str(tmp_path)
        await pilot.pause()
        assert app.query_one("#variable-0", Input).value == ""
        assert not app.query_one("#trust-code", Checkbox).value
        assert app.profile is None and app.discovery is None


@pytest.mark.asyncio
async def test_saved_profile_provider_mismatch_stops_without_target_transplant(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    docker = project / "Dockerfile"
    docker.write_text("FROM scratch\n")
    profile = discovery.resolve(discovery.discover(docker, "railway"), {"name": "fixture", "trust_code": True, "target": {"account": "acct", "project": "project", "environment": "env", "service": "service"}})
    saved = tmp_path / "saved.json"
    discovery.save_profile(profile, saved)
    app = DeploymentWizard(path=saved)
    async with app.run_test() as pilot:
        app.query_one("#provider", Select).value = "modal"
        await pilot.pause()
        await press(app, "provider-next")
        await press(app, "load-profile")
        await settle(app, pilot)
        assert app.profile is None and app.discovery is None
        assert "differs" in str(app.query_one("#status", Static).render())


@pytest.mark.asyncio
async def test_explicit_runtime_reference_env_file_and_profile_choice(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    docker = project / "Dockerfile"
    docker.write_text("FROM scratch\n")
    private_env = tmp_path / "app.env"
    private_env.write_text("API_TOKEN=fixture-private-value\nUNSELECTED=must-not-forward\n")
    private_env.chmod(0o600)
    app = DeploymentWizard(path=docker, env_file=str(private_env), allow_dirty=True)
    async with app.run_test(size=(55, 24)) as pilot:
        await press(app, "provider-next")
        await press(app, "discover")
        await settle(app, pilot)
        assert app.query_one("#runtime-profile", Select).value is Select.NULL
        app.query_one("#runtime-profile", Select).value = "worker"
        app.query_one("#new-variable-name", Input).value = "TOKEN"
        app.query_one("#new-variable-source", Input).value = "API_TOKEN"
        await pilot.pause()
        await press(app, "add-variable")
        await pilot.pause()
        assert app.answers["variables"]["TOKEN"]["source"] == "env:API_TOKEN"
        assert app.query_one("#variable-0", Input).password
        values = {"profile": "worker", "name": "fixture", "target.account": "acct", "target.project": "project", "target.environment": "env", "target.service": "service"}
        await fill(app, pilot, values)
        await press(app, "resolve-project")
        await settle(app, pilot)
        if app.profile is None:
            await fill(app, pilot, values)
            await press(app, "resolve-project")
            await settle(app, pilot)
        assert app.profile is not None
        assert app.profile["manifest"]["profile"] == "worker"
        assert "fixture-private-value" not in json.dumps(app.profile)
        await press(app, "review-plan")
        await settle(app, pilot)
        assert app.plan is not None, str(app.query_one("#status", Static).render())
        assert app.plan["variable_references"] == {"TOKEN": "env:API_TOKEN"}
        assert "UNSELECTED" not in json.dumps(app.plan)
        assert "fixture-private-value" not in json.dumps(app.plan)
        app.action_back()
        app.query_one("#remove-variable-name", Select).value = "TOKEN"
        await pilot.pause()
        await press(app, "remove-variable")
        assert "TOKEN" not in app.answers["variables"]
        assert app.profile is None and app.plan is None


@pytest.mark.asyncio
async def test_portable_intent_export_import_rebinds_fresh_source_and_target(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    for root in (first, second):
        (root / "Dockerfile").write_text("FROM scratch\nEXPOSE 8000\n")
    (first / "app.txt").write_text("original source")
    (second / "app.txt").write_text("new source requires fresh trust")
    original = discovery.resolve(discovery.discover(first / "Dockerfile", "railway"), {"name": "fixture", "profile": "docker-http", "trust_code": True, "runtime": {"port": 8000}, "variables": {"TOKEN": {"type": "string", "secret": True, "required": False, "source": "env:TOKEN"}}, "target": {"account": "old-acct", "project": "old-project", "environment": "old-env", "service": "old-service"}})
    output = tmp_path / "portable-intent.json"
    app = DeploymentWizard(path=second / "Dockerfile", allow_dirty=True)
    async with app.run_test(size=(60, 24)) as pilot:
        app.profile = original
        app.query_one("#intent-file", Input).value = str(output)
        await pilot.pause()
        await press(app, "export-intent")
        await settle(app, pilot)
        exported = discovery.read_intent(output)
        encoded = json.dumps(exported)
        for omitted in (str(first), "old-acct", "trust_code", "config_binding", "bundle_sha256"):
            assert omitted not in encoded
        assert exported["kind"] == "intent"
        await press(app, "provider-next")
        await press(app, "discover")
        await settle(app, pilot)
        app.query_one("#env-file", Input).value = "/private/old.env"
        app.query_one("#trust-code", Checkbox).value = True
        app.query_one("#inspect-remote", Checkbox).value = True
        await pilot.pause()
        await press(app, "import-intent")
        await settle(app, pilot)
        assert app._imported_intent == exported
        assert app.profile is None and app.plan is None
        assert not app.query_one("#trust-code", Checkbox).value
        assert not app.query_one("#inspect-remote", Checkbox).value
        assert app.query_one("#env-file", Input).value == ""
        assert app.query_one("#variable-0", Input).value == ""
        assert app.query_one("#variable-0", Input).password
        assert "target.account" in {item["field"] for item in app.question_fields}
        # Trust alone cannot bypass fresh target confirmation.
        app.query_one("#trust-code", Checkbox).value = True
        await pilot.pause()
        await press(app, "resolve-project")
        await settle(app, pilot)
        assert app.profile is None
        await fill(app, pilot, {"profile": "docker-http", "target.account": "new-acct", "target.project": "new-project", "target.environment": "new-env", "target.service": "new-service"})
        await press(app, "resolve-project")
        await settle(app, pilot)
        assert app.profile is not None, str(app.query_one("#status", Static).render())
        assert app.profile["root"] == str(second)
        assert app.profile["manifest"]["target"]["account"] == "new-acct"
        assert app.plan is None
        await press(app, "review-plan")
        await settle(app, pilot)
        assert app.plan is not None
        assert app.plan["source"]["root"] == str(second)
        assert app.query_one("#deploy", Button).disabled
        assert "trust_code" not in encoded


@pytest.mark.asyncio
async def test_portable_storage_fields_preserve_templates_and_only_rebind_ids():
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        template = {"name": "data", "mount_path": "/data", "kind": "volume", "access": "read-write", "single_writer": True}
        app._imported_intent = {"requirements": {"storage": [template]}}
        await app._render_questions({"missing": [{"field": "storage", "type": "array", "reason": "Fresh resource IDs", "choices": ["data:/data:rw"]}]})
        editable = [q["field"] for q in app.question_fields if "fixed" not in q]
        assert editable == ["storage.0.resource_id"]
        index = next(i for i, q in enumerate(app.question_fields) if q["field"] == "storage.0.resource_id")
        app.query_one(f"#answer-{index}", Input).value = "fresh-volume"
        answers = app._collect_answers()
        assert answers["storage"] == [{**template, "resource_id": "fresh-volume"}]


@pytest.mark.asyncio
async def test_portable_export_cannot_change_discovered_source_inputs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    docker = project / "Dockerfile"
    docker.write_text("FROM scratch\n")
    original = discovery.resolve(discovery.discover(docker, "railway"), {"name": "fixture", "trust_code": True, "target": {"account": "acct", "project": "project", "environment": "env", "service": "service"}})
    blocked = project / "portable.json"
    app = DeploymentWizard()
    async with app.run_test() as pilot:
        app.profile = original
        app.query_one("#intent-file", Input).value = str(blocked)
        await pilot.pause()
        await press(app, "export-intent")
        await settle(app, pilot)
        assert not blocked.exists()
        assert "outside" in str(app.query_one("#status", Static).render()) or ".onedeploy" in str(app.query_one("#status", Static).render())
        discovery.validate_profile(original)
