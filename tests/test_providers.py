import json
from types import SimpleNamespace
import pytest
from onedeploy.providers import get_adapter
from onedeploy.providers import daytona_bridge
from onedeploy.errors import OnedeployError


def manifest(provider):
    targets = {'railway':{'account':'user@example.com','project':'proj','environment':'env','service':'svc'},
               'modal':{'account':'workspace','environment':'main','app':'my-app'},
               'daytona':{'account':'org','sandbox':'sandbox','snapshot':'snap','mode':'create'}}
    return {'version':1,'name':'demo','provider':provider,'profile':'asgi',
            'source':({'path':'.','entrypoint':'app.py','trust_code':True} if provider=='modal' else {'path':'.'}),'target':targets[provider],
            'variables':{'API_KEY':{'type':'string','secret':True}}}


@pytest.mark.parametrize('provider',['railway','modal','daytona'])
def test_plans_contain_no_secret_values_and_no_shell(provider):
    adapter = get_adapter(provider)
    steps = adapter.plan(manifest(provider))
    assert steps and all(isinstance(s['argv'],list) and s['mutating'] for s in steps)
    assert not any('shell' in s for s in steps)
    assert 'API_KEY=' not in json.dumps(steps)
    assert adapter.capabilities['qualification'] == 'experimental/offline-tested'


def test_railway_secret_stdin_and_exact_target():
    steps = get_adapter('railway').plan(manifest('railway'))
    assert steps[0]['argv'] == ['railway','variable','set','API_KEY','--stdin','--skip-deploys','--project','proj','--service','svc','--environment','env']
    assert steps[0]['stdin_env_keys'] == ['API_KEY']
    assert steps[0]['stdin_format'] == 'raw'
    assert steps[-1]['argv'] == ['railway','up','.','--project','proj','--service','svc','--environment','env','--ci','--json']


def test_railway_inspection_projects_secret_fields_out():
    adapter = get_adapter('railway')
    raw = {'id':'proj','name':'test','env':{'SECRET':'do-not-return'},
           'environments':{'edges':[{'node':{'id':'env','name':'main'}}]},
           'services':{'edges':[{'node':{'id':'svc','name':'web','serviceInstances':{'edges':[{'node':{
               'environmentId':'env','serviceId':'svc','latestDeployment':{'id':'dep','status':'SUCCESS','secret':'do-not-return'}}}]}}}]}}
    parsed = adapter.parse('inspect',json.dumps(raw))
    assert 'do-not-return' not in json.dumps(parsed)
    assert parsed['services'][0]['instances'][0]['deployment']['id']=='dep'
    assert adapter.assert_target(manifest('railway'),{'email':'user@example.com'},parsed)
    with pytest.raises(OnedeployError):
        adapter.assert_target(manifest('railway'),{'email':'wrong@example.com'},parsed)


@pytest.mark.parametrize('provider',['railway','modal','daytona'])
@pytest.mark.parametrize('bad',['--help','a; rm -rf /','$(whoami)','https://evil.test','a\nsecret'])
def test_target_injection_rejected(provider,bad):
    m=manifest(provider); m['target']['account']=bad
    with pytest.raises(OnedeployError): get_adapter(provider).plan(m)


@pytest.mark.parametrize('key',['PATH','HOME','DAYTONA_API_KEY','MODAL_TOKEN_SECRET','PYTHONPATH','LD_PRELOAD'])
def test_control_plane_cannot_be_runtime_variable(key):
    m=manifest('modal'); m['variables']={key:{'secret':True}}
    with pytest.raises(OnedeployError): get_adapter('modal').plan(m)


def test_modal_trust_and_runtime_env():
    m=manifest('modal'); adapter=get_adapter('modal')
    assert adapter.plan(m)[0]['runtime_env_keys']==['API_KEY']
    assert adapter.plan(m)[0]['env_keys']==[]
    m['source']['trust_code']=False
    with pytest.raises(OnedeployError): adapter.plan(m)


