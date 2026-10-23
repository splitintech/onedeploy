"""Shared local planning, bounded execution, approval and recovery engine.

No plan ID authenticates a human. Callers must preserve their own authorization
boundary. Runtime credentials are resolved locally and are never plan fields.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .config import load_manifest, resolve_variables, validate_manifest, schema, secure_path
from .errors import OnedeployError

PLAN_VERSION=1
MAX_OUTPUT=1024*1024
MAX_STATE=16*1024*1024
MAX_FILES=10000
MAX_BUNDLE=512*1024*1024
EXCLUDED={".git",".onedeploy",".venv","venv","__pycache__",".pytest_cache","node_modules"}

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()

def digest(value): return hashlib.sha256(canonical(value)).hexdigest()

def _error(code,message,**kwargs): raise OnedeployError(code,message,**kwargs)

def redact(value, secrets=()):
    """Best effort for explicit logs. Normal results should not collect secrets."""
    if isinstance(value,dict):
        return {k:("[REDACTED]" if re.search(r"(?i)(password|token|authorization|credential|secret|api.?key)",k) else redact(v,secrets)) for k,v in value.items()}
    if isinstance(value,list): return [redact(v,secrets) for v in value]
    if not isinstance(value,str): return value
    for secret in sorted((str(s) for s in secrets if s),key=len,reverse=True):
        value=value.replace(secret,"[REDACTED]")
    value=re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@",r"\1[REDACTED]@",value)
    value=re.sub(r"(?i)([?&](?:token|key|secret|signature|access_token)=)[^&\s]+",r"\1[REDACTED]",value)
    return value

def _no_symlink(path):
    p=Path(path).absolute()
    if any(x.is_symlink() for x in [p,*p.parents]):
        _error("invalid_config","Symlink paths are not accepted")
    return p.resolve()

def _private_dir(path):
    p=_no_symlink(path)
    p.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name=="posix" and stat.S_IMODE(p.stat().st_mode)&0o077:
        _error("invalid_config","State directory must have private permissions (chmod 700)")
    return p

def _atomic(path,data,exclusive=False):
    if len(data)>MAX_STATE: _error("invalid_config","Plan or journal exceeds the state size limit")
    path=_no_symlink(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix=".onedeploy-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(tmp,path)
            except FileExistsError:
                _error("conflict", "Plan output already exists; choose a new path, originals are never overwritten")
        else:
            os.replace(tmp,path)
        if os.name=="posix":
            parent_fd=os.open(path.parent,os.O_RDONLY)
            try: os.fsync(parent_fd)
            finally: os.close(parent_fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _key(root,create=False):
    directory=_private_dir(Path(root)/".onedeploy")
    path=directory/"binding.key"
    if not path.exists() and create:
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
            with os.fdopen(fd,"wb") as f: f.write(os.urandom(32)); f.flush(); os.fsync(f.fileno())
        except FileExistsError: pass
    secure_path(path,private=True)
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    try:
        if os.fstat(fd).st_mode & 0o077: _error("invalid_config","Plan binding key is not private")
        key=os.read(fd,33)
    finally: os.close(fd)
    if len(key)!=32: _error("invalid_config","Invalid local plan binding key")
    return key

def _git(root,args,check=True):
    result=subprocess.run(["git","-C",str(root),*args],shell=False,capture_output=True,timeout=15,
                          env={"PATH":os.defpath,"LANG":"C","GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":os.devnull})
    if check and result.returncode:
        _error("invalid_config","Could not inspect source Git state")
    return result

def inventory(root,allow_dirty=False,exclude_paths=()):
    root=_no_symlink(root)
    if not root.is_dir(): _error("invalid_config","Source path must be a directory")
    exclusions={str(_no_symlink(p)) for p in exclude_paths}
    git=_git(root,["rev-parse","--show-toplevel"],False)
    revision=None
    if git.returncode==0:
        revision=_git(root,["rev-parse","HEAD"]).stdout.decode().strip()
    elif not allow_dirty:
        _error("invalid_config","Source is not a Git checkout; use explicit allow-dirty to bind local files")
    records=[]; total=0
    for parent,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d not in EXCLUDED)
        for directory in dirs:
            if (Path(parent)/directory).is_symlink(): _error("invalid_config","Source directory symlinks are not accepted")
        for name in sorted(files):
            p=Path(parent)/name; rel=p.relative_to(root).as_posix()
            if name in EXCLUDED or name.startswith(".env") or name.endswith((".pem",".key",".p12",".pfx")) or str(p) in exclusions:
                continue
            if name in {"plan.json","recovery-plan.json","rollback-plan.json"}: continue
            if p.is_symlink(): _error("invalid_config","Source file symlinks are not accepted")
            if not p.is_file(): _error("invalid_config","Source contains non-regular files")
            if git.returncode==0 and _git(root,["check-ignore","--quiet","--",rel],False).returncode==0:
                continue
            st=p.stat(); total+=st.st_size
            if len(records)>=MAX_FILES or total>MAX_BUNDLE: _error("invalid_config","Source bundle exceeds safety limits")
            content=p.read_bytes()
            records.append({"path":rel,"size":len(content),"sha256":hashlib.sha256(content).hexdigest(),"executable":bool(st.st_mode&0o111)})
    if not allow_dirty and git.returncode==0:
        # Scope cleanliness to actual build inputs, not unrelated files elsewhere in the checkout.
        for entry in records:
            rel=entry["path"]
            if _git(root,["ls-files","--error-unmatch","--",rel],False).returncode:
                _error("invalid_config","Untracked build inputs require explicit allow-dirty")
            if _git(root,["diff","HEAD","--quiet","--",rel],False).returncode:
                _error("invalid_config","Modified build inputs require explicit allow-dirty")
        # Deleted tracked files affect the upload too.
        if _git(root,["diff","HEAD","--name-only","--diff-filter=D","--","."],False).stdout.strip():
            _error("invalid_config","Deleted build inputs require explicit allow-dirty")
    return {"root":str(root),"revision":revision,"allow_dirty":bool(allow_dirty),"excluded_paths":sorted(exclusions),"files":records,"bundle_sha256":digest(records)}

def _adapter(manifest):
    from .providers import get_adapter
    return get_adapter(manifest["provider"])

def _runtime_manifest(manifest, names):
    effective=dict(manifest)
    effective["variables"]={key:value for key,value in manifest.get("variables",{}).items() if key in names}
    return effective

def _binding(root,values,create=False):
    return hmac.new(_key(root,create),canonical(values),hashlib.sha256).hexdigest()

def _plan_id(plan): return digest({k:v for k,v in plan.items() if k!="id"})

def make_plan(manifest_path,env_file=None,allow_dirty=False,env_overrides=None,inspect_remote=False,plan_output=None):
    manifest_path=secure_path(manifest_path)
    manifest=load_manifest(manifest_path)
    root=_no_symlink(manifest_path.parent/manifest["source"]["path"])
    return _make_plan(manifest, root, str(manifest_path), env_file, allow_dirty,
                      env_overrides, inspect_remote, plan_output)


def make_plan_from_profile(profile, env_file=None, allow_dirty=False, env_overrides=None,
                           inspect_remote=False, plan_output=None):
    """Plan an already resolved profile without writing application configuration.

    Profile evidence is retained in the immutable plan and rechecked before apply.
    No provider inspection occurs unless explicitly requested.
    """
    from . import discovery
    if not isinstance(profile, dict):
        profile = discovery.read_profile(profile)
    profile = json.loads(canonical(profile))
    manifest, root = discovery.validate_profile(profile)
    if plan_output is not None:
        _profile_output_path(profile, root, plan_output)
    plan = _make_plan(manifest, _no_symlink(root), None, env_file, allow_dirty,
                      env_overrides, inspect_remote, plan_output)
    plan["normalized_profile"] = profile
    plan["id"] = _plan_id(plan)
    return plan


def _profile_output_path(profile, root, output):
    """Do not mutate discovery inputs or silently exempt arbitrary source paths."""
    output = _no_symlink(output)
    discovery_root = _no_symlink(profile.get("discovery", {}).get("root", root))
    if output.is_relative_to(discovery_root) and ".onedeploy" not in output.relative_to(discovery_root).parts:
        _error("invalid_config", "Profile plan output must be outside discovered source or inside .onedeploy")
    return output


def _make_plan(manifest, root, manifest_path, env_file=None, allow_dirty=False,
               env_overrides=None, inspect_remote=False, plan_output=None):
    validate_manifest(manifest)
    for section,key in [("build","context"),("build","dockerfile"),("source","entrypoint")]:
        value=manifest.get(section,{}).get(key)
        if value:
            candidate=_no_symlink(root/value)
            if not candidate.is_relative_to(root): _error("invalid_config","Build paths must remain within source")
    variables=resolve_variables(manifest,env_file,env_overrides)
    adapter=_adapter(manifest); effective=_runtime_manifest(manifest,variables); adapter.validate(effective)
    excluded=[path for path in (env_file,plan_output) if path is not None]
    if env_file and plan_output and _no_symlink(env_file)==_no_symlink(plan_output):
        _error("invalid_config","Plan output cannot overwrite the environment input")
    source=inventory(root,allow_dirty,excluded)
    required_inputs=[]
    if manifest["provider"]=="modal":
        required_inputs.append(manifest["source"].get("entrypoint"))
    if manifest["provider"]=="railway" or (manifest["provider"]=="daytona" and manifest.get("build")):
        required_inputs.append(manifest.get("build",{}).get("dockerfile","Dockerfile"))
    bundled={record["path"] for record in source["files"]}
    if any(not item or _no_symlink(root/item).relative_to(root).as_posix() not in bundled for item in required_inputs):
        _error("invalid_config","Required deployment input is missing or excluded from the sanitized bundle")
    if manifest["source"].get("revision") and manifest["source"]["revision"]!=source["revision"]:
        _error("invalid_config","Declared source revision does not match checkout")
    operations=adapter.plan(effective)
    plan={"version":PLAN_VERSION,"kind":"deploy","manifest":manifest,"manifest_path":manifest_path,
          "source":source,"adapter_version":getattr(adapter,"version","1"),"operations":operations,
          "variable_references":{k:v.get("source",f"env:{k}") for k,v in manifest.get("variables",{}).items() if k in variables},
          "config_binding":_binding(root,variables,True),"remote_preconditions":None,
          "warnings":["Plan approval records caller intent, not proof of human consent.",
                      "Application build/startup code is trusted executable code.",
                      "Provider resource costs are not capped by this plan.",
                      "Runtime variables will be transmitted only to the named provider and target."],
          "qualification":"experimental/offline-tested"}
    plan["warnings"].extend(_listener_warnings(manifest))
    if manifest.get("dependencies"):
        plan["warnings"].append("External dependency configuration is validated locally; connectivity, migrations, ownership and authorization are not inferred.")
    if manifest.get("storage"):
        plan["warnings"].append("Only the explicitly named existing storage resources may be attached. Existing data is not deleted or migrated.")
        if any(s["access"]=="read-write" and not s["single_writer"] for s in manifest["storage"]):
            plan["warnings"].append("Shared writable storage is explicitly permitted by this manifest; the application must support concurrent writers.")
    if inspect_remote:
        plan["remote_preconditions"]=_observe(adapter,effective,root)
    else:
        plan["warnings"].append("Local preview only; replan with remote inspection before execution.")
    _set_release_tag(plan,adapter,effective)
    plan["id"]=_plan_id(plan)
    return plan

def _set_release_tag(plan,adapter,effective):
    if effective["provider"]!="modal" or plan["kind"]!="deploy": return
    plan["release_tag"]="od-"+digest({"source":plan["source"]["bundle_sha256"],"configuration":plan["config_binding"],
                                     "target":_target_identity(effective),"preconditions":plan["remote_preconditions"]})[:32]
    plan["operations"]=adapter.plan(effective,release_tag=plan["release_tag"])

def _check_tag_absent(adapter,manifest,tag,cwd):
    if not tag or manifest["target"].get("mode")=="create": return
    history=_execute(adapter,{**adapter.command(manifest,"history"),"operation":"history"},manifest,cwd)
    if any(release.get("tag")==tag for release in history.get("releases",[])):
        _error("stale_plan","This deployment tag already exists; reconcile instead of submitting it again")

def save_plan(plan,path):
    verify_plan(plan)
    output=_no_symlink(path)
    root=Path(plan["source"]["root"])
    if output.is_relative_to(root) and str(output) not in plan["source"].get("excluded_paths",[]) and output.name not in {"plan.json","recovery-plan.json","rollback-plan.json"} and ".onedeploy" not in output.relative_to(root).parts:
        _error("invalid_config","Plan output inside source must be selected at planning time")
    if "normalized_profile" in plan:
        _profile_output_path(plan["normalized_profile"], root, output)
    _atomic(path,canonical(plan)+b"\n",exclusive="normalized_profile" in plan)
    return {"id":plan["id"],"path":str(Path(path).absolute())}

def read_plan(path):
    p=secure_path(path,max_size=MAX_STATE)
    try: plan=json.loads(p.read_text())
    except (ValueError,UnicodeError): _error("invalid_config","Invalid saved plan JSON")
    verify_plan(plan)
    return plan

def verify_plan(plan):
    if not isinstance(plan,dict) or plan.get("version")!=PLAN_VERSION or plan.get("id")!=_plan_id(plan):
        _error("stale_plan","Plan contents do not match the canonical plan ID")
    required={"version","kind","manifest","manifest_path","source","adapter_version","operations","variable_references","config_binding","remote_preconditions","warnings","qualification","id"}
    if set(plan)-required-{"rollback_to","rollback_from","recovery_of","resolution_of","resolution_observation","release_tag","rollback_artifact","artifact_receipt","normalized_profile"} or required-set(plan): _error("invalid_config","Unknown or missing plan fields")
    validate_manifest(plan["manifest"])
    if plan["kind"] not in {"deploy","rollback"}: _error("unsupported","This plan kind cannot be executed")
    if plan["kind"]=="rollback" and (not isinstance(plan.get("rollback_to"),str) or not plan.get("rollback_from")):
        _error("invalid_config","Rollback plan lacks prior release evidence")
    return plan

class CommandRunner:
    """Central transport. Bounded output, no shell, explicit environment only."""
    def run(self,step,*,cwd,variables=None,timeout=120,cancel_event=None,raw_output=False):
        variables=variables or {}
        argv=step.get("argv")
        if not isinstance(argv,list) or not argv or not all(isinstance(a,str) and "\x00" not in a for a in argv):
            _error("invalid_config","Invalid provider command arguments")
        allowed={"railway","daytona","modal"}
        if argv[0]==sys.executable:
            bridge=Path(__file__).parent/"providers"/"daytona_bridge.py"
            if len(argv)<4 or argv[1]!="-I" or Path(argv[2]).absolute()!=bridge.absolute():
                _error("invalid_config","Untrusted Python command rejected")
        elif argv[0] not in allowed:
            _error("invalid_config","Untrusted provider executable rejected")
        env={"PATH":os.environ.get("PATH",os.defpath),"LANG":"C.UTF-8","LC_ALL":"C.UTF-8"}
        # HOME is needed for explicitly existing CLI authentication, never application forwarding.
        if "HOME" in os.environ: env["HOME"]=os.environ["HOME"]
        keys=step.get("env_keys",[])
        control={"RAILWAY_TOKEN","RAILWAY_API_TOKEN","DAYTONA_API_KEY","MODAL_TOKEN_ID","MODAL_TOKEN_SECRET"}
        if any(k not in control for k in keys): _error("invalid_config","Provider requested an unapproved environment variable")
        for key in keys:
            if key in os.environ: env[key]=os.environ[key]
        runtime_keys=step.get("runtime_env_keys",[])
        reserved=("RAILWAY_","DAYTONA_","MODAL_","PYTHON","LD_","DYLD_")
        for key in runtime_keys:
            if key not in variables or key.startswith(reserved) or key in {"PATH","HOME","SHELL","BASH_ENV","ENV"}:
                _error("invalid_config","Unsafe or unresolved runtime environment variable")
            env[key]=variables[key]
        selected={key:variables[key] for key in step.get("stdin_env_keys",[]) if key in variables}
        if step.get("stdin_env_keys") and set(selected)!=set(step["stdin_env_keys"]):
            _error("invalid_config","A required runtime variable is missing")
        stdin=None
        if selected:
            if step.get("stdin_format")=="json": stdin=canonical(selected)
            elif step.get("stdin_format")=="raw":
                if len(selected)!=1: _error("invalid_config","Raw stdin requires exactly one variable")
                stdin=next(iter(selected.values())).encode()
            else:
                if any("\n" in value or "\r" in value for value in selected.values()):
                    _error("unsupported","This provider stdin transport does not support multiline variables")
                stdin="\n".join(f"{k}={v}" for k,v in selected.items()).encode()+b"\n"
        mutating=bool(step.get("mutating"))
        try:
            proc=subprocess.Popen(argv,shell=False,cwd=str(cwd),env=env,stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=(os.name=="posix"))
        except (OSError,ValueError): _error("provider_failed","Provider executable could not be started")
        buffers={"stdout":bytearray(),"stderr":bytearray()}; overflow=threading.Event()
        def collect(pipe,label):
            try:
                while True:
                    block=pipe.read(4096)
                    if not block: break
                    remaining=MAX_OUTPUT-len(buffers[label])
                    buffers[label].extend(block[:max(remaining,0)])
                    if len(block)>remaining: overflow.set(); break
            finally: pipe.close()
        threads=[threading.Thread(target=collect,args=(proc.stdout,"stdout"),daemon=True),threading.Thread(target=collect,args=(proc.stderr,"stderr"),daemon=True)]
        for t in threads: t.start()
        def feed():
            try:
                if stdin: proc.stdin.write(stdin); proc.stdin.flush()
            except (BrokenPipeError,OSError): pass
            finally: proc.stdin.close()
        feeder=threading.Thread(target=feed,daemon=True); feeder.start()
        deadline=time.monotonic()+timeout; reason=None
        try:
            while proc.poll() is None:
                if overflow.is_set(): reason="Provider output exceeded the safety limit"; break
                if cancel_event is not None and cancel_event.is_set(): reason="Cancellation requested during provider operation"; break
                if time.monotonic()>deadline: reason="Provider operation timed out"; break
                time.sleep(0.02)
        except BaseException:
            if os.name=="posix":
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            else: proc.kill()
            proc.wait(timeout=5)
            for t in threads: t.join(timeout=5)
            _error("uncertain" if mutating else "cancelled","Provider process interrupted; remote effects may remain",uncertain=mutating,mutation_possible=mutating)
        if reason:
            if os.name=="posix":
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            else: proc.kill()
            proc.wait(timeout=5)
        for t in threads: t.join(timeout=5)
        if reason or overflow.is_set():
            _error("uncertain" if mutating else "provider_failed",reason or "Provider output exceeded safety limit",
                   uncertain=mutating,mutation_possible=mutating,retryable=not mutating)
        secrets=list(variables.values())+[env[k] for k in control if k in env]
        if proc.returncode:
            # A nonzero result is not proof a provider write had no effects.
            _error("uncertain" if mutating else "provider_failed","Provider command did not complete successfully",
                   uncertain=mutating,mutation_possible=mutating,retryable=not mutating)
        stdout=buffers["stdout"].decode("utf-8",errors="replace")
        stderr=buffers["stderr"].decode("utf-8",errors="replace")
        return {"stdout":stdout if raw_output else redact(stdout,secrets),
                "stderr":stderr if raw_output else redact(stderr,secrets),"returncode":0}

runner=CommandRunner()

def _execute(adapter,step,manifest,cwd,variables=None,cancel_event=None):
    result=runner.run(step,cwd=cwd,variables=variables,timeout=manifest.get("runtime",{}).get("timeout",120),cancel_event=cancel_event,raw_output=True)
    operation=step.get("operation",step.get("id","deploy"))
    if getattr(adapter,"requires_binding_key",False) and operation in {"identity","inspect","status","reconcile"}:
        parsed=adapter.parse(operation,result["stdout"],binding_key=_key(cwd))
    else:
        parsed=adapter.parse(operation,result["stdout"])
    secrets=[value for name,value in (variables or {}).items() if manifest.get("variables",{}).get(name,{}).get("secret",True)]
    secrets += [os.environ[name] for name in step.get("env_keys",[]) if name in os.environ]
    return redact(parsed,secrets)

def _observe(adapter,manifest,cwd):
    transport=_check_version(adapter,cwd)
    evidence={}
    for operation in ("identity","inspect"):
        step=adapter.command(manifest,operation)
        if step.get("mutating"): _error("invalid_config","Inspection command unexpectedly mutates state")
        evidence[operation]=_execute(adapter,{**step,"operation":operation},manifest,cwd)
    normalized=adapter.assert_target(manifest,evidence["identity"],evidence["inspect"])
    result=normalized if normalized is not None else evidence
    if transport is not None: result["transport"]=transport
    return result

def _assert_preconditions(adapter,manifest,planned,observed,completed):
    if hasattr(adapter,"assert_preconditions"):
        adapter.assert_preconditions(manifest,planned,observed,completed)
    elif planned!=observed:
        _error("stale_plan","Provider account, target, or remote generation changed; replan")

@contextmanager
def _deployment_locks(directory,plan):
    keys={_target_lock_key(plan)}
    for storage in plan["manifest"].get("storage",[]):
        if storage["access"]=="read-write" and storage["single_writer"]:
            keys.add(digest({"volume":storage["resource_id"],"account":plan["manifest"]["target"]["account"],"provider":plan["manifest"]["provider"]}))
    with ExitStack() as stack:
        for key in sorted(keys): stack.enter_context(_lock(directory,key))
        yield

def _check_version(adapter,cwd):
    if not hasattr(adapter,"version_command"): return None
    step=adapter.version_command()
    if step.get("mutating"): _error("invalid_config","Version inspection cannot mutate state")
    result=runner.run(step,cwd=cwd,timeout=15)
    return adapter.parse_version(result["stdout"])

@contextmanager
def _lock(state_dir,deployment_id):
    directory=_private_dir(state_dir)
    path=directory/f"{deployment_id}.lock"
    _no_symlink(path)
    fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,"O_NOFOLLOW",0),0o600)
    try:
        if os.name!="posix": _error("unsupported","Execution locking currently requires a POSIX platform")
        import fcntl
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: _error("locked","Another local process holds this deployment lock")
        yield
    finally: os.close(fd)

def _state_dir(plan=None,state_dir=None):
    if state_dir: return _private_dir(state_dir)
    return _private_dir(Path.home()/".local"/"state"/"onedeploy")

def _journal_path(state_dir,deployment_id):
    if not re.fullmatch(r"[a-f0-9]{64}",deployment_id): _error("invalid_config","Invalid deployment identifier")
    return Path(state_dir)/f"{deployment_id}.json"

def _read_journal(path):
    p=secure_path(path,private=True,max_size=MAX_STATE)
    try: result=json.loads(p.read_text())
    except (ValueError,UnicodeError): _error("invalid_config","Invalid deployment journal")
    if not isinstance(result,dict): _error("invalid_config","Invalid deployment journal")
    return result

@contextmanager
def _bundle(source):
    with tempfile.TemporaryDirectory(prefix="onedeploy-bundle-") as directory:
        root=Path(source["root"]); dest=Path(directory)
        for record in source["files"]:
            relative=Path(record["path"])
            if relative.is_absolute() or ".." in relative.parts: _error("invalid_config","Invalid source bundle path")
            source_file=_no_symlink(root/relative)
            content=source_file.read_bytes()
            if hashlib.sha256(content).hexdigest()!=record["sha256"]:
                _error("stale_plan","Source changed while preparing upload bundle")
            output=dest/relative; output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(content); output.chmod(0o700 if record["executable"] else 0o600)
        yield dest

def _retain_bundle(plan,directory,bundle):
    archive_root=_private_dir(Path(directory)/"artifacts")
    destination=_no_symlink(archive_root/plan["id"])
    if not destination.exists():
        temporary=Path(tempfile.mkdtemp(prefix=".retain-",dir=archive_root))
        try:
            source=temporary/"source"; source.mkdir(mode=0o700)
            for record in plan["source"]["files"]:
                relative=Path(record["path"])
                output=source/relative; output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                data=(Path(bundle)/relative).read_bytes()
                if hashlib.sha256(data).hexdigest()!=record["sha256"]:
                    _error("stale_plan","Source changed before retaining the reviewed bundle")
                output.write_bytes(data); output.chmod(0o700 if record["executable"] else 0o600)
            metadata=_private_dir(source/".onedeploy")
            _atomic(metadata/"binding.key",_key(plan["source"]["root"]))
            os.replace(temporary,destination)
        finally:
            if temporary.exists(): shutil.rmtree(temporary)
    retained=inventory(destination/"source",True)
    if retained["bundle_sha256"]!=plan["source"]["bundle_sha256"]:
        _error("stale_plan","Retained release bundle does not match the reviewed source")
    return retained

def deploy(plan,env_file=None,approval=None,state_dir=None,env_overrides=None,cancel_event=None,event_callback=None):
    if not isinstance(plan,dict): plan=read_plan(plan)
    verify_plan(plan)
    if approval!=plan["id"]: _error("approval_required","Explicit approval of this exact plan is required")
    if plan["remote_preconditions"] is None:
        _error("stale_plan","Local-only preview cannot execute; regenerate with remote inspection")
    if "normalized_profile" in plan and plan["kind"] == "deploy":
        from . import discovery
        profile_manifest, profile_root = discovery.validate_profile(plan["normalized_profile"])
        resolved_recovery = False
        if plan.get("resolution_of"):
            parent = _read_journal(_journal_path(_state_dir(plan, state_dir), plan["resolution_of"]))
            if (parent.get("state") != "operator_resolved"
                or parent.get("recovery_plan_id") != plan["id"]
                or parent.get("operator_resolution", {}).get("observation_id") != plan.get("resolution_observation")
                or parent.get("plan", {}).get("normalized_profile") != plan["normalized_profile"]):
                _error("stale_plan", "Profile recovery is not bound to the recorded resolution plan")
            resolved_recovery = True
        if plan.get("artifact_receipt") and not (resolved_recovery and plan["manifest"]["provider"] == "daytona"):
            _error("stale_plan", "Snapshot receipt is not a journal-bound Daytona recovery")
        if not resolved_recovery and profile_manifest != plan["manifest"]:
            _error("stale_plan", "Normalized profile differs from the reviewed configuration")
        if str(profile_root) != plan["source"]["root"]:
            _error("stale_plan", "Normalized profile source differs from the reviewed bundle")
    manifest=plan["manifest"]
    variables=resolve_variables(manifest,env_file,env_overrides)
    effective=_runtime_manifest(manifest,variables)
    adapter=_adapter(manifest); adapter.validate(effective)
    if plan.get("artifact_receipt") and manifest["provider"]=="daytona":
        expected_operations=adapter.rollback_steps(effective,plan.get("resolution_of","recovery"),artifact=plan["artifact_receipt"])
    elif plan["kind"]=="rollback":
        if manifest["provider"] in {"railway","daytona"}:
            expected_operations=adapter.rollback_steps(effective,plan["rollback_to"],artifact=plan.get("rollback_artifact"))
        else:
            expected_operations=adapter.rollback_steps(effective,plan["rollback_to"])
    elif manifest["provider"]=="modal":
        expected_operations=adapter.plan(effective,release_tag=plan.get("release_tag"))
    else:
        expected_operations=adapter.plan(effective)
    if getattr(adapter,"version","1")!=plan["adapter_version"] or expected_operations!=plan["operations"]:
        _error("stale_plan","Provider adapter or operation construction changed")
    root=plan["source"]["root"]
    if not hmac.compare_digest(_binding(root,variables),plan["config_binding"]):
        _error("stale_plan","Runtime configuration changed; generate and approve a new plan")
    actual=inventory(root,plan["source"]["allow_dirty"],plan["source"].get("excluded_paths",[]))
    if actual!=plan["source"]: _error("stale_plan","Source bundle changed; generate and approve a new plan")
    directory=_state_dir(plan,state_dir); deployment_id=plan["id"]; path=_journal_path(directory,deployment_id)
    target_lock=_target_lock_key(plan)
    with _deployment_locks(directory,plan):
        if path.exists():
            prior=_read_journal(path)
            if prior.get("state")=="succeeded": return prior
            _error("uncertain","Existing operation must be reconciled before any new execution",uncertain=True,mutation_possible=True)
        for candidate_path in directory.glob("*.json"):
            candidate=_read_journal(candidate_path)
            other=candidate.get("plan",{})
            if (candidate.get("deployment_id")!=deployment_id and other.get("manifest",{}).get("provider")==manifest["provider"]
                and _same_target(other,plan,candidate)
                and candidate.get("state") in {"running","uncertain"}):
                _error("uncertain","An unresolved earlier deployment targets this resource; reconcile it before any new write",uncertain=True,mutation_possible=True)
        if plan.get("resolution_of"):
            resolved=_read_journal(_journal_path(directory,plan["resolution_of"]))
            if resolved.get("state")!="operator_resolved" or resolved.get("operator_resolution",{}).get("observation_id")!=plan.get("resolution_observation"):
                _error("stale_plan","Operator resolution record does not match this recovery plan")
        recovery_parent=None
        if plan.get("recovery_of"):
            recovery_parent=_read_journal(_journal_path(directory,plan["recovery_of"]))
            if recovery_parent.get("operations") or recovery_parent.get("state") not in {"planned","cancelled","recovery_planned"}:
                _error("uncertain","Recovery parent does not prove that no operation began",uncertain=True,mutation_possible=True)
            if recovery_parent.get("recovery_deployment_id") not in (None,deployment_id):
                _error("stale_plan","Another recovery plan already owns this interrupted deployment")
        observed=_observe(adapter,effective,root)
        _assert_preconditions(adapter,effective,plan["remote_preconditions"],observed,[])
        if plan.get("release_tag") and plan["kind"]=="deploy":
            _check_tag_absent(adapter,effective,plan["release_tag"],root)
        journal={"version":1,"deployment_id":deployment_id,"plan":plan,"state":"planned","operations":[],
                 "layers":{"provider_accepted":False,"deployment_observed":False,"process_running":False,"readiness_passed":False,"ingress_verified":False,"application_checks_passed":False,"application_auth_verified":False,"application_qualified":False}}
        _atomic(path,canonical(journal))
        if recovery_parent is not None:
            recovery_parent["state"]="recovery_planned"; recovery_parent["recovery_deployment_id"]=deployment_id
            _atomic(_journal_path(directory,plan["recovery_of"]),canonical(recovery_parent))
        with _bundle(plan["source"]) as bundle:
            journal["retained_source"]=_retain_bundle(plan,directory,bundle)
            _atomic(path,canonical(journal))
            for index,step in enumerate(plan["operations"]):
                if cancel_event is not None and cancel_event.is_set():
                    journal["state"]="cancelled"; _atomic(path,canonical(journal))
                    _error("cancelled","Cancelled before the next operation; existing resources may remain",mutation_possible=index>0)
                if step.get("mutating"):
                    try:
                        _assert_preconditions(adapter,effective,plan["remote_preconditions"],_observe(adapter,effective,root),journal["operations"])
                    except OnedeployError:
                        journal["state"]="failed"; _atomic(path,canonical(journal)); raise
                executable_step=adapter.materialize_step(step,journal["operations"]) if hasattr(adapter,"materialize_step") else step
                if executable_step.get("id")!=step["id"] or executable_step.get("mutating")!=step.get("mutating"):
                    _error("invalid_config","Materialized operation changed its approved identity")
                if plan.get("release_tag") and plan["kind"]=="deploy":
                    _check_tag_absent(adapter,effective,plan["release_tag"],root)
                operation={"operation_id":digest({"plan":deployment_id,"index":index}),"id":step["id"],"state":"running"}
                journal["operations"].append(operation); journal["state"]="running"
                _atomic(path,canonical(journal)) # Write-ahead, before any provider request.
                if event_callback: event_callback({"phase":step["id"],"deployment_id":deployment_id,"operation_id":operation["operation_id"],"state":"running"})
                try:
                    operation_cwd=root if step.get("cwd_role","bundle")=="source" else bundle
                    result=_execute(adapter,executable_step,manifest,operation_cwd,variables,cancel_event)
                except BaseException as exc:
                    uncertain=step.get("mutating",False)
                    operation["state"]="uncertain" if uncertain else "failed"; journal["state"]=operation["state"]
                    _atomic(path,canonical(journal))
                    if isinstance(exc,OnedeployError):
                        if uncertain and not exc.uncertain:
                            _error("uncertain","Provider operation outcome is uncertain; reconcile before retrying",uncertain=True,mutation_possible=True)
                        raise
                    _error("uncertain" if uncertain else "provider_failed","Operation interrupted; reconcile before retrying",uncertain=uncertain,mutation_possible=uncertain)
                operation["state"]="succeeded"; operation["result"]=result
                _atomic(path,canonical(journal))
                if event_callback: event_callback({"phase":step["id"],"deployment_id":deployment_id,"operation_id":operation["operation_id"],"state":"succeeded"})
        journal["state"]="succeeded"; journal["layers"]["provider_accepted"]=True
        try:
            deadline=time.monotonic()+manifest.get("runtime",{}).get("timeout",120)
            while True:
                observed=_execute(adapter,{**adapter.command(effective,"status"),"operation":"status"},manifest,root)
                journal["remote_release"]=observed
                if manifest["provider"]=="modal" and plan.get("release_tag") and plan["kind"]=="deploy":
                    history=_execute(adapter,{**adapter.command(effective,"history"),"operation":"history"},manifest,root)
                    correlated=adapter.correlate_release(history,plan["release_tag"])
                    if correlated:
                        journal["operations"][-1]["result"]["accepted_release_id"]=correlated["release_version"]
                        journal["operations"][-1]["result"]["release_tag"]=plan["release_tag"]
                status_value=_process_state(manifest,observed)
                bound_generation=_new_generation_observed(plan,observed,journal["operations"])
                journal["layers"]["deployment_observed"]=bound_generation
                journal["layers"]["process_running"]=bound_generation and status_value in {"started","running","deployed","success"}
                generation_supported=(manifest["provider"]=="railway" and "services" in observed) or (manifest["provider"]=="modal" and "app_id" in observed)
                release_binding_available=any(op.get("result",{}).get("accepted_release_id") or op.get("result",{}).get("deployment_id") for op in journal["operations"])
                if manifest["provider"]=="modal" and not release_binding_available and not (plan.get("release_tag") and plan["kind"]=="deploy"):
                    generation_supported=False
                if status_value in {"failed","crashed","error","removed","stopped"}:
                    break
                if status_value not in {"starting","pending","building","deploying","queued","initializing"} and (bound_generation or not generation_supported):
                    break
                if time.monotonic()>=deadline or (cancel_event is not None and cancel_event.is_set()):
                    break
                time.sleep(min(2,max(0,deadline-time.monotonic())))
            runtime=manifest.get("runtime",{})
            if journal["layers"]["deployment_observed"] and manifest["provider"]=="daytona" and runtime.get("port") and (runtime.get("health_path") or runtime.get("ready_path")):
                verification=_execute(adapter,{**adapter.command(effective,"verify"),"operation":"verify"},manifest,root,cancel_event=cancel_event)
                journal["verification"]=verification
                journal["layers"]["process_running"]=bool(verification.get("process_running"))
                journal["layers"]["readiness_passed"]=bool(verification.get("readiness_verified"))
            elif manifest["provider"]!="daytona" and journal["layers"]["deployment_observed"] and (runtime.get("health_path") or runtime.get("ready_path") or manifest.get("checks")):
                from .verification import verify_http
                if manifest["provider"]=="modal" and manifest["target"].get("function"):
                    function_info=_execute(adapter,{**adapter.command(effective,"endpoint"),"operation":"endpoint"},manifest,root)
                    refreshed=_execute(adapter,{**adapter.command(effective,"status"),"operation":"status"},manifest,root)
                    if _generation(manifest,refreshed)!=_generation(manifest,observed):
                        _error("stale_plan","Deployment generation changed before endpoint verification")
                    observed=dict(refreshed); observed["function_info"]=function_info
                endpoint=adapter.readiness_endpoint(manifest,observed) if hasattr(adapter,"readiness_endpoint") else None
                verification=verify_http(manifest["provider"],[endpoint] if endpoint else [],runtime.get("health_path"),runtime.get("ready_path"))
                journal["verification"]=verification
                for layer in ("process_running","readiness_passed"):
                    journal["layers"][layer]=journal["layers"][layer] or verification[layer]
                # Public liveness alone does not establish the intended authentication policy.
                if manifest.get("checks"):
                    from .verification import verify_checks
                    checked=verify_checks(manifest["provider"],[endpoint] if endpoint else [],manifest["checks"],variables,cancel_event=cancel_event)
                    journal["application_checks"]=checked
                    journal["layers"]["application_checks_passed"]=bool(checked.get("application_checks_passed"))
                    journal["layers"]["ingress_verified"]=bool(checked.get("ingress_verified"))
            if journal["layers"]["deployment_observed"] and manifest["provider"]=="daytona" and manifest.get("checks"):
                if cancel_event is not None and cancel_event.is_set():
                    checked={"application_checks_passed":False,"ingress_verified":False,"checks":[],"reason":"cancelled"}
                else:
                    checked=_execute(adapter,{**adapter.command(effective,"verify-checks"),"operation":"verify-checks"},manifest,root,variables,cancel_event)
                checked["local_route_auth_verified"]=bool(checked.pop("ingress_verified",False))
                checked["scope"]="sandbox-localhost"
                journal["application_checks"]=checked
                journal["layers"]["application_checks_passed"]=bool(checked.get("application_checks_passed"))
                journal["layers"]["application_auth_verified"]=checked["local_route_auth_verified"]
        except OnedeployError as exc:
            journal["verification_error"]={"code":exc.code,"message":"Provider accepted the request; subsequent verification was unavailable"}
        journal["warnings"]=["Provider acceptance is not application readiness or production qualification."]
        if not journal["layers"]["deployment_observed"]:
            journal["warnings"].append("The newly requested deployment generation has not been observed; readiness remains unverified.")
        _atomic(path,canonical(journal))
        if recovery_parent is not None:
            recovery_parent["state"]="recovered"
            _atomic(_journal_path(directory,plan["recovery_of"]),canonical(recovery_parent))
        return journal

def status(deployment_id,state_dir=None):
    directory=_state_dir(state_dir=state_dir)
    journal=_read_journal(_journal_path(directory,deployment_id))
    adapter=_adapter(journal["plan"]["manifest"])
    manifest=journal["plan"]["manifest"]
    step=adapter.command(_runtime_manifest(manifest,journal["plan"]["variable_references"]),"status")
    if step.get("mutating"): _error("invalid_config","Status cannot mutate provider state")
    return {"deployment_id":deployment_id,"local_state":journal["state"],"remote":_execute(adapter,{**step,"operation":"status"},manifest,journal["plan"]["source"]["root"]),"layers":journal["layers"]}

def resume(deployment_id,state_dir=None,resolve_with_observation=None,outcome=None):
    directory=_state_dir(state_dir=state_dir)
    initial=_read_journal(_journal_path(directory,deployment_id))
    manifest=initial["plan"]["manifest"]
    target_lock=_target_lock_key(initial["plan"])
    with _lock(directory,target_lock):
        journal=_read_journal(_journal_path(directory,deployment_id))
        return _resume_locked(deployment_id,directory,journal,resolve_with_observation,outcome)

def _resume_locked(deployment_id,directory,journal,resolve_with_observation,outcome):
    plan=journal["plan"]; adapter=_adapter(plan["manifest"])
    manifest=_runtime_manifest(plan["manifest"],plan["variable_references"])
    if bool(resolve_with_observation)!=bool(outcome):
        _error("invalid_config","Operator resolution requires both the observation ID and an explicit outcome")
    if journal.get("state") in {"succeeded","recovered"}:
        return {"deployment_id":deployment_id,"state":"completed","mutation_performed":False,"plan":None,"next_actions":[]}
    if not journal.get("operations") and journal.get("state") in {"planned","cancelled","recovery_planned"}:
        if journal.get("recovery_deployment_id"):
            child=_read_journal(_journal_path(directory,journal["recovery_deployment_id"]))
            return {"deployment_id":deployment_id,"state":child["state"],"mutation_performed":False,"plan":None,
                    "next_actions":["Inspect the existing recovery deployment instead of creating another."]}
        recovery=json.loads(json.dumps(plan)); recovery["recovery_of"]=deployment_id
        recovery["remote_preconditions"]=_observe(adapter,manifest,plan["source"]["root"])
        _set_release_tag(recovery,adapter,manifest)
        recovery["id"]=_plan_id(recovery)
        if plan.get("normalized_profile") and plan.get("resolution_of"):
            # A cancelled, no-write recovery may itself be resumed. Advance the
            # original resolution's exact child binding under the target lock,
            # without granting an arbitrary rehashed plan a profile exemption.
            origin_path = _journal_path(directory, plan["resolution_of"])
            origin = _read_journal(origin_path)
            if (origin.get("state") != "operator_resolved"
                or origin.get("recovery_plan_id") not in {plan["id"], recovery["id"]}
                or origin.get("operator_resolution", {}).get("observation_id") != plan.get("resolution_observation")
                or origin.get("plan", {}).get("normalized_profile") != plan["normalized_profile"]):
                _error("stale_plan", "Profile recovery is not bound to its operator-resolution journal")
            origin["recovery_plan_id"] = recovery["id"]
            _atomic(origin_path, canonical(origin))
        return {"deployment_id":deployment_id,"state":"approval_required","mutation_performed":False,"plan":recovery,
                "next_actions":["No provider operation began. Review and approve this separate recovery plan."]}
    root=plan["source"]["root"]
    transport=_check_version(adapter,root)
    identity=_execute(adapter,{**adapter.command(manifest,"identity"),"operation":"identity"},manifest,root)
    step=adapter.command(manifest,"reconcile")
    if step.get("mutating"): _error("invalid_config","Reconciliation cannot mutate provider state")
    observed=_execute(adapter,{**step,"operation":"reconcile"},manifest,root)
    target_manifest=json.loads(json.dumps(manifest))
    if target_manifest["target"].get("mode")=="create" and observed.get("exists") is not False:
        target_manifest["target"]["mode"]="existing"
    evidence=adapter.assert_target(target_manifest,identity,observed)
    if evidence is None: evidence={"identity":identity,"inspection":observed}
    if transport is not None: evidence["transport"]=transport
    observation_id=digest(evidence)
    if resolve_with_observation:
        if outcome not in {"operator-confirmed-completed","operator-confirmed-not-executed"}:
            _error("invalid_config","Unknown operator resolution outcome")
        if resolve_with_observation!=observation_id:
            _error("stale_plan","Fresh provider observation differs from the reviewed observation")
        journal["state"]="operator_resolved"
        journal["operator_resolution"]={"observation_id":observation_id,"outcome":outcome,"time":int(time.time()),
                                       "verified_by_tool":False,"warning":"Explicit operator assertion, not independent proof of outcome or human consent"}
        _atomic(_journal_path(directory,deployment_id),canonical(journal))
        recovery=json.loads(json.dumps(plan))
        recovery.pop("recovery_of",None)
        recovery["resolution_of"]=deployment_id; recovery["resolution_observation"]=observation_id
        recovery["manifest"]["target"]=target_manifest["target"]
        if recovery["manifest"]["provider"]=="daytona" and target_manifest["target"].get("mode")=="existing" and manifest.get("variables"):
            # Existing sandbox environment cannot be reset; do not propose a false equivalent retry.
            recovery=None
        else:
            receipt=None
            if recovery["manifest"]["provider"]=="daytona" and recovery["manifest"].get("build"):
                receipts=[op.get("result",{}) for op in journal.get("operations",[]) if op.get("id")=="snapshot-create" and op.get("state")=="succeeded"]
                if (observed.get("exists") is False and len(receipts)==1
                    and receipts[0].get("snapshot_id")==observed.get("snapshot_id")
                    and receipts[0].get("snapshot_image")==observed.get("snapshot_image")):
                    receipt={key:receipts[0][key] for key in ("snapshot_id","snapshot_image","account") if key in receipts[0]}
                    recovery["manifest"].pop("build")
                    recovery["manifest"]["target"]["snapshot"]=receipt["snapshot_id"]
                    recovery["artifact_receipt"]=receipt
                else:
                    return {"deployment_id":deployment_id,"state":"operator_resolved","observed":observed,"observation_id":observation_id,
                            "mutation_performed":False,"plan":None,"next_actions":["A matching completed snapshot and absent sandbox were not proven. Inspect the retained artifacts and create a separate reviewed plan; no build or create was retried."]}
            effective=_runtime_manifest(recovery["manifest"],recovery["variable_references"])
            recovery["operations"]=adapter.rollback_steps(effective,deployment_id,artifact=receipt) if receipt else adapter.plan(effective)
            recovery["remote_preconditions"]=_observe(adapter,effective,root)
            _set_release_tag(recovery,adapter,effective)
            recovery["warnings"].append("Earlier outcome was resolved by operator assertion; this is a separate proposed execution, not an automatic retry.")
            recovery["id"]=_plan_id(recovery)
            journal["recovery_plan_id"] = recovery["id"]
            _atomic(_journal_path(directory,deployment_id),canonical(journal))
        return {"deployment_id":deployment_id,"state":"operator_resolved","observed":observed,"observation_id":observation_id,
                "mutation_performed":False,"plan":recovery,"next_actions":["No provider action was retried. Review a new plan before any further execution."]}
    return {"deployment_id":deployment_id,"state":"reconciliation_required","observed":observed,"observation_id":observation_id,
            "mutation_performed":False,"plan":None,
            "next_actions":["Review the fresh provider evidence; do not blindly retry.",
                            "An authorized operator may explicitly attest the outcome using this observation ID. The attestation is not independently verified."]}

def _process_state(manifest,observed):
    if manifest["provider"]=="modal":
        return str(observed.get("lifecycle",{}).get("state",observed.get("state",""))).lower()
    if manifest["provider"]=="railway":
        target=manifest["target"]
        for service in observed.get("services",[]):
            if service.get("id")!=target.get("service"): continue
            for instance in service.get("instances",[]):
                if instance.get("environmentId")==target.get("environment"):
                    return str(instance.get("deployment",{}).get("status","")).lower()
    return str(observed.get("state",observed.get("status",""))).lower()

def _generation(manifest,observed):
    if manifest["provider"]=="modal": return _release_version(observed)
    if manifest["provider"]=="railway":
        target=manifest["target"]
        for service in observed.get("services",[]):
            if service.get("id")!=target.get("service"): continue
            for instance in service.get("instances",[]):
                if instance.get("environmentId")==target.get("environment"):
                    return instance.get("deployment",{}).get("id")
    if manifest["provider"]=="daytona": return observed.get("id")
    return None

def _new_generation_observed(plan,observed,operations=()):
    manifest=plan["manifest"]
    current=_generation(manifest,observed)
    if not current: return False
    accepted={op.get("result",{}).get("accepted_release_id") or op.get("result",{}).get("deployment_id") for op in operations if op.get("state")=="succeeded"}
    accepted.discard(None)
    if manifest["provider"]=="daytona":
        accepted.update(op.get("result",{}).get("id") for op in operations if op.get("id") in {"create","start"} and op.get("state")=="succeeded")
        return current in accepted and _process_state(manifest,observed)=="started"
    return current in accepted

def _release_version(observation):
    version=observation.get("version",observation.get("lifecycle",{}).get("version"))
    if isinstance(version,int): return f"v{version}"
    return version

def _target_identity(manifest):
    keys={"railway":["account","project","environment","service"],"modal":["account","environment","app"],"daytona":["account","sandbox"]}[manifest["provider"]]
    return {key:manifest["target"].get(key) for key in keys}

def _target_lock_key(plan):
    manifest=plan["manifest"]; identity=_target_identity(manifest)
    if manifest["provider"]=="daytona":
        remote=plan.get("remote_preconditions") or {}
        inspection=remote.get("inspection",remote.get("inspect",{}))
        if inspection.get("id"): identity["sandbox"]=inspection["id"]
    return digest({"provider":manifest["provider"],"target":identity})

def _same_target(first,second,journal=None):
    if first["manifest"]["provider"]!=second["manifest"]["provider"]: return False
    if first["manifest"]["provider"]!="daytona":
        return _target_identity(first["manifest"])==_target_identity(second["manifest"])
    if first["manifest"]["target"]["account"]!=second["manifest"]["target"]["account"]: return False
    def aliases(plan):
        remote=plan.get("remote_preconditions") or {}
        observed=remote.get("inspection",remote.get("inspect",{}))
        return {v for v in [plan["manifest"]["target"].get("sandbox"),observed.get("id"),observed.get("name")] if v}
    left=aliases(first)
    if journal:
        for operation in journal.get("operations",[]):
            result=operation.get("result",{})
            left.update(v for v in (result.get("id"),result.get("name")) if v)
    return bool(left & aliases(second))

def rollback(deployment_id,to,state_dir=None,replacement_name=None):
    directory=_state_dir(state_dir=state_dir)
    current=_read_journal(_journal_path(directory,deployment_id))
    current_plan=current["plan"]
    if current_plan["manifest"]["provider"] in {"railway","daytona"}:
        return _artifact_rollback(current,to,directory,replacement_name)
    if replacement_name is not None:
        _error("invalid_config","A replacement sandbox name only applies to Daytona rollback")
    if current.get("state")!="succeeded":
        _error("uncertain","Resolve the current deployment outcome before planning rollback",uncertain=True,mutation_possible=True)
    if not current_plan["manifest"].get("rollback",{}).get("compatible"):
        _error("unsupported","Rollback requires an explicit rollback.compatible operator assertion; data and schema changes are not automatically undone")
    prior=None
    for path in sorted(directory.glob("*.json")):
        journal=_read_journal(path)
        candidate=journal.get("plan",{})
        if (journal.get("state")=="succeeded" and journal.get("layers",{}).get("deployment_observed")
            and candidate.get("manifest",{}).get("provider")=="modal"
            and _target_identity(candidate["manifest"])==_target_identity(current_plan["manifest"])
            and _release_version(journal.get("remote_release",{}))==to
            and candidate["manifest"].get("rollback",{}).get("compatible")):
            prior=journal;break
    if prior is None:
        _error("unsupported","Requested release has no locally verified compatible deployment record")
    plan=json.loads(json.dumps(prior["plan"]))
    manifest=plan["manifest"]; manifest["target"]["mode"]="existing"
    adapter=_adapter(manifest); adapter.validate(manifest)
    plan["kind"]="rollback"; plan["rollback_to"]=to; plan["rollback_from"]=deployment_id
    plan["operations"]=adapter.rollback_steps(manifest,to)
    plan["remote_preconditions"]=_observe(adapter,manifest,plan["source"]["root"])
    plan["warnings"]=["Rollback restores a provider release; it does not undo data, schema migrations, or side effects.",
                      "Compatibility is asserted by the operator, not independently proven.",
                      "The previous runtime configuration and source binding must still match at apply."]
    plan["id"]=_plan_id(plan)
    return plan

def _artifact_rollback(current,to,directory,replacement_name):
    current_plan=current["plan"]; provider=current_plan["manifest"]["provider"]
    if current.get("state")!="succeeded":
        _error("uncertain","Resolve the current deployment outcome before planning rollback",uncertain=True,mutation_possible=True)
    prior=_read_journal(_journal_path(directory,to))
    previous=prior["plan"]
    if (prior.get("state")!="succeeded" or not prior.get("layers",{}).get("deployment_observed")
        or previous["manifest"]["provider"]!=provider or previous["manifest"]["name"]!=current_plan["manifest"]["name"]
        or previous["manifest"]["profile"]!=current_plan["manifest"]["profile"]):
        _error("unsupported","Rollback requires a locally verified compatible prior application release")
    if not all(p["manifest"].get("rollback",{}).get("compatible") for p in (previous,current_plan)):
        _error("unsupported","Both current and prior manifests must explicitly assert rollback compatibility")
    if provider=="railway" and _target_identity(previous["manifest"])!=_target_identity(current_plan["manifest"]):
        _error("invalid_config","Railway rollback must keep the exact account, project, environment and service")
    if previous["manifest"]["target"]["account"]!=current_plan["manifest"]["target"]["account"]:
        _error("invalid_config","Rollback cannot change provider accounts")
    retained=prior.get("retained_source")
    if not retained: _error("unsupported","The prior release has no retained reviewed source bundle")
    if inventory(retained["root"],True,retained.get("excluded_paths",[]))!=retained:
        _error("stale_plan","The retained prior source bundle has changed")
    plan=json.loads(json.dumps(previous))
    plan.pop("recovery_of",None); plan.pop("resolution_of",None); plan.pop("resolution_observation",None)
    plan["source"]=retained
    plan["kind"]="rollback"; plan["rollback_to"]=to; plan["rollback_from"]=current["deployment_id"]
    manifest=plan["manifest"]; artifact=None
    manifest["source"]["path"]="."
    if provider=="daytona":
        if not isinstance(replacement_name,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",replacement_name):
            _error("invalid_config","Daytona rollback requires an explicit safe replacement sandbox name")
        if replacement_name in {current_plan["manifest"]["target"]["sandbox"],previous["manifest"]["target"]["sandbox"]}:
            _error("invalid_config","Rollback must use a new sandbox name; existing sandboxes are preserved")
        for operation in reversed(prior.get("operations",[])):
            result=operation.get("result",{})
            if operation.get("state")=="succeeded" and result.get("snapshot_id") and result.get("snapshot_image"):
                artifact={key:result[key] for key in ("snapshot_id","snapshot_image","account") if key in result};break
        if not artifact: _error("unsupported","Prior sandbox has no verified immutable snapshot receipt")
        manifest.pop("build",None)
        manifest["target"].update(mode="create",sandbox=replacement_name,snapshot=artifact["snapshot_id"])
    else:
        if replacement_name is not None: _error("invalid_config","A replacement name only applies to Daytona")
        for storage in manifest.get("storage",[]): storage["attachment"]="existing"
    effective=_runtime_manifest(manifest,plan["variable_references"])
    adapter=_adapter(manifest); adapter.validate(effective)
    plan["rollback_artifact"]=artifact
    plan["operations"]=adapter.rollback_steps(effective,to,artifact=artifact)
    plan["remote_preconditions"]=_observe(adapter,effective,retained["root"])
    plan["warnings"]=["Rollback is a separately approved deployment of a retained immutable source/configuration or snapshot.",
                      "Data, migrations, external side effects and authentication policies are not undone.",
                      "Compatibility is explicitly asserted by the operator, not independently proven."]
    if provider=="daytona": plan["warnings"].append("The original sandbox remains intact and may continue running or incurring costs; this plan creates the reviewed replacement only.")
    plan["id"]=_plan_id(plan)
    return plan

def logs(deployment_id,state_dir=None,follow=False):
    if follow: _error("unsupported","Unbounded log following is not supported; request a bounded snapshot")
    directory=_state_dir(state_dir=state_dir)
    journal=_read_journal(_journal_path(directory,deployment_id)); plan=journal["plan"]
    adapter=_adapter(plan["manifest"]); step=adapter.command(plan["manifest"],"logs")
    if step.get("mutating"): _error("invalid_config","Logs cannot mutate provider state")
    return {"deployment_id":deployment_id,"logs":_execute(adapter,{**step,"operation":"logs"},plan["manifest"],plan["source"]["root"]),"warnings":["Application logs may contain sensitive information; redaction is best effort."]}

def _listener_warnings(manifest):
    if manifest["provider"] in {"railway","modal"} and manifest.get("runtime",{}).get("port"):
        return ["runtime.port is descriptive metadata only; it does not configure or verify listener routing. Railway Dockerfile/PORT or Modal Python web function owns startup."]
    return []

def inspect(manifest_path):
    manifest=load_manifest(manifest_path); adapter=_adapter(manifest); adapter.validate(manifest)
    return {"manifest":manifest,"capabilities":getattr(adapter,"capabilities",{}),"operations":adapter.plan(manifest),"remote_inspected":False,"warnings":_listener_warnings(manifest)}

def inspect_profile(profile):
    """Inspect capabilities for resolved input without provider reads or writes."""
    from . import discovery
    manifest, _ = discovery.validate_profile(profile)
    adapter = _adapter(manifest)
    adapter.validate(manifest)
    return {"manifest": manifest, "capabilities": getattr(adapter, "capabilities", {}),
            "operations": adapter.plan(manifest), "remote_inspected": False,
            "warnings": _listener_warnings(manifest)}


def doctor(provider):
    from .providers import get_adapter
    adapter=get_adapter(provider)
    result={"provider":provider,"executable_available":False,"compatible":False,
            "expected_version":getattr(adapter,"supported_version",None),
            "capabilities":getattr(adapter,"capabilities",{}),"qualification":"experimental/offline-tested",
            "warnings":["This check does not log in, validate credentials, or create resources."]}
    try:
        result["transport"]=_check_version(adapter,Path.cwd())
        result["executable_available"]=True; result["compatible"]=True
    except OnedeployError as error:
        result["error"]=error.to_dict()
    return result
