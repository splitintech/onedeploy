import json
import os
from pathlib import Path
import subprocess
import threading
import pytest
import yaml
from onedeploy import core
from onedeploy.errors import OnedeployError

class Adapter:
    version="test-1"
    capabilities={"offline":True}
    def validate(self,manifest): pass
    def plan(self,manifest,release_tag=None): return [{"id":"upload","argv":["railway","up","."],"mutating":True,"env_keys":[]}]
    def command(self,manifest,operation): return {"id":operation,"argv":["railway",operation],"mutating":False,"env_keys":[]}
    def parse(self,operation,stdout): return json.loads(stdout)
    def assert_target(self,manifest,identity,inspection): return {"identity":identity,"inspect":inspection}

@pytest.fixture
def project(tmp_path,monkeypatch):
    root=tmp_path/"project"; root.mkdir()
    (root/"Dockerfile").write_text("FROM scratch\n")
    manifest={"version":1,"name":"test","provider":"railway","profile":"docker-http","source":{"path":"."},
              "target":{"account":"a","project":"p","environment":"e","service":"s"}}
    path=root/"onedeploy.yaml"; path.write_text(yaml.safe_dump(manifest))
    monkeypatch.setattr(core,"_adapter",lambda m:Adapter())
    return path

@pytest.fixture
def fake_transport(monkeypatch):
    calls=[]
    def run(step,**kwargs):
        calls.append((step,kwargs))
        return {"stdout":json.dumps({"id":"stable","status":"ok"}),"stderr":"","returncode":0}
    monkeypatch.setattr(core.runner,"run",run)
    return calls

def plan(project,**kwargs): return core.make_plan(project,allow_dirty=True,**kwargs)

def test_plan_no_provider_call_by_default(project,fake_transport):
    p=plan(project)
    assert fake_transport==[] and p["remote_preconditions"] is None
    with pytest.raises(OnedeployError) as e: core.deploy(p,approval=p["id"])
    assert e.value.code=="stale_plan"

def test_approval_zero_calls(project,fake_transport):
    p=plan(project,inspect_remote=True); fake_transport.clear()
    with pytest.raises(OnedeployError) as e: core.deploy(p,approval="wrong")
    assert e.value.code=="approval_required" and not fake_transport

def test_secret_binding_and_exclusion(project,fake_transport):
    m=yaml.safe_load(project.read_text());m["variables"]={"API_SECRET":{"secret":True,"required":True}}
    project.write_text(yaml.safe_dump(m))
    env=project.parent/".env";env.write_text("API_SECRET=synthetic-sensitive-secret");env.chmod(0o600)
    p=plan(project,env_file=env,inspect_remote=True)
    assert "synthetic-sensitive-secret" not in json.dumps(p)
    assert not any(e["path"].startswith((".env",".onedeploy")) for e in p["source"]["files"])
    env.write_text("API_SECRET=changed-secret");fake_transport.clear()
    with pytest.raises(OnedeployError) as e: core.deploy(p,env_file=env,approval=p["id"])
    assert e.value.code=="stale_plan" and not fake_transport

def test_source_tamper_rejected(project,fake_transport):
    p=plan(project,inspect_remote=True); fake_transport.clear()
    (project.parent/"Dockerfile").write_text("FROM attacker\n")
    with pytest.raises(OnedeployError) as e: core.deploy(p,approval=p["id"])
    assert e.value.code=="stale_plan" and not fake_transport

def test_plan_tamper_rejected(project):
    p=plan(project);p["manifest"]["target"]["project"]="other"
    with pytest.raises(OnedeployError): core.verify_plan(p)

def test_symlink_bundle_rejected(project):
    (project.parent/"link").symlink_to(project)
    with pytest.raises(OnedeployError): plan(project)

def test_untracked_default_rejected(project):
    subprocess.run(["git","init",str(project.parent)],capture_output=True,check=True)
    subprocess.run(["git","-C",str(project.parent),"-c","user.email=test@example.invalid","-c","user.name=Test","commit","--allow-empty","-m","empty"],capture_output=True,check=True)
    with pytest.raises(OnedeployError): core.make_plan(project)

