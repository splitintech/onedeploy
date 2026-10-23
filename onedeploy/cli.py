"""Versioned machine interface; no interactive approval bypass."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time

from . import __version__


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages may contain arbitrary arguments, including secrets.
        raise UsageError("Invalid command arguments. Use --help for supported options.")


def parser():
    p = Parser(prog="onedeploy", description="Review immutable plans before deploying.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "config", "inspect", "discover", "resolve", "export", "import", "plan", "deploy", "status", "logs", "doctor", "resume", "rollback", "schema", "tui"):
        c = sub.add_parser(name)
        c.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "config":
            c.add_argument("action", choices=["validate"])
        if name in {"config", "inspect", "plan"}:
            source = c.add_mutually_exclusive_group()
            source.add_argument("--manifest", default="onedeploy.yaml")
            source.add_argument("--profile", help="Resolved, source-bound profile from resolve")
        if name == "tui":
            mode = c.add_mutually_exclusive_group()
            mode.add_argument("--manifest", help="Use the legacy manifest interface")
            mode.add_argument("--wizard", action="store_true", help="Opt in to the experimental three-step project wizard")
            c.add_argument("--path", help="Project folder or supported file for --wizard (default: current directory)")
        if name in {"config", "inspect", "plan", "tui"}:
            c.add_argument("--env-file")
        if name == "discover":
            c.add_argument("--provider", required=True, choices=["daytona", "railway", "modal"])
            c.add_argument("--path", default=".", help="Project folder or supported configuration file; never executes source")
            c.add_argument("--out", help="Optionally save a private discovery artifact; existing files are never overwritten")
        if name in {"resolve", "import"}:
            if name == "resolve":
                c.add_argument("--discovery", required=True)
            else:
                c.add_argument("--intent", required=True, help="Non-executable portable intent; creates a freshly bound local profile")
                c.add_argument("--path", default=".", help="New local project folder; source is rediscovered, never relocated by rewriting hashes")
            c.add_argument("--answers", help="Strict JSON containing nonsecret answers and variable references, never secret values")
            c.add_argument("--out", help="Optionally save a private profile; existing files are never overwritten")
        if name == "export":
            c.add_argument("--profile", required=True, help="Validated local profile to export as non-executable portable intent")
            c.add_argument("--out", help="Optionally save private intent outside source or under .onedeploy; no credentials, approval receipts or absolute source paths")
        if name in {"plan", "tui"}:
            c.add_argument("--allow-dirty", action="store_true")
            c.add_argument("--inspect-remote", action="store_true", help="Read authenticated provider identity and target before approval")
        if name == "plan":
            c.add_argument("--out", help="Plan file (default: plan.json for manifests, .onedeploy/plan.json for profiles)")
        if name == "deploy":
            c.add_argument("--plan", required=True)
            c.add_argument("--approve-plan")
            c.add_argument("--env-file")
        if name in {"status", "logs", "resume", "rollback"}:
            c.add_argument("--deployment", required=True)
        if name in {"deploy", "status", "logs", "resume", "rollback", "tui"}:
            c.add_argument("--state-dir")
        if name in {"resume", "rollback"}:
            c.add_argument("--plan-out")
        if name == "resume":
            c.add_argument("--resolve-with-observation", help="Bind an explicit operator assertion to this fresh observation; the hash is not proof of consent")
            c.add_argument("--outcome", choices=["operator-confirmed-completed", "operator-confirmed-not-executed"])
        if name == "rollback":
            c.add_argument("--to", required=True)
            c.add_argument("--replacement-name", help="Daytona rollback creates this new sandbox; never deletes the original")
        if name == "logs":
            c.add_argument("--follow", action="store_true", help="Poll bounded snapshots until interrupted; not a lossless stream")
            c.add_argument("--poll-interval", type=float, default=5.0)
            c.add_argument("--max-polls", type=int, default=0, help="Stop after this many reads; 0 waits until interrupted")
        if name == "doctor":
            c.add_argument("--provider", required=True, choices=["daytona", "railway", "modal"])
        if name == "schema":
            c.add_argument("--kind", choices=["manifest", "discovery", "profile", "intent", "answers", "resolution", "result", "progress", "error"], default="manifest")
        if name == "init":
            c.add_argument("--provider", choices=["daytona", "railway", "modal"])
            for field in ("account", "project", "environment", "service", "sandbox", "app", "function", "snapshot-name"):
                c.add_argument("--" + field)
            c.add_argument("--template", choices=["docker-http", "asgi", "worker", "model"])
            c.add_argument("--directory", default=".")
    return p


def envelope(command, result=None, error=None):
    # Preserve the v1 envelope: failed commands have no successful result.
    # Resolution questions are an additive, typed error.details object.
    details = error.get("details") if error else result
    next_actions = details.get("next_actions", []) if isinstance(details, dict) else []
    if not next_actions and command in {"discover", "resolve", "import"}:
        status = details.get("status") if isinstance(details, dict) else None
        guidance = {
            "ready": "Resolve the discovery artifact into a source-bound profile; review executable source before planning.",
            "input_required": "Supply the listed missing fields as typed answers; reference runtime variables by environment name, never secret value.",
            "conflict": "Choose an explicit candidate or correct conflicting source settings, then resolve again.",
            "unsupported_capability": "Select a supported input/provider combination; unsupported behavior cannot be silently omitted.",
        }
        if status in guidance:
            next_actions = [guidance[status]]
        elif command in {"resolve", "import"} and not error:
            next_actions = ["Validate and inspect the profile, then create a plan; deployment requires explicit approval of that exact plan."]
        elif error and error.get("code") == "conflict":
            next_actions = ["Choose a fresh artifact output path; existing files are never overwritten."]
    if not next_actions and command == "export" and not error:
        next_actions = ["Import this non-executable intent against a fresh local project with new source trust and target confirmation, then review a new plan."]
    return {"schema_version": "1", "command": command,
            "status": "error" if error else "success", "result": None if error else result,
            "error": error, "warnings": details.get("warnings", []) if isinstance(details, dict) else [],
            "next_actions": next_actions}


def resolution_error(exc, command):
    """Expose only validated questions, and never change legacy error payloads."""
    error = exc.to_dict()
    if command not in {"discover", "resolve", "import"} or exc.code not in {
        "input_required", "conflict", "unsupported_capability"
    }:
        return error
    details = getattr(exc, "result", None)
    if not isinstance(details, dict) or not set(details) <= {
        "status", "missing", "conflicts", "unsupported"
    }:
        return error
    if details.get("status") != exc.code:
        return error
    try:
        from jsonschema import Draft202012Validator
        from . import discovery
        if not Draft202012Validator(discovery.resolution_schema()).is_valid(details):
            return error
    except Exception:
        # An error-reporting failure must never expose the original exception.
        return error
    error["details"] = details
    return error


def init_template(name, destination):
    from .errors import OnedeployError
    source = Path(__file__).parent / "templates" / name
    if not source.is_dir():
        raise OnedeployError("invalid_config", "Unknown or unavailable template.")
    destination = Path(destination).absolute()
    if destination.is_symlink():
        raise OnedeployError("invalid_config", "Template destination must not be a symlink.")
    files = [p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}]
    if any((destination / p.relative_to(source)).exists() or (destination / p.relative_to(source)).is_symlink() for p in files):
        raise OnedeployError("invalid_config", "Template would overwrite an existing file.")
    destination.mkdir(parents=True, exist_ok=True)
    for p in files:
        target = destination / p.relative_to(source)
        if any(parent.is_symlink() for parent in target.parents):
            raise OnedeployError("invalid_config", "Template destination contains a symlink.")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create also prevents overwriting concurrent changes.
        with target.open("xb") as output, p.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
    return {"files": sorted(str(p.relative_to(source)) for p in files),
            "warnings": ["Review executable build/startup code before deployment. Copy placeholders into a private env file; do not commit secrets."]}



PROVIDER_TEMPLATES = {"railway": ("docker-http", "worker"), "daytona": ("docker-http", "model"), "modal": ("asgi",)}
PROVIDER_FIELDS = {"railway": ("account", "project", "environment", "service"), "daytona": ("account", "sandbox", "snapshot-name"), "modal": ("account", "environment", "app", "function")}


def create_project(provider, template, directory, targets):
    """Create a fresh local project with a coherent, explicit provider target."""
    from . import config
    from .errors import OnedeployError
    import yaml
    if template not in PROVIDER_TEMPLATES.get(provider, ()):
        raise OnedeployError("invalid_config", "Template does not support the chosen provider.")
    required = PROVIDER_FIELDS[provider]
    if any(value for key, value in targets.items() if key not in required):
        raise OnedeployError("invalid_config", "Target fields from another provider are not accepted.")
    if any(not targets.get(key) or str(targets[key]).startswith("REPLACE_") for key in required):
        raise OnedeployError("invalid_config", "Enter every exact provider target identifier; placeholders are not accepted in guided setup.")
    source = Path(__file__).parent / "templates" / template / "onedeploy.yaml"
    manifest = config.load_manifest(source)
    manifest["provider"] = provider
    manifest["target"] = {key: targets[key] for key in required if key != "snapshot-name"}
    if provider in {"daytona", "modal"}:
        manifest["target"]["mode"] = "create"
    if provider == "daytona":
        manifest.setdefault("build", {})["snapshot_name"] = targets["snapshot-name"]
        manifest["ingress"] = {"mode": "private"}
    config.validate_manifest(manifest)
    result = init_template(template, directory)
    manifest_path = Path(directory).absolute() / "onedeploy.yaml"
    config.secure_path(manifest_path)
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    result.update(manifest=str(manifest_path), provider=provider,
                  next_actions=["Review generated source and manifest. Configure private variables and commit source before planning."])
    return result


def machine_schema(kind):
    from . import discovery
    if kind in {"discovery", "profile", "intent", "answers", "resolution"}:
        return getattr(discovery, kind + "_schema")()
    details = discovery.resolution_schema()
    details["properties"].pop("profile", None)
    details["properties"]["status"] = {"enum": ["input_required", "conflict", "unsupported_capability"]}
    error = {"type": "object", "additionalProperties": False,
             "required": ["code", "message", "retryable", "uncertain", "mutation_possible"],
             "properties": {"code": {"type": "string"}, "message": {"type": "string"},
                            **{key: {"type": "boolean"} for key in ("retryable", "uncertain", "mutation_possible")},
                            "details": details}}
    if kind == "error":
        return {"$schema": "https://json-schema.org/draft/2020-12/schema", **error}
    if kind == "progress":
        return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
                "required": ["schema_version", "sequence", "timestamp", "phase"],
                "properties": {"schema_version": {"const": "1"}, "sequence": {"type": "integer", "minimum": 1},
                               **{key: {"type": "string"} for key in ("timestamp", "deployment_id", "operation_id", "phase", "state")}}}
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
            "required": ["schema_version", "command", "status", "result", "error", "warnings", "next_actions"],
            "properties": {"schema_version": {"const": "1"}, "command": {"type": "string"}, "status": {"enum": ["success", "error"]},
                           "result": {"type": ["object", "null"]}, "error": {"anyOf": [error, {"type": "null"}]},
                           **{key: {"type": "array", "items": {"type": "string"}} for key in ("warnings", "next_actions")}},
            "allOf": [{"if": {"properties": {"status": {"const": "success"}}}, "then": {"properties": {"error": {"type": "null"}}},
                       "else": {"properties": {"error": error, "result": {"type": "null"}}}}]}

def event_writer():
    sequence = 0
    def emit(event):
        nonlocal sequence
        sequence += 1
        # Only engine metadata belongs on the progress channel.
        payload = {key: event[key] for key in ("deployment_id", "operation_id", "phase", "state") if key in event}
        payload.update(schema_version="1", sequence=sequence,
                       timestamp=datetime.now(timezone.utc).isoformat())
        print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)
    return emit


def follow_logs(args):
    """Bounded provider reads; explicitly snapshots, not exactly-once log delivery."""
    from . import core
    if not 1 <= args.poll_interval <= 300 or not 0 <= args.max_polls <= 100000:
        raise UsageError("Log polling interval must be 1–300 seconds and max polls 0–100000.")
    previous = None
    polls = sequence = 0
    while True:
        snapshot = core.logs(args.deployment, state_dir=args.state_dir, follow=False)
        polls += 1
        serialized = json.dumps(snapshot, sort_keys=True, default=str)
        if serialized != previous:
            sequence += 1
            event = {"event": "log_snapshot", "sequence": sequence,
                     "timestamp": datetime.now(timezone.utc).isoformat(),
                     "deployment_id": args.deployment, "snapshot": snapshot}
            print(json.dumps(envelope("logs", result=event) if args.json else event,
                             sort_keys=True, default=str), flush=True)
            previous = serialized
        if args.max_polls and polls >= args.max_polls:
            return {"deployment_id": args.deployment, "state": "follow_complete", "polls": polls,
                    "snapshots_emitted": sequence,
                    "warnings": ["Snapshots are best-effort, may overlap, and are not a lossless log stream."]}
        time.sleep(args.poll_interval)


def dispatch(args):
    from . import config, core
    cmd = args.command
    if cmd == "init":
        if args.provider:
            targets = {key: getattr(args, key.replace("-", "_")) for key in ("account", "project", "environment", "service", "sandbox", "app", "function", "snapshot-name")}
            return create_project(args.provider, args.template or PROVIDER_TEMPLATES[args.provider][0], args.directory, targets)
        if any(getattr(args, key) for key in ("account", "project", "environment", "service", "sandbox", "app", "function", "snapshot_name")):
            raise UsageError("Target arguments require an explicit --provider.")
        return init_template(args.template or "docker-http", args.directory)
    if cmd == "config":
        if args.profile:
            from . import discovery
            manifest, _ = discovery.validate_profile(args.profile)
        else:
            manifest = config.load_manifest(args.manifest)
        return config.validate_config(manifest, env_file=args.env_file)
    if cmd == "inspect":
        return core.inspect_profile(args.profile) if args.profile else core.inspect(args.manifest)
    if cmd == "discover":
        from . import discovery
        result = discovery.discover(args.path, args.provider)
        if args.out:
            discovery.save_discovery(result, args.out)
        return result
    if cmd == "resolve":
        from . import discovery
        answers = discovery.read_answers(args.answers) if args.answers else None
        result = discovery.resolve(args.discovery, answers)
        if args.out:
            discovery.save_profile(result, args.out)
        return result
    if cmd == "export":
        from . import discovery
        profile = discovery.read_profile(args.profile)
        result = discovery.export_intent(profile)
        if args.out:
            discovery.save_intent(result, args.out, source_root=profile["discovery"]["root"])
        return result
    if cmd == "import":
        from . import discovery
        answers = discovery.read_answers(args.answers) if args.answers else None
        result = discovery.import_intent(args.intent, args.path, answers)
        if args.out:
            discovery.save_profile(result, args.out)
        return result
    if cmd == "plan":
        make_plan = core.make_plan_from_profile if args.profile else core.make_plan
        source = args.profile or args.manifest
        output = args.out or (".onedeploy/plan.json" if args.profile else "plan.json")
        plan = make_plan(source, env_file=args.env_file, allow_dirty=args.allow_dirty, inspect_remote=args.inspect_remote, plan_output=output)
        core.save_plan(plan, output)
        return plan
    if cmd == "deploy":
        return core.deploy(core.read_plan(args.plan), env_file=args.env_file,
                           approval=args.approve_plan, state_dir=args.state_dir, event_callback=event_writer())
    if cmd == "status":
        return core.status(args.deployment, state_dir=args.state_dir)
    if cmd == "logs":
        print("Provider logs may contain private data; redaction is best-effort.", file=sys.stderr)
        if args.follow:
            return follow_logs(args)
        return core.logs(args.deployment, state_dir=args.state_dir, follow=False)
    if cmd == "doctor":
        return core.doctor(args.provider)
    if cmd in {"resume", "rollback"}:
        kwargs = {"state_dir": args.state_dir}
        if cmd == "rollback":
            kwargs["to"] = args.to
            if args.replacement_name:
                kwargs["replacement_name"] = args.replacement_name
        elif args.resolve_with_observation or args.outcome:
            if not (args.resolve_with_observation and args.outcome):
                raise UsageError("Resolution requires both an observation ID and an explicit operator-confirmed outcome.")
            kwargs.update(resolve_with_observation=args.resolve_with_observation, outcome=args.outcome)
        plan = getattr(core, cmd)(args.deployment, **kwargs)
        candidate = plan if "id" in plan else plan.get("plan")
        if candidate and args.plan_out:
            core.save_plan(candidate, args.plan_out)
        return plan
    if cmd == "schema":
        return config.schema() if args.kind == "manifest" else machine_schema(args.kind)
    if cmd == "tui":
        if args.json or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise UsageError("TUI requires an interactive terminal; use plan and deploy for automation.")
        if args.path and not args.wizard:
            raise UsageError("Project paths require the experimental --wizard option; use --manifest for the legacy interface.")
        try:
            if args.wizard:
                from .wizard import DeploymentWizard
                app = DeploymentWizard(path=args.path or ".", env_file=args.env_file,
                                       state_dir=args.state_dir, allow_dirty=args.allow_dirty,
                                       inspect_remote=args.inspect_remote)
            else:
                from .tui import DeploymentApp
                app = DeploymentApp(args.manifest or "onedeploy.yaml", args.env_file,
                                    args.state_dir, args.allow_dirty, args.inspect_remote)
        except ModuleNotFoundError as exc:
            if exc.name != "textual" and not (exc.name or "").startswith("textual."):
                raise
            raise UsageError("Install the tui extra: pip install 'onedeploy[tui]'.") from None
        app.run()
        return {"closed": True}
    raise UsageError("Unknown command.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = "unknown"
    machine = "--json" in argv
    try:
        args = parser().parse_args(argv)
        command = args.command + (" validate" if args.command == "config" else "")
        machine = args.json
        result = dispatch(args)
        output, code = envelope(command, result=result), 0
    except KeyboardInterrupt:
        output, code = envelope(command, error={"code": "cancelled", "message": "Log polling stopped; the deployment was not stopped." if command == "logs" else "Interrupted. Inspect deployment state before retrying; remote work may still be running.", "retryable": False, "uncertain": command == "deploy", "mutation_possible": command == "deploy"}), 8
    except UsageError as exc:
        output, code = envelope(command, error={"code": "invalid_config", "message": str(exc), "retryable": False, "uncertain": False, "mutation_possible": False}), 2
    except Exception as exc:
        from .errors import OnedeployError
        if isinstance(exc, OnedeployError):
            output, code = envelope(command, error=resolution_error(exc, command)), exc.exit_code
        else:
            # Never render raw exceptions: provider and OS exceptions can carry secrets.
            output, code = envelope(command, error={"code": "internal_error", "message": "Operation failed. Inspect configuration and local deployment state before retrying.", "retryable": False, "uncertain": True, "mutation_possible": command == "deploy"}), 1
    if machine:
        print(json.dumps(output, sort_keys=True, default=str))
    elif output["error"]:
        print(output["error"]["message"], file=sys.stderr)
    else:
        print(json.dumps(output["result"], indent=2, sort_keys=True, default=str))
    return code