def test_modal_profile_and_app_evidence():
    adapter=get_adapter('modal'); m=manifest('modal')
    identity=adapter.parse('identity','[{"name":"default","workspace":"workspace","active":true}]')
    inspection=adapter.parse('inspect','{"app_id":"ap-123","description":"my-app","lifecycle":{"version":2,"state":"deployed"},"secret":"hidden"}')
    assert adapter.assert_target(m,identity,inspection)
    assert inspection['lifecycle']['version']==2
    assert 'hidden' not in json.dumps(inspection)
    with pytest.raises(OnedeployError): adapter.parse('identity','[{"active":true,"workspace":"Unknown (authentication failure)"}]')


def test_modal_create_proves_absence():
    adapter=get_adapter('modal'); m=manifest('modal');m['target']['mode']='create'
    assert adapter.command(m,'inspect')['argv']==['modal','app','list','--env','main','--json']
    assert adapter.assert_target(m,{'workspace':'workspace'},{'apps':[]})['inspection']['exists'] is False
    with pytest.raises(OnedeployError):
        adapter.assert_target(m,{'workspace':'workspace'},{'apps':[{'description':'my-app'}]})


def test_modal_rollback_is_specific_separate_write():
    adapter=get_adapter('modal')
    step=adapter.rollback_steps(manifest('modal'),'v3')[0]
    assert step['argv']==['modal','app','rollback','my-app','v3','--env','main']
    assert step['mutating']
    with pytest.raises(OnedeployError): adapter.rollback_steps(manifest('modal'),'latest')


def test_known_artifact_rollback_contracts():
    railway=get_adapter('railway')
    assert railway.rollback_steps(manifest('railway'),'local-deployment')==railway.plan(manifest('railway'))
    daytona=get_adapter('daytona')
    with pytest.raises(OnedeployError):daytona.rollback_steps(manifest('daytona'),'local-deployment')
    artifact={'snapshot_id':'snap-id','snapshot_image':'image@sha256:'+'a'*64}
    operation=daytona.rollback_steps(manifest('daytona'),'local-deployment',artifact)[0]
    assert json.loads(operation['argv'][-1])['snapshot_receipt']==artifact
    assert operation['id']=='create' and operation['mutating']


def test_daytona_bridge_configuration_is_nonsecret():
    step=get_adapter('daytona').plan(manifest('daytona'))[0]
    assert step['argv'][1]=='-I'
    assert step['argv'][2].endswith('/providers/daytona_bridge.py')
    assert step['stdin_format']=='json'
    assert step['stdin_env_keys']==['API_KEY']
    assert step['env_keys']==['DAYTONA_API_KEY']
    config=json.loads(step['argv'][-1]); assert config['target']['sandbox']=='sandbox'


class Missing(Exception): pass


def sandbox(**overrides):
    return SimpleNamespace(id='sb-id',name='sandbox',organization_id='org',state='started',
                           snapshot='snap',public=False,auto_stop_interval=15,**overrides)


def test_daytona_real_sdk_construction_private_secret_runtime_and_absence():
    calls=[]
    def get(*args,**kwargs): raise Missing()
    def create(params,**kwargs): calls.append((params,kwargs));return sandbox()
    client=SimpleNamespace(get=get,create=create,snapshot=SimpleNamespace(get=lambda x:SimpleNamespace(id='snap-id',image_name='image@sha256:'+'a'*64,organization_id='org')))
    config={'target':manifest('daytona')['target'],'auto_stop_minutes':15,'timeout':60}
    observed=daytona_bridge.execute('inspect',config,{},client,Missing,SimpleNamespace)
    assert observed=={'exists':False,'account':'org','snapshot_id':'snap-id','snapshot_image':'image@sha256:'+'a'*64,'volumes':[]}
    assert calls==[]
    result=daytona_bridge.execute('create',config,{'API_KEY':'synthetic-secret'},client,Missing,SimpleNamespace)
    assert calls[0][0].env_vars=={'API_KEY':'synthetic-secret'}
    assert calls[0][0].public is False and calls[0][0].auto_delete_interval == -1
    assert calls[0][0].target == 'us'
    assert 'synthetic-secret' not in json.dumps(result)
    assert result['readiness_verified'] is False


def test_daytona_create_never_replays_existing_resource():
    client=SimpleNamespace(get=lambda *a,**k:sandbox())
    config={'target':manifest('daytona')['target'],'auto_stop_minutes':15,'timeout':60}
    with pytest.raises(ValueError,match='already exists'):
        daytona_bridge.execute('create',config,{},client,Missing,SimpleNamespace)


