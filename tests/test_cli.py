import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from onedeploy import cli, core
from onedeploy.errors import OnedeployError


def test_parse_failure_is_json_and_redacted(capsys):
    assert cli.main(["deploy", "--json", "--secret=NeverPrintThis"]) == 2
    out = capsys.readouterr()
    assert "NeverPrintThis" not in out.out + out.err
    assert json.loads(out.out)["error"]["code"] == "invalid_config"


def test_approval_required_is_stable(monkeypatch, capsys):
    monkeypatch.setattr(core, "read_plan", lambda p: {"id": "abc"})
    def no_approval(*args, **kwargs):
        assert kwargs["approval"] is None
        raise OnedeployError("approval_required", "Explicit approval required.")
    monkeypatch.setattr(core, "deploy", no_approval)
    assert cli.main(["deploy", "--plan", "plan.json", "--json"]) == 4
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "1"
    assert result["status"] == "error"
    assert not result["error"]["mutation_possible"]


def test_plan_calls_shared_engine_and_saves(monkeypatch, capsys):
    calls = []
    plan = {"id": "abc", "warnings": ["offline"]}
    monkeypatch.setattr(core, "make_plan", lambda *args, **kw: plan)
    monkeypatch.setattr(core, "save_plan", lambda *args: calls.append(args))
    assert cli.main(["--json", "plan", "--out", "saved.json"]) == 0
    assert calls == [(plan, "saved.json")]
    assert json.loads(capsys.readouterr().out)["result"] == plan


