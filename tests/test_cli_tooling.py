"""Offline checks for installed-CLI diagnostics and saved-login context."""
import io
import json

import pytest

from onedeploy import core
from onedeploy.errors import OnedeployError
from onedeploy.providers import get_adapter


@pytest.mark.parametrize("provider", ["daytona", "railway", "modal"])
def test_doctor_uses_selected_installed_cli_without_authentication(monkeypatch, provider):
    adapter = get_adapter(provider)
    calls = []
    monkeypatch.setattr(core.shutil, "which", lambda name: f"/tools/{name}")
    def run(step, **kwargs):
        calls.append((step, kwargs))
        return {"stdout": f"{provider} version {adapter.supported_version}", "stderr": "", "returncode": 0}
    monkeypatch.setattr(core.runner, "run", run)
    result = core.doctor(provider)
    assert result["executable_available"] and result["compatible"]
    assert result["authentication"] == "not_checked" and result["status"] == "ready"
    assert len(calls) == 1
    assert calls[0][0]["id"] == "version" and not calls[0][0]["mutating"]
    assert calls[0][1]["timeout"] == 15
    assert "existing login" in " ".join(result["warnings"])


def test_doctor_missing_cli_does_not_run_install_or_login(monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    monkeypatch.setattr(core.runner, "run", lambda *a, **kw: pytest.fail("No subprocess when CLI is missing"))
    result = core.doctor("daytona")
    assert result["status"] == "missing" and not result["executable_available"]
    assert not result["compatible"] and result["authentication"] == "not_checked"
    assert "PATH" in result["next_step"]


@pytest.mark.parametrize("stdout", ["provider 99.99.99", "unexpected version", "1.2.3 4.5.6"])
def test_doctor_incompatible_is_still_installed(monkeypatch, stdout):
    monkeypatch.setattr(core.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(core.runner, "run", lambda *a, **kw: {"stdout": stdout})
    result = core.doctor("daytona")
    assert result["status"] == "incompatible" and result["executable_available"]
    assert not result["compatible"] and result["error"]["code"] == "unsupported"
    assert result["expected_version"] in result["next_step"]


def test_doctor_version_failure_does_not_claim_missing_or_authenticated(monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda name: f"/tools/{name}")
    def fail(*args, **kwargs):
        raise OnedeployError("provider_failed", "Provider operation timed out")
    monkeypatch.setattr(core.runner, "run", fail)
    result = core.doctor("daytona")
    assert result["status"] == "version_check_failed" and result["executable_available"]
    assert not result["compatible"] and result["authentication"] == "not_checked"


@pytest.fixture
def process_capture(monkeypatch):
    captured = {}
    class Process:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b'{}')
            self.stderr = io.BytesIO()
            self.returncode = 0
        def poll(self):
            return 0
    monkeypatch.setattr(core.subprocess, "Popen", Process)
    return captured


def test_daytona_preserves_only_saved_login_context(monkeypatch, tmp_path, process_capture):
    for key in ("HOME", "XDG_CONFIG_HOME", "DAYTONA_CONFIG_DIR"):
        monkeypatch.setenv(key, str(tmp_path / key))
    for key in ("DAYTONA_API_KEY", "DAYTONA_API_URL", "UNRELATED_SECRET", "PYTHONPATH"):
        monkeypatch.setenv(key, "must-not-forward")
    core.CommandRunner().run({"argv": ["daytona", "--version"]}, cwd=tmp_path)
    env = process_capture["env"]
    for key in ("HOME", "XDG_CONFIG_HOME", "DAYTONA_CONFIG_DIR"):
        assert env[key] == str(tmp_path / key)
    assert "must-not-forward" not in json.dumps(env)
    assert process_capture["shell"] is False


@pytest.mark.parametrize("key", ["HOME", "XDG_CONFIG_HOME", "DAYTONA_CONFIG_DIR"])
@pytest.mark.parametrize("value", ["relative/path", ""])
def test_invalid_daytona_context_rejected_before_subprocess(monkeypatch, tmp_path, key, value):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(core.subprocess, "Popen", lambda *a, **kw: pytest.fail("Invalid config must fail closed"))
    with pytest.raises(OnedeployError, match="absolute nonempty path"):
        core.CommandRunner().run({"argv": ["daytona", "--version"]}, cwd=tmp_path)


def test_other_providers_do_not_gain_daytona_context(monkeypatch, tmp_path, process_capture):
    monkeypatch.setenv("DAYTONA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    core.CommandRunner().run({"argv": ["railway", "--version"]}, cwd=tmp_path)
    assert "DAYTONA_CONFIG_DIR" not in process_capture["env"]
    assert "XDG_CONFIG_HOME" not in process_capture["env"]


@pytest.mark.asyncio
async def test_wizard_explains_selected_cli_and_saved_login(monkeypatch):
    pytest.importorskip("textual")
    from textual.widgets import Button, Static
    from onedeploy.wizard import DeploymentWizard
    monkeypatch.setattr(core.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(core.runner, "run", lambda *a, **kw: {"stdout": "daytona 99.99.99"})
    app = DeploymentWizard()
    app.provider = "daytona"
    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        guidance = str(app.query_one("#setup-guidance", Static).content)
        assert "existing login" in guidance and "same OS user" in guidance
        await app.on_button_pressed(Button.Pressed(app.query_one("#doctor", Button)))
        await app.workers.wait_for_complete()
        await pilot.pause()
        status = str(app.query_one("#tool-status", Static).content)
        assert "daytona" in status and "incompatible" in status
        assert "Installed: True" in status and "Authentication: not checked" in status


@pytest.mark.parametrize("key", ["XDG_CONFIG_HOME", "APPDATA", "DAYTONA_CONFIG_DIR"])
def test_runtime_variables_cannot_replace_login_context(monkeypatch, tmp_path, key):
    monkeypatch.setattr(core.subprocess, "Popen", lambda *a, **kw: pytest.fail("Must reject before launching"))
    with pytest.raises(OnedeployError, match="Unsafe or unresolved"):
        core.CommandRunner().run({"argv": ["modal", "deploy", "app.py"], "runtime_env_keys": [key]},
                                 cwd=tmp_path, variables={key: str(tmp_path)})


def test_daytona_bridge_preserves_saved_login_context(monkeypatch, tmp_path, process_capture):
    monkeypatch.setenv("DAYTONA_CONFIG_DIR", str(tmp_path))
    bridge = core.Path(core.__file__).parent / "providers" / "daytona_bridge.py"
    core.CommandRunner().run({"argv": [core.sys.executable, "-I", str(bridge), "identity", "{}"]}, cwd=tmp_path)
    assert process_capture["env"]["DAYTONA_CONFIG_DIR"] == str(tmp_path)


def test_doctor_subprocess_reuses_cli_without_sdk(tmp_path):
    """Exercise real process spawning, but never a real provider CLI or login."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    home = tmp_path / "isolated-home"
    home.mkdir()
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    fake_cli = binary_dir / "daytona"
    version = get_adapter("daytona").supported_version
    fake_cli.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:] == ['--version'], 'No provider operations allowed'\n"
        "assert not any(key in os.environ for key in ('DAYTONA_API_KEY', 'DAYTONA_API_URL'))\n"
        "Path(os.environ['HOME'], 'cli-call.json').write_text(json.dumps(sys.argv[1:]))\n"
        f"print('daytona version {version}')\n"
    )
    fake_cli.chmod(0o700)
    root = Path(core.__file__).resolve().parent.parent
    script = f"""
import importlib.abc, json, sys
class NoSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'daytona' or fullname.startswith('daytona.'):
            raise ImportError('Daytona SDK intentionally unavailable in this process')
sys.meta_path.insert(0, NoSDK())
sys.path.insert(0, {str(root)!r})
from onedeploy import core
result = core.doctor('daytona')
assert 'daytona' not in sys.modules
print(json.dumps(result))
"""
    env = {"PATH": str(binary_dir), "HOME": str(home),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "DAYTONA_API_KEY": "must-not-reach-cli", "DAYTONA_API_URL": "must-not-reach-cli"}
    completed = subprocess.run([sys.executable, "-I", "-c", script], env=env,
                               cwd=tmp_path, capture_output=True, text=True, timeout=20, check=True)
    result = json.loads(completed.stdout)
    assert result["status"] == "ready" and result["compatible"]
    assert result["selected_transport"] == "cli" and result["authentication"] == "not_checked"
    assert json.loads((home / "cli-call.json").read_text()) == ["--version"]
    assert "must-not-reach-cli" not in completed.stdout + completed.stderr