def test_daytona_permission_error_is_not_absence():
    def denied(*args,**kwargs): raise PermissionError('denied')
    client=SimpleNamespace(get=denied)
    config={'target':manifest('daytona')['target'],'auto_stop_minutes':15,'timeout':60}
    with pytest.raises(PermissionError): daytona_bridge.execute('inspect',config,{},client,Missing,SimpleNamespace)


def test_daytona_existing_secrets_are_rejected_not_ignored():
    m=manifest('daytona');m['target']['mode']='existing'
    with pytest.raises(OnedeployError):get_adapter('daytona').plan(m)


@pytest.mark.parametrize('provider',['railway','modal','daytona'])
def test_malformed_read_responses_fail(provider):
    with pytest.raises(OnedeployError):get_adapter(provider).parse('inspect','not-json')


def test_no_fake_deployment_readiness():
    with pytest.raises(OnedeployError): get_adapter('railway').parse('deploy','anything')
    assert get_adapter('modal').parse('deploy','anything')['readiness_verified'] is False

@pytest.mark.parametrize('provider',['railway','modal'])
def test_private_ingress_is_not_silently_ignored(provider):
    m=manifest(provider);m['ingress']={'mode':'private'}
    with pytest.raises(OnedeployError):get_adapter(provider).plan(m)


def test_existing_public_daytona_cannot_claim_private():
    m=manifest('daytona');m['variables']={};m['target']['mode']='existing';m['ingress']={'mode':'private'}
    with pytest.raises(OnedeployError):
        get_adapter('daytona').assert_target(m,{'account':'org'},{'exists':True,'account':'org','name':'sandbox','snapshot':'snap','public':True})


def test_daytona_readiness_probe_is_fixed_and_no_response_body_saved():
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs));return SimpleNamespace(exit_code=0,result='[true,false]')
    sb=sandbox(process=SimpleNamespace(exec=run))
    result=daytona_bridge.verify(sb,{'port':8000,'health_path':'/health','ready_path':'/ready'})
    assert result=={'process_running':True,'health_verified':True,'readiness_verified':False}
    assert calls[0][1]['timeout']==20
    assert '127.0.0.1' in calls[0][0] and 'python3 -I' in calls[0][0]
    with pytest.raises(ValueError):daytona_bridge.verify(sb,{'port':8000,'ready_path':'/;cat /etc/passwd'})
    assert len(calls)==1


def test_daytona_logs_are_explicit_bounded_entrypoint_read():
    sb=sandbox(process=SimpleNamespace(get_entrypoint_logs=lambda **k:SimpleNamespace(output='x'*70000)))
    client=SimpleNamespace(get=lambda *a,**k:sb)
    config={'target':manifest('daytona')['target'],'timeout':60}
    result=daytona_bridge.execute('logs',config,{},client,Missing,SimpleNamespace)
    assert result['truncated'] and len(result['logs'])==65536


def test_railway_variable_write_uses_explicit_project_without_link():
    steps=get_adapter('railway').plan(manifest('railway'))
    assert '--project' in steps[0]['argv']
    assert 'cwd_role' not in steps[0]
    assert get_adapter('railway').command(manifest('railway'),'inspect')['argv']==['railway','status','--project','proj','--environment','env','--json']

@pytest.mark.parametrize('url',['https://evil.test/ready','http://web.up.railway.app/ready','https://user:pass@web.up.railway.app/ready','https://web.up.railway.app/ready?token=secret','https://web.up.railway.app:bad/ready','https://web.up.railway.app.evil.test/ready'])
def test_readiness_endpoint_rejects_untrusted_or_secret_url(url):
    m=manifest('railway');m['runtime']={'ready_url':url}
    with pytest.raises(OnedeployError):get_adapter('railway').validate(m)


def test_readiness_endpoint_explicitly_bound_not_guessed():
    m=manifest('railway');a=get_adapter('railway')
    assert a.readiness_endpoint(m,{}) is None
    m['runtime']={'ready_url':'https://my-app.up.railway.app','ready_path':'/ready'}
    assert a.readiness_endpoint(m,{}) is None  # User-configured origin is not target-ownership evidence.


