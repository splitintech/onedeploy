import os
from pathlib import Path
import pytest
from onedeploy.config import load_manifest, load_env, resolve_variables, validate_manifest
from onedeploy.errors import OnedeployError

@pytest.fixture
def manifest():
    return {"version":1,"name":"example","provider":"railway","profile":"docker-http",
            "source":{"path":"."},"target":{"account":"test","project":"p","environment":"e","service":"s"}}

def envfile(tmp_path,text):
    p=tmp_path/".env"; p.write_text(text); p.chmod(0o600); return p

def test_dotenv_literal_not_executed(tmp_path):
    p=envfile(tmp_path,"A=$(touch-pwned)\nB=${HOME}\nC='quoted value' # comment\nD=\"hello\\nworld\"\n")
    assert load_env(p)=={"A":"$(touch-pwned)","B":"${HOME}","C":"quoted value","D":"hello\nworld"}
    assert not (tmp_path/"pwned").exists()

@pytest.mark.parametrize("text",["A=1\nA=2", "export A=1", "A='unterminated", "A=x y", "A=\\bad", "A='ok' garbage", "A=\"bad\\q\"", "=secret", "A='multi\nline'"])
def test_dotenv_invalid_redacted(tmp_path,text):
    with pytest.raises(OnedeployError) as error: load_env(envfile(tmp_path,text))
    assert error.value.code=="invalid_config"
    assert text not in str(error.value)

def test_env_private_permissions(tmp_path):
    p=envfile(tmp_path,"KEY=secret"); p.chmod(0o644)
    with pytest.raises(OnedeployError): load_env(p)

def test_symlink_rejected(tmp_path):
    original=envfile(tmp_path,"KEY=secret"); link=tmp_path/"link"; link.symlink_to(original)
    with pytest.raises(OnedeployError): load_env(link)

@pytest.mark.parametrize("text",["name: x\nname: y", "a: &anchor foo\nb: *anchor", "!!python/object/apply:os.system ['echo bad']", "[]"])
def test_yaml_dangerous_rejected(tmp_path,text):
    p=tmp_path/"m.yaml";p.write_text(text)
    with pytest.raises(OnedeployError): load_manifest(p)

def test_unknown_fields(manifest):
    manifest["arbitrary_hook"]="touch pwned"
    with pytest.raises(OnedeployError): validate_manifest(manifest)

def test_secret_default_rejected(manifest):
    manifest["variables"]={"PASSWORD":{"secret":True,"default":"sensitive"}}
    with pytest.raises(OnedeployError) as error: validate_manifest(manifest)
    assert "sensitive" not in str(error.value)

def test_precedence_and_types(tmp_path,monkeypatch,manifest):
    manifest["variables"]={"COUNT":{"type":"integer","secret":False,"default":1,"source":"env:EXPLICIT_COUNT"}}
    p=envfile(tmp_path,"EXPLICIT_COUNT=2")
    assert resolve_variables(manifest,p)=={"COUNT":"2"}
    monkeypatch.setenv("EXPLICIT_COUNT","3")
    assert resolve_variables(manifest,p)=={"COUNT":"3"}
    assert resolve_variables(manifest,p,{"COUNT":"4"})=={"COUNT":"4"}

def test_empty_required_rejected(manifest):
    manifest["variables"]={"EMPTY_REQUIRED_VALUE":{"required":True}}
    with pytest.raises(OnedeployError): resolve_variables(manifest,env_overrides={"EMPTY_REQUIRED_VALUE":""})

def test_boolean_not_integer(manifest):
    manifest["variables"]={"N":{"type":"integer","secret":False,"default":True}}
    with pytest.raises(OnedeployError): validate_manifest(manifest)

def test_excessive_yaml_nesting(tmp_path):
    p=tmp_path/"m.yaml";p.write_text("a: "+"["*30+"0"+"]"*30)
    with pytest.raises(OnedeployError): load_manifest(p)

@pytest.mark.parametrize("source",["env:RAILWAY_API_TOKEN","env:DAYTONA_API_KEY","env:MODAL_TOKEN_SECRET","env:LD_PRELOAD","env:HOME"])
def test_control_credential_alias_rejected(manifest,source):
    manifest["variables"]={"APP_SECRET":{"source":source}}
    with pytest.raises(OnedeployError): validate_manifest(manifest)