def test_private_save_and_roundtrip(project,tmp_path):
    p=plan(project); out=tmp_path/"plan.json";core.save_plan(p,out)
    assert core.read_plan(out)==p and out.stat().st_mode&0o777==0o600

def test_bundle_upload_and_idempotent_journal(project,fake_transport,tmp_path):
    p=plan(project,inspect_remote=True)
    calls=[]
    old=core.runner.run
    def recording(step,**kw):
        if step["mutating"]:
            assert Path(kw["cwd"])!=project.parent
            assert not (Path(kw["cwd"])/".onedeploy").exists()
            calls.append(step)
        return old(step,**kw)
    core.runner.run=recording
    try:
        state=tmp_path/"state"; result=core.deploy(p,approval=p["id"],state_dir=state)
        assert result["state"]=="succeeded" and result["layers"]["readiness_passed"] is False
        assert core.deploy(p,approval=p["id"],state_dir=state)==result
        assert len(calls)==1 and (state/(p["id"]+".json")).stat().st_mode&0o777==0o600
    finally: core.runner.run=old

def test_uncertain_write_never_replayed(project,fake_transport,tmp_path,monkeypatch):
    p=plan(project,inspect_remote=True); old=core.runner.run
    writes=[]
    def timeout(step,**kw):
        if step["mutating"]:
            writes.append(step);raise OnedeployError("uncertain","Synthetic timeout",uncertain=True,mutation_possible=True)
        return old(step,**kw)
    monkeypatch.setattr(core.runner,"run",timeout)
    for _ in range(2):
        with pytest.raises(OnedeployError) as e: core.deploy(p,approval=p["id"],state_dir=tmp_path/"state")
        assert e.value.uncertain
    assert len(writes)==1
    recovery=core.resume(p["id"],state_dir=tmp_path/"state")
    assert not recovery["mutation_performed"] and recovery["plan"] is None

def test_stale_remote_preconditions(project,fake_transport,tmp_path,monkeypatch):
    p=plan(project,inspect_remote=True)
    monkeypatch.setattr(core.runner,"run",lambda *a,**kw:{"stdout":'{"id":"changed"}',"stderr":"","returncode":0})
    with pytest.raises(OnedeployError) as e: core.deploy(p,approval=p["id"],state_dir=tmp_path/"state")
    assert e.value.code=="stale_plan"

def test_local_lock(tmp_path):
    with core._lock(tmp_path/"state","a"*64):
        with pytest.raises(OnedeployError) as e:
            with core._lock(tmp_path/"state","a"*64): pass
    assert e.value.code=="locked"

def test_redaction():
    value=core.redact({"token":"secret","url":"https://me:pass@example.com?a=1&token=123","message":"hello abc"},["abc"])
    assert value["token"]=="[REDACTED]" and "pass" not in value["url"] and "123" not in value["url"] and "abc" not in value["message"]

def test_runner_rejects_arbitrary_executable(tmp_path):
    with pytest.raises(OnedeployError): core.CommandRunner().run({"argv":["bash","-c","echo bad"]},cwd=tmp_path)

def test_cancel_before_write(project,fake_transport,tmp_path):
    p=plan(project,inspect_remote=True);fake_transport.clear();event=threading.Event();event.set()
    with pytest.raises(OnedeployError) as e: core.deploy(p,approval=p["id"],state_dir=tmp_path/"state",cancel_event=event)
    assert e.value.code=="cancelled" and not any(s[0]["mutating"] for s in fake_transport)

def test_env_file_traversal_excluded(project,fake_transport):
    root=project.parent; (root/"input").mkdir(); env=root/"input"/"config.txt"
    env.write_text("SECRET=do-not-upload-this-secret");env.chmod(0o600)
    p=plan(project,env_file=root/"input"/".."/"input"/"config.txt")
    assert "input/config.txt" not in {r["path"] for r in p["source"]["files"]}

def test_build_traversal_rejected(project):
    m=yaml.safe_load(project.read_text());m["build"]={"context":"../outside"};project.write_text(yaml.safe_dump(m))
    with pytest.raises(OnedeployError):plan(project)