def test_daytona_mutable_snapshot_rejected():
    def get(*a,**k):raise Missing()
    client=SimpleNamespace(get=get,snapshot=SimpleNamespace(get=lambda x:SimpleNamespace(id='snap-id',image_name='image:latest',organization_id='org')))
    with pytest.raises(ValueError,match='immutable'):
        daytona_bridge.execute('inspect',{'target':manifest('daytona')['target']},{},client,Missing,SimpleNamespace)

@pytest.mark.parametrize('provider,version',[('railway','5.71.0'),('modal','1.6.2'),('daytona','0.226.0')])
def test_exact_offline_contract_versions(provider,version):
    adapter=get_adapter(provider)
    assert adapter.version_command()['mutating'] is False
    assert adapter.parse_version('version '+version)['version']==version
    with pytest.raises(OnedeployError):adapter.parse_version('version 99.0.0')


def test_railway_pinned_status_environment_instances_and_trusted_domain():
    # Shape follows v5.71.0 EnvironmentInstances.graphql and status.rs.
    raw={'id':'proj','environments':{'edges':[{'node':{'id':'env','name':'main','serviceInstances':{'edges':[{'node':{
        'id':'instance','serviceId':'svc','environmentId':'env','latestDeployment':{'id':'dep','status':'SUCCESS'},
        'domains':{'serviceDomains':[{'domain':'app.up.railway.app'}],'customDomains':[{'domain':'untrusted.example'}]},
        'startCommand':'SECRET=do-not-store command','source':{'image':'do-not-store'}}}]}}}]},
        'services':{'edges':[{'node':{'id':'svc','name':'web'}}]}}
    adapter=get_adapter('railway');m=manifest('railway');m['runtime']={'ready_path':'/ready'}
    parsed=adapter.parse('status',json.dumps(raw),binding_key=b'test-key')
    adapter.assert_target(m,{'email':'user@example.com'},parsed)
    assert adapter.readiness_endpoint(m,parsed)=='https://app.up.railway.app'
    assert 'do-not-store' not in json.dumps(parsed)
    assert 'untrusted.example' not in json.dumps(parsed)


def test_railway_deploy_binds_returned_id_and_discards_logs_urls():
    text='{"message":"synthetic-secret"}\n'+json.dumps({'deploymentId':'dep-123','projectId':'proj','environmentId':'env','serviceId':'svc','status':'SUCCESS','logsUrl':'https://private.example/?token=secret'})
    result=get_adapter('railway').parse('deploy',text)
    assert result['deployment_id']=='dep-123'
    assert 'secret' not in json.dumps(result)
    assert result['readiness_verified'] is False


def test_modal_success_without_version_remains_unbound():
    result=get_adapter('modal').parse('deploy','Deployed at https://modal.com/apps/example')
    assert result['release_binding']=='unavailable'
    assert 'release_version' not in result
    m=manifest('modal');m['runtime']={'ready_url':'https://other-app.modal.run','ready_path':'/ready'}
    assert get_adapter('modal').readiness_endpoint(m,{'app_id':'ap-real'}) is None


def test_railway_explicit_endpoint_requires_observed_target_domain():
    m=manifest('railway');m['runtime']={'ready_url':'https://unrelated.up.railway.app','ready_path':'/ready'}
    observed={'services':[{'id':'svc','instances':[{'environmentId':'env','domains':['actual.up.railway.app']}]}]}
    assert get_adapter('railway').readiness_endpoint(m,observed) is None
    m['runtime']['ready_url']='https://actual.up.railway.app'
    assert get_adapter('railway').readiness_endpoint(m,observed)=='https://actual.up.railway.app'


def test_modal_plan_tag_and_history_are_provider_supported_correlation():
    a=get_adapter('modal');m=manifest('modal');tag='od-'+'a'*32
    assert a.plan(m,release_tag=tag)[0]['argv'][-2:]==['--tag',tag]
    assert a.command(m,'history')['argv']==['modal','app','history','my-app','--env','main','--json']
    history=a.parse('history',json.dumps([{'version':'v3','tag':tag,'commit':'sensitive-ignored','deployed_by':'ignored'}]))
    assert a.correlate_release(history,tag)=={'release_version':'v3','release_tag':tag}
    assert a.correlate_release(history,'od-'+'b'*32) is None
    assert a.correlate_release({'releases':history['releases']*2},tag) is None
    assert 'sensitive' not in json.dumps(history)


