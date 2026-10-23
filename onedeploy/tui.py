"""Optional Textual frontend using the same plan and approval engine as CLI."""
from __future__ import annotations
import json
from threading import Event
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.validation import Regex
from textual.widgets import Button, Checkbox, Collapsible, Footer, Header, Input, Label, Select, Static
from . import config, core
from .errors import OnedeployError
from .cli import PROVIDER_FIELDS, PROVIDER_TEMPLATES, create_project


def review_text(plan):
    """Prominent trust boundaries followed by the complete nonsecret engine plan."""
    manifest = plan.get("manifest", {})
    variables = manifest.get("variables", {})
    secret_names = sorted(key for key, spec in variables.items() if spec.get("secret", True))
    provider = manifest.get("provider", "review target below")
    lines = ["EXACT PLAN REVIEW", f"Provider: {provider}",
             "Build/start code runs with local or provider permissions. Trust its source before approval.",
             "Runtime secrets sent to this provider: " + (", ".join(secret_names) or "none declared"),
             "Provider compute/storage charges can continue after cancellation; this plan is not a spending cap."]
    if manifest.get("storage"):
        lines.append("Existing storage is attached, not backed up or migrated. Review volume ownership and single-writer/cache constraints below.")
    if manifest.get("dependencies"):
        lines.append("External dependency syntax/TLS checks do not prove connectivity or authorization, provision databases, or migrate schemas.")
    if plan.get("kind") == "rollback":
        lines.append("ROLLBACK DOES NOT UNDO DATA, SCHEMA MIGRATIONS OR APPLICATION SIDE EFFECTS.")
    lines.extend(["", json.dumps(plan, indent=2, sort_keys=True)])
    return "\n".join(lines)