def test_custom_plan_output_does_not_change_bundle(project,fake_transport,tmp_path):
    output=project.parent/"custom-preview.json"
    p=plan(project,plan_output=output,inspect_remote=True);core.save_plan(p,output)
    result=core.deploy(core.read_plan(output),approval=p["id"],state_dir=tmp_path/"state")
    assert result["state"]=="succeeded"
    assert output.name not in {r["path"] for r in p["source"]["files"]}

def test_unbound_custom_output_rejected(project):
    p=plan(project)
    with pytest.raises(OnedeployError):core.save_plan(p,project.parent/"arbitrary.json")

def test_same_target_lock_for_distinct_plans(project,fake_transport,tmp_path):
    p=plan(project,inspect_remote=True);state=tmp_path/"state"
    lock=core.digest({"provider":p["manifest"]["provider"],"target":core._target_identity(p["manifest"])})
    with core._lock(state,lock):
        with pytest.raises(OnedeployError) as error:core.deploy(p,approval=p["id"],state_dir=state)
    assert error.value.code=="locked"

def test_recovery_only_when_no_operation_began(project,fake_transport,tmp_path):
    p=plan(project,inspect_remote=True);event=threading.Event();event.set();state=tmp_path/"state"
    with pytest.raises(OnedeployError):core.deploy(p,approval=p["id"],state_dir=state,cancel_event=event)
    report=core.resume(p["id"],state_dir=state);recovery=report["plan"]
    assert recovery["id"]!=p["id"] and recovery["recovery_of"]==p["id"]
    assert core.deploy(recovery,approval=recovery["id"],state_dir=state)["state"]=="succeeded"
    assert core.resume(p["id"],state_dir=state)["state"]=="completed"

def test_malformed_mutating_response_is_uncertain(project,fake_transport,tmp_path,monkeypatch):
    p=plan(project,inspect_remote=True);old=core.runner.run
    def broken(step,**kwargs):return {"stdout":"not json"} if step["mutating"] else old(step,**kwargs)
    monkeypatch.setattr(core.runner,"run",broken)
    with pytest.raises(OnedeployError) as error:core.deploy(p,approval=p["id"],state_dir=tmp_path/"state")
    assert error.value.uncertain and error.value.mutation_possible

def test_large_saved_plan_can_be_read(project,tmp_path):
    p=plan(project);p["warnings"].append("x"*(1024*1024+1));p["id"]=core._plan_id(p)
    output=tmp_path/"large.json";core.save_plan(p,output)
    assert core.read_plan(output)==p

def test_runner_minimal_env_raw_stdin(tmp_path,monkeypatch):
    import io
    captured={}
    class Input(io.BytesIO):
        def close(self):captured["stdin"]=self.getvalue();super().close()
    class Proc:
        def __init__(self,*args,**kwargs):
            captured.update(kwargs);captured["argv"]=args[0]
            self.stdin=Input();self.stdout=io.BytesIO(b'{}');self.stderr=io.BytesIO();self.returncode=0
        def poll(self):return 0
    monkeypatch.setenv("UNRELATED_LOCAL_SECRET","not-forwarded")
    monkeypatch.setattr(core.subprocess,"Popen",Proc)
    result=core.CommandRunner().run({"argv":["railway","variable","set","APP_KEY","--stdin"],"mutating":True,"stdin_env_keys":["APP_KEY"],"stdin_format":"raw"},cwd=tmp_path,variables={"APP_KEY":"literal\nvalue"})
    assert captured["stdin"]==b"literal\nvalue" and captured["shell"] is False
    assert "UNRELATED_LOCAL_SECRET" not in captured["env"] and "APP_KEY" not in captured["env"]

def test_runner_runtime_environment_explicit_only(tmp_path,monkeypatch):
    import io
    captured={}
    class Proc:
        def __init__(self,*args,**kwargs):
            captured.update(kwargs);self.stdin=io.BytesIO();self.stdout=io.BytesIO(b'{}');self.stderr=io.BytesIO();self.returncode=0
        def poll(self):return 0
    monkeypatch.setattr(core.subprocess,"Popen",Proc)
    core.CommandRunner().run({"argv":["modal","deploy","app.py"],"runtime_env_keys":["APP_KEY"]},cwd=tmp_path,variables={"APP_KEY":"synthetic","OTHER":"not-forwarded"})
    assert captured["env"]["APP_KEY"]=="synthetic" and "OTHER" not in captured["env"]