def test_modal_tag_injection_rejected():
    with pytest.raises(OnedeployError):get_adapter('modal').plan(manifest('modal'),release_tag='--evil')


def test_modal_endpoint_requires_actual_function_and_app_identity():
    a=get_adapter('modal');m=manifest('modal');m['target']['function']='web'
    assert a.command(m,'endpoint')['argv']==['modal','function','info','my-app/web','--env','main','--json']
    info=a.parse('endpoint',json.dumps({'app_id':'ap-real','object_id':'fu-real','name':'web','web_info':{'web_url':'https://actual.modal.run','unauthenticated':False},'secrets':['discard']}))
    observed={'app_id':'ap-real','functions':{'web':'fu-real'},'function_info':info}
    assert a.readiness_endpoint(m,observed)=='https://actual.modal.run'
    observed['functions']['web']='fu-other'
    assert a.readiness_endpoint(m,observed) is None
    assert 'secrets' not in info


def test_modal_endpoint_private_or_secret_url_rejected():
    with pytest.raises(OnedeployError):
        get_adapter('modal').parse('endpoint',json.dumps({'app_id':'ap-a','web_info':{'web_url':'https://u:p@actual.modal.run'}}))


def test_daytona_project_build_steps_and_receipt_binding():
    m=manifest('daytona');m['target'].pop('snapshot');m['build']={'context':'.','dockerfile':'Dockerfile','snapshot_name':'new-snapshot'}
    a=get_adapter('daytona');steps=a.plan(m)
    assert [s['id'] for s in steps]==['snapshot-create','create']
    assert 'stdin_env_keys' not in steps[0]
    receipt={'id':'snapshot-create','state':'succeeded','result':{'snapshot_id':'immutable-id','snapshot_image':'image@sha256:'+'b'*64}}
    bound=a.materialize_step(steps[1],[receipt]);assert json.loads(bound['argv'][-1])['snapshot_receipt']==receipt['result']
    assert 'snapshot_receipt' not in steps[1]['argv'][-1]
    with pytest.raises(OnedeployError): a.materialize_step(steps[1],[])
    planned={'identity':{'account':'org'},'inspection':{'exists':False,'account':'org','snapshot_pending':True,'volumes':[]}}
    observed={'identity':{'account':'org'},'inspection':{'exists':False,'account':'org','volumes':[],**receipt['result']}}
    a.assert_preconditions(m,planned,observed,[receipt])
    observed['inspection']['snapshot_id']='other'
    with pytest.raises(OnedeployError): a.assert_preconditions(m,planned,observed,[receipt])


def test_daytona_snapshot_build_uses_strict_bundle_and_no_runtime_secrets(tmp_path):
    calls=[]
    def missing(*args,**kwargs): raise Missing()
    snap=SimpleNamespace(id='snap-id',image_name='image@sha256:'+'a'*64,organization_id='org')
    def create(params,**kwargs): calls.append(('create',params,kwargs));return snap
    class Image:
        @staticmethod
        def from_dockerfile(path,**kwargs): calls.append(('image',path,kwargs));return 'IMAGE'
    client=SimpleNamespace(get=missing,snapshot=SimpleNamespace(get=missing,create=create))
    config={'target':{'account':'org','sandbox':'new','mode':'create'},'build':{'snapshot_name':'new-snapshot'},'bundle_path':str(tmp_path),'timeout':60}
    out=daytona_bridge.execute('snapshot-create',config,{},client,Missing,SimpleNamespace,Image,SimpleNamespace)
    assert out['snapshot_id']=='snap-id'
    assert calls[0][1]==tmp_path/'Dockerfile' and calls[0][2]=={'strict_context':True}
    assert vars(calls[1][1])=={'name':'new-snapshot','image':'IMAGE'}
    client.snapshot.get=lambda _:snap
    with pytest.raises(ValueError):daytona_bridge.execute('snapshot-create',config,{},client,Missing,SimpleNamespace,Image,SimpleNamespace)


