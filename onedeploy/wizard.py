"""Experimental three-step human UI over the shared discovery and execution APIs.

This module is intentionally separate from the compatible manifest-first TUI.
Discovery is passive, approval is a deliberate click bound to an immutable review,
and workers never invent provider commands or retry an uncertain write.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from threading import Event
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Footer, Header, Input, Label, Select, Static, TextArea

from . import config, core
from .errors import OnedeployError
from .providers import get_adapter

PROVIDERS = ("railway", "daytona", "modal")
SETUP_LINKS = {
    "railway": "https://docs.railway.com/guides/cli",
    "daytona": "https://www.daytona.io/docs/en/getting-started/",
    "modal": "https://modal.com/docs/guide",
}


def fingerprint(value: dict) -> str:
    """Independent display binding; the engine additionally verifies its plan ID."""
    return hashlib.sha256(core.canonical(value)).hexdigest()


def review_summary(plan: dict) -> str:
    """Keep the consequential details visible without expanding low-level JSON."""
    manifest = plan.get("manifest", {})
    source = plan.get("source", {})
    executable = plan.get("remote_preconditions") is not None
    names = sorted(name for name, spec in manifest.get("variables", {}).items() if spec.get("secret", True))
    lines = ["REMOTELY INSPECTED PLAN" if executable else "LOCAL PREVIEW ONLY · cannot deploy",
             f"Provider: {manifest.get('provider', 'unknown')} · profile: {manifest.get('profile', 'unknown')}",
             "Exact destination: " + json.dumps(manifest.get("target", {}), sort_keys=True),
             "Authenticated identity / remote preconditions: " + json.dumps(plan.get("remote_preconditions"), sort_keys=True),
             f"Source: {source.get('root', 'unknown')}",
             f"Revision: {source.get('revision') or 'local inventory, not a clean commit'}",
             f"Bundle: {source.get('bundle_sha256', 'not reported')}",
             "Artifact / build: " + json.dumps(manifest.get("build", manifest.get("source", {})), sort_keys=True),
             "Changes: " + (", ".join(str(op.get("id", op.get("operation", "operation"))) for op in plan.get("operations", [])) or "Changes: none reported"),
             "Runtime secret recipients: " + str(manifest.get("provider", "unknown")) + " at the exact destination above",
             "Secret variable names: " + (", ".join(names) or "none declared"),
             "Ingress: " + json.dumps(manifest.get("ingress", {"mode": "existing"}), sort_keys=True),
             "Storage: " + json.dumps(manifest.get("storage", []), sort_keys=True),
             "Resources / lifecycle: " + json.dumps(manifest.get("resources", {}), sort_keys=True),
             "External dependencies: " + json.dumps(manifest.get("dependencies", []), sort_keys=True),
             "Build/start instructions execute trusted project code. Syntax validation is not a security audit.",
             "COST UNCERTAINTY: this is not a quote or spending cap. Cancellation does not delete resources or stop charges.",
             "Verification is layered: provider acceptance is not process readiness, authentication or production qualification.",
             "Storage is not backed up or migrated. Review ownership, persistence and single-writer requirements."]
    try:
        caps = get_adapter(manifest["provider"], manifest).capabilities
        lines.append("Provider lifecycle: " + caps["lifecycle_assumptions"])
        lines.append("Listener limits: " + caps["listener_port"])
    except (KeyError, OnedeployError):
        pass
    if plan.get("kind") == "rollback":
        lines.append("ROLLBACK DOES NOT UNDO DATA, SCHEMA MIGRATIONS OR APPLICATION SIDE EFFECTS.")
    lines.extend(str(warning) for warning in plan.get("warnings", []))
    lines.append("Plan ID (reference only, never type it): " + str(plan.get("id", "missing")))
    return "\n".join(lines)


def _set_path(target: dict, field: str, value: Any) -> None:
    """Set dotted typed answers, including structured collection rows."""
    parts = field.split(".")
    current: Any = target
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        key: Any = int(part) if isinstance(current, list) else part
        if isinstance(current, list):
            while len(current) <= key:
                current.append(None)
        if last:
            current[key] = value
            return
        child_list = parts[index + 1].isdigit()
        if isinstance(current, list):
            if current[key] is None:
                current[key] = [] if child_list else {}
        elif key not in current:
            current[key] = [] if child_list else {}
        current = current[key]


def _delete_path(target: dict, field: str) -> None:
    parts = field.split(".")
    current = target
    try:
        for part in parts[:-1]:
            current = current[int(part)] if isinstance(current, list) else current[part]
        if isinstance(current, list):
            current[int(parts[-1])] = None
        else:
            current.pop(parts[-1], None)
    except (KeyError, IndexError, TypeError, ValueError):
        pass


def _get_path(target: dict, field: str, default: Any = None) -> Any:
    current: Any = target
    try:
        for part in field.split("."):
            current = current[int(part)] if isinstance(current, list) else current[part]
        return current
    except (KeyError, IndexError, TypeError, ValueError):
        return default


class DeploymentWizard(App):
    """Opt-in existing-project flow. No profile or manifest is written implicitly."""

    TITLE = "Onedeploy · experimental three-step wizard"
    CSS = """
    #body { padding: 1 2; }
    .step, #questions, #runtime-values, #candidate-details, #result { height: auto; }
    Input, Select { margin-bottom: 1; }
    Button { width: 100%; margin-bottom: 1; height: auto; min-height: 3; }
    Label, Static { height: auto; }
    Checkbox { height: auto; min-height: 3; }
    #steps { padding: 0 1; text-style: bold; }
    #status { height: auto; max-height: 7; padding: 1; border-top: solid $primary; }
    #review { padding: 1; border: solid $primary; }
    .hint { color: $text-muted; margin-bottom: 1; }
    #candidate-details, #result { padding: 1; }
    """
    BINDINGS = [
        ("escape", "cancel_or_back", "Cancel / back"),
        ("ctrl+q", "safe_quit", "Quit"),
        ("ctrl+c", "cancel_operation", "Cancel safely"),
        ("alt+left", "back", "Back"),
    ]

    def __init__(self, path=".", env_file=None, state_dir=None, allow_dirty=False, inspect_remote=False):
        super().__init__()
        self.initial_path = str(path)
        self.env_file = env_file
        self.state_dir = state_dir
        self.allow_dirty = allow_dirty
        self.inspect_remote = inspect_remote
        self.step = 1
        self.provider = "railway"
        self.discovery = None
        self.profile = None
        self.answers: dict = {}
        self.question_fields: list[dict] = []
        self.variable_keys: list[str] = []
        self.variable_specs: dict = {}
        self._added_variables: set[str] = set()
        self._generated_references: set[str] = set()
        self._profile_inferred = False
        self._runtime_scope = None
        self._selection_scope = None
        self._imported_intent = None
        self._intent_suggestions = {}
        self.plan = None
        self.review_binding = None
        self.deployment_id = None
        self.busy = False
        self.cancel_event = Event()
        self._revision = 0
        self._submitted: set[str] = set()
        self._baseline: dict[str, Any] = {}
        self._artifact_defaults = {"plan-file": ".onedeploy/plan.json", "profile-file": ".onedeploy/profile.json", "intent-file": ".onedeploy/intent.json"}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("1 · Provider  →  2 · Project  →  3 · Review", id="steps", markup=False)
        with VerticalScroll(id="body"):
            with Vertical(id="step-provider", classes="step"):
                yield Label("1 · Choose provider")
                yield Static("Experimental / offline-tested. No live deployment qualification is implied.", classes="hint")
                yield Select([(name.title(), name) for name in PROVIDERS], value=self.provider, allow_blank=False, id="provider")
                yield Static("", id="provider-details", markup=False)
                yield Static("Selected provider CLI: not checked. Authentication: not checked.", id="tool-status", markup=False)
                yield Button("Check selected CLI", id="doctor")
                yield Static("", id="setup-guidance", markup=False)
                yield Button("Continue to project", id="provider-next", variant="primary")
            with Vertical(id="step-project", classes="step"):
                yield Label("2 · Choose project")
                yield Label("Existing project folder or supported configuration file")
                yield Input(self.initial_path, placeholder="./my-project or ./compose.yaml", id="project-path")
                yield Button("Discover project (local, read-only)", id="discover", variant="primary")
                yield Button("Load saved profile from this path", id="load-profile")
                yield Static("Discovery never builds, imports Python, logs in or changes project files.", classes="hint")
                yield Select([], prompt="Choose the exact service / configuration", id="candidate")
                yield Static("Choose a project to see recognized settings and provenance.", id="candidate-details", markup=False)
                yield Label("Runtime profile (suggestions require your selection; explicit imported profiles stay fixed)")
                yield Select([], prompt="Choose the supported runtime profile", id="runtime-profile")
                with Vertical(id="questions"):
                    yield Static("Missing settings and conflicts will appear here.", markup=False)
                yield Checkbox("I trust this project's build/start code to execute during deployment", id="trust-code")
                yield Button("Apply answers and resolve project", id="resolve-project")
                yield Label("Optional private .env file (chosen explicitly, never auto-loaded)")
                yield Input(self.env_file or "", placeholder="/private/app.env", id="env-file")
                with Vertical(id="runtime-values"):
                    yield Static("Runtime variable overrides appear after project resolution. Values stay in memory.", markup=False)
                with Collapsible(title="Add runtime variable references", collapsed=True, id="add-variables"):
                    yield Static("Only explicitly declared variables are sent. Add names here, not values. Enter values in the masked fields above or an explicitly selected private .env file.", classes="hint")
                    yield Input(placeholder="Application variable name, e.g. DATABASE_URL", id="new-variable-name")
                    yield Input(placeholder="Local environment source name (defaults to same name)", id="new-variable-source")
                    yield Select([("String", "string"), ("Integer", "integer"), ("Boolean", "boolean")], value="string", allow_blank=False, id="new-variable-type")
                    yield Checkbox("Secret / sensitive runtime value", value=True, id="new-variable-secret")
                    yield Checkbox("Required at deployment", value=True, id="new-variable-required")
                    yield Button("Add this runtime variable reference", id="add-variable")
                    yield Select([], prompt="Choose a variable added in this session", id="remove-variable-name")
                    yield Button("Remove selected added reference", id="remove-variable")
                yield Checkbox("Bind modified / untracked local files (otherwise require clean Git inputs)", value=self.allow_dirty, id="allow-dirty")
                yield Checkbox("Inspect the authenticated remote destination (read-only; required to deploy)", value=self.inspect_remote, id="inspect-remote")
                yield Static("Remote inspection uses your existing authentication, checks the explicit destination and never logs in. Offline preview works without it.", classes="hint")
                yield Button("Review exact plan", id="review-plan", variant="primary", disabled=True)
                yield Button("Back to provider", id="project-back")
                with Collapsible(title="Optional profile export / input validation", collapsed=True, id="profile-tools"):
                    yield Input(".onedeploy/profile.json", placeholder="New private profile output path", id="profile-file")
                    yield Button("Save resolved profile", id="save-profile", disabled=True)
                    yield Static("Optional export contains operational identifiers and secret references, never runtime values. Original project files are not overwritten.", classes="hint")
                    yield Button("Validate runtime inputs", id="validate", disabled=True)
                with Collapsible(title="Optional portable intent (not an executable plan)", collapsed=True, id="portable-tools"):
                    yield Static("Portable intent contains reusable nonsecret settings and variable references, not runtime values, local source paths, source trust or execution approval. Always discover the destination project and confirm its target afresh.", classes="hint")
                    yield Input(".onedeploy/intent.json", placeholder="Portable intent JSON path (new file for export)", id="intent-file")
                    yield Button("Export portable intent", id="export-intent", disabled=True)
                    yield Button("Import intent into this discovered project", id="import-intent", disabled=True)
                    yield Static("Import clears entered runtime values, the selected .env, source trust and any plan approval. It never executes code or changes the destination project.", classes="hint")
            with Vertical(id="step-review", classes="step"):
                yield Label("3 · Review and deploy")
                yield Static("No plan reviewed.", id="review", markup=False)
                yield Static("Deploy this plan approves only this displayed immutable plan. Any edit, back or cancellation clears it.", classes="hint")
                yield Button("Deploy this plan", id="deploy", variant="warning", disabled=True)
                yield Button("Back to project / change inputs", id="review-back")
                with Collapsible(title="Complete exact plan (all operations and provenance)", collapsed=True, id="exact-plan"):
                    yield Static("No plan.", id="plan-json", markup=False)
                yield Static("No deployment has been started.", id="result", markup=False)
                yield Button("Refresh deployment status", id="status-button", disabled=True)
                yield Button("Read log snapshot", id="logs", disabled=True)
            with Collapsible(title="Advanced / recovery", collapsed=True, id="advanced"):
                yield Static("Recovery never blindly retries an uncertain write. Every recovery or rollback plan needs a fresh review and Deploy action.", classes="hint")
                yield Input(placeholder="Deployment ID (auto-filled after execution)", id="deployment")
                yield Button("Inspect saved deployment status", id="advanced-status")
                yield Button("Reconcile interrupted deployment", id="resume")
                yield Input(placeholder="Prior deployment ID, or Modal vN", id="release")
                yield Input(placeholder="Daytona rollback: NEW replacement sandbox name", id="replacement-name")
                yield Button("Prepare rollback plan", id="rollback")
                yield Input(".onedeploy/plan.json", placeholder="New saved plan path (choose before review)", id="plan-file")
                yield Static("Use a new private file inside .onedeploy or outside the project. Existing profile-plan outputs are not overwritten. Choose the output path before review.", classes="hint")
                yield Button("Save reviewed plan", id="save-plan")
                yield Button("Load saved plan for review", id="load-plan")
                yield Input(placeholder="Fresh reconciliation observation ID", id="observation")
                yield Select([("Independently confirmed completed", "operator-confirmed-completed"), ("Independently confirmed not executed", "operator-confirmed-not-executed")], prompt="Verified operator outcome", id="outcome")
                yield Checkbox("I independently verified this outcome and am authorized to record it", id="resolution-consent")
                yield Button("Record operator resolution", id="operator-resolve")
                yield Static("An operator assertion is not verified truth. Logs may contain private data; redaction is best-effort.", classes="hint")
            yield Button("Cancel / clear approval", id="cancel")
        yield Static("Ready. Explore locally without changing a provider or project.", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        for control in self.query("Input, Select, Checkbox"):
            self._baseline[control.id] = control.value
        self._provider_details()
        self.query_one("#review", Static).can_focus = True
        self.show_step(1)
        self._refresh_controls()

    def say(self, message: str):
        # Never use Rich markup for discovered, provider-supplied, or user text.
        self.query_one("#status", Static).update(message)

    def _provider_details(self):
        adapter = get_adapter(self.provider, self.profile.get("manifest") if self.profile else None)
        caps = adapter.capabilities
        self.query_one("#provider-details", Static).update(
            "Supported profiles: " + ", ".join(caps["profiles"]) + "\n"
            + "Artifact support: " + ", ".join(caps["artifact_types"]) + "\n"
            + "Lifecycle: " + caps["lifecycle_assumptions"] + "\n"
            + "Storage: " + caps["persistent_storage"])
        transport = caps.get("transport", "cli")
        auth = ("Explicit SDK transport requires DAYTONA_API_KEY; it does not reuse CLI login. "
                if transport == "sdk" else "Reuses your installed CLI and existing login for the same OS user. ")
        self.query_one("#doctor", Button).label = "Check selected " + transport.upper()
        self.query_one("#setup-guidance", Static).update(
            f"Official setup: {SETUP_LINKS[self.provider]}\n"
            f"Exact offline-tested {transport.upper()} version: {adapter.supported_version}. " + auth +
            "No automatic installation, login, credential storage or transport fallback. Tool availability is not authentication.")

    def show_step(self, number: int):
        self.step = number
        for index, name in enumerate(("provider", "project", "review"), 1):
            self.query_one(f"#step-{name}").display = number == index
        self.query_one("#steps", Static).update(f"Step {number} of 3 · Provider → Project → Review")
        self.query_one("#body", VerticalScroll).scroll_home(animate=False)
        target = {1: "provider", 2: "project-path", 3: "review"}[number]
        self.query_one(f"#{target}").focus(scroll_visible=False)

    def _refresh_controls(self):
        for control in self.query("Input, Select, Checkbox, Button, TextArea"):
            control.disabled = self.busy and control.id != "cancel"
        available = {
            "resolve-project": self.discovery is not None,
            "review-plan": self.profile is not None,
            "validate": self.profile is not None,
            "save-profile": self.profile is not None,
            "export-intent": self.profile is not None,
            "import-intent": self.discovery is not None,
            "save-plan": self.plan is not None,
            "deploy": self.plan is not None and self.plan.get("remote_preconditions") is not None and self.review_binding == fingerprint(self.plan),
            "status-button": bool(self.deployment_id),
            "logs": bool(self.deployment_id),
            "candidate": bool(self.discovery and self.discovery.get("candidates")),
            "add-variable": self.discovery is not None,
            "remove-variable": bool(self._added_variables),
            "runtime-profile": self._profile_inferred and self.discovery is not None,
        }
        for ident, enabled in available.items():
            self.query_one(f"#{ident}").disabled = self.busy or not enabled

    def invalidate(self, *, profile=False, discovery=False):
        self._revision += 1
        self.plan = None
        self.review_binding = None
        if profile:
            self.profile = None
        if discovery:
            self.discovery = None
            self.profile = None
            self.answers = {}
            self._imported_intent = None
            self._intent_suggestions = {}
            self._added_variables.clear()
            self._generated_references.clear()
            if self.is_mounted:
                self.query_one("#remove-variable-name", Select).set_options([])
                self._clear_source_consent()
        if self.is_mounted:
            self.query_one("#review", Static).update("Plan cleared. Review again before deployment.")
            self.query_one("#plan-json", Static).update("No current plan.")
            self._refresh_controls()

    def _default_artifact_paths(self, root):
        for ident, name in (("plan-file", "plan.json"), ("profile-file", "profile.json"), ("intent-file", "intent.json")):
            if self.query_one(f"#{ident}", Input).value == self._artifact_defaults[ident]:
                value = str(Path(root) / ".onedeploy" / name)
                self._assign(ident, value)
                self._artifact_defaults[ident] = value

    def _clear_source_consent(self):
        self._assign("trust-code", False)
        for index in range(len(self.variable_keys)):
            if self.query(f"#variable-{index}"):
                self._assign(f"variable-{index}", "")

    def _changed(self, control, value) -> bool:
        # Textual may queue an intermediate NULL when set_options replaces a
        # selection. Ignore obsolete events rather than resetting a newly
        # rendered imported profile or mounting duplicate question controls.
        actual = control.text if isinstance(control, TextArea) else control.value
        if actual != value:
            return False
        previous = self._baseline.get(control.id, value)
        self._baseline[control.id] = value
        return previous != value

    def on_input_changed(self, event: Input.Changed):
        ident = event.input.id or ""
        if not self._changed(event.input, event.value):
            return
        if ident == "project-path":
            self.invalidate(discovery=True)
            self._assign("env-file", "")
        elif ident.startswith("answer-"):
            self.invalidate(profile=True)
        elif ident.startswith("variable-") or ident in {"env-file", "plan-file"}:
            self.invalidate()
        elif ident in {"deployment", "release", "replacement-name", "observation"}:
            # Changing the recovery target must never leave another target approved.
            self.invalidate()
        self._refresh_controls()

    async def on_select_changed(self, event: Select.Changed):
        if not self._changed(event.select, event.value):
            return
        ident = event.select.id or ""
        if ident == "provider" and event.value is not Select.NULL:
            self.provider = str(event.value)
            self.invalidate(discovery=True)
            self.variable_keys = []
            self.variable_specs = {}
            await self.query_one("#runtime-values").remove_children()
            await self.query_one("#questions").remove_children()
            self.question_fields = []
            self._assign("trust-code", False)
            self._assign("env-file", "")
            self._assign("candidate", Select.NULL)
            self.query_one("#candidate", Select).set_options([])
            self.query_one("#candidate-details", Static).update("Provider changed. Discover again; target mappings and runtime overrides were cleared.")
            self.query_one("#tool-status", Static).update("Selected provider CLI: not checked. Authentication: not checked.")
            self._provider_details()
        elif ident == "candidate":
            self.invalidate(profile=True)
            if self.answers.get("candidate"):
                self._assign("env-file", "")
            self.answers = {}
            self._assign("trust-code", False)
            if self.discovery and event.value is not Select.NULL:
                await self._show_candidate(str(event.value))
        elif ident == "runtime-profile":
            self.invalidate(profile=True)
        elif ident.startswith("answer-"):
            self.invalidate(profile=True)
        elif ident == "outcome":
            self.invalidate()
        self._refresh_controls()

    def on_text_area_changed(self, event: TextArea.Changed):
        if self._changed(event.text_area, event.text_area.text):
            self.invalidate(profile=True)

    def on_checkbox_changed(self, event: Checkbox.Changed):
        if not self._changed(event.checkbox, event.value):
            return
        ident = event.checkbox.id or ""
        if ident in {"inspect-remote", "allow-dirty"}:
            self.invalidate()
        elif ident == "trust-code" or ident.startswith("answer-"):
            self.invalidate(profile=True)
        elif ident == "resolution-consent":
            self.invalidate()

    def _assign(self, ident: str, value: Any):
        self._baseline[ident] = value
        self.query_one(f"#{ident}").value = value

    @staticmethod
    def _question_list(data: dict) -> list[dict]:
        result, seen = [], set()
        for question in [*data.get("missing", []), *data.get("conflicts", [])]:
            if not isinstance(question, dict) or not question.get("field"):
                continue
            if question["field"] in seen or question["field"] in {"trust_code", "candidate", "source", "provider"}:
                continue
            seen.add(question["field"])
            result.append(question)
        return result

    def _expand_questions(self, questions: list[dict]) -> list[dict]:
        expanded = []
        for question in questions:
            root = question["field"]
            if root not in {"dependencies", "storage"}:
                expanded.append(question)
                continue
            declarations = question.get("choices", [])
            if not declarations and self.discovery:
                candidate_id = self.query_one("#candidate", Select).value
                candidate = next((item for item in self.discovery.get("candidates", []) if item["id"] == candidate_id), {})
                original = next((item for item in candidate.get("missing", []) if item["field"] == root), {})
                declarations = original.get("choices", [])
            if not declarations:
                # A malformed structured request is blocked, never guessed empty.
                expanded.append({**question, "type": "unsupported-structured"})
                continue
            for row, declaration in enumerate(declarations):
                prefix = f"{root}.{row}."
                def add(field, kind="string", choices=None, fixed=None):
                    item = {"field": prefix + field, "type": kind, "reason": f"{root} mapping for {declaration}", "required": True, "collection_root": root}
                    if choices is not None:
                        item["choices"] = choices
                    if fixed is not None:
                        item["fixed"] = fixed
                    expanded.append(item)
                if root == "dependencies":
                    add("name", fixed=declaration)
                    add("kind", choices=["postgres", "redis", "http"])
                    add("variable")
                    expanded[-1]["reason"] = "Environment variable name for this endpoint; creates a secret env reference, never enter its value here"
                    add("tls", "boolean")
                    add("required", "boolean")
                else:
                    name, path, *mode = str(declaration).split(":")
                    add("name", fixed=name)
                    add("mount_path", fixed=path)
                    add("access", fixed="read-only" if mode == ["ro"] else "read-write")
                    add("resource_id")
                    template = next((item for item in (self._imported_intent or {}).get("requirements", {}).get("storage", []) if item["name"] == name and item["mount_path"] == path), None)
                    if template is not None:
                        add("kind", fixed=template["kind"])
                        add("single_writer", "boolean", fixed=template["single_writer"])
                        if "attachment" in template:
                            add("attachment", fixed=template["attachment"])
                    else:
                        add("kind", choices=["volume", "model-cache"])
                        add("single_writer", "boolean")
                        if self.provider == "railway":
                            add("attachment", choices=["attach", "existing"])
        return expanded

    async def _render_questions(self, data: dict):
        container = self.query_one("#questions")
        await container.remove_children()
        self.question_fields = self._expand_questions(self._question_list(data))
        for index, question in enumerate(self.question_fields):
            field, kind = question["field"], question.get("type", "string")
            ident = f"answer-{index}"
            prior = _get_path(self.answers, field)
            await container.mount(Label(f"{field} · {kind}: {question.get('reason', 'Choose an explicit value')}", markup=False))
            if "fixed" in question:
                await container.mount(Static("Preserved from source: " + str(question["fixed"]), markup=False))
                continue
            choices = question.get("choices")
            if choices:
                options = [(str(choice.get("label", choice.get("value"))), str(choice.get("value"))) if isinstance(choice, dict) else (str(choice), str(choice)) for choice in choices]
                value = str(prior) if prior is not None else Select.NULL
                control = Select(options, prompt="Choose explicitly", value=value, id=ident)
            elif kind == "boolean":
                control = Select([("Yes", "true"), ("No", "false")], prompt="Choose yes or no", value=str(prior).lower() if prior is not None else Select.NULL, id=ident)
            elif kind in {"array", "array[string]"}:
                await container.mount(Static("One exact argument per line. No shell evaluation or splitting. Blank lines are not arguments.", markup=False))
                control = TextArea("\n".join(prior) if isinstance(prior, list) else "", id=ident)
                control.styles.height = 5
            else:
                control = Input(str(prior) if prior is not None else "", placeholder="Enter the explicit value", id=ident)
            self._baseline[ident] = control.text if isinstance(control, TextArea) else control.value
            await container.mount(control)
        guidance = [question for question in data.get("conflicts", []) if question.get("field") in {"candidate", "source", "provider"}]
        if guidance:
            await container.mount(Static("Choose or correct the local source; these conflicts cannot be overridden by an answer.\n" + json.dumps(guidance, indent=2, sort_keys=True), markup=False))
        unsupported = data.get("unsupported", [])
        if unsupported:
            await container.mount(Static("BLOCKED: unsupported mappings are never silently dropped.\n" + json.dumps(unsupported, indent=2, sort_keys=True), markup=False))
        if not self.question_fields and not unsupported:
            await container.mount(Static("No additional nonsecret questions for this selection. Review its source trust below.", markup=False))

    def _enter_source_scope(self, root, candidate_id, manifest):
        # Track selection independently from answers, which are reset during
        # rediscovery. A first explicit --env-file belongs to the first scope;
        # later service/source/target changes never inherit it implicitly.
        scope = fingerprint({"root": str(root), "candidate": candidate_id,
                             "provider": manifest.get("provider", self.provider),
                             "target": manifest.get("target", {})})
        if self._selection_scope is not None and self._selection_scope != scope:
            self._assign("env-file", "")
        self._selection_scope = scope

    @staticmethod
    def _runtime_identity(root, manifest):
        return fingerprint({"root": str(root), "provider": manifest.get("provider"),
                            "target": manifest.get("target", {}),
                            "references": {name: spec.get("source", "env:" + name) for name, spec in manifest.get("variables", {}).items()}})

    def _set_profile_choices(self, candidate: dict, value=None):
        self._profile_inferred = candidate.get("format") in {"dockerfile", "compose", "railway", "snapshot"}
        profile = candidate.get("manifest", {}).get("profile")
        choices = get_adapter(self.provider, candidate.get("manifest")).capabilities["profiles"] if self._profile_inferred else ([profile] if profile else [])
        self.query_one("#runtime-profile", Select).set_options([(name, name) for name in choices])
        self._assign("runtime-profile", value if value is not None else Select.NULL if self._profile_inferred else profile or Select.NULL)

    async def _show_candidate(self, candidate_id: str):
        candidate = next((item for item in self.discovery.get("candidates", []) if item["id"] == candidate_id), None)
        if candidate is None:
            return
        self._enter_source_scope(candidate.get("root", self.discovery.get("root", "")), candidate_id, candidate.get("manifest", {}))
        self.answers = {**deepcopy(self._intent_suggestions), "candidate": candidate_id} if self._imported_intent else {"candidate": candidate_id}
        self._runtime_scope = None
        self._set_profile_choices(candidate, self.answers.get("profile"))
        self._added_variables.clear()
        self._generated_references.clear()
        self.query_one("#remove-variable-name", Select).set_options([])
        self._clear_source_consent()
        details = {key: candidate[key] for key in ("format", "path", "service", "manifest", "provenance", "warnings", "unsupported") if key in candidate}
        self.query_one("#candidate-details", Static).update(json.dumps(details, indent=2, sort_keys=True))
        await self._render_questions(candidate)
        variable_manifest = deepcopy(candidate.get("manifest", {}))
        if self._imported_intent:
            variable_manifest.setdefault("variables", {}).update(self.answers.get("variables", {}))
        await self._render_variables(variable_manifest, preserve=False)

    async def _render_variables(self, manifest: dict, *, preserve=True):
        previous = {key: self.query_one(f"#variable-{index}", Input).value for index, key in enumerate(self.variable_keys) if self.query(f"#variable-{index}")}
        if not preserve:
            previous = {}
        container = self.query_one("#runtime-values")
        await container.remove_children()
        self.variable_specs = manifest.get("variables", {})
        self.variable_keys = list(self.variable_specs)
        for index, key in enumerate(self.variable_keys):
            spec = self.variable_specs[key]
            kind = spec.get("type", "string")
            required = "required" if spec.get("required", "required_if" not in spec) else "conditional" if "required_if" in spec else "optional"
            await container.mount(Label(f"{key} · {kind} · {required} · source {spec.get('source', 'env:' + key)}", markup=False))
            control = Input(previous.get(key, ""), password=bool(spec.get("secret", True)), placeholder="Leave blank to use selected .env / environment source", id=f"variable-{index}")
            self._baseline[control.id] = control.value
            await container.mount(control)
        if not self.variable_keys:
            await container.mount(Static("No runtime variables declared by the selected configuration.", markup=False))

    def _collect_answers(self) -> dict:
        answers = deepcopy(self.answers)
        for name in self._generated_references:
            if name not in self._added_variables:
                answers.get("variables", {}).pop(name, None)
        self._generated_references.clear()
        candidate = self.query_one("#candidate", Select).value
        if candidate is not Select.NULL:
            answers["candidate"] = str(candidate)
        answers["trust_code"] = self.query_one("#trust-code", Checkbox).value
        if self._profile_inferred:
            runtime_profile = self.query_one("#runtime-profile", Select).value
            if runtime_profile is Select.NULL:
                self.query_one("#runtime-profile", Select).focus()
                raise OnedeployError("input_required", "Choose the supported runtime profile explicitly; a detected suggestion is not a selection.")
            answers["profile"] = str(runtime_profile)
        # Blank is a deletion, not permission to reuse a previous hidden answer.
        for root in {q["collection_root"] for q in self.question_fields if q.get("collection_root")} :
            answers.pop(root, None)
        for question in self.question_fields:
            _delete_path(answers, question["field"])
        for index, question in enumerate(self.question_fields):
            if "fixed" in question:
                _set_path(answers, question["field"], question["fixed"])
                continue
            control = self.query_one(f"#answer-{index}")
            value = control.text if isinstance(control, TextArea) else control.value
            if value is Select.NULL or value == "":
                if question.get("required"):
                    control.focus()
                    raise OnedeployError("input_required", f"Complete {question['field']}; declared dependency/storage mappings cannot be dropped.")
                continue
            kind = question.get("type", "string")
            try:
                if kind == "integer":
                    value = int(str(value))
                elif kind == "number":
                    value = float(str(value))
                elif kind == "boolean":
                    if str(value).lower() not in {"true", "false"}:
                        raise ValueError
                    value = str(value).lower() == "true"
                elif kind in {"array", "array[string]"}:
                    value = str(value).splitlines()
                    if not all(value):
                        raise ValueError
                elif kind in {"object", "array[object]", "unsupported-structured"}:
                    raise ValueError
            except (ValueError, TypeError):
                control.focus()
                raise OnedeployError("invalid_config", f"Answer for {question['field']} must match its labeled type; input values are not displayed.") from None
            _set_path(answers, question["field"], value)
        # Dependency endpoint names are explicitly supplied; only references are saved.
        candidate_manifest = next((item.get("manifest", {}) for item in (self.discovery or {}).get("candidates", []) if item["id"] == answers.get("candidate")), {})
        for dependency in answers.get("dependencies", []):
            name = dependency["variable"]
            if name not in candidate_manifest.get("variables", {}) and name not in answers.get("variables", {}):
                answers.setdefault("variables", {})[name] = {"type": "string", "secret": True, "required": dependency["required"], "source": "env:" + name}
                self._generated_references.add(name)
        return answers

    def _runtime_overrides(self) -> dict:
        result = {}
        for index, key in enumerate(self.variable_keys):
            control = self.query_one(f"#variable-{index}", Input)
            value = control.value
            if not value:
                continue
            kind = self.variable_specs[key].get("type", "string")
            if kind == "integer":
                try:
                    int(value)
                except ValueError:
                    control.focus()
                    raise OnedeployError("invalid_config", "A runtime variable requires an integer; values are not displayed.") from None
            if kind == "boolean" and value.lower() not in {"true", "false"}:
                control.focus()
                raise OnedeployError("invalid_config", "A runtime variable requires true or false; values are not displayed.")
            result[key] = value
        return result

    async def _edit_variable(self, action: str):
        if not self.discovery:
            raise OnedeployError("input_required", "Discover a project before adding runtime references.")
        candidate_id = self.query_one("#candidate", Select).value
        candidate = next((item for item in self.discovery.get("candidates", []) if item["id"] == candidate_id), None)
        if candidate is None:
            raise OnedeployError("input_required", "Choose the exact project configuration first.")
        if action == "add-variable":
            name = self.query_one("#new-variable-name", Input).value.strip()
            source = self.query_one("#new-variable-source", Input).value.strip() or name
            if not config.KEY.fullmatch(name) or not config.KEY.fullmatch(source):
                raise OnedeployError("invalid_config", "Use environment-variable names only, not values or credential URLs.")
            if name in candidate.get("manifest", {}).get("variables", {}) or name in self.answers.get("variables", {}):
                raise OnedeployError("conflict", "This variable is already declared. Imported settings cannot be silently replaced; remove an added reference before adding its replacement.")
            self.answers.setdefault("variables", {})[name] = {
                "type": str(self.query_one("#new-variable-type", Select).value),
                "secret": self.query_one("#new-variable-secret", Checkbox).value,
                "required": self.query_one("#new-variable-required", Checkbox).value,
                "source": "env:" + source,
            }
            self._added_variables.add(name)
            self._assign("new-variable-name", "")
            self._assign("new-variable-source", "")
        else:
            name = self.query_one("#remove-variable-name", Select).value
            if name not in self._added_variables:
                raise OnedeployError("input_required", "Choose a runtime reference added in this session.")
            self.answers.get("variables", {}).pop(str(name), None)
            self._added_variables.remove(str(name))
        self.query_one("#remove-variable-name", Select).set_options([(name, name) for name in sorted(self._added_variables)])
        self._baseline["remove-variable-name"] = self.query_one("#remove-variable-name", Select).value
        manifest = deepcopy(candidate.get("manifest", {}))
        manifest.setdefault("variables", {}).update(self.answers.get("variables", {}))
        self.invalidate(profile=True)
        await self._render_variables(manifest)
        self._refresh_controls()
        self.say("Runtime reference updated, not a value. Apply answers again before reviewing a plan; only declared names can be transmitted.")

    async def on_button_pressed(self, event: Button.Pressed):
        action = event.button.id
        if action == "cancel":
            self.action_cancel_operation()
            return
        if self.busy:
            return
        if action in {"add-variable", "remove-variable"}:
            try:
                await self._edit_variable(action)
            except OnedeployError as exc:
                self.show_error(exc)
            return
        if action == "provider-next":
            self.show_step(2)
            return
        if action in {"project-back", "review-back"}:
            self.action_back()
            return
        try:
            args = self._operation_args(action)
        except OnedeployError as exc:
            self.show_error(exc)
            return
        if args is None:
            return
        self.busy = True
        self.cancel_event = Event()
        self._refresh_controls()
        self.say("Working. Cancel stops new steps; an in-flight provider operation may still complete.")
        self.run_operation(action, args, self._revision, self.cancel_event)

    def _operation_args(self, action: str) -> dict | None:
        args: dict = {"state_dir": self.state_dir}
        if action in {"discover", "load-profile"}:
            path = self.query_one("#project-path", Input).value.strip()
            if not path:
                raise OnedeployError("input_required", "Choose an existing project folder or configuration file.")
            self.invalidate(discovery=True)
            args.update(path=path, provider=self.provider)
        elif action == "export-intent":
            if not self.profile:
                raise OnedeployError("input_required", "Resolve a local profile before exporting portable intent.")
            path = self.query_one("#intent-file", Input).value.strip()
            if not path:
                raise OnedeployError("input_required", "Choose a new private portable-intent output path.")
            args.update(profile=deepcopy(self.profile), path=path)
        elif action == "import-intent":
            if not self.discovery:
                raise OnedeployError("input_required", "Discover the destination project and choose its exact configuration first.")
            candidate = self.query_one("#candidate", Select).value
            if candidate is Select.NULL:
                raise OnedeployError("input_required", "Choose the exact destination configuration before importing intent.")
            path = self.query_one("#intent-file", Input).value.strip()
            if not path:
                raise OnedeployError("input_required", "Choose a saved portable-intent JSON file.")
            args.update(path=path, project_path=self.query_one("#project-path", Input).value, provider=self.provider, candidate=str(candidate))
            self.invalidate(profile=True)
            self._clear_source_consent()
            self._assign("env-file", "")
            self.answers = {}
            self._added_variables.clear()
            self._generated_references.clear()
            self._imported_intent = None
            self._intent_suggestions = {}
        elif action == "resolve-project":
            if not self.discovery:
                raise OnedeployError("input_required", "Discover and select a project first.")
            if not self.query_one("#trust-code", Checkbox).value:
                self.query_one("#trust-code", Checkbox).focus()
                raise OnedeployError("input_required", "Review the original project and explicitly confirm source-code trust before resolving it.")
            answers = self._collect_answers()
            self.answers = answers
            self.invalidate(profile=True)
            args.update(discovery=deepcopy(self.discovery), answers=answers, intent=deepcopy(self._imported_intent))
        elif action in {"review-plan", "validate"}:
            if not self.profile:
                raise OnedeployError("input_required", "Resolve the selected project and any conflicts first.")
            args.update(profile=deepcopy(self.profile), env_file=self.query_one("#env-file", Input).value or None,
                        env_overrides=self._runtime_overrides(), allow_dirty=self.query_one("#allow-dirty", Checkbox).value,
                        inspect_remote=self.query_one("#inspect-remote", Checkbox).value,
                        plan_output=self.query_one("#plan-file", Input).value or None)
            self.invalidate()
        elif action == "deploy":
            if not self.plan or self.review_binding != fingerprint(self.plan):
                self.invalidate()
                raise OnedeployError("approval_required", "Review the unchanged exact plan before choosing Deploy this plan.")
            if self.plan.get("remote_preconditions") is None:
                raise OnedeployError("approval_required", "This is a local preview. Go back, opt into remote inspection and review the executable plan.")
            if self.plan["id"] in self._submitted:
                raise OnedeployError("uncertain", "This plan was already submitted. Inspect or reconcile its result; do not submit it again.")
            args.update(plan=deepcopy(self.plan), env_file=self.query_one("#env-file", Input).value or None, env_overrides=self._runtime_overrides())
            self._submitted.add(self.plan["id"])
            self.deployment_id = self.plan["id"]
            self._assign("deployment", self.deployment_id)
            self.invalidate()
        elif action == "doctor":
            args["provider"] = self.provider
            args["transport"] = self.profile.get("manifest", {}).get("target", {}).get("transport", "cli") if self.profile else "cli"
        elif action == "save-profile":
            if not self.profile:
                raise OnedeployError("input_required", "Resolve a profile before exporting it.")
            args.update(profile=deepcopy(self.profile), path=self.query_one("#profile-file", Input).value)
        elif action == "save-plan":
            if not self.plan:
                raise OnedeployError("input_required", "Review a plan before saving it.")
            args.update(plan=deepcopy(self.plan), path=self.query_one("#plan-file", Input).value)
        elif action == "load-plan":
            self.invalidate()
            args["path"] = self.query_one("#plan-file", Input).value
        elif action in {"status-button", "advanced-status", "logs", "resume", "rollback", "operator-resolve"}:
            deployment = self.query_one("#deployment", Input).value.strip()
            if not deployment:
                raise OnedeployError("input_required", "Choose a recorded deployment in Advanced / recovery first.")
            args["deployment"] = deployment
            if action in {"resume", "rollback", "operator-resolve"}:
                self.invalidate()
            if action == "rollback":
                args["to"] = self.query_one("#release", Input).value.strip()
                if not args["to"]:
                    raise OnedeployError("input_required", "Choose a known compatible prior release for rollback.")
                args["replacement_name"] = self.query_one("#replacement-name", Input).value.strip() or None
            if action == "operator-resolve":
                outcome = self.query_one("#outcome", Select).value
                observation = self.query_one("#observation", Input).value.strip()
                if not observation or outcome is Select.NULL or not self.query_one("#resolution-consent", Checkbox).value:
                    raise OnedeployError("approval_required", "Supply fresh evidence, an independently verified outcome and explicit operator consent.")
                args.update(observation=observation, outcome=str(outcome))
        else:
            return None
        return args

    @work(thread=True, exit_on_error=False, group="wizard-operations")
    def run_operation(self, action: str, args: dict, revision: int, cancel_event: Event):
        try:
            if cancel_event.is_set():
                raise OnedeployError("cancelled", "Cancelled before work began.")
            if action in {"discover", "resolve-project", "save-profile", "load-profile", "export-intent", "import-intent"}:
                from . import discovery
                if action == "discover":
                    selected = Path(args["path"])
                    is_profile = False
                    if selected.is_file() and selected.suffix == ".json":
                        safe_path = config.secure_path(selected)
                        try:
                            header = json.loads(safe_path.read_text())
                            is_profile = isinstance(header, dict) and header.get("kind") == "profile"
                        except (ValueError, UnicodeError):
                            pass
                    if is_profile:
                        result = discovery.read_profile(args["path"])
                        discovery.validate_profile(result)
                        if result["manifest"]["provider"] != args["provider"]:
                            raise OnedeployError("conflict", "Saved profile provider differs from Step 1. Select its provider first; targets are never transplanted.")
                        action = "load-profile"
                    else:
                        result = discovery.discover(args["path"], args["provider"])
                elif action == "load-profile":
                    result = discovery.read_profile(args["path"])
                    discovery.validate_profile(result)
                    if result["manifest"]["provider"] != args["provider"]:
                        raise OnedeployError("conflict", "Saved profile provider differs from Step 1. Select its provider first; targets are never transplanted.")
                elif action == "resolve-project":
                    if args.get("intent") is not None:
                        result = discovery.inspect_intent_resolution(args["intent"], args["discovery"], args["answers"])
                    else:
                        result = discovery.inspect_resolution(args["discovery"], args["answers"])
                elif action == "export-intent":
                    result = discovery.save_intent(discovery.export_intent(args["profile"]), args["path"], source_root=args["profile"]["discovery"]["root"])
                elif action == "import-intent":
                    intent = discovery.read_intent(args["path"])
                    prepared = discovery.prepare_intent_import(intent, args["project_path"])
                    fresh = prepared["discovery"]
                    if fresh["provider"] != args["provider"]:
                        raise OnedeployError("conflict", "Portable intent provider differs from Step 1. Select the intended provider first; target settings are not transplanted.")
                    answers = deepcopy(prepared["answers"])
                    if args["candidate"] not in {item["id"] for item in fresh.get("candidates", [])}:
                        raise OnedeployError("stale_plan", "The selected destination configuration changed. Discover and select it again before importing intent.")
                    answers["candidate"] = args["candidate"]
                    result = {"intent": intent, "discovery": fresh, "suggestions": prepared["answers"], "answers": answers,
                              "resolution": discovery.inspect_intent_resolution(intent, fresh, answers)}
                else:
                    result = discovery.save_profile(args["profile"], args["path"])
            elif action == "review-plan":
                result = core.make_plan_from_profile(args["profile"], env_file=args["env_file"], env_overrides=args["env_overrides"], allow_dirty=args["allow_dirty"], inspect_remote=args["inspect_remote"], plan_output=args["plan_output"])
            elif action == "validate":
                result = config.validate_config(args["profile"]["manifest"], env_file=args["env_file"], env_overrides=args["env_overrides"])
            elif action == "deploy":
                result = core.deploy(args["plan"], approval=args["plan"]["id"], env_file=args["env_file"], env_overrides=args["env_overrides"], state_dir=args["state_dir"], cancel_event=cancel_event, event_callback=self.progress)
            elif action == "doctor":
                result = core.doctor(args["provider"], transport=args.get("transport", "cli"))
            elif action in {"status-button", "advanced-status"}:
                result = core.status(args["deployment"], state_dir=args["state_dir"])
            elif action == "logs":
                result = core.logs(args["deployment"], state_dir=args["state_dir"], follow=False)
            elif action in {"resume", "operator-resolve"}:
                kwargs = {"state_dir": args["state_dir"]}
                if action == "operator-resolve":
                    kwargs.update(resolve_with_observation=args["observation"], outcome=args["outcome"])
                result = core.resume(args["deployment"], **kwargs)
            elif action == "rollback":
                kwargs = {"to": args["to"], "state_dir": args["state_dir"]}
                if args["replacement_name"]:
                    kwargs["replacement_name"] = args["replacement_name"]
                result = core.rollback(args["deployment"], **kwargs)
            elif action == "save-plan":
                result = core.save_plan(args["plan"], args["path"])
            else:
                result = core.read_plan(args["path"])
            self.call_from_thread(self.finished, action, result, revision, cancel_event)
        except Exception as exc:
            self.call_from_thread(self.failed, action, exc)
        finally:
            # Keep runtime values out of long-lived worker argument snapshots.
            args.pop("env_overrides", None)

    def progress(self, event: dict):
        # Only engine-owned phase metadata, never raw provider stdout.
        self.call_from_thread(self.say, f"Deployment phase: {event.get('phase', 'working')}. Cancellation is not remote deletion.")

    async def finished(self, action: str, result: Any, revision: int, cancel_event: Event):
        self.busy = False
        if action == "operator-resolve":
            self._assign("resolution-consent", False)
            self._assign("observation", "")
        stale = revision != self._revision or cancel_event.is_set()
        if action in {"discover", "load-profile", "import-intent", "resolve-project", "review-plan", "load-plan", "resume", "rollback", "operator-resolve"} and stale:
            self.invalidate()
            self.say("Result not adopted because inputs changed or cancellation was requested. No approval is retained.")
            if action in {"resume", "rollback", "operator-resolve"}:
                self.query_one("#advanced", Collapsible).collapsed = False
            return
        if action == "discover":
            self.discovery = result
            self._default_artifact_paths(result.get("root", str(Path(self.initial_path).absolute())))
            candidates = result.get("candidates", [])
            select = self.query_one("#candidate", Select)
            select.set_options([(f"{item.get('format', 'configuration')} · {item.get('service') or item.get('path', item['id'])}", item["id"]) for item in candidates])
            if len(candidates) == 1:
                self._assign("candidate", candidates[0]["id"])
                await self._show_candidate(candidates[0]["id"])
            else:
                self._assign("candidate", Select.NULL)
                self.query_one("#candidate-details", Static).update("Select one exact service/configuration; nothing is chosen implicitly.\n" + json.dumps({key: result.get(key, []) for key in ("conflicts", "unsupported", "warnings")}, indent=2, sort_keys=True))
                await self._render_questions(result)
            self.say("Local discovery complete. Choose the exact configuration and answer only missing or conflicting settings.")
        elif action == "import-intent":
            self.discovery = result["discovery"]
            self._imported_intent = result["intent"]
            self._intent_suggestions = deepcopy(result["suggestions"])
            self.answers = deepcopy(result["answers"])
            self._default_artifact_paths(self.discovery["root"])
            candidates = self.discovery["candidates"]
            self.query_one("#candidate", Select).set_options([(f"{item.get('format')} · {item.get('service') or item['path']}", item["id"]) for item in candidates])
            candidate_id = self.answers["candidate"]
            self._assign("candidate", candidate_id)
            candidate = next(item for item in candidates if item["id"] == candidate_id)
            self._enter_source_scope(candidate.get("root", self.discovery["root"]), candidate_id, candidate.get("manifest", {}))
            self._clear_source_consent()
            self._assign("env-file", "")
            self._assign("inspect-remote", False)
            self._set_profile_choices(candidate, self.answers.get("profile"))
            self.query_one("#candidate-details", Static).update("Portable intent imported as non-executable suggestions. Review current source, confirm target and source trust afresh.\n" + json.dumps({"fresh_manifest": candidate.get("manifest", {}), "suggestions": self.answers, "portable_requirements": self._imported_intent.get("requirements", {}), "questions": result["resolution"]}, indent=2, sort_keys=True))
            await self._render_questions(result["resolution"])
            variable_manifest = deepcopy(candidate.get("manifest", {}))
            variable_manifest.setdefault("variables", {}).update(self.answers.get("variables", {}))
            await self._render_variables(variable_manifest, preserve=False)
            self.profile = None
            self.show_step(2)
            self.say("Portable intent imported, not approved. Runtime inputs and trust were cleared. Complete fresh target confirmations and resolve before reviewing any plan.")
        elif action == "load-profile":
            self.discovery = result["discovery"]
            self._default_artifact_paths(result["root"])
            self.answers = deepcopy(result["resolution"]["answers"])
            candidate_id = result["resolution"]["candidate"]
            self._enter_source_scope(result["root"], candidate_id, result["manifest"])
            candidates = self.discovery["candidates"]
            self.query_one("#candidate", Select).set_options([(f"{item.get('format')} · {item.get('service') or item['path']}", item["id"]) for item in candidates])
            self._assign("candidate", candidate_id)
            self._clear_source_consent()
            self.answers["trust_code"] = False
            self.query_one("#candidate-details", Static).update("Saved source-bound profile loaded. Review settings and confirm source trust afresh.\n" + json.dumps(result["manifest"], indent=2, sort_keys=True))
            candidate = next(item for item in candidates if item["id"] == candidate_id)
            self._set_profile_choices(candidate, result["manifest"]["profile"])
            await self._render_questions(candidate)
            await self._render_variables(result["manifest"], preserve=False)
            self.profile = None
            self.show_step(2)
            self.say("Saved profile verified against current source. Confirm source trust and apply answers before planning; no authorization was loaded.")
        elif action == "resolve-project":
            profile = result.get("profile") if isinstance(result, dict) else None
            if profile:
                self.profile = profile
                self._runtime_scope = self._runtime_identity(profile.get("root", ""), profile["manifest"])
                await self._render_variables(profile["manifest"])
                self.say("Project resolved in memory. Set runtime variables if needed, then review the plan. No project files were changed.")
            else:
                await self._render_questions(result)
                self.say("Resolution needs the labeled inputs or a supported alternative. No settings were silently discarded.")
        elif action in {"review-plan", "load-plan", "resume", "rollback", "operator-resolve"}:
            candidate = result if isinstance(result, dict) and "id" in result and "manifest" in result else result.get("plan") if isinstance(result, dict) else None
            if candidate:
                scope = self._runtime_identity(candidate.get("source", {}).get("root", ""), candidate.get("manifest", {}))
                scope_matches = self._runtime_scope == scope
                if action != "review-plan" and not scope_matches:
                    self._clear_source_consent()
                    self._assign("env-file", "")
                self._runtime_scope = scope
                self.plan = deepcopy(candidate)
                self.review_binding = fingerprint(self.plan)
                self.query_one("#review", Static).update(review_summary(self.plan))
                self.query_one("#plan-json", Static).update(json.dumps(self.plan, indent=2, sort_keys=True))
                if action == "load-plan" or not scope_matches:
                    await self._render_variables(self.plan.get("manifest", {}), preserve=action == "review-plan" or scope_matches)
                self.show_step(3)
                self.say("Review the exact destination, secrets, operations and limits. " + ("Deploy this plan is the explicit approval action." if self.plan.get("remote_preconditions") is not None else "Local preview cannot execute; return to project and opt into remote inspection to deploy."))
            else:
                self._show_result(result)
                self.query_one("#advanced", Collapsible).collapsed = False
                self.say("Reconciliation returned no executable plan. Read the result and use the evidence before any retry.")
        elif action == "doctor":
            self.query_one("#tool-status", Static).update(
                f"Selected provider {result.get('selected_transport', 'cli').upper()} ({result.get('provider', self.provider)}): {result.get('status', 'not checked')}. "
                f"Installed: {bool(result.get('executable_available'))}. Compatible version: {bool(result.get('compatible'))}.\n"
                + str(result.get("next_step", "")) + "\n" +
                "Authentication: not checked. Explicit remote inspection verifies identity before a plan can deploy.\n" + json.dumps(result, indent=2, sort_keys=True))
            self.say("Tool check complete. This is not an authentication or deployment check; offline exploration remains available.")
        elif action == "export-intent":
            self.say("Portable intent saved with private permissions. It contains no runtime values, absolute source paths, source trust or deployment approval; import requires fresh source and target confirmation.")
        elif action in {"save-profile", "save-plan"}:
            self.say("Private export saved. It does not approve a deployment and contains no runtime input values.")
        elif action == "validate":
            self.say("Runtime input validation complete. This does not prove connectivity, authorization or readiness.")
        else:
            if action == "deploy":
                self.invalidate()
            self._show_result(result)
            if action == "deploy" or action in {"status-button", "advanced-status", "logs"}:
                self.show_step(3)
            self.say("Read each reported result layer. Provider acceptance is not application readiness or production qualification.")
        self._refresh_controls()

    def _show_result(self, result: Any):
        # Explicit logs are already best-effort redacted by the shared engine.
        text = json.dumps(result, indent=2, sort_keys=True)
        if isinstance(result, dict):
            if result.get("deployment_id"):
                self.deployment_id = result["deployment_id"]
                self._assign("deployment", self.deployment_id)
            state = result.get("state", result.get("local_state", "unknown"))
            layers = result.get("layers", {})
            layer_lines = [f"{name.replace('_', ' ')}: {'verified' if value is True else 'not verified'}" for name, value in layers.items()]
            text = "Reported state: " + str(state) + "\n" + "\n".join(layer_lines) + "\n\n" + text
            if state in {"uncertain", "cancelled", "failed", "running", "reconciliation_required", "operator_confirmation_required"}:
                self.query_one("#advanced", Collapsible).collapsed = False
        self.query_one("#result", Static).update(text)

    def show_error(self, exc: Exception):
        if isinstance(exc, OnedeployError):
            self.say(exc.to_dict()["message"])
        else:
            self.say("Operation failed. Check inputs and inspect deployment state before retrying. Private input values are not displayed.")

    def failed(self, action: str, exc: Exception):
        self.busy = False
        if action in {"deploy", "review-plan", "load-plan", "resume", "rollback", "operator-resolve"}:
            self.invalidate()
        if action == "deploy" or isinstance(exc, OnedeployError) and (exc.uncertain or exc.mutation_possible):
            self.query_one("#advanced", Collapsible).collapsed = False
            self.show_step(3)
            self.query_one("#result", Static).update("Execution did not return a fully verified result. Use the recorded deployment ID to inspect/reconcile. Do not blindly retry; remote resources and costs may remain.")
        self._refresh_controls()
        self.show_error(exc)

    def action_back(self):
        if self.busy:
            self.action_cancel_operation()
            return
        self.invalidate()
        self.show_step(max(1, self.step - 1))
        self.say("Back. No deployment approval retained.")

    def action_cancel_operation(self):
        if self.busy:
            self.cancel_event.set()
            self.say("Cancellation requested. Waiting for the current operation to settle; remote work and costs may continue. Do not retry yet.")
            return
        self.invalidate()
        for index in range(len(self.variable_keys)):
            self._assign(f"variable-{index}", "")
        self._assign("trust-code", False)
        self.profile = None
        self._refresh_controls()
        self.say("Cancelled. Approval and entered runtime values cleared. Existing resources and recovery records are not deleted.")

    def action_cancel_or_back(self):
        if self.busy:
            self.action_cancel_operation()
        elif self.step > 1:
            self.action_back()
        else:
            self.action_cancel_operation()

    def action_safe_quit(self):
        if self.busy:
            self.action_cancel_operation()
        else:
            self.exit()


WizardApp = DeploymentWizard