def test_rollback_requires_verified_local_release(project,fake_transport,tmp_path,monkeypatch):
    class Modal(Adapter):
        def correlate_release(self,history,tag):return None
        def rollback_steps(self,manifest,release):return [{"id":"rollback","argv":["modal","app","rollback",release],"mutating":True,"env_keys":[]}]
    monkeypatch.setattr(core,"_adapter",lambda m:Modal())
    m=yaml.safe_load(project.read_text());m["provider"]="modal";m["profile"]="asgi";m["target"]={"account":"a","environment":"e","app":"app"};m["rollback"]={"compatible":True};m["source"]["entrypoint"]="app.py";(project.parent/"app.py").write_text("# mock source\n");project.write_text(yaml.safe_dump(m))
    p=plan(project,inspect_remote=True);state=tmp_path/"state";result=core.deploy(p,approval=p["id"],state_dir=state)
    result["remote_release"]={"lifecycle":{"version":1,"state":"deployed"}}
    result["layers"]["deployment_observed"]=True
    core._atomic(state/(p["id"]+".json"),core.canonical(result))
    rollback=core.rollback(p["id"],"v1",state_dir=state)
    assert rollback["kind"]=="rollback" and rollback["id"]!=p["id"]
    assert core.deploy(rollback,approval=rollback["id"],state_dir=state)["state"]=="succeeded"
    with pytest.raises(OnedeployError):core.rollback(p["id"],"v999",state_dir=state)

def test_unresolved_other_plan_blocks_target(project,fake_transport,tmp_path,monkeypatch):
    first=plan(project,inspect_remote=True);old=core.runner.run;state=tmp_path/"state"
    def interrupted(step,**kw):
        if step["mutating"]:raise OnedeployError("uncertain","interrupted",uncertain=True,mutation_possible=True)
        return old(step,**kw)
    monkeypatch.setattr(core.runner,"run",interrupted)
    with pytest.raises(OnedeployError):core.deploy(first,approval=first["id"],state_dir=state)
    (project.parent/"Dockerfile").write_text("FROM scratch\n# next\n")
    second=plan(project,inspect_remote=True)
    assert first["id"]!=second["id"]
    monkeypatch.setattr(core.runner,"run",old)
    with pytest.raises(OnedeployError) as error:core.deploy(second,approval=second["id"],state_dir=state)
    assert error.value.uncertain
    report=core.resume(first["id"],state_dir=state)
    with pytest.raises(OnedeployError):core.resume(first["id"],state_dir=state,resolve_with_observation="wrong",outcome="operator-confirmed-not-executed")
    resolved=core.resume(first["id"],state_dir=state,resolve_with_observation=report["observation_id"],outcome="operator-confirmed-not-executed")
    assert resolved["state"]=="operator_resolved" and resolved["mutation_performed"] is False
    assert core.deploy(second,approval=second["id"],state_dir=state)["state"]=="succeeded"

def test_optional_absent_variable_not_in_provider_steps(project,monkeypatch):
    class Variables(Adapter):
        def plan(self,manifest,release_tag=None):return [{"id":"v:"+key,"argv":["railway","set",key],"mutating":True} for key in manifest.get("variables",{})]
    monkeypatch.setattr(core,"_adapter",lambda m:Variables())
    m=yaml.safe_load(project.read_text());m["variables"]={"MISSING_OPTIONAL_UNIQUE":{"required":False}};project.write_text(yaml.safe_dump(m))
    p=plan(project)
    assert p["operations"]==[] and p["variable_references"]=={}

def test_unrelated_release_does_not_verify_deployment(project):
    p=plan(project)
    observed={"services":[{"id":"s","instances":[{"environmentId":"e","deployment":{"id":"external-release","status":"SUCCESS"}}]}]}
    assert not core._new_generation_observed(p,observed,[{"id":"deploy","state":"succeeded","result":{"accepted_release_id":"our-release"}}])
    assert core._new_generation_observed(p,observed,[{"id":"deploy","state":"succeeded","result":{"accepted_release_id":"external-release"}}])