def test_daytona_existing_volume_verified_and_resolved_to_id():
    calls=[]
    def missing(*args,**kwargs):raise Missing()
    client=SimpleNamespace(get=missing,snapshot=SimpleNamespace(get=lambda _:SimpleNamespace(id='snap-id',image_name='image@sha256:'+'a'*64,organization_id='org')),
        volume=SimpleNamespace(get=lambda name,create:SimpleNamespace(id='volume-id',name=name,organization_id='org',state='ready')),
        create=lambda params,**kwargs:(calls.append(params) or sandbox()))
    config={'target':manifest('daytona')['target'],'storage':[{'resource_id':'cache','mount_path':'/cache'}],'auto_stop_minutes':15,'timeout':60}
    daytona_bridge.execute('create',config,{'SECRET':'value'},client,Missing,SimpleNamespace,volume_type=SimpleNamespace)
    assert calls[0].volumes[0].volume_id=='volume-id'
    assert calls[0].env_vars=={'SECRET':'value'}
    client.volume.get=lambda *a,**k:SimpleNamespace(id='wrong',name='cache',organization_id='other',state='ready')
    with pytest.raises(ValueError):daytona_bridge.execute('create',config,{},client,Missing,SimpleNamespace,volume_type=SimpleNamespace)


def test_railway_volume_mutation_and_transition():
    m=manifest('railway');m['storage']=[{'name':'data','resource_id':'vol','mount_path':'/data','kind':'volume','access':'read-write','single_writer':True,'attachment':'attach'}]
    a=get_adapter('railway');steps=a.plan(m)
    assert steps[0]['argv']==['railway','volume','--project','proj','--environment','env','--service','svc','attach','--volume','vol','--yes','--json']
    before={'inspection':{'volumes':[{'id':'vol','environmentId':'env','serviceId':None,'mountPath':'/data'}]}}
    after=json.loads(json.dumps(before));after['inspection']['volumes'][0]['serviceId']='svc'
    a.assert_preconditions(m,before,after,[{'id':'attach-volume:vol','state':'succeeded','result':{'volume_id':'vol'}}])
    with pytest.raises(OnedeployError):a.assert_preconditions(m,before,after,[])
    m['storage'][0]['attachment']='existing';assert not any(s['id'].startswith('attach-volume') for s in a.plan(m))


def test_railway_keyed_settings_change_without_deployment_change():
    a=get_adapter('railway');one={'startCommand':'secret command','numReplicas':1}
    two={**one,'startCommand':'other secret command'}
    assert a._settings_binding(one,b'key')!=a._settings_binding(two,b'key')
    assert 'secret' not in a._settings_binding(one,b'key')
    with pytest.raises(OnedeployError):a._settings_binding(one,None)


def test_daytona_auth_checks_use_stdin_and_sdk_env_not_argv():
    m=manifest('daytona');m['runtime']={'port':8000,'ready_path':'/ready'}
    m['checks']=[{'name':'protected','path':'/protected','expected_status':200,'auth_variable':'API_KEY','unauthenticated_status':401,'safety':'read-only'}]
    operation=get_adapter('daytona').command(m,'verify-checks')
    assert operation['stdin_env_keys']==['API_KEY'] and operation['stdin_format']=='json'
    calls=[]
    result={'application_checks_passed':True,'ingress_verified':True,'checks':[{'name':'protected','passed':True,'status':200,'unauthenticated_status':401}],'reason':'requested_checks_passed'}
    def execute(command,**kwargs):calls.append((command,kwargs));return SimpleNamespace(exit_code=0,result=json.dumps(result))
    s=SimpleNamespace(process=SimpleNamespace(exec=execute))
    observed=daytona_bridge.verify_checks(s,{'port':8000,'checks':m['checks']},{'API_KEY':'super-secret'})
    assert observed==result
    assert 'super-secret' not in calls[0][0]
    assert json.loads(calls[0][1]['env']['ONEDEPLOY_CHECK_VALUES'])=={'API_KEY':'super-secret'}
    with pytest.raises(ValueError):daytona_bridge.verify_checks(s,{'port':8000,'checks':m['checks']},{'API_KEY':'bad\nheader'})


