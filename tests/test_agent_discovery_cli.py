"""Stable, offline-only agent entrypoints and experimental wizard routing."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, validate

from onedeploy import cli, core
from onedeploy.errors import EXIT_CODES, OnedeployError


def read_output(capsys):
    captured = capsys.readouterr()
    assert not captured.err
    output = json.loads(captured.out)
    validate(output, cli.machine_schema("result"))
    return output


def test_legacy_manifest_default_and_profile_route():
    legacy = cli.parser().parse_args(["plan"])
    assert legacy.manifest == "onedeploy.yaml" and legacy.profile is None
    profile = cli.parser().parse_args(["plan", "--profile", "profile.json"])
    assert profile.profile == "profile.json"


@pytest.mark.parametrize("argv", [
    ["plan", "--profile", "profile.json", "--manifest", "original.yaml"],
    ["tui", "--wizard", "--manifest", "original.yaml"],
    ["discover", "--path", "."],
    ["resolve", "--discovery", "discovery.json", "--secret", "never-echo-this"],
    ["resolve", "--discovery", "discovery.json", "--answers-json", '{"secret":"never-echo-this"}'],
])
def test_invalid_new_arguments_are_redacted_json(argv, capsys):
    assert cli.main([*argv, "--json"]) == 2
    output = capsys.readouterr()
    assert "never-echo-this" not in output.out + output.err
    assert json.loads(output.out)["error"]["code"] == "invalid_config"


def test_discover_delegates_without_implicit_persistence(monkeypatch, capsys):
    from onedeploy import discovery
    calls = []
    artifact = {"status": "input_required", "warnings": ["Review source."]}
    def discover(path, provider):
        calls.append((path, provider))
        return artifact
    monkeypatch.setattr(discovery, "discover", discover)
    monkeypatch.setattr(discovery, "save_discovery", lambda *a: pytest.fail("Unexpected artifact write"))
    assert cli.main(["discover", "--provider", "railway", "--path", "project", "--json"]) == 0
    output = read_output(capsys)
    assert calls == [("project", "railway")]
    assert output["result"] == artifact
    assert output["warnings"] == ["Review source."]


def test_discover_saves_only_when_requested(monkeypatch, capsys):
    from onedeploy import discovery
    artifact = {"status": "ready"}
    saved = []
    monkeypatch.setattr(discovery, "discover", lambda *a: artifact)
    monkeypatch.setattr(discovery, "save_discovery", lambda *a: saved.append(a))
    assert cli.main(["--json", "discover", "--provider", "daytona", "--out", "discovery.json"]) == 0
    assert saved == [(artifact, "discovery.json")]
    assert read_output(capsys)["result"] == artifact


@pytest.mark.parametrize("with_answers", [False, True])
def test_resolve_delegates_typed_file_answers(monkeypatch, capsys, with_answers):
    from onedeploy import discovery
    calls, saved = [], []
    answers = {"trust_code": True}
    profile = {"kind": "profile"}
    monkeypatch.setattr(discovery, "read_answers", lambda p: answers if p == "answers.json" else pytest.fail("Unexpected input"))
    def resolve(path, data):
        calls.append((path, data))
        return profile
    monkeypatch.setattr(discovery, "resolve", resolve)
    monkeypatch.setattr(discovery, "save_profile", lambda *a: saved.append(a))
    argv = ["resolve", "--discovery", "discovery.json", "--out", "profile.json", "--json"]
    if with_answers:
        argv += ["--answers", "answers.json"]
    assert cli.main(argv) == 0
    assert calls == [("discovery.json", answers if with_answers else None)]
    assert saved == [(profile, "profile.json")]
    assert read_output(capsys)["result"] == profile


def test_resolve_without_out_never_persists(monkeypatch, capsys):
    from onedeploy import discovery
    monkeypatch.setattr(discovery, "resolve", lambda *a: {"kind": "profile"})
    monkeypatch.setattr(discovery, "save_profile", lambda *a: pytest.fail("Unexpected artifact write"))
    assert cli.main(["resolve", "--discovery", "discovery.json", "--json"]) == 0
    assert read_output(capsys)["status"] == "success"


def test_profile_plan_uses_shared_bridge_and_immutable_save(monkeypatch, capsys):
    calls, saved = [], []
    plan = {"id": "immutable-plan", "warnings": ["Preview only."]}
    def make_plan(profile, **kwargs):
        calls.append((profile, kwargs))
        return plan
    monkeypatch.setattr(core, "make_plan_from_profile", make_plan)
    monkeypatch.setattr(core, "make_plan", lambda *a, **kw: pytest.fail("Legacy planner selected"))
    monkeypatch.setattr(core, "save_plan", lambda *a: saved.append(a))
    assert cli.main(["plan", "--profile", "profile.json", "--env-file", "private.env", "--allow-dirty",
                     "--inspect-remote", "--out", "review.json", "--json"]) == 0
    assert calls == [("profile.json", {"env_file": "private.env", "allow_dirty": True,
                                       "inspect_remote": True, "plan_output": "review.json"})]
    assert saved == [(plan, "review.json")]
    assert read_output(capsys)["result"] == plan


@pytest.mark.parametrize("kind", ["discovery", "profile", "intent", "answers", "resolution", "result", "error", "progress"])
def test_new_machine_schemas_are_published(kind, capsys):
    assert cli.main(["schema", "--kind", kind, "--json"]) == 0
    output = read_output(capsys)
    Draft202012Validator.check_schema(output["result"])
    assert output["result"] == cli.machine_schema(kind)


def test_legacy_error_shape_remains_unchanged(monkeypatch, capsys):
    def fail(*a, **kw):
        raise OnedeployError("invalid_config", "Invalid input.", result={"password": "never-echo-this"})
    monkeypatch.setattr(core, "doctor", fail)
    assert cli.main(["doctor", "--provider", "modal", "--json"]) == 2
    output = read_output(capsys)
    assert output["result"] is None
    assert set(output["error"]) == {"code", "message", "retryable", "uncertain", "mutation_possible"}
    assert "never-echo-this" not in json.dumps(output)


@pytest.mark.parametrize("details", [
    {"password": "never-echo-this"},
    {"status": "input_required", "missing": [{"password": "never-echo-this"}]},
    "never-echo-this",
])
def test_typed_errors_never_dump_arbitrary_metadata(monkeypatch, capsys, details):
    from onedeploy import discovery
    def fail(*a, **kw):
        raise OnedeployError("input_required", "More input is required.", result=details)
    monkeypatch.setattr(discovery, "resolve", fail)
    assert cli.main(["resolve", "--discovery", "discovery.json", "--json"]) == 11
    output = read_output(capsys)
    assert "details" not in output["error"]
    assert "never-echo-this" not in json.dumps(output)


@pytest.mark.parametrize("code,exit_code", [("input_required", 11), ("conflict", 12), ("unsupported_capability", 13)])
def test_new_codes_do_not_change_existing_exit_codes(code, exit_code):
    assert EXIT_CODES[code] == OnedeployError(code, "Safe message.").exit_code == exit_code
    assert [EXIT_CODES[key] for key in ("internal_error", "invalid_config", "unsupported", "approval_required",
            "authentication_failed", "provider_failed", "uncertain", "cancelled", "stale_plan", "locked")] == list(range(1, 11))


def test_experimental_wizard_routes_separately(monkeypatch, capsys):
    calls = []
    class FakeWizard:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def run(self):
            calls.append("run")
    monkeypatch.setitem(sys.modules, "onedeploy.wizard", SimpleNamespace(DeploymentWizard=FakeWizard))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["tui", "--wizard", "--path", "project", "--env-file", "private.env", "--state-dir", "state",
                     "--allow-dirty", "--inspect-remote"]) == 0
    assert calls == [{"path": "project", "env_file": "private.env", "state_dir": "state",
                      "allow_dirty": True, "inspect_remote": True}, "run"]
    assert json.loads(capsys.readouterr().out) == {"closed": True}


def test_legacy_tui_remains_default(monkeypatch, capsys):
    calls = []
    class FakeApp:
        def __init__(self, *args):
            calls.append(args)
        def run(self):
            calls.append("run")
    monkeypatch.setitem(sys.modules, "onedeploy.tui", SimpleNamespace(DeploymentApp=FakeApp))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["tui"]) == 0
    assert calls == [("onedeploy.yaml", None, None, False, False), "run"]
    assert json.loads(capsys.readouterr().out) == {"closed": True}


def test_wizard_rejects_noninteractive_usage(capsys):
    assert cli.main(["tui", "--wizard", "--path", "project", "--json"]) == 2
    assert read_output(capsys)["error"]["code"] == "invalid_config"


def test_project_path_requires_explicit_experimental_opt_in(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["tui", "--path", "project"]) == 2
    captured = capsys.readouterr()
    assert "--wizard" in captured.err and not captured.out


@pytest.mark.parametrize("command", [["config", "validate"], ["inspect"]])
def test_profile_and_manifest_validation_routes_are_exclusive(command, capsys):
    assert cli.main([*command, "--profile", "profile.json", "--manifest", "original.yaml", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_config"


def test_config_validate_profile_uses_validated_normalized_manifest(monkeypatch, capsys):
    from onedeploy import config, discovery
    calls = []
    manifest = {"version": 1}
    def validate_profile(path):
        calls.append(("profile", path))
        return manifest, Path("project")
    def validate_config(value, **kwargs):
        calls.append(("config", value, kwargs))
        return {"valid": True}
    monkeypatch.setattr(discovery, "validate_profile", validate_profile)
    monkeypatch.setattr(config, "validate_config", validate_config)
    assert cli.main(["config", "validate", "--profile", "profile.json", "--env-file", "private.env", "--json"]) == 0
    assert calls == [("profile", "profile.json"), ("config", manifest, {"env_file": "private.env"})]
    assert read_output(capsys)["result"] == {"valid": True}


def test_inspect_profile_uses_shared_engine(monkeypatch, capsys):
    calls = []
    def inspect_profile(path):
        calls.append(path)
        return {"provider": "railway"}
    monkeypatch.setattr(core, "inspect_profile", inspect_profile)
    assert cli.main(["inspect", "--profile", "profile.json", "--json"]) == 0
    assert calls == ["profile.json"]
    assert read_output(capsys)["result"] == {"provider": "railway"}


@pytest.fixture
def docker_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "Dockerfile").write_text('FROM python:3.11-slim\nEXPOSE 8080\nCMD ["python", "-m", "http.server", "8080"]\n')
    (project / "app.txt").write_text("Original application content.\n")
    artifacts = tmp_path / "private-artifacts"
    artifacts.mkdir(mode=0o700)
    answers = {
        "name": "cli-demo", "trust_code": True,
        "runtime": {"port": 8080, "health_path": "/", "ready_path": "/"},
        "target": {"account": "example-org", "project": "example-project",
                   "environment": "example-env", "service": "example-service"},
    }
    return project, artifacts, answers


def private_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return str(path)


def test_real_json_agent_round_trip_preserves_source_and_requires_approval(docker_project, capsys, monkeypatch):
    project, artifacts, answers = docker_project
    original = {path.name: path.read_bytes() for path in project.iterdir()}
    discovery_path, profile_path, plan_path = [artifacts / name for name in ("discovery.json", "profile.json", "plan.json")]
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("Machine commands must not prompt"))
    assert cli.main(["discover", "--provider", "railway", "--path", str(project), "--out", str(discovery_path), "--json"]) == 0
    found = read_output(capsys)["result"]
    validate(found, cli.machine_schema("discovery"))
    assert found["status"] == "input_required"
    assert cli.main(["resolve", "--discovery", str(discovery_path), "--out", str(profile_path), "--json"]) == 11
    blocked = read_output(capsys)
    assert blocked["result"] is None and blocked["error"]["code"] == "input_required"
    assert blocked["error"]["details"]["status"] == "input_required"
    assert any(item["field"] == "target.account" for item in blocked["error"]["details"]["missing"])
    assert not profile_path.exists()
    answers_path = private_json(artifacts / "answers.json", answers)
    assert cli.main(["resolve", "--discovery", str(discovery_path), "--answers", answers_path, "--out", str(profile_path), "--json"]) == 0
    profile = read_output(capsys)["result"]
    validate(profile, cli.machine_schema("profile"))
    assert profile["manifest"]["provider"] == "railway"
    assert cli.main(["config", "validate", "--profile", str(profile_path), "--json"]) == 0
    assert read_output(capsys)["result"]["valid"]
    assert cli.main(["inspect", "--profile", str(profile_path), "--json"]) == 0
    assert read_output(capsys)["status"] == "success"
    assert cli.main(["plan", "--profile", str(profile_path), "--allow-dirty", "--out", str(plan_path), "--json"]) == 0
    plan = read_output(capsys)["result"]
    assert core.read_plan(plan_path)["id"] == plan["id"]
    assert plan["normalized_profile"]["id"] == profile["id"]
    assert plan["remote_preconditions"] is None
    assert cli.main(["deploy", "--plan", str(plan_path), "--json"]) == 4
    assert read_output(capsys)["error"]["code"] == "approval_required"
    assert all((project / name).read_bytes() == data for name, data in original.items())
    assert not (project / "onedeploy.yaml").exists()
    assert discovery_path.stat().st_mode & 0o077 == profile_path.stat().st_mode & 0o077 == 0


def test_discovery_output_is_exclusive_and_source_output_is_rejected(docker_project, capsys):
    project, artifacts, _ = docker_project
    path = artifacts / "discovery.json"
    argv = ["discover", "--provider", "railway", "--path", str(project), "--out", str(path), "--json"]
    assert cli.main(argv) == 0
    read_output(capsys)
    original = path.read_bytes()
    assert cli.main(argv) == 12
    assert read_output(capsys)["error"]["code"] == "conflict"
    assert path.read_bytes() == original
    assert cli.main(["discover", "--provider", "railway", "--path", str(project), "--out", str(project / "discovery.json"), "--json"]) == 2
    assert read_output(capsys)["error"]["code"] == "invalid_config"
    assert not (project / "discovery.json").exists()


def test_profile_output_is_exclusive(docker_project, capsys):
    from onedeploy import discovery
    project, artifacts, answers = docker_project
    discovery_path = artifacts / "discovery.json"
    discovery.save_discovery(discovery.discover(project, "railway"), discovery_path)
    answers_path = private_json(artifacts / "answers.json", answers)
    profile_path = artifacts / "profile.json"
    argv = ["resolve", "--discovery", str(discovery_path), "--answers", answers_path, "--out", str(profile_path), "--json"]
    assert cli.main(argv) == 0
    read_output(capsys)
    original = profile_path.read_bytes()
    assert cli.main(argv) == 12
    assert read_output(capsys)["error"]["code"] == "conflict"
    assert profile_path.read_bytes() == original


def test_stale_source_invalidates_cli_resolution(docker_project, capsys):
    from onedeploy import discovery
    project, artifacts, answers = docker_project
    discovery_path = artifacts / "discovery.json"
    discovery.save_discovery(discovery.discover(project, "railway"), discovery_path)
    answers_path = private_json(artifacts / "answers.json", answers)
    (project / "app.txt").write_text("Changed after discovery.\n")
    assert cli.main(["resolve", "--discovery", str(discovery_path), "--answers", answers_path, "--json"]) == 9
    assert read_output(capsys)["error"]["code"] == "stale_plan"


@pytest.mark.parametrize("bad_answers", [
    {"password": "never-echo-this"},
    {"variables": {"API_TOKEN": {"secret": True, "default": "never-echo-this"}}},
    {"variables": {"API_TOKEN": {"secret": True, "value": "never-echo-this"}}},
])
def test_secret_answer_values_are_rejected_without_echo(docker_project, capsys, bad_answers):
    from onedeploy import discovery
    project, artifacts, _ = docker_project
    discovery_path = artifacts / "discovery.json"
    discovery.save_discovery(discovery.discover(project, "railway"), discovery_path)
    answers_path = private_json(artifacts / "answers.json", bad_answers)
    assert cli.main(["resolve", "--discovery", str(discovery_path), "--answers", answers_path, "--json"]) == 2
    output = read_output(capsys)
    assert output["error"]["code"] == "invalid_config"
    assert "never-echo-this" not in json.dumps(output)


def test_actual_unsupported_mapping_returns_structured_error(docker_project, capsys):
    from onedeploy import discovery
    project, artifacts, _ = docker_project
    path = artifacts / "discovery.json"
    discovery.save_discovery(discovery.discover(project, "modal"), path)
    assert cli.main(["resolve", "--discovery", str(path), "--json"]) == 13
    output = read_output(capsys)
    assert output["error"]["code"] == "unsupported_capability"
    details = output["error"]["details"]
    assert details["status"] == "unsupported_capability" and details["unsupported"]


def test_actual_port_conflict_returns_structured_error(docker_project, capsys):
    from onedeploy import discovery
    project, artifacts, answers = docker_project
    path = artifacts / "discovery.json"
    discovery.save_discovery(discovery.discover(project, "railway"), path)
    answers["runtime"]["port"] = 9090
    answers_path = private_json(artifacts / "answers.json", answers)
    assert cli.main(["resolve", "--discovery", str(path), "--answers", answers_path, "--json"]) == 12
    output = read_output(capsys)
    assert output["error"]["code"] == "conflict"
    assert output["error"]["details"]["conflicts"][0]["field"] == "runtime.port"


def test_error_details_schema_only_accepts_blocked_nonsecret_questions():
    error = OnedeployError("input_required", "More input is required.").to_dict()
    error["details"] = {"status": "input_required", "missing": [], "conflicts": [], "unsupported": []}
    validator = Draft202012Validator(cli.machine_schema("error"))
    assert validator.is_valid(error)
    error["details"]["profile"] = {}
    assert not validator.is_valid(error)
    error["details"].pop("profile")
    error["details"]["status"] = "ready"
    assert not validator.is_valid(error)


@pytest.mark.parametrize("profile,expected", [(False, "plan.json"), (True, ".onedeploy/plan.json")])
def test_plan_default_output_preserves_legacy_and_profile_binding(monkeypatch, capsys, profile, expected):
    calls, saves = [], []
    def make_plan(*args, **kwargs):
        calls.append(kwargs)
        return {"id": "plan"}
    monkeypatch.setattr(core, "make_plan", make_plan)
    monkeypatch.setattr(core, "make_plan_from_profile", make_plan)
    monkeypatch.setattr(core, "save_plan", lambda *args: saves.append(args))
    argv = ["plan", "--json"] + (["--profile", "profile.json"] if profile else [])
    assert cli.main(argv) == 0
    assert calls[0]["plan_output"] == expected
    assert saves == [({"id": "plan"}, expected)]
    assert read_output(capsys)["status"] == "success"


def test_profile_default_plan_stays_out_of_bound_source_and_never_overwrites(docker_project, capsys, monkeypatch):
    from onedeploy import discovery
    project, _, answers = docker_project
    monkeypatch.chdir(project)
    artifact_dir = project / ".onedeploy"
    artifact_dir.mkdir(mode=0o700)
    discovery_path = artifact_dir / "discovery.json"
    discovery.save_discovery(discovery.discover(project, "railway"), discovery_path)
    profile_path = artifact_dir / "profile.json"
    discovery.save_profile(discovery.resolve(discovery_path, answers), profile_path)
    argv = ["plan", "--profile", str(profile_path), "--allow-dirty", "--json"]
    assert cli.main(argv) == 0
    first = read_output(capsys)["result"]
    plan_path = artifact_dir / "plan.json"
    original = plan_path.read_bytes()
    assert core.read_plan(plan_path)["id"] == first["id"]
    assert not (project / "plan.json").exists()
    assert cli.main(["config", "validate", "--profile", str(profile_path), "--json"]) == 0
    assert read_output(capsys)["result"]["valid"]
    assert cli.main(argv) == 12
    assert read_output(capsys)["error"]["code"] == "conflict"
    assert plan_path.read_bytes() == original


@pytest.mark.parametrize("kind", ["manifest", "discovery", "profile", "intent", "answers", "resolution", "result", "error", "progress"])
def test_checked_in_schemas_match_published_contract(kind, capsys):
    schema_file = Path(__file__).resolve().parents[1] / "schemas" / f"{kind}-v1.schema.json"
    assert cli.main(["schema", "--kind", kind, "--json"]) == 0
    actual = json.loads(capsys.readouterr().out)["result"]
    assert json.loads(schema_file.read_text()) == actual
    Draft202012Validator.check_schema(actual)


@pytest.mark.parametrize("status", ["ready", "input_required", "conflict", "unsupported_capability"])
def test_discovery_envelope_supplies_next_actions_without_changing_artifact(status):
    artifact = {"status": status}
    output = cli.envelope("discover", result=artifact)
    assert output["next_actions"]
    assert output["result"] == artifact == {"status": status}


def test_resolution_envelope_supplies_next_steps_without_changing_legacy_envelope():
    assert cli.envelope("resolve", result={"kind": "profile"})["next_actions"]
    assert cli.envelope("plan", result={"id": "plan"})["next_actions"] == []
    assert cli.envelope("doctor", error=OnedeployError("conflict", "Safe.").to_dict())["next_actions"] == []


@pytest.mark.parametrize("provider", ["daytona", "modal"])
def test_all_provider_json_journeys_without_manifest_or_provider_calls(tmp_path, capsys, monkeypatch, provider):
    project = tmp_path / provider
    project.mkdir()
    artifacts = tmp_path / (provider + "-private")
    artifacts.mkdir(mode=0o700)
    if provider == "daytona":
        source_path = project / "Dockerfile"
        source_path.write_text('FROM python:3.11-slim\nEXPOSE 8080\nCMD ["python", "-m", "http.server", "8080"]\n')
        answers = {"name": "daytona-demo", "trust_code": True, "snapshot_name": "reviewed-build",
                   "target": {"account": "example-org", "sandbox": "new-example", "mode": "create"},
                   "runtime": {"port": 8080, "health_path": "/", "ready_path": "/"},
                   "ingress": {"mode": "private"}}
        required_fields = {"target.mode", "snapshot_name", "trust_code", "target.account", "target.sandbox", "runtime.port"}
    else:
        source_path = project / "modal_app.py"
        source_path.write_bytes((Path(cli.__file__).parent / "templates" / "asgi" / "modal_app.py").read_bytes())
        answers = {"name": "modal-demo", "trust_code": True,
                   "target": {"account": "example-workspace", "environment": "main", "mode": "create"},
                   "runtime": {"health_path": "/", "ready_path": "/"}, "ingress": {"mode": "existing"}}
        required_fields = {"trust_code", "target.account", "target.environment", "target.mode"}
    original = source_path.read_bytes()
    provider_calls = []
    def no_provider(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError("Preview must not invoke any provider command")
    monkeypatch.setattr(core.runner, "run", no_provider)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("Machine commands must not prompt"))
    discovered_path, profile_path, plan_path = [artifacts / name for name in ("discovery.json", "profile.json", "plan.json")]
    assert cli.main(["discover", "--provider", provider, "--path", str(project), "--out", str(discovered_path), "--json"]) == 0
    artifact = read_output(capsys)["result"]
    assert artifact["provider"] == provider and artifact["status"] == "input_required"
    assert not artifact["unsupported"]
    assert required_fields <= {question["field"] for question in artifact["missing"]}
    if provider == "modal":
        assert artifact["candidates"][0]["manifest"]["target"] == {"app": "example-asgi", "function": "web"}
        assert "target.function" not in {question["field"] for question in artifact["missing"]}
    assert cli.main(["resolve", "--discovery", str(discovered_path), "--json"]) == 11
    blocked = read_output(capsys)["error"]["details"]
    assert required_fields <= {question["field"] for question in blocked["missing"]}
    answers_path = private_json(artifacts / "answers.json", answers)
    assert cli.main(["resolve", "--discovery", str(discovered_path), "--answers", answers_path, "--out", str(profile_path), "--json"]) == 0
    profile = read_output(capsys)["result"]
    assert profile["manifest"]["provider"] == provider
    assert profile["manifest"]["source"]["trust_code"] is True
    assert profile["manifest"]["target"]["mode"] == "create"
    assert cli.main(["plan", "--profile", str(profile_path), "--allow-dirty", "--out", str(plan_path), "--json"]) == 0
    plan = read_output(capsys)["result"]
    assert plan["operations"] and plan["remote_preconditions"] is None
    assert core.read_plan(plan_path)["normalized_profile"]["id"] == profile["id"]
    assert not provider_calls
    assert source_path.read_bytes() == original
    assert not (project / "onedeploy.yaml").exists()
    if provider == "daytona":
        assert plan["manifest"]["build"]["snapshot_name"] == "reviewed-build"
    else:
        assert plan["manifest"]["target"]["function"] == "web"
        assert plan["manifest"]["source"]["entrypoint"] == "modal_app.py"


def test_modal_function_ambiguity_requires_typed_selection(tmp_path, capsys, monkeypatch):
    project = tmp_path / "modal-project"
    project.mkdir()
    source = project / "modal_app.py"
    source.write_text((Path(cli.__file__).parent / "templates" / "asgi" / "modal_app.py").read_text() + '''\n@app.function(image=image, max_containers=1, timeout=60)
@modal.asgi_app(requires_proxy_auth=True)
def second():
    return None
''')
    discovery_path = tmp_path / "discovery.json"
    assert cli.main(["discover", "--provider", "modal", "--path", str(project), "--out", str(discovery_path), "--json"]) == 0
    output = read_output(capsys)["result"]
    function = [question for question in output["missing"] if question["field"] == "target.function"]
    assert len(function) == 1 and function[0]["choices"] == ["second", "web"]
    answers = {"name": "multi-function-demo", "trust_code": True,
               "target": {"account": "workspace", "environment": "main", "mode": "create"}}
    answers_path = private_json(tmp_path / "answers.json", answers)
    assert cli.main(["resolve", "--discovery", str(discovery_path), "--answers", answers_path, "--json"]) == 11
    assert any(item["field"] == "target.function" for item in read_output(capsys)["error"]["details"]["missing"])
    answers["target"]["function"] = "web"
    private_json(Path(answers_path), answers)
    profile_path = tmp_path / "profile.json"
    assert cli.main(["resolve", "--discovery", str(discovery_path), "--answers", answers_path, "--out", str(profile_path), "--json"]) == 0
    assert read_output(capsys)["result"]["manifest"]["target"]["function"] == "web"
    monkeypatch.setattr(core.runner, "run", lambda *a, **kw: pytest.fail("Unexpected provider command"))
    assert cli.main(["plan", "--profile", str(profile_path), "--allow-dirty", "--out", str(tmp_path / "plan.json"), "--json"]) == 0
    assert read_output(capsys)["result"]["remote_preconditions"] is None


def test_daytona_snapshot_question_does_not_claim_verified_immutability(tmp_path, capsys):
    project = tmp_path / "snapshot-project"
    project.mkdir()
    discovery_path = tmp_path / "discovery.json"
    assert cli.main(["discover", "--provider", "daytona", "--path", str(project), "--out", str(discovery_path), "--json"]) == 0
    result = read_output(capsys)["result"]
    missing = {question["field"]: question for question in result["missing"]}
    assert {"snapshot", "target.mode", "trust_code"} <= set(missing)
    assert "inspection" in missing["snapshot"]["reason"]
    assert cli.main(["resolve", "--discovery", str(discovery_path), "--json"]) == 11
    assert {"snapshot", "target.mode", "trust_code"} <= {item["field"] for item in read_output(capsys)["error"]["details"]["missing"]}


@pytest.mark.parametrize("save", [False, True])
def test_export_cli_routes_without_implicit_writes(monkeypatch, capsys, save):
    from onedeploy import discovery
    calls = []
    intent = {"version": 1, "kind": "intent"}
    profile = {"discovery": {"root": "/synthetic-project"}}
    monkeypatch.setattr(discovery, "read_profile", lambda path: profile if path == "profile.json" else pytest.fail("Wrong input"))
    monkeypatch.setattr(discovery, "export_intent", lambda value: intent if value is profile else pytest.fail("Wrong profile"))
    monkeypatch.setattr(discovery, "save_intent", lambda *args, **kwargs: calls.append((args, kwargs)))
    argv = ["export", "--profile", "profile.json", "--json"] + (["--out", "intent.json"] if save else [])
    assert cli.main(argv) == 0
    output = read_output(capsys)
    assert output["result"] == intent and output["next_actions"]
    assert calls == ([((intent, "intent.json"), {"source_root": "/synthetic-project"})] if save else [])


@pytest.mark.parametrize("save,with_answers", [(False, False), (True, True)])
def test_import_cli_routes_through_fresh_binding(monkeypatch, capsys, save, with_answers):
    from onedeploy import discovery
    calls, saved = [], []
    profile = {"version": 1, "kind": "profile"}
    answers = {"trust_code": True}
    def import_intent(intent, path, inputs):
        calls.append((intent, path, inputs))
        return profile
    monkeypatch.setattr(discovery, "import_intent", import_intent)
    monkeypatch.setattr(discovery, "read_answers", lambda path: answers)
    monkeypatch.setattr(discovery, "save_profile", lambda *args: saved.append(args))
    argv = ["import", "--intent", "intent.json", "--path", "relocated-project", "--json"]
    if save:
        argv += ["--out", "new-profile.json"]
    if with_answers:
        argv += ["--answers", "fresh-answers.json"]
    assert cli.main(argv) == 0
    output = read_output(capsys)
    assert output["result"] == profile and output["next_actions"]
    assert calls == [("intent.json", "relocated-project", answers if with_answers else None)]
    assert saved == ([(profile, "new-profile.json")] if save else [])


@pytest.fixture
def portable_intent_case(docker_project):
    import shutil
    from onedeploy import discovery
    project, artifacts, answers = docker_project
    answers["target"] = {"account": "private-account-47893", "project": "private-project-47893",
                         "environment": "private-environment-47893", "service": "private-service-47893"}
    answers["variables"] = {"API_TOKEN": {"secret": True, "required": False, "source": "env:LOCAL_TOKEN"}}
    (project / ".env").write_text("LOCAL_TOKEN=never-export-this-runtime-secret\n")
    (project / ".env").chmod(0o600)
    profile = discovery.resolve(discovery.discover(project, "railway"), answers)
    profile_path = artifacts / "original-profile.json"
    discovery.save_profile(profile, profile_path)
    relocated = project.parent / "relocated-project"
    shutil.copytree(project, relocated, ignore=shutil.ignore_patterns(".env", ".onedeploy"))
    new_answers = {"trust_code": True, "target": {"account": "fresh-account", "project": "fresh-project",
                                                  "environment": "fresh-environment", "service": "fresh-service"}}
    return project, artifacts, profile, profile_path, relocated, new_answers


def test_portable_intent_json_cross_directory_requires_fresh_target_trust_and_plan(portable_intent_case, capsys, monkeypatch):
    project, artifacts, original, original_path, relocated, fresh = portable_intent_case
    intent_path = artifacts / "intent.json"
    original_bytes = original_path.read_bytes()
    monkeypatch.setattr(core.runner, "run", lambda *a, **kw: pytest.fail("Portable flow must not invoke providers"))
    assert cli.main(["export", "--profile", str(original_path), "--out", str(intent_path), "--json"]) == 0
    intent = read_output(capsys)["result"]
    validate(intent, cli.machine_schema("intent"))
    assert intent["kind"] == "intent" and intent["provider"] == "railway"
    serialized = json.dumps(intent)
    assert "env:LOCAL_TOKEN" in serialized
    for forbidden in (str(project), "private-account-47893", "private-project-47893", "private-environment-47893",
                      "private-service-47893", "never-export-this-runtime-secret", original["id"], original["discovery_id"],
                      original["source"]["bundle_sha256"]):
        assert forbidden not in serialized
    profile_path = artifacts / "relocated-profile.json"
    argv = ["import", "--intent", str(intent_path), "--path", str(relocated), "--out", str(profile_path), "--json"]
    assert cli.main(argv) == 11
    missing = {item["field"] for item in read_output(capsys)["error"]["details"]["missing"]}
    assert "trust_code" in missing and {"target.account", "target.project", "target.environment", "target.service"} <= missing
    assert not profile_path.exists()
    partial = private_json(artifacts / "partial.json", {"trust_code": True, "target": {"account": "fresh-account"}})
    assert cli.main([*argv, "--answers", partial]) == 11
    assert "target.service" in {item["field"] for item in read_output(capsys)["error"]["details"]["missing"]}
    answers_path = private_json(artifacts / "fresh-answers.json", fresh)
    assert cli.main([*argv, "--answers", answers_path]) == 0
    imported = read_output(capsys)["result"]
    assert imported["root"] == str(relocated) and imported["root"] != original["root"]
    assert imported["id"] != original["id"] and imported["discovery_id"] != original["discovery_id"]
    assert imported["manifest"]["target"] == fresh["target"]
    assert imported["manifest"]["variables"]["API_TOKEN"]["source"] == "env:LOCAL_TOKEN"
    assert cli.main(["plan", "--profile", str(profile_path), "--allow-dirty", "--out", str(artifacts / "new-plan.json"), "--json"]) == 0
    plan = read_output(capsys)["result"]
    assert plan["normalized_profile"]["id"] == imported["id"] and plan["remote_preconditions"] is None
    assert cli.main(["deploy", "--plan", str(artifacts / "new-plan.json"), "--json"]) == 4
    assert read_output(capsys)["error"]["code"] == "approval_required"
    assert original_path.read_bytes() == original_bytes
    assert not (relocated / "onedeploy.yaml").exists()
    assert intent_path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("command", [["plan"], ["inspect"], ["config", "validate"]])
def test_portable_intent_is_not_an_executable_profile(portable_intent_case, capsys, command):
    from onedeploy import discovery
    _, artifacts, original, _, _, _ = portable_intent_case
    intent_path = artifacts / "intent.json"
    discovery.save_intent(discovery.export_intent(original), intent_path)
    argv = [*command, "--profile", str(intent_path), "--json"]
    if command == ["plan"]:
        argv += ["--allow-dirty", "--out", str(artifacts / "should-not-exist.json")]
    assert cli.main(argv) == 2
    assert read_output(capsys)["error"]["code"] == "invalid_config"
    assert not (artifacts / "should-not-exist.json").exists()


def test_export_rejects_stale_profile_and_preserves_existing_intent(portable_intent_case, capsys):
    project, artifacts, _, original_path, _, _ = portable_intent_case
    path = artifacts / "intent.json"
    argv = ["export", "--profile", str(original_path), "--out", str(path), "--json"]
    assert cli.main(argv) == 0
    read_output(capsys)
    original_bytes = path.read_bytes()
    assert cli.main(argv) == 12
    assert read_output(capsys)["error"]["code"] == "conflict"
    assert path.read_bytes() == original_bytes
    (project / "app.txt").write_text("Source changed after resolution.\n")
    assert cli.main(["export", "--profile", str(original_path), "--json"]) == 9
    assert read_output(capsys)["error"]["code"] == "stale_plan"


def test_import_rejects_runtime_secrets_in_fresh_answers(portable_intent_case, capsys):
    from onedeploy import discovery
    _, artifacts, original, _, relocated, _ = portable_intent_case
    path = artifacts / "intent.json"
    discovery.save_intent(discovery.export_intent(original), path)
    answers_path = private_json(artifacts / "secret-answers.json", {"token": "never-echo-import-secret"})
    assert cli.main(["import", "--intent", str(path), "--path", str(relocated), "--answers", answers_path, "--json"]) == 2
    output = read_output(capsys)
    assert "never-echo-import-secret" not in json.dumps(output)


@pytest.mark.parametrize("mutation", ["absolute", "traversal", "target", "trust", "secret", "version"])
def test_unsafe_portable_intent_is_rejected_without_echo(portable_intent_case, capsys, mutation):
    from onedeploy import discovery
    _, artifacts, original, _, relocated, _ = portable_intent_case
    intent = discovery.export_intent(original)
    if mutation == "absolute":
        intent["selection"]["source_path"] = "/private/never-echo-unsafe-marker"
    elif mutation == "traversal":
        intent["selection"]["path"] = "../never-echo-unsafe-marker"
    elif mutation == "target":
        intent["settings"]["target"] = {"account": "never-echo-unsafe-marker"}
    elif mutation == "trust":
        intent["settings"]["trust_code"] = True
    elif mutation == "secret":
        intent["settings"]["variables"]["API_TOKEN"]["default"] = "never-echo-unsafe-marker"
    else:
        intent["version"] = 99
    path = private_json(artifacts / "unsafe-intent.json", intent)
    output_path = artifacts / "must-not-exist.json"
    assert cli.main(["import", "--intent", path, "--path", str(relocated), "--out", str(output_path), "--json"]) == 2
    output = read_output(capsys)
    assert output["error"]["code"] == "invalid_config"
    assert "never-echo-unsafe-marker" not in json.dumps(output)
    assert not output_path.exists()


@pytest.mark.parametrize("change,expected", [("port", "conflict"), ("missing", "conflict"), ("unsupported", "unsupported_capability")])
def test_import_detects_changed_or_unsupported_fresh_source(portable_intent_case, capsys, change, expected):
    from onedeploy import discovery
    _, artifacts, original, _, relocated, fresh = portable_intent_case
    intent_path = artifacts / "intent.json"
    discovery.save_intent(discovery.export_intent(original), intent_path)
    dockerfile = relocated / "Dockerfile"
    if change == "port":
        dockerfile.write_text(dockerfile.read_text().replace("EXPOSE 8080", "EXPOSE 9090"))
    elif change == "missing":
        dockerfile.unlink()
    else:
        dockerfile.write_text(dockerfile.read_text() + "ENV API_TOKEN=never-echo-source-secret\n")
    answers_path = private_json(artifacts / "fresh.json", fresh)
    assert cli.main(["import", "--intent", str(intent_path), "--path", str(relocated), "--answers", answers_path, "--json"]) == EXIT_CODES[expected]
    output = read_output(capsys)
    assert output["error"]["code"] == expected
    assert output["error"]["details"]["status"] == expected
    assert "never-echo-source-secret" not in json.dumps(output)


def test_import_refuses_source_symlink(portable_intent_case, capsys):
    from onedeploy import discovery
    project, artifacts, original, _, relocated, fresh = portable_intent_case
    intent_path = artifacts / "intent.json"
    discovery.save_intent(discovery.export_intent(original), intent_path)
    (relocated / "Dockerfile").unlink()
    (relocated / "Dockerfile").symlink_to(project / "Dockerfile")
    answers_path = private_json(artifacts / "fresh.json", fresh)
    assert cli.main(["import", "--intent", str(intent_path), "--path", str(relocated), "--answers", answers_path, "--json"]) == 2
    assert read_output(capsys)["error"]["code"] == "invalid_config"


def test_import_profile_output_is_exclusive(portable_intent_case, capsys):
    from onedeploy import discovery
    _, artifacts, original, _, relocated, fresh = portable_intent_case
    intent_path = artifacts / "intent.json"
    discovery.save_intent(discovery.export_intent(original), intent_path)
    profile_path = artifacts / "imported-profile.json"
    answers_path = private_json(artifacts / "fresh.json", fresh)
    argv = ["import", "--intent", str(intent_path), "--path", str(relocated), "--answers", answers_path,
            "--out", str(profile_path), "--json"]
    assert cli.main(argv) == 0
    read_output(capsys)
    original_bytes = profile_path.read_bytes()
    assert cli.main(argv) == 12
    assert read_output(capsys)["error"]["code"] == "conflict"
    assert profile_path.read_bytes() == original_bytes


@pytest.mark.parametrize("provider", ["daytona", "modal"])
def test_portable_import_rebinds_each_provider_without_calls(tmp_path, capsys, monkeypatch, provider):
    import shutil
    from onedeploy import discovery
    project = tmp_path / "original"
    project.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    if provider == "daytona":
        (project / "Dockerfile").write_text("FROM python:3.11-slim\nEXPOSE 8080\n")
        old_answers = {"name": "portable-daytona", "trust_code": True, "runtime": {"port": 8080},
                       "snapshot_name": "old-private-snapshot-9881", "target": {"account": "old-account-9881", "sandbox": "old-sandbox-9881", "mode": "create"}}
        fresh = {"trust_code": True, "target": {"account": "new-account", "sandbox": "new-sandbox", "mode": "create"}}
    else:
        (project / "modal_app.py").write_bytes((Path(cli.__file__).parent / "templates" / "asgi" / "modal_app.py").read_bytes())
        old_answers = {"name": "portable-modal", "trust_code": True,
                       "target": {"account": "old-account-9881", "environment": "old-environment-9881", "mode": "create"}}
        fresh = {"trust_code": True, "target": {"account": "new-account", "environment": "new-environment", "mode": "create"}}
    old_profile = discovery.resolve(discovery.discover(project, provider), old_answers)
    original_path = artifacts / "original-profile.json"
    discovery.save_profile(old_profile, original_path)
    intent_path = artifacts / "intent.json"
    assert cli.main(["export", "--profile", str(original_path), "--out", str(intent_path), "--json"]) == 0
    serialized = json.dumps(read_output(capsys)["result"])
    assert "old-account-9881" not in serialized and str(project) not in serialized
    assert "old-private-snapshot-9881" not in serialized and "old-environment-9881" not in serialized
    relocated = tmp_path / "relocated"
    shutil.copytree(project, relocated)
    answers_path = private_json(artifacts / "fresh.json", fresh)
    profile_path = artifacts / "new-profile.json"
    argv = ["import", "--intent", str(intent_path), "--path", str(relocated), "--answers", answers_path, "--out", str(profile_path), "--json"]
    assert cli.main(argv) == 11
    missing = {item["field"] for item in read_output(capsys)["error"]["details"]["missing"]}
    if provider == "daytona":
        assert "snapshot_name" in missing
        fresh["snapshot_name"] = "fresh-reviewed-snapshot"
        fresh["target"]["transport"] = "cli"
    else:
        assert {"target.app", "target.function"} <= missing
        fresh["target"].update(app="example-asgi", function="web")
    private_json(Path(answers_path), fresh)
    monkeypatch.setattr(core.runner, "run", lambda *a, **kw: pytest.fail("Import and preview must not invoke provider commands"))
    assert cli.main(argv) == 0
    profile = read_output(capsys)["result"]
    assert profile["root"] == str(relocated) and profile["manifest"]["target"] == fresh["target"]
    assert cli.main(["plan", "--profile", str(profile_path), "--allow-dirty", "--out", str(artifacts / "new-plan.json"), "--json"]) == 0
    assert read_output(capsys)["result"]["remote_preconditions"] is None


def test_portable_storage_requires_new_resource_binding(docker_project, capsys):
    import shutil
    from onedeploy import discovery
    project, artifacts, answers = docker_project
    answers["storage"] = [{"name": "cache", "resource_id": "private-original-volume-9821", "mount_path": "/data",
                           "kind": "volume", "access": "read-write", "single_writer": True, "attachment": "existing"}]
    original = discovery.resolve(discovery.discover(project, "railway"), answers)
    intent = discovery.export_intent(original)
    assert "private-original-volume-9821" not in json.dumps(intent)
    intent_path = artifacts / "intent.json"
    discovery.save_intent(intent, intent_path)
    relocated = project.parent / "relocated"
    shutil.copytree(project, relocated)
    fresh = {"trust_code": True, "target": answers["target"]}
    answers_path = private_json(artifacts / "fresh.json", fresh)
    argv = ["import", "--intent", str(intent_path), "--path", str(relocated), "--answers", answers_path, "--json"]
    assert cli.main(argv) == 11
    assert "storage" in {item["field"] for item in read_output(capsys)["error"]["details"]["missing"]}
    fresh["storage"] = [{**answers["storage"][0], "resource_id": "new-reviewed-volume"}]
    private_json(Path(answers_path), fresh)
    assert cli.main(argv) == 0
    assert read_output(capsys)["result"]["manifest"]["storage"][0]["resource_id"] == "new-reviewed-volume"


def test_export_output_cannot_mutate_bound_source(portable_intent_case, capsys):
    from onedeploy import discovery
    project, _, original, original_path, _, _ = portable_intent_case
    output = project / "intent.json"
    assert cli.main(["export", "--profile", str(original_path), "--out", str(output), "--json"]) == 2
    assert read_output(capsys)["error"]["code"] == "invalid_config"
    assert not output.exists()
    private_output = project / ".onedeploy" / "intent.json"
    assert cli.main(["export", "--profile", str(original_path), "--out", str(private_output), "--json"]) == 0
    assert read_output(capsys)["result"]["kind"] == "intent"
    assert discovery.read_profile(original_path)["id"] == original["id"]


def test_export_rejects_conditional_answer_semantics_instead_of_dropping_them(docker_project, capsys):
    from onedeploy import discovery
    project, artifacts, answers = docker_project
    answers["variables"] = {
        "ENABLED": {"type": "boolean", "secret": False, "required": False, "source": "env:ENABLED"},
        "API_TOKEN": {"secret": True, "required": True, "source": "env:API_TOKEN",
                      "required_if": {"variable": "ENABLED", "equals": True}},
    }
    profile = discovery.resolve(discovery.discover(project, "railway"), answers)
    profile_path = artifacts / "conditional-profile.json"
    discovery.save_profile(profile, profile_path)
    output = artifacts / "must-not-exist-intent.json"
    assert cli.main(["export", "--profile", str(profile_path), "--out", str(output), "--json"]) == 13
    assert read_output(capsys)["error"]["code"] == "unsupported_capability"
    assert not output.exists()