def test_daytona_identity_excludes_desired_artifact():
    first={"provider":"daytona","target":{"account":"a","sandbox":"s","snapshot":"one"}}
    second={"provider":"daytona","target":{"account":"a","sandbox":"s","snapshot":"two","region":"us"}}
    assert core._target_identity(first)==core._target_identity(second)

def test_interrupted_runner_kills_process_group(tmp_path,monkeypatch):
    import io
    killed=[]
    class Proc:
        pid=123456
        returncode=None
        def __init__(self,*a,**k):self.stdin=io.BytesIO();self.stdout=io.BytesIO();self.stderr=io.BytesIO()
        def poll(self):raise KeyboardInterrupt()
        def wait(self,timeout):self.returncode=-9
    monkeypatch.setattr(core.subprocess,"Popen",Proc)
    monkeypatch.setattr(core.os,"killpg",lambda pid,sig:killed.append(pid))
    with pytest.raises(OnedeployError) as error:core.CommandRunner().run({"argv":["railway","up"],"mutating":True},cwd=tmp_path)
    assert killed==[123456] and error.value.uncertain

def test_oversized_runner_output_is_uncertain(tmp_path,monkeypatch):
    import io
    class Proc:
        returncode=0
        def __init__(self,*a,**k):self.stdin=io.BytesIO();self.stdout=io.BytesIO(b'x'*(core.MAX_OUTPUT+4096));self.stderr=io.BytesIO()
        def poll(self):return 0
    monkeypatch.setattr(core.subprocess,"Popen",Proc)
    with pytest.raises(OnedeployError) as error:core.CommandRunner().run({"argv":["railway","up"],"mutating":True},cwd=tmp_path)
    assert error.value.uncertain

def test_daytona_alias_identity_lock_and_uncertainty():
    def p(name):return {"manifest":{"provider":"daytona","target":{"account":"a","sandbox":name}},"remote_preconditions":{"inspection":{"id":"sandbox-id","name":"sandbox-name"}}}
    assert core._target_lock_key(p("sandbox-id"))==core._target_lock_key(p("sandbox-name"))
    assert core._same_target(p("sandbox-id"),p("sandbox-name"))

def test_modal_tag_binds_exact_observed_release(project,tmp_path,monkeypatch):
    class Tagged(Adapter):
        def plan(self,manifest,release_tag=None):return [{"id":"deploy","argv":["modal","deploy","app.py","--tag",release_tag or "none"],"mutating":True}]
        def correlate_release(self,history,tag):
            versions=[x["version"] for x in history.get("releases",[]) if x.get("tag")==tag]
            return {"release_version":versions[0]} if len(versions)==1 else None
    adapter=Tagged();monkeypatch.setattr(core,"_adapter",lambda m:adapter)
    m=yaml.safe_load(project.read_text());m.update(provider="modal",profile="asgi",target={"account":"a","environment":"e","app":"app"})
    m["source"]["entrypoint"]="app.py";(project.parent/"app.py").write_text("# mock source\n")
    project.write_text(yaml.safe_dump(m))
    data={"written":False,"tag":None}
    def run(step,**kwargs):
        operation=step["id"]
        if step.get("mutating"):
            data["written"]=True;data["tag"]=step["argv"][-1];result={"provider_command_completed":True}
        elif operation=="history":result={"releases":[{"version":"v2","tag":data["tag"]}]} if data["written"] else {"releases":[]}
        elif operation=="status":result={"app_id":"app-id","lifecycle":{"version":2,"state":"deployed"}}
        else:result={"app_id":"app-id","lifecycle":{"version":1,"state":"deployed"}}
        return {"stdout":json.dumps(result),"stderr":"","returncode":0}
    monkeypatch.setattr(core.runner,"run",run)
    p=plan(project,inspect_remote=True)
    assert p["release_tag"].startswith("od-") and p["operations"][0]["argv"][-1]==p["release_tag"]
    assert p["release_tag"]==plan(project,inspect_remote=True)["release_tag"]
    result=core.deploy(p,approval=p["id"],state_dir=tmp_path/"state")
    assert result["layers"]["deployment_observed"] and result["layers"]["process_running"]
    assert result["operations"][-1]["result"]["accepted_release_id"]=="v2"