def test_conditional_required_is_deterministic(manifest):
    manifest["variables"]={"ENABLED":{"type":"boolean","secret":False,"default":False},
                           "CONDITIONAL_SECRET":{"required_if":{"variable":"ENABLED","equals":True}}}
    validate_manifest(manifest)
    assert resolve_variables(manifest)=={"ENABLED":"false"}
    with pytest.raises(OnedeployError): resolve_variables(manifest,env_overrides={"ENABLED":True})
    assert resolve_variables(manifest,env_overrides={"ENABLED":True,"CONDITIONAL_SECRET":"present"})["CONDITIONAL_SECRET"]=="present"

def test_conditional_cycles_and_secret_controls_rejected(manifest):
    manifest["variables"]={"A":{"secret":False,"required_if":{"variable":"B","equals":"x"}},
                           "B":{"secret":False,"required_if":{"variable":"A","equals":"x"}}}
    with pytest.raises(OnedeployError):validate_manifest(manifest)
    manifest["variables"]["B"]={"secret":True}
    with pytest.raises(OnedeployError):validate_manifest(manifest)

def test_dependency_reference_tls_and_redaction(manifest):
    manifest["variables"]={"DATABASE_URL":{"secret":True,"required":True}}
    manifest["dependencies"]=[{"name":"database","kind":"postgres","variable":"DATABASE_URL","tls":True,"required":True}]
    validate_manifest(manifest)
    good="postgresql://user:synthetic-secret@db.example.invalid/app?sslmode=verify-full"
    assert resolve_variables(manifest,env_overrides={"DATABASE_URL":good})["DATABASE_URL"]==good
    with pytest.raises(OnedeployError) as error:resolve_variables(manifest,env_overrides={"DATABASE_URL":"postgresql://user:synthetic-secret@db.example.invalid/app"})
    assert "synthetic-secret" not in str(error.value)

@pytest.mark.parametrize("mount",["/","/etc/keys","/data/../etc","/proc/files","relative"])
def test_unsafe_storage_mounts_rejected(manifest,mount):
    manifest["storage"]=[{"name":"cache","resource_id":"existing-cache","mount_path":mount,"kind":"model-cache","access":"read-write","single_writer":False}]
    with pytest.raises(OnedeployError):validate_manifest(manifest)

def test_checks_require_readonly_and_secret_reference(manifest):
    manifest["variables"]={"AUTH":{"secret":True}}
    manifest["checks"]=[{"name":"authenticated","path":"/whoami","expected_status":200,"auth_variable":"AUTH","unauthenticated_status":401,"safety":"read-only"}]
    validate_manifest(manifest)
    manifest["checks"][0]["path"]="//attacker.invalid"
    with pytest.raises(OnedeployError):validate_manifest(manifest)

@pytest.mark.parametrize("checks",[[{"name":"unsafe","path":"/send"}], [{"name":"auth","path":"/private","safety":"read-only","unauthenticated_status":401}]])
def test_ambiguous_checks_rejected(manifest,checks):
    manifest["checks"]=checks
    with pytest.raises(OnedeployError):validate_manifest(manifest)

@pytest.mark.parametrize("token",[None,"","has space","header\r\ninjection"])
def test_auth_check_missing_or_invalid_credential_fails_before_execution(manifest,token):
    manifest["variables"]={"BEARER":{"secret":True,"required":False}}
    manifest["checks"]=[{"name":"auth","path":"/private","auth_variable":"BEARER","safety":"read-only"}]
    with pytest.raises(OnedeployError):resolve_variables(manifest,env_overrides={"BEARER":token} if token is not None else {})

def test_unimplemented_build_image_rejected(manifest):
    manifest["build"]={"image":"example.invalid/app@sha256:"+"a"*64}
    with pytest.raises(OnedeployError):validate_manifest(manifest)

@pytest.mark.parametrize("typ,value,expected,canonical",[("boolean","TRUE",True,"true"),("boolean","FALSE",False,"false"),("integer","01",1,"1"),("integer","-0",0,"0")])
def test_conditional_comparison_canonicalizes_typed_inputs(manifest,typ,value,expected,canonical):
    manifest["variables"]={"CONTROL":{"type":typ,"secret":False},"DEPENDENT":{"required_if":{"variable":"CONTROL","equals":expected}}}
    validate_manifest(manifest)
    with pytest.raises(OnedeployError):resolve_variables(manifest,env_overrides={"CONTROL":value})
    resolved=resolve_variables(manifest,env_overrides={"CONTROL":value,"DEPENDENT":"present"})
    assert resolved["CONTROL"]==canonical