def test_progress_stderr_only(monkeypatch, capsys):
    monkeypatch.setattr(core, "read_plan", lambda p: {"id": "abc"})
    def deploy(*args, **kw):
        kw["event_callback"]({"phase": "verify", "state": "running", "secret": "NOPE"})
        return {"deployment_id": "d1"}
    monkeypatch.setattr(core, "deploy", deploy)
    assert cli.main(["deploy", "--plan", "p", "--approve-plan", "abc", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["result"]["deployment_id"] == "d1"
    assert json.loads(captured.err)["sequence"] == 1
    assert "NOPE" not in captured.err


def test_unexpected_exception_does_not_leak(monkeypatch, capsys):
    def fail(*args):
        raise ValueError("super-secret-credential")
    monkeypatch.setattr(core, "doctor", fail)
    assert cli.main(["doctor", "--provider", "daytona", "--json"]) == 1
    captured = capsys.readouterr()
    assert "super-secret" not in captured.out + captured.err


def test_tui_refuses_noninteractive_json(capsys):
    assert cli.main(["tui", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_config"


def test_init_never_overwrites(tmp_path, monkeypatch):
    module_dir = tmp_path / "package"
    source = module_dir / "templates" / "docker-http"
    source.mkdir(parents=True)
    (source / "onedeploy.yaml").write_text("safe")
    monkeypatch.setattr(cli, "__file__", str(module_dir / "cli.py"))
    dest = tmp_path / "output"
    assert cli.init_template("docker-http", dest)["files"] == ["onedeploy.yaml"]
    with pytest.raises(OnedeployError):
        cli.init_template("docker-http", dest)
    assert (dest / "onedeploy.yaml").read_text() == "safe"


def test_interrupt_uses_stable_cancellation_code(monkeypatch, capsys):
    monkeypatch.setattr(core, "read_plan", lambda p: {"id": "abc"})
    def interrupt(*args, **kw):
        raise KeyboardInterrupt()
    monkeypatch.setattr(core, "deploy", interrupt)
    assert cli.main(["deploy", "--plan", "p", "--json"]) == 8
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "cancelled" and error["mutation_possible"]


def test_schema_command_uses_real_schema(capsys):
    assert cli.main(["schema", "--json"]) == 0
    schema = json.loads(capsys.readouterr().out)["result"]
    assert schema["properties"]["provider"]["enum"] == ["daytona", "railway", "modal"]


def test_init_rejects_symlink_destination(tmp_path, monkeypatch):
    module_dir = tmp_path / "package"
    source = module_dir / "templates" / "docker-http"
    source.mkdir(parents=True)
    (source / "onedeploy.yaml").write_text("safe")
    monkeypatch.setattr(cli, "__file__", str(module_dir / "cli.py"))
    real = tmp_path / "real"
    real.mkdir()
    dest = tmp_path / "link"
    dest.symlink_to(real, target_is_directory=True)
    with pytest.raises(OnedeployError):
        cli.init_template("docker-http", dest)
    assert not list(real.iterdir())


def test_real_template_init_validate_and_plan(tmp_path, capsys):
    import subprocess
    assert cli.main(["init", "--template", "docker-http", "--directory", str(tmp_path), "--json"]) == 0
    capsys.readouterr()
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Offline Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    manifest = str(tmp_path / "onedeploy.yaml")
    assert cli.main(["config", "validate", "--manifest", manifest, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["valid"]
    plan_file = tmp_path.parent / (tmp_path.name + "-plan.json")
    assert cli.main(["plan", "--manifest", manifest, "--out", str(plan_file), "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)["result"]
    assert core.read_plan(plan_file)["id"] == plan["id"]
    assert plan["remote_preconditions"] is None
    assert cli.main(["deploy", "--plan", str(plan_file), "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "approval_required"


def test_resume_report_is_not_saved_as_executable_plan(monkeypatch, capsys):
    result = {"state": "reconciliation_required", "plan": None, "next_actions": ["Inspect first."]}
    monkeypatch.setattr(core, "resume", lambda *a, **kw: result)
    calls = []
    monkeypatch.setattr(core, "save_plan", lambda *a: calls.append(a))
    assert cli.main(["resume", "--deployment", "d1", "--plan-out", "p", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert not calls
    assert output["next_actions"] == ["Inspect first."]


def test_follow_logs_emits_changed_snapshot_envelopes(monkeypatch, capsys):
    snapshots = iter([{"logs": "first"}, {"logs": "first"}, {"logs": "second"}])
    calls = []
    def logs(*args, **kwargs):
        assert kwargs["follow"] is False
        calls.append(args)
        return next(snapshots)
    monkeypatch.setattr(core, "logs", logs)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    assert cli.main(["logs", "--deployment", "d1", "--follow", "--max-polls", "3", "--json"]) == 0
    captured = capsys.readouterr()
    output = [json.loads(line) for line in captured.out.splitlines()]
    assert len(calls) == 3 and len(output) == 3
    assert [line["result"]["sequence"] for line in output[:-1]] == [1, 2]
    assert output[-1]["result"]["state"] == "follow_complete"
    assert all(line["schema_version"] == "1" for line in output)


def test_follow_cancel_is_read_only(monkeypatch, capsys):
    monkeypatch.setattr(core, "logs", lambda *a, **kw: {"logs": "first"})
    def interrupt(seconds):
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli.time, "sleep", interrupt)
    assert cli.main(["logs", "--deployment", "d1", "--follow", "--json"]) == 8
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1]["error"]["code"] == "cancelled"
    assert not lines[-1]["error"]["mutation_possible"]
    assert not lines[-1]["error"]["uncertain"]


def test_follow_rejects_unbounded_poll_rate(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(core, "logs", lambda *a, **kw: calls.append(kw))
    assert cli.main(["logs", "--deployment", "d1", "--follow", "--poll-interval", "0", "--json"]) == 2
    assert not calls
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_resume_real_plan_is_saved(monkeypatch, capsys):
    plan = {"id": "p1"}
    monkeypatch.setattr(core, "resume", lambda *a, **kw: {"plan": plan, "state": "approval_required"})
    saved = []
    monkeypatch.setattr(core, "save_plan", lambda *a: saved.append(a))
    assert cli.main(["resume", "--deployment", "d1", "--plan-out", "recovery-plan.json", "--json"]) == 0
    assert saved == [(plan, "recovery-plan.json")]


def test_plan_output_passed_to_inventory(monkeypatch, capsys):
    calls = []
    def make_plan(*a, **kw):
        calls.append(kw)
        return {"id": "p1"}
    monkeypatch.setattr(core, "make_plan", make_plan)
    monkeypatch.setattr(core, "save_plan", lambda *a: None)
    assert cli.main(["plan", "--out", "preview.json", "--inspect-remote", "--json"]) == 0
    assert calls[0]["plan_output"] == "preview.json"
    assert calls[0]["inspect_remote"] is True


def test_resume_resolution_requires_paired_assertion(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(core, "resume", lambda *a, **kw: calls.append(kw))
    assert cli.main(["resume", "--deployment", "d1", "--resolve-with-observation", "hash", "--json"]) == 2
    assert not calls
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_resume_forwards_explicit_operator_assertion(monkeypatch, capsys):
    calls = []
    def resume(*a, **kw):
        calls.append(kw)
        return {"plan": None, "state": "operator-confirmed-completed"}
    monkeypatch.setattr(core, "resume", resume)
    assert cli.main(["resume", "--deployment", "d1", "--resolve-with-observation", "hash", "--outcome", "operator-confirmed-completed", "--json"]) == 0
    assert calls[0]["resolve_with_observation"] == "hash"
    assert calls[0]["outcome"] == "operator-confirmed-completed"


@pytest.mark.parametrize("provider,template,targets", [
    ("railway", "docker-http", {"account":"org-example", "project":"p-example", "environment":"e-example", "service":"s-example"}),
    ("daytona", "docker-http", {"account":"org-example", "sandbox":"sandbox-new", "snapshot-name":"snapshot-new"}),
    ("modal", "asgi", {"account":"workspace-example", "environment":"main", "app":"example", "function":"web"}),
])
def test_guided_init_coherent_targets(tmp_path, provider, template, targets):
    from onedeploy import config
    destination = tmp_path / provider
    result = cli.create_project(provider, template, destination, targets)
    manifest = config.load_manifest(result["manifest"])
    assert manifest["provider"] == provider
    assert "REPLACE_" not in json.dumps(manifest["target"])
    assert manifest["target"]["account"] == targets["account"]
    plan = core.make_plan(result["manifest"], allow_dirty=True, plan_output=destination / "plan.json")
    assert plan["manifest"]["provider"] == provider
    assert plan["operations"]
    assert plan["remote_preconditions"] is None
    if provider == "daytona":
        assert "service" not in manifest["target"]
        assert manifest["build"]["snapshot_name"] == "snapshot-new"
        assert manifest["target"]["mode"] == "create"


def test_guided_init_rejects_missing_targets_without_writes(tmp_path):
    with pytest.raises(OnedeployError):
        cli.create_project("daytona", "docker-http", tmp_path / "new", {"account": "org"})
    assert not (tmp_path / "new").exists()


def test_machine_schemas_validate_emitted_shapes(capsys):
    from jsonschema import validate
    error = OnedeployError("invalid_config", "Invalid input.").to_dict()
    validate(error, cli.machine_schema("error"))
    validate(cli.envelope("plan", error=error), cli.machine_schema("result"))
    writer = cli.event_writer()
    writer({"phase":"build", "state":"running", "deployment_id":"synthetic"})
    validate(json.loads(capsys.readouterr().err), cli.machine_schema("progress"))
    assert cli.main(["schema", "--kind", "result", "--json"]) == 0
    validate(json.loads(capsys.readouterr().out), cli.machine_schema("result"))


def test_daytona_rollback_replacement_forwarded(monkeypatch, capsys):
    calls = []
    def rollback(*args, **kwargs):
        calls.append(kwargs)
        return {"id":"review-replacement", "kind":"rollback"}
    monkeypatch.setattr(core, "rollback", rollback)
    assert cli.main(["rollback", "--deployment", "old", "--to", "known", "--replacement-name", "new-sandbox", "--json"]) == 0
    assert calls[0]["replacement_name"] == "new-sandbox"