def test_version_mismatch_blocks_remote_read(project,monkeypatch):
    class Versioned(Adapter):
        def version_command(self):return {"id":"version","argv":["railway","--version"],"mutating":False}
        def parse_version(self,stdout):raise OnedeployError("unsupported","version mismatch")
    calls=[];monkeypatch.setattr(core,"_adapter",lambda m:Versioned())
    monkeypatch.setattr(core.runner,"run",lambda step,**kw:(calls.append(step) or {"stdout":"wrong"}))
    with pytest.raises(OnedeployError):plan(project,inspect_remote=True)
    assert [s["id"] for s in calls]==["version"]

def test_daytona_project_snapshot_create_and_replacement_rollback(project,tmp_path,monkeypatch):
    from onedeploy.providers import get_adapter
    monkeypatch.setattr(core,"_adapter",lambda m:get_adapter(m["provider"]))
    m=yaml.safe_load(project.read_text());m.update(provider="daytona",profile="docker-http",target={"account":"a","sandbox":"first-sandbox","mode":"create"},build={"context":".","dockerfile":"Dockerfile","snapshot_name":"reviewed-build"},rollback={"compatible":True})
    project.write_text(yaml.safe_dump(m));state=tmp_path/"state"
    digest="image.example/app@sha256:"+"a"*64
    data={"snapshot":False,"sandboxes":{},"writes":[]}
    def transport(step,**kwargs):
        operation=step["id"]
        config=json.loads(step["argv"][-1]) if operation!="version" else {}
        target=config.get("target",{})
        if operation=="version":value={"version":"0.226.0"}
        elif operation=="identity":value={"account":"a"}
        elif operation=="inspect":
            value={"exists":False,"account":"a","volumes":[]}
            value.update({"snapshot_id":"snapshot-id","snapshot_image":digest} if data["snapshot"] else {"snapshot_pending":True})
        elif operation=="snapshot-create":
            assert (Path(kwargs["cwd"])/"Dockerfile").exists()
            assert not (Path(kwargs["cwd"])/".onedeploy").exists()
            data["snapshot"]=True;data["writes"].append(operation)
            value={"snapshot_id":"snapshot-id","snapshot_image":digest,"account":"a"}
        elif operation=="create":
            assert config["snapshot_receipt"]["snapshot_id"]=="snapshot-id"
            assert config["snapshot_receipt"]["snapshot_image"]==digest
            data["writes"].append(operation)
            value={"id":"sandbox-"+str(len(data["sandboxes"])+1),"name":target["sandbox"],"account":"a","state":"started","snapshot":"snapshot-id","snapshot_id":"snapshot-id","snapshot_image":digest,"volumes":[],"public":False}
            data["sandboxes"][target["sandbox"]]=value
        elif operation=="status":value=data["sandboxes"][target["sandbox"]]
        else:raise AssertionError(operation)
        return {"stdout":json.dumps(value),"stderr":"","returncode":0}
    monkeypatch.setattr(core.runner,"run",transport)
    first=plan(project,inspect_remote=True)
    deployed=core.deploy(first,approval=first["id"],state_dir=state)
    assert [op["id"] for op in deployed["operations"]]==["snapshot-create","create"]
    assert deployed["layers"]["deployment_observed"]
    assert all(op["state"]=="succeeded" for op in deployed["operations"])
    rollback=core.rollback(first["id"],first["id"],state_dir=state,replacement_name="replacement-sandbox")
    assert rollback["manifest"]["target"]["sandbox"]=="replacement-sandbox" and "build" not in rollback["manifest"]
    restored=core.deploy(rollback,approval=rollback["id"],state_dir=state)
    assert restored["layers"]["deployment_observed"] and set(data["sandboxes"])=={"first-sandbox","replacement-sandbox"}
    assert data["writes"]==["snapshot-create","create","create"]