class DeploymentApp(App):
    TITLE = "Onedeploy · review before deploy"
    CSS = """
    VerticalScroll { padding: 1 2; }
    Input, Select { margin-bottom: 1; }
    Horizontal { height: auto; }
    Button { margin-right: 1; }
    #review { height: auto; min-height: 3; padding: 1; border: solid $primary; }
    #status { height: auto; max-height: 6; padding: 1; }
    #variables { height: auto; }
    """
    BINDINGS = [("escape", "cancel_operation", "Cancel / back"), ("ctrl+q", "safe_quit", "Quit")]

    def __init__(self, manifest="onedeploy.yaml", env_file=None, state_dir=None, allow_dirty=False, inspect_remote=False):
        super().__init__()
        self.manifest_path, self.env_file = manifest, env_file
        self.state_dir, self.allow_dirty = state_dir, allow_dirty
        self.inspect_remote = inspect_remote
        self.plan = None
        self.busy = False
        self.cancel_event = Event()
        self.variable_keys = []
        self.overrides = {}
        self.deployment_id = None
        self.loaded_manifest = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            with Collapsible(title="Create a provider-specific project", collapsed=True, id="new-project"):
                yield Select([(name.title(), name) for name in PROVIDER_TEMPLATES], value="railway", allow_blank=False, id="new-provider")
                yield Select([(name, name) for name in PROVIDER_TEMPLATES["railway"]], value="docker-http", allow_blank=False, id="new-template")
                yield Input(placeholder="New local project directory (existing files are never overwritten)", id="new-directory")
                for field in sorted(set(key for values in PROVIDER_FIELDS.values() for key in values)):
                    yield Input(placeholder=f"Exact {field}", id="target-" + field)
                yield Button("Create and load project", id="create-project")
                yield Label("This writes only local template files. Daytona/Modal select a NEW resource; Railway selects an EXISTING service. Review source before deployment.")
            yield Label("1 · Select a trusted manifest and optional private env file")
            yield Input(self.manifest_path, placeholder="onedeploy.yaml", id="manifest")
            yield Input(self.env_file or "", placeholder="Optional .env path (never auto-discovered)", id="env-file")
            yield Select([(p.title(), p) for p in ("daytona", "railway", "modal")], prompt="Provider from manifest", id="provider", disabled=True)
            with Horizontal():
                yield Button("Load configuration", id="load")
                yield Button("Prerequisites", id="doctor")
                yield Button("Validate inputs", id="validate")
            with VerticalScroll(id="variables"):
                yield Label("Required variables appear here. Secret entry stays in memory.")
            yield Label("2 · Validate and review the exact plan")
            yield Checkbox("Inspect authenticated remote target (read-only; required to deploy)", value=self.inspect_remote, id="inspect-remote")
            with Horizontal():
                yield Button("Review plan", id="plan", variant="primary")
                yield Button("Back", id="back")
                yield Button("Cancel", id="cancel")
            yield Static("No plan approved. No provider changes have been made.", id="review", markup=False)
            yield Label("3 · Explicit approval: type the reviewed plan ID")
            yield Input(placeholder="Exact plan ID", id="approval")
            yield Button("Deploy approved plan", id="deploy", variant="warning", disabled=True)
            yield Label("Inspect or prepare recovery (execution always needs a new approval)")
            yield Input(placeholder="Deployment ID", id="deployment")
            yield Input(placeholder="Prior deployment ID (Railway/Daytona) or vN (Modal)", id="release")
            with Horizontal():
                yield Button("Status", id="status-button")
                yield Button("Reconcile interrupted", id="resume")
                yield Button("Rollback plan", id="rollback")
            with Collapsible(title="Saved plans, logs and operator recovery", collapsed=True, id="advanced"):
                yield Input("plan.json", placeholder="Plan output path (choose before reviewing)", id="plan-file")
                with Horizontal():
                    yield Button("Save reviewed plan", id="save-plan")
                    yield Button("Load saved plan", id="load-plan")
                    yield Button("Log snapshot", id="logs")
                yield Label("Logs may contain private data. Redaction is best-effort.")
                yield Input(placeholder="Daytona rollback: NEW replacement sandbox name", id="replacement-name")
                yield Input(placeholder="Fresh reconciliation observation ID", id="observation")
                yield Select([("Operator confirmed completed", "operator-confirmed-completed"), ("Operator confirmed not executed", "operator-confirmed-not-executed")], prompt="Choose independently confirmed outcome", id="outcome")
                yield Checkbox("I independently verified this outcome and am authorized to record it", id="resolution-consent")
                yield Button("Record operator resolution", id="resolve")
                yield Label("This assertion is not verified truth. No writes are retried; any returned plan needs a new approval.")
        yield Static("Ready. Build/start commands execute local code; only trust known manifests.", id="status", markup=False)
        yield Footer()

    def say(self, text):
        self.query_one("#status", Static).update(text)

    def invalidate(self):
        self.plan = None
        self.query_one("#approval", Input).value = ""
        self.query_one("#deploy", Button).disabled = True
        self.query_one("#review", Static).update("Plan cleared. Review again before deployment.")

    def on_input_changed(self, event: Input.Changed):
        if event.input.id in {"manifest", "env-file", "plan-file"} or (event.input.id or "").startswith(("var-", "target-", "new-directory")):
            self.invalidate()

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "new-provider" and event.value is not Select.BLANK:
            provider = str(event.value)
            template = self.query_one("#new-template", Select)
            template.set_options([(name, name) for name in PROVIDER_TEMPLATES[provider]])
            template.value = PROVIDER_TEMPLATES[provider][0]
            for field in self.query(Input):
                if (field.id or "").startswith("target-"):
                    field.value = ""
                    field.display = field.id[7:] in PROVIDER_FIELDS[provider]
            self.invalidate()

    def on_checkbox_changed(self, event: Checkbox.Changed):
        if event.checkbox.id == "inspect-remote":
            self.inspect_remote = event.value
            self.invalidate()

    async def load_configuration(self):
        self.invalidate()
        try:
            self.loaded_manifest = config.load_manifest(self.query_one("#manifest", Input).value)
            self.query_one("#provider", Select).value = self.loaded_manifest["provider"]
            container = self.query_one("#variables", VerticalScroll)
            await container.remove_children()
            self.variable_keys = list(self.loaded_manifest.get("variables", {}))
            for index, key in enumerate(self.variable_keys):
                spec = self.loaded_manifest["variables"][key]
                required = spec.get("required", "required_if" not in spec)
                requirement = "required" if required else "conditional" if "required_if" in spec else "optional"
                await container.mount(Label(f"{key} · {spec.get('type', 'string')} · {requirement}"))
                kind = spec.get("type", "string")
                validators = [Regex(r"-?[0-9]+", failure_description="Enter an integer.")] if kind == "integer" else [Regex(r"(?i:true|false)", failure_description="Enter true or false.")] if kind == "boolean" else None
                await container.mount(Input(placeholder=f"{kind}: leave empty to use configured source", password=bool(spec.get("secret", True)), validators=validators, valid_empty=True, id=f"var-{index}"))
            self.say("Configuration loaded. Enter overrides only if needed; secret values are masked and never saved by this UI.")
        except Exception as exc:
            self.show_error(exc)

    def show_error(self, exc):
        self.say(exc.to_dict()["message"] if isinstance(exc, OnedeployError) else "Operation failed. Check configuration and inspect deployment state before retrying.")

    def set_busy(self, value):
        self.busy = value
        for button_id in ("load", "plan", "deploy", "back", "status-button", "resume", "rollback", "doctor", "validate", "save-plan", "load-plan", "logs", "resolve", "create-project"):
            self.query_one(f"#{button_id}", Button).disabled = value or (button_id == "deploy" and self.plan is None)
        for field in self.query(Input):
            field.disabled = value
        self.query_one("#inspect-remote", Checkbox).disabled = value
        self.query_one("#resolution-consent", Checkbox).disabled = value
        self.query_one("#outcome", Select).disabled = value
        self.query_one("#new-provider", Select).disabled = value
        self.query_one("#new-template", Select).disabled = value

    async def on_button_pressed(self, event: Button.Pressed):
        action = event.button.id
        if action == "cancel":
            self.action_cancel_operation()
            return
        if self.busy:
            return
        if action == "load":
            await self.load_configuration()
        elif action == "back":
            self.invalidate()
            self.say("Back to configuration. No approval retained.")
        elif action in {"plan", "deploy", "status-button", "resume", "rollback", "doctor", "validate", "save-plan", "load-plan", "logs", "resolve", "create-project"}:
            if action == "create-project":
                provider = str(self.query_one("#new-provider", Select).value)
                self.new_project_args = (provider, str(self.query_one("#new-template", Select).value), self.query_one("#new-directory", Input).value,
                                         {key: self.query_one("#target-" + key, Input).value for key in PROVIDER_FIELDS[provider]})
                if not self.new_project_args[2]:
                    self.say("Choose a new local directory first.")
                    return
                self.invalidate()
            if action == "save-plan" and not self.plan:
                self.say("Review a plan before saving it.")
                return
            if action in {"plan", "deploy", "validate"}:
                for field in self.query(Input):
                    if (field.id or "").startswith("var-"):
                        validation = field.validate(field.value)
                        if validation and not validation.is_valid:
                            self.say("A variable has the wrong type. Use integers or true/false as labeled; values are not displayed.")
                            field.focus()
                            return
            if action == "resolve" and (not self.query_one("#resolution-consent", Checkbox).value or not self.query_one("#observation", Input).value or self.query_one("#outcome", Select).value is Select.BLANK):
                self.say("Supply fresh observation evidence, an independently confirmed outcome, and explicit authorization. The observation ID alone is not consent.")
                return
            if action == "deploy" and (not self.plan or self.query_one("#approval", Input).value != self.plan["id"]):
                self.say("Enter the exact reviewed plan ID to approve. Review its destination, secret recipients and cost notes first.")
                return
            self.manifest_path = self.query_one("#manifest", Input).value
            self.env_file = self.query_one("#env-file", Input).value or None
            self.overrides = {key: self.query_one(f"#var-{index}", Input).value for index, key in enumerate(self.variable_keys) if self.query_one(f"#var-{index}", Input).value}
            deployment = self.query_one("#deployment", Input).value
            release = self.query_one("#release", Input).value
            self.plan_file = self.query_one("#plan-file", Input).value
            self.replacement_name = self.query_one("#replacement-name", Input).value or None
            self.resolution_observation = self.query_one("#observation", Input).value
            self.resolution_outcome = self.query_one("#outcome", Select).value
            self.cancel_event = Event()
            self.set_busy(True)
            self.say("Working. Cancel stops new steps; an in-flight remote operation may still complete.")
            self.run_operation(action, deployment, release)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def run_operation(self, action, deployment, release):
        try:
            if action == "create-project":
                result = create_project(*self.new_project_args)
            elif action == "plan":
                result = core.make_plan(self.manifest_path, env_file=self.env_file, allow_dirty=self.allow_dirty, env_overrides=self.overrides, inspect_remote=self.inspect_remote, plan_output=self.plan_file)
            elif action == "deploy":
                result = core.deploy(self.plan, env_file=self.env_file, approval=self.plan["id"], state_dir=self.state_dir, env_overrides=self.overrides, cancel_event=self.cancel_event, event_callback=self.progress)
            elif action == "doctor":
                result = core.doctor(config.load_manifest(self.manifest_path)["provider"])
            elif action == "validate":
                result = config.validate_config(config.load_manifest(self.manifest_path), env_file=self.env_file, env_overrides=self.overrides)
            elif action == "save-plan":
                core.save_plan(self.plan, self.plan_file)
                result = {"saved": True}
            elif action == "load-plan":
                result = core.read_plan(self.plan_file)
            elif action == "logs":
                result = core.logs(deployment, state_dir=self.state_dir, follow=False)
            elif action == "resolve":
                result = core.resume(deployment, state_dir=self.state_dir, resolve_with_observation=self.resolution_observation, outcome=self.resolution_outcome)
            elif action == "status-button":
                result = core.status(deployment, state_dir=self.state_dir)
            elif action == "resume":
                result = core.resume(deployment, state_dir=self.state_dir)
            else:
                kwargs = {"to": release, "state_dir": self.state_dir}
                if self.replacement_name:
                    kwargs["replacement_name"] = self.replacement_name
                result = core.rollback(deployment, **kwargs)
            self.call_from_thread(self.finished, action, result)
        except Exception as exc:
            self.call_from_thread(self.failed, action, exc)

    def progress(self, event):
        # Render only allowlisted engine metadata, not provider stdout or secrets.
        phase = str(event.get("phase", "working"))
        self.call_from_thread(self.say, f"Deployment phase: {phase}. Cancel requests no new steps, not remote deletion.")

    def finished(self, action, result):
        self.set_busy(False)
        if action == "resolve":
            self.query_one("#resolution-consent", Checkbox).value = False
            self.query_one("#observation", Input).value = ""
        if action == "create-project":
            self.query_one("#manifest", Input).value = result["manifest"]
            self.query_one("#env-file", Input).value = ""
            self.query_one("#new-project", Collapsible).collapsed = True
            self.run_worker(self.load_configuration())
            return
        candidate = result if isinstance(result, dict) and "id" in result else result.get("plan") if isinstance(result, dict) else None
        if action in {"plan", "resume", "rollback", "load-plan", "resolve"} and candidate:
            if self.cancel_event.is_set():
                self.invalidate()
                self.say("Planning cancelled; no deployment was started.")
                return
            self.plan = candidate
            self.query_one("#approval", Input).value = ""
            self.query_one("#review", Static).update(review_text(candidate))
            self.query_one("#deploy", Button).disabled = False
            self.say("Review destination, runtime secret recipients, exposure and cost notes. Approval is for this exact plan only.")
        elif action == "save-plan":
            self.say("Reviewed plan saved with private permissions. Saving does not approve or execute it.")
        else:
            self.invalidate()
            if isinstance(result, dict):
                deployment_id = result.get("deployment_id")
                if deployment_id:
                    self.query_one("#deployment", Input).value = deployment_id
            self.query_one("#review", Static).update(json.dumps(result, indent=2, sort_keys=True))
            self.say("Operation returned. Read the reported state; provider acceptance is not production qualification.")

    def failed(self, action, exc):
        self.set_busy(False)
        if action in {"deploy", "load-plan", "resolve"}:
            self.invalidate()
        self.show_error(exc)

    def action_cancel_operation(self):
        if self.busy:
            self.cancel_event.set()
            self.say("Cancellation requested. Waiting for the current operation to settle; remote work and costs may continue. Do not retry yet.")
        else:
            self.invalidate()
            self.overrides.clear()
            for field in self.query(Input):
                if (field.id or "").startswith("var-"):
                    field.value = ""
            self.say("Cancelled. Approval and entered runtime values cleared.")

    def action_safe_quit(self):
        if self.busy:
            self.action_cancel_operation()
        else:
            self.exit()