def test_daytona_project_pipeline_real_adapter_and_core(tmp_path,monkeypatch):
    import yaml
    from onedeploy import core
    root=tmp_path/'project';root.mkdir();(root/'Dockerfile').write_text('FROM scratch\n')
    m={'version':1,'name':'build-test','provider':'daytona','profile':'docker-http','source':{'path':'.'},
       'target':{'account':'org','sandbox':'new-sandbox','mode':'create'},
       'build':{'context':'.','dockerfile':'Dockerfile','snapshot_name':'new-snapshot'}}
    path=root/'onedeploy.yaml';path.write_text(yaml.safe_dump(m))
    calls=[];state={'snapshot':False,'sandbox':False}
    receipt={'snapshot_id':'snapshot-id','snapshot_image':'image@sha256:'+'a'*64}
    def run(operation,**kwargs):
        op=operation['id'];calls.append(op)
        if op=='version':result={'version':'0.226.0'}
        elif op=='identity':result={'account':'org'}
        elif op in {'inspect','reconcile','status'}:
            if state['sandbox']:result={'exists':True,'id':'sandbox-id','name':'new-sandbox','account':'org','state':'started','snapshot':'snapshot-id','public':False}
            else:result={'exists':False,'account':'org','volumes':[],**(receipt if state['snapshot'] else {'snapshot_pending':True})}
        elif op=='snapshot-create':
            assert (kwargs['cwd']/'Dockerfile').read_text()=='FROM scratch\n'
            assert kwargs['cwd']!=root
            assert not state['snapshot'];state['snapshot']=True;result={**receipt,'account':'org'}
        elif op=='create':
            assert json.loads(operation['argv'][-1])['snapshot_receipt']==receipt
            assert state['snapshot'] and not state['sandbox'];state['sandbox']=True
            result={'exists':True,'id':'sandbox-id','name':'new-sandbox','account':'org','state':'started','snapshot':'snapshot-id','public':False,**receipt}
        else:raise AssertionError(op)
        return {'stdout':json.dumps(result),'stderr':'','returncode':0}
    monkeypatch.setattr(core.runner,'run',run)
    plan=core.make_plan(path,allow_dirty=True,inspect_remote=True)
    out=core.deploy(plan,approval=plan['id'])
    assert state=={'snapshot':True,'sandbox':True}
    assert calls.count('snapshot-create')==1 and calls.count('create')==1


def test_railway_attached_volume_owner_and_replica_proof():
    a=get_adapter('railway');m=manifest('railway');m['storage']=[{'name':'data','resource_id':'vol','mount_path':'/data','kind':'volume','access':'read-write','single_writer':True,'attachment':'existing'}]
    raw={'id':'proj','services':{'edges':[{'node':{'id':'svc'}}]},'environments':{'edges':[{'node':{'id':'env',
         'serviceInstances':{'edges':[{'node':{'id':'instance','serviceId':'svc','environmentId':'env','numReplicas':1}}]},
         'volumeInstances':{'edges':[{'node':{'volume':{'id':'vol','name':'data'},'environmentId':'env','serviceId':'svc','mountPath':'/data','isPendingDeletion':False}}]}}}]}}
    parsed=a.parse('inspect',json.dumps(raw),binding_key=b'key')
    assert a.assert_target(m,{'email':'user@example.com'},parsed)
    parsed['services'][0]['instances'][0]['numReplicas']=2
    with pytest.raises(OnedeployError):a.assert_target(m,{'email':'user@example.com'},parsed)


def test_daytona_rollback_bridge_rejects_wrong_immutable_receipt():
    def missing(*args,**kwargs):raise Missing()
    client=SimpleNamespace(get=missing,snapshot=SimpleNamespace(get=lambda _:SimpleNamespace(id='different',image_name='image@sha256:'+'a'*64,organization_id='org')))
    config={'target':manifest('daytona')['target'],'snapshot_receipt':{'snapshot_id':'expected','snapshot_image':'image@sha256:'+'a'*64},'timeout':60,'auto_stop_minutes':15}
    with pytest.raises(ValueError):daytona_bridge.execute('create',config,{},client,Missing,SimpleNamespace)


@pytest.mark.parametrize('provider',['railway','daytona'])
def test_direct_adapter_rejects_ignored_build_image(provider):
    m=manifest(provider);m['build']={'image':'registry/image@sha256:'+'a'*64}
    with pytest.raises(OnedeployError) as error:get_adapter(provider).validate(m)
    assert error.value.code=='invalid_config'



