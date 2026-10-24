"""Strict manifests and a small non-executing dotenv grammar."""
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import yaml
from jsonschema import Draft202012Validator
from .errors import OnedeployError

MAX_INPUT = 1024 * 1024
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def fail(message):
    raise OnedeployError("invalid_config", message)

def secure_path(path, *, private=False, max_size=MAX_INPUT):
    p = Path(path).absolute()
    for part in [p, *p.parents]:
        if part.is_symlink():
            fail("Symlink paths are not accepted")
    if not p.is_file():
        fail("Input must be an existing regular file")
    if p.stat().st_size > max_size:
        fail("Input exceeds the size limit")
    if private and os.name == "posix" and stat.S_IMODE(p.stat().st_mode) & 0o077:
        fail("Environment file must have private permissions (chmod 600)")
    return p.resolve()

def _object(properties, required=()):
    return {"type":"object", "properties":properties, "required":list(required), "additionalProperties":False}

def schema():
    string = {"type":"string", "minLength":1, "maxLength":4096}
    identifier = {"type":"string", "pattern":r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$"}
    probe_path = {"type":"string", "pattern":r"^/(?!/)[A-Za-z0-9_./-]*$", "maxLength":2048}
    variable = _object({"type":{"enum":["string","integer","boolean"]}, "required":{"type":"boolean"},
                        "secret":{"type":"boolean"}, "default":{"type":["string","integer","boolean"]},
                        "source":{"type":"string","pattern":r"^env:[A-Za-z_][A-Za-z0-9_]*$"},
                        "required_if":_object({"variable":{"type":"string","pattern":KEY.pattern},
                                               "equals":{"type":["string","integer","boolean"]}},["variable","equals"])})
    return {"$schema":"https://json-schema.org/draft/2020-12/schema", **_object({
        "version":{"const":1,"type":"integer"}, "name":identifier,
        "provider":{"enum":["daytona","railway","modal"]},
        "profile":{"enum":["docker-http","asgi","worker","model"]},
        "source":_object({"path":string,"revision":string,"entrypoint":string,"trust_code":{"type":"boolean"}},["path"]),
        "build":_object({"dockerfile":string,"context":string,"snapshot_name":identifier}),
        "runtime":_object({"command":{"type":"array","items":string,"minItems":1,"maxItems":64},
            "port":{"type":"integer","minimum":1,"maximum":65535},
            "health_path":probe_path,"ready_url":{"type":"string","pattern":"^https://","maxLength":2048},"ready_path":probe_path,
            "timeout":{"type":"integer","minimum":1,"maximum":3600}}),
        "variables":{"type":"object","propertyNames":{"pattern":KEY.pattern},"additionalProperties":variable,"maxProperties":128},
        "target":_object({**{k:identifier for k in ["account","project","environment","service","sandbox","snapshot","region","app","function"]}, "mode":{"enum":["create","existing"]}, "transport":{"enum":["cli","sdk"]}},["account"]),
        "resources":_object({k:{"type":"integer","minimum":0,"maximum":1048576} for k in ["cpu","memory_mb","disk_gb","auto_stop_minutes"]}),
        "ingress":_object({"mode":{"enum":["private","existing"]}}),
        "storage":{"type":"array","maxItems":16,"items":_object({
            "name":identifier,"resource_id":identifier,"mount_path":{"type":"string","pattern":"^/","maxLength":1024},
            "kind":{"enum":["volume","model-cache"]},"access":{"enum":["read-only","read-write"]},
            "single_writer":{"type":"boolean"},"attachment":{"enum":["attach","existing"]}},["name","resource_id","mount_path","kind","access","single_writer"])},
        "dependencies":{"type":"array","maxItems":32,"items":_object({
            "name":identifier,"kind":{"enum":["postgres","redis","http"]},
            "variable":{"type":"string","pattern":KEY.pattern},"tls":{"type":"boolean"},"required":{"type":"boolean"}},
            ["name","kind","variable","tls","required"])},
        "checks":{"type":"array","maxItems":8,"items":_object({
            "name":identifier,"path":probe_path,
            "expected_status":{"type":"integer","minimum":200,"maximum":299},
            "auth_variable":{"type":"string","pattern":KEY.pattern},
            "unauthenticated_status":{"enum":[401,403]},"safety":{"const":"read-only"}},["name","path","safety"])},
        "rollback":_object({"compatible":{"type":"boolean"}}),
    },["version","name","provider","profile","source","target"])}

class StrictLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            fail("YAML aliases are not accepted")
        self.depth += 1
        if self.depth > 24:
            fail("Manifest nesting exceeds limit")
        try:
            return super().compose_node(parent,index)
        finally:
            self.depth -= 1
    def construct_mapping(self,node,deep=False):
        mapping = {}
        for key_node,value_node in node.value:
            key = self.construct_object(key_node,deep=deep)
            if not isinstance(key,str) or key in mapping:
                fail("Manifest contains duplicate or non-string keys")
            mapping[key] = self.construct_object(value_node,deep=deep)
        return mapping

def validate_manifest(manifest):
    errors = list(Draft202012Validator(schema()).iter_errors(manifest))
    if errors:
        # Never include schema error text: it can echo an input credential.
        fail("Manifest does not match the strict version 1 schema")
    paths=[manifest.get("runtime",{}).get(field) for field in ("health_path","ready_path")]
    paths.extend(check["path"] for check in manifest.get("checks",[]))
    if any(path is not None and re.fullmatch(r"/(?!/)[A-Za-z0-9_./-]*",path) is None for path in paths):
        fail("Probe paths must be simple local absolute paths")
    for name,spec in manifest.get("variables",{}).items():
        source=spec.get("source",f"env:{name}").split(":",1)[1]
        if source.startswith(("RAILWAY_","DAYTONA_","MODAL_","PYTHON","LD_","DYLD_")) or source in {"PATH","HOME","SHELL","BASH_ENV","ENV"}:
            fail("Runtime references cannot select control-plane or process environment variables")
        if spec.get("secret",True) and "default" in spec:
            fail("Secret variables cannot have manifest defaults")
        if "default" in spec:
            _typed(name,spec,spec["default"])
        if "required_if" in spec:
            condition=spec["required_if"]; reference=condition["variable"]
            controller=manifest.get("variables",{}).get(reference)
            if reference==name or controller is None or controller.get("secret",True):
                fail("Conditional requirements need a distinct declared nonsecret controlling variable")
            _typed(reference,controller,condition["equals"])
    # Cycles obscure requiredness even though values are resolved before conditions.
    for name in manifest.get("variables",{}):
        seen=set(); current=name
        while "required_if" in manifest["variables"][current]:
            if current in seen: fail("Conditional requirement cycles are not accepted")
            seen.add(current); current=manifest["variables"][current]["required_if"]["variable"]
    mounts=set(); names=set()
    for storage in manifest.get("storage",[]):
        mount=Path(storage["mount_path"])
        if (not mount.is_absolute() or ".." in mount.parts or str(mount) in {"/","/proc","/sys","/dev","/etc"}
            or any(str(mount).startswith(prefix+"/") for prefix in ("/proc","/sys","/dev","/etc"))):
            fail("Storage mount path is unsafe")
        if storage["name"] in names or str(mount) in mounts:
            fail("Storage labels and mount paths must be unique")
        if any(mount.is_relative_to(Path(other)) or Path(other).is_relative_to(mount) for other in mounts):
            fail("Overlapping storage mount paths are not accepted")
        mounts.add(str(mount)); names.add(storage["name"])
        if storage["access"]=="read-only" and storage["single_writer"]:
            fail("Read-only storage cannot declare a writer")
    names=set()
    for dependency in manifest.get("dependencies",[]):
        if dependency["name"] in names: fail("Dependency names must be unique")
        names.add(dependency["name"])
        variable=manifest.get("variables",{}).get(dependency["variable"])
        if variable is None or variable.get("type","string")!="string":
            fail("Dependency connection references require a declared string variable")
    names=set()
    for check in manifest.get("checks",[]):
        if check["name"] in names: fail("Check names must be unique")
        names.add(check["name"])
        if check["path"].startswith("//") or any(ch in check["path"] for ch in ("?","#","\\","\r","\n")):
            fail("Checks must use a local absolute path without URL parameters")
        auth=check.get("auth_variable")
        if "unauthenticated_status" in check and not auth:
            fail("Negative authentication checks require an authentication variable")
        if auth:
            spec=manifest.get("variables",{}).get(auth)
            if spec is None or not spec.get("secret",True) or spec.get("type","string")!="string":
                fail("Authentication checks require a declared secret string variable")
    return manifest

def load_manifest(path):
    p = secure_path(path)
    try:
        value = yaml.load(p.read_text(encoding="utf-8"),Loader=StrictLoader)
    except (yaml.YAMLError,UnicodeError,ValueError):
        fail("Invalid YAML manifest")
    return validate_manifest(value)

def load_env(path):
    if path is None:
        return {}
    p = secure_path(path,private=True)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except UnicodeError:
        fail("Environment file must be UTF-8")
    values = {}
    for line_no,line in enumerate(lines,1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        def bad():
            fail(f"Invalid or duplicate environment assignment at line {line_no}")
        if "=" not in text:
            bad()
        key,value = text.split("=",1)
        key,value = key.strip(),value.strip()
        if not KEY.fullmatch(key) or key in values:
            bad()
        if value.startswith(('"',"'")):
            quote = value[0]
            out, index, closed = [],1,False
            while index < len(value):
                c=value[index]
                if c==quote:
                    tail=value[index+1:].strip()
                    if tail and not tail.startswith("#"):
                        bad()
                    closed=True
                    break
                if c=="\\":
                    index+=1
                    if index >= len(value): bad()
                    escapes={"n":"\n","r":"\r","t":"\t","\\":"\\",'"':'"',"'":"'"}
                    if value[index] not in escapes: bad()
                    c=escapes[value[index]]
                out.append(c); index+=1
            if not closed: bad()
            value="".join(out)
        else:
            value=re.split(r"\s+#",value,maxsplit=1)[0].rstrip()
            if any(c.isspace() for c in value) or any(c in value for c in "'\"\\"):
                bad()
        # Expansion-looking syntax is literal data, never evaluated or interpolated.
        if "\x00" in value: bad()
        values[key]=value
    return values

def _typed(name,spec,value):
    typ=spec.get("type","string")
    if typ=="integer" and (isinstance(value,bool) or not re.fullmatch(r"-?[0-9]+",str(value))):
        fail(f"Variable {name} must be an integer")
    if typ=="boolean" and str(value).lower() not in ("true","false"):
        fail(f"Variable {name} must be a boolean")
    if not isinstance(value,(str,int,bool)):
        fail(f"Variable {name} has an unsupported value type")
    if typ=="boolean":
        return str(value).lower()
    if typ=="integer":
        digits=str(value).lstrip("-").lstrip("0") or "0"
        return ("-" if str(value).startswith("-") and digits!="0" else "")+digits
    return str(value).lower() if isinstance(value,bool) else str(value)

def resolve_variables(manifest, env_file=None, env_overrides=None):
    values = load_env(env_file)
    overrides = env_overrides or {}
    resolved={}
    specs=manifest.get("variables",{})
    for name,spec in specs.items():
        source = spec.get("source",f"env:{name}").split(":",1)[1]
        value = overrides.get(name, os.environ.get(source, values.get(source, spec.get("default"))))
        if value is not None:
            resolved[name]=_typed(name,spec,value)
    for name,spec in specs.items():
        required=spec.get("required","required_if" not in spec)
        if "required_if" in spec:
            condition=spec["required_if"]; reference=condition["variable"]
            # A missing controlling variable makes the condition false.
            expected=_typed(reference,specs[reference],condition["equals"])
            required=required or (reference in resolved and resolved[reference]==expected)
        if required and (name not in resolved or resolved[name]==""):
            fail(f"Required variable {name} is missing")
    validate_dependencies(manifest,resolved)
    for check in manifest.get("checks",[]):
        if check.get("auth_variable"):
            value=resolved.get(check["auth_variable"],"")
            if not value or len(value)>8192 or not re.fullmatch(r"[\x21-\x7e]+",value):
                fail(f"Authentication check {check['name']} requires a valid resolved bearer credential")
    return resolved

def validate_dependencies(manifest,resolved):
    for dependency in manifest.get("dependencies",[]):
        value=resolved.get(dependency["variable"])
        if not value:
            if dependency["required"]: fail(f"Required dependency {dependency['name']} has no connection variable")
            continue
        try:
            url=urlsplit(value)
            if not url.hostname or url.fragment or url.port==0: raise ValueError()
            if url.password is not None and not manifest["variables"][dependency["variable"]].get("secret",True):
                raise ValueError()
            schemes={"postgres":{"postgres","postgresql"},"redis":{"redis","rediss"},"http":{"http","https"}}[dependency["kind"]]
            if url.scheme not in schemes: raise ValueError()
            if dependency["tls"]:
                if dependency["kind"]=="postgres":
                    mode=parse_qs(url.query).get("sslmode",[])
                    if len(mode)!=1 or mode[0] not in {"require","verify-ca","verify-full"}: raise ValueError()
                elif url.scheme not in {"rediss","https"}: raise ValueError()
        except (ValueError,TypeError):
            fail(f"Dependency {dependency['name']} has an invalid connection or TLS configuration")

def validate_config(manifest, env_file=None, env_overrides=None):
    if not isinstance(manifest,dict): manifest=load_manifest(manifest)
    validate_manifest(manifest)
    resolved=resolve_variables(manifest,env_file,env_overrides)
    return {"valid":True,"variables":sorted(resolved),"precedence":["explicit overrides","selected environment","env file","nonsecret defaults"],"dependencies":[{"name":d["name"],"configuration_valid":True,"connectivity_verified":False} for d in manifest.get("dependencies",[])]}