def test_railway_rollback_uses_retained_source_and_binding(project,tmp_path,monkeypatch):
    class Retained(Adapter):
        def rollback_steps(self,manifest,release,artifact=None):return self.plan(manifest)
    monkeypatch.setattr(core,"_adapter",lambda m:Retained())
    m=yaml.safe_load(project.read_text());m["rollback"]={"compatible":True};m["variables"]={"APP_SECRET":{"secret":True}}
    project.write_text(yaml.safe_dump(m));env=project.parent/".env";env.write_text("APP_SECRET=never-in-archive-synthetic-secret");env.chmod(0o600)
    state=tmp_path/"state"; data={"version":0,"uploads":[]}
    def transport(step,**kwargs):
        if step.get("mutating"):
            data["version"]+=1;data["uploads"].append((Path(kwargs["cwd"])/"Dockerfile").read_text())
            value={"deployment_id":"release-"+str(data["version"])}
        elif step["id"]=="status":value={"services":[{"id":"s","instances":[{"environmentId":"e","deployment":{"id":"release-"+str(data["version"]),"status":"SUCCESS"}}]}]}
        else:value={"generation":data["version"]}
        return {"stdout":json.dumps(value),"stderr":"","returncode":0}
    monkeypatch.setattr(core.runner,"run",transport)
    first=plan(project,env_file=env,inspect_remote=True);a=core.deploy(first,env_file=env,approval=first["id"],state_dir=state)
    (project.parent/"Dockerfile").write_text("FROM scratch\n# second release\n")
    second=plan(project,env_file=env,inspect_remote=True);core.deploy(second,env_file=env,approval=second["id"],state_dir=state)
    rollback=core.rollback(second["id"],first["id"],state_dir=state)
    assert rollback["source"]["root"]==a["retained_source"]["root"]
    for path in Path(a["retained_source"]["root"]).rglob("*"):
        if path.is_file(): assert b"never-in-archive-synthetic-secret" not in path.read_bytes()
    core.deploy(rollback,env_file=env,approval=rollback["id"],state_dir=state)
    assert data["uploads"]==["FROM scratch\n","FROM scratch\n# second release\n","FROM scratch\n"]
    (Path(a["retained_source"]["root"])/"Dockerfile").write_text("tampered")
    with pytest.raises(OnedeployError):core.rollback(second["id"],first["id"],state_dir=state)

def test_structured_output_redaction_preserves_json_scalars(project,monkeypatch):
    monkeypatch.setattr(core.runner,"run",lambda *a,**kw:{"stdout":'{"ready":true,"count":1,"id":"public"}'})
    m=yaml.safe_load(project.read_text());m["variables"]={"FLAG":{"secret":False,"type":"boolean"},"N":{"secret":False,"type":"integer"}}
    result=core._execute(Adapter(),{"id":"status"},m,project.parent,{"FLAG":"true","N":"1"})
    assert result=={"ready":True,"count":1,"id":"public"}

def test_snapshot_recovery_reuses_receipt_without_rebuilding(project,tmp_path,monkeypatch):
    class Snapshot(Adapter):
        def plan(self,manifest,release_tag=None):
            return ([{"id":"snapshot-create","argv":["daytona","snapshot"],"mutating":True}] if manifest.get("build") else [])+[{"id":"create","argv":["daytona","create"],"mutating":True}]
        def rollback_steps(self,manifest,release,artifact=None):
            return [{"id":"create","argv":["daytona","create"],"mutating":True,"artifact":artifact}]
        def assert_preconditions(self,manifest,planned,observed,completed):
            expected=json.loads(json.dumps(planned))
            receipts=[op["result"] for op in completed if op["id"]=="snapshot-create" and op["state"]=="succeeded"]
            if receipts:
                expected["inspect"].pop("snapshot_pending",None);expected["inspect"].update(receipts[0])
            assert expected==observed
    monkeypatch.setattr(core,"_adapter",lambda m:Snapshot())
    m=yaml.safe_load(project.read_text());m.update(provider="daytona",target={"account":"a","sandbox":"new","mode":"create"},build={"context":".","dockerfile":"Dockerfile","snapshot_name":"new-image"})
    project.write_text(yaml.safe_dump(m));state=tmp_path/"state"
    receipt={"snapshot_id":"snapshot-id","snapshot_image":"example.invalid/app@sha256:"+"b"*64}
    data={"built":False,"fail":True,"builds":0,"creates":0}
    def transport(step,**kwargs):
        op=step["id"]
        if op=="snapshot-create":data["built"]=True;data["builds"]+=1;result=receipt
        elif op=="create":
            data["creates"]+=1
            if data["fail"]:raise OnedeployError("uncertain","create transport interrupted",uncertain=True,mutation_possible=True)
            assert step["artifact"]["snapshot_id"]==receipt["snapshot_id"]
            result={"id":"new-sandbox","state":"started"}
        elif op=="identity":result={"account":"a"}
        elif op=="status":result={"id":"new-sandbox","state":"started"}
        else:
            result={"exists":False}
            result.update(receipt if data["built"] else {"snapshot_pending":True})
        return {"stdout":json.dumps(result),"stderr":"","returncode":0}
    monkeypatch.setattr(core.runner,"run",transport)
    original=plan(project,inspect_remote=True)
    with pytest.raises(OnedeployError):core.deploy(original,approval=original["id"],state_dir=state)
    report=core.resume(original["id"],state_dir=state)
    resolution=core.resume(original["id"],state_dir=state,resolve_with_observation=report["observation_id"],outcome="operator-confirmed-not-executed")
    recovery=resolution["plan"]
    assert "build" not in recovery["manifest"] and recovery["manifest"]["target"]["snapshot"]==receipt["snapshot_id"]
    assert [op["id"] for op in recovery["operations"]]==["create"]
    data["fail"]=False
    assert core.deploy(recovery,approval=recovery["id"],state_dir=state)["state"]=="succeeded"
    assert data["builds"]==1 and data["creates"]==2