def test_daytona_checks_only_require_port_not_ready_path():
    m=manifest('daytona');m['runtime']={'port':8000}
    m['checks']=[{'name':'ping','path':'/ping','expected_status':200,'safety':'read-only'}]
    adapter=get_adapter('daytona')
    assert adapter.plan(m)
    operation=adapter.command(m,'verify-checks')
    assert json.loads(operation['argv'][-1])['checks']==m['checks']
    assert json.loads(operation['argv'][-1])['ready_path'] is None
    m['runtime']={}
    with pytest.raises(OnedeployError) as error:adapter.plan(m)
    assert error.value.code=='invalid_config'


@pytest.mark.parametrize('path',['health_path','ready_path'])
def test_daytona_missing_probe_port_rejected_before_plan(path):
    m=manifest('daytona');m['runtime']={path:'/health'}
    with pytest.raises(OnedeployError) as error:get_adapter('daytona').plan(m)
    assert error.value.code=='invalid_config'



@pytest.mark.parametrize('kind',['checks','health'])
def test_railway_endpoint_discovery_does_not_require_readiness_path(kind):
    m=manifest('railway')
    if kind=='health':m['runtime']={'health_path':'/health'}
    else:m['checks']=[{'name':'check','path':'/check','expected_status':200,'safety':'read-only'}]
    remote={'services':[{'id':'svc','instances':[{'environmentId':'env','domains':['target.up.railway.app']}]}]}
    adapter=get_adapter('railway')
    assert adapter.readiness_endpoint(m,remote)=='https://target.up.railway.app'
    m.setdefault('runtime',{})['ready_url']='https://target.up.railway.app'
    assert adapter.readiness_endpoint(m,remote)=='https://target.up.railway.app'


def test_daytona_health_only_is_not_readiness():
    m=manifest('daytona');m['runtime']={'port':8000,'health_path':'/health'}
    operation=get_adapter('daytona').command(m,'verify')
    config=json.loads(operation['argv'][-1])
    sandbox=SimpleNamespace(state='started',process=SimpleNamespace(exec=lambda *args,**kwargs:SimpleNamespace(exit_code=0,result='[true]')))
    observed=daytona_bridge.verify(sandbox,config)
    assert observed=={'process_running':True,'health_verified':True,'readiness_verified':False}



def test_modal_rejects_ignored_startup_override():
    m=manifest('modal');m['runtime']={'command':['python','other.py']}
    with pytest.raises(OnedeployError):get_adapter('modal').plan(m)


@pytest.mark.parametrize('kind',['health_path','ready_path','ready_url','checks'])
def test_modal_probe_requires_target_function_preflight(kind):
    m=manifest('modal')
    if kind=='checks':m['checks']=[{'name':'ping','path':'/ping','safety':'read-only'}]
    else:m['runtime']={kind:'https://app.modal.run' if kind=='ready_url' else '/health'}
    with pytest.raises(OnedeployError) as error:get_adapter('modal').plan(m)
    assert error.value.code=='invalid_config'
    m['target']['function']='web';assert get_adapter('modal').plan(m)


def test_provider_specific_build_and_attachment_intent_rejected():
    m=manifest('railway');m['build']={'snapshot_name':'ignored'}
    with pytest.raises(OnedeployError):get_adapter('railway').plan(m)
    m=manifest('daytona');m['storage']=[{'name':'cache','resource_id':'cache','mount_path':'/cache','kind':'volume','access':'read-write','single_writer':False,'attachment':'existing'}]
    with pytest.raises(OnedeployError):get_adapter('daytona').plan(m)


@pytest.mark.parametrize('provider',['railway','daytona'])
def test_container_source_entrypoint_is_rejected(provider):
    m=manifest(provider);m['source']['entrypoint']='app.py'
    with pytest.raises(OnedeployError):get_adapter(provider).plan(m)



def test_daytona_existing_region_cannot_silently_change_placement():
    m=manifest('daytona');m['target'].update(mode='existing',region='eu');m['variables']={}
    with pytest.raises(OnedeployError):get_adapter('daytona').plan(m)


def test_daytona_external_ready_url_rejected():
    m=manifest('daytona');m['runtime']={'ready_url':'https://app.up.railway.app'}
    with pytest.raises(OnedeployError):get_adapter('daytona').plan(m)


@pytest.mark.parametrize('provider',['railway','modal'])
def test_non_daytona_port_semantics_are_explicit_capabilities(provider):
    assert 'does not configure or verify' in get_adapter(provider).capabilities['listener_port']
