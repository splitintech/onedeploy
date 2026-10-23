"""Profile planning retains the legacy engine's approval and binding boundaries."""
import copy
import json
import sys
from types import SimpleNamespace

import pytest
from onedeploy import core
from onedeploy.errors import OnedeployError


@pytest.fixture
def profile_case(tmp_path, monkeypatch):
    root=tmp_path/'app';root.mkdir()
    (root/'Dockerfile').write_text('FROM scratch\n')
    manifest={'version':1,'name':'demo','provider':'railway','profile':'docker-http',
              'source':{'path':'.'},'build':{'context':'.','dockerfile':'Dockerfile'},
              'target':{'account':'owner','project':'project','environment':'env','service':'service'}}
    profile={'kind':'profile','version':1,'manifest':manifest,'id':'synthetic-profile'}
    calls=[]
    def validate(value):
        calls.append(copy.deepcopy(value))
        return copy.deepcopy(value['manifest']),root
    module=SimpleNamespace(validate_profile=validate, read_profile=lambda path:copy.deepcopy(profile))
    monkeypatch.setitem(sys.modules,'onedeploy.discovery',module)
    import onedeploy
    monkeypatch.setattr(onedeploy,'discovery',module,raising=False)
    monkeypatch.setattr(core,'_observe',lambda *args:{'identity':'synthetic-observation'})
    return root,profile,module,calls


def test_profile_no_handwritten_manifest_or_provider_calls(profile_case,monkeypatch):
    root,profile,module,calls=profile_case
    monkeypatch.setattr(core.runner,'run',lambda *a,**k:pytest.fail('offline plan called provider'))
    plan=core.make_plan_from_profile(profile,allow_dirty=True)
    assert plan['manifest_path'] is None
    assert plan['normalized_profile']==profile
    assert plan['remote_preconditions'] is None
    assert not (root/'onedeploy.yaml').exists()
    assert (root/'Dockerfile').read_text()=='FROM scratch\n'
    assert len(calls)==1
    core.verify_plan(plan)
    profile['manifest']['target']['service']='edited'
    assert plan['manifest']['target']['service']=='service'
    assert plan['normalized_profile']['manifest']['target']['service']=='service'


def test_profile_plan_roundtrip_and_tamper(profile_case,tmp_path):
    _,profile,_,_=profile_case
    plan=core.make_plan_from_profile(profile,allow_dirty=True)
    destination=tmp_path/'preview.json'
    core.save_plan(plan,destination)
    assert core.read_plan(destination)==plan
    plan['normalized_profile']['id']='tampered'
    with pytest.raises(OnedeployError,match='canonical'):
        core.verify_plan(plan)


def test_stale_profile_rejected_before_provider_write(profile_case,monkeypatch):
    _,profile,module,_=profile_case
    plan=core.make_plan_from_profile(profile,allow_dirty=True,inspect_remote=True)
    def stale(value):raise OnedeployError('stale_plan','Imported input changed')
    module.validate_profile=stale
    monkeypatch.setattr(core.runner,'run',lambda *a,**k:pytest.fail('stale profile called provider'))
    with pytest.raises(OnedeployError,match='Imported input changed'):
        core.deploy(plan,approval=plan['id'])


def test_profile_no_approval_or_preview_execution(profile_case,monkeypatch):
    _,profile,_,_=profile_case
    plan=core.make_plan_from_profile(profile,allow_dirty=True)
    monkeypatch.setattr(core.runner,'run',lambda *a,**k:pytest.fail('unapproved call'))
    with pytest.raises(OnedeployError) as error:
        core.deploy(plan)
    assert error.value.code=='approval_required'
    with pytest.raises(OnedeployError) as error:
        core.deploy(plan,approval=plan['id'])
    assert error.value.code=='stale_plan'


def test_profile_runtime_secret_never_serialized(profile_case,tmp_path):
    _,profile,_,_=profile_case
    profile['manifest']['variables']={'APP_SECRET':{'secret':True,'source':'env:APP_SECRET'}}
    env=tmp_path/'app.env';env.write_text('APP_SECRET=synthetic-sensitive-value\n');env.chmod(0o600)
    plan=core.make_plan_from_profile(profile,env_file=env,allow_dirty=True)
    assert 'synthetic-sensitive-value' not in json.dumps(plan)


def test_inspect_profile_offline_parity(profile_case,monkeypatch):
    _,profile,_,_=profile_case
    monkeypatch.setattr(core.runner,'run',lambda *a,**k:pytest.fail('inspection called provider'))
    observed=core.inspect_profile(profile)
    assert observed['remote_inspected'] is False
    assert observed['manifest']['target']==profile['manifest']['target']
    assert observed['operations']


def test_profile_output_protects_input_and_is_exclusive(profile_case):
    root,profile,_,_=profile_case
    with pytest.raises(OnedeployError,match='outside discovered source'):
        core.make_plan_from_profile(profile,allow_dirty=True,plan_output=root/'preview.json')
    path=root/'.onedeploy'/'preview.json'
    plan=core.make_plan_from_profile(profile,allow_dirty=True,plan_output=path)
    core.save_plan(plan,path)
    original=path.read_bytes()
    with pytest.raises(OnedeployError) as error:core.save_plan(plan,path)
    assert error.value.code=='conflict'
    assert path.read_bytes()==original


def test_rehashed_plan_cannot_disagree_with_profile_configuration(profile_case,monkeypatch):
    _,profile,_,_=profile_case
    plan=core.make_plan_from_profile(profile,allow_dirty=True,inspect_remote=True)
    plan['manifest']['target']['service']='different'
    plan['id']=core._plan_id(plan)
    monkeypatch.setattr(core.runner,'run',lambda *a,**k:pytest.fail('inconsistent profile called provider'))
    with pytest.raises(OnedeployError,match='differs from the reviewed configuration'):
        core.deploy(plan,approval=plan['id'])


def test_arbitrary_receipt_cannot_bypass_profile_consistency(profile_case,monkeypatch):
    _,profile,_,_=profile_case
    plan=core.make_plan_from_profile(profile,allow_dirty=True,inspect_remote=True)
    plan['manifest']['target']['service']='different'
    plan['artifact_receipt']={'snapshot_id':'unrelated'}
    plan['id']=core._plan_id(plan)
    monkeypatch.setattr(core.runner,'run',lambda *a,**k:pytest.fail('forged receipt called provider'))
    with pytest.raises(OnedeployError,match='journal-bound Daytona'):
        core.deploy(plan,approval=plan['id'])