def test_daytona_health_only_probe_is_dispatched(project,tmp_path,monkeypatch):
    m=yaml.safe_load(project.read_text())
    m.update(provider="daytona",target={"account":"a","sandbox":"sandbox-id"},runtime={"port":8000,"health_path":"/health"})
    project.write_text(yaml.safe_dump(m))
    calls=[]
    def transport(step,**kwargs):
        calls.append(step["id"])
        if step["id"]=="verify":
            value={"process_running":True,"health_verified":True,"readiness_verified":False}
        else:
            value={"id":"sandbox-id","state":"started","accepted_release_id":"sandbox-id"}
        return {"stdout":json.dumps(value),"stderr":"","returncode":0}
    monkeypatch.setattr(core.runner,"run",transport)
    p=plan(project,inspect_remote=True)
    result=core.deploy(p,approval=p["id"],state_dir=tmp_path/"state")
    assert calls.count("verify")==1
    assert result["verification"]["health_verified"] is True
    assert result["layers"]["process_running"] is True
    assert result["layers"]["readiness_passed"] is False

@pytest.mark.parametrize("field",["health_path","ready_path"])
@pytest.mark.parametrize("value",["/health?token=x","/health#fragment","/health\n","//other.invalid","/health\\x","/health space"])
def test_invalid_probe_path_rejected_before_provider_calls(project,fake_transport,field,value):
    m=yaml.safe_load(project.read_text());m["runtime"]={field:value}
    project.write_text(yaml.safe_dump(m))
    with pytest.raises(OnedeployError) as error:
        plan(project,inspect_remote=True)
    assert error.value.code=="invalid_config"
    assert not fake_transport

def test_listener_metadata_warning_in_plan_and_inspect(project):
    m=yaml.safe_load(project.read_text());m["runtime"]={"port":8080}
    project.write_text(yaml.safe_dump(m))
    p=plan(project)
    inspected=core.inspect(project)
    for result in (p,inspected):
        assert any("runtime.port is descriptive metadata only" in warning and "does not configure or verify" in warning for warning in result["warnings"])

@pytest.mark.parametrize("provider",["railway","daytona","modal"])
@pytest.mark.parametrize("excluded",[False,True])
def test_required_deployment_inputs_must_be_bundled(project,fake_transport,provider,excluded):
    m=yaml.safe_load(project.read_text());m["provider"]=provider
    if provider=="modal":
        m["source"]["entrypoint"]="app.py"
        required=project.parent/"app.py"
    else:
        required=project.parent/"Dockerfile"
        if provider=="daytona":m["build"]={"snapshot_name":"new-image"}
    project.write_text(yaml.safe_dump(m))
    if excluded:
        required.write_text("APP=1\n");required.chmod(0o600)
    elif required.exists():required.unlink()
    with pytest.raises(OnedeployError) as error:
        plan(project,inspect_remote=True,env_file=required if excluded else None)
    assert error.value.code=="invalid_config"
    assert not fake_transport
