"""Offline contracts for the official Daytona CLI v0.226.0 transport."""
import json
from copy import deepcopy
import pytest
from onedeploy.providers import get_adapter
from onedeploy.providers import daytona_cli_bridge as bridge
from onedeploy.errors import OnedeployError


def manifest():
    return {'version':1,'name':'demo','provider':'daytona','profile':'docker-http','source':{'path':'.'},
            'target':{'account':'org','sandbox':'sandbox','snapshot':'snap','mode':'create'}}


def config():
    return {'target':manifest()['target'],'timeout':60,'auto_stop_minutes':15,'approved_inspection':{'exists':False,'account':'org','volumes':[]},
            'snapshot_receipt':{'snapshot_id':'snap-id','snapshot_image':'image@sha256:'+'a'*64}}


def sandbox():
    return {'id':'sb-id','name':'sandbox','organizationId':'org','state':'started','snapshot':'snap-id',
            'public':False,'autoStopInterval':15,'env':{'SECRET':'do-not-leak'},'signedUrl':'do-not-leak'}


def snapshot():
    return {'id':'snap-id','name':'snap','organizationId':'org','state':'active','imageName':'image@sha256:'+'a'*64}


class CLI:
    def __init__(self):
        self.rows=[]; self.snaps=[snapshot()]; self.calls=[]; self.org='org'; self.mutation_attempted=False
    def call(self,args,*,mutating=False):
        self.calls.append((args,mutating))
        if mutating:self.mutation_attempted=True
        if args[:2]==['organization','members']:
            return json.dumps([{'organizationId':self.org,'email':'private@example.test','name':'PRIVATE'}])
        if args[0]=='list':return json.dumps({'items':self.rows,'nextCursor':None})
        if args[0]=='info':return json.dumps(self.rows[0])
        if args[:2]==['snapshot','list']:return json.dumps(self.snaps)
        if args[:2]==['snapshot','create']:
            row=snapshot();row['name']=args[2];self.snaps=[row];return 'build output SECRET not emitted'
        if args[0]=='create':self.rows=[sandbox()];return 'created sb-id'
        if args[0]=='start':return 'started'
        if args[:2]==['volume','get']:return json.dumps({'id':'v-id','name':'cache','organizationId':'org','state':'ready'})
        if args[0]=='exec':return '[200,503]'
        raise AssertionError(args)


def test_cli_is_default_and_sdk_is_explicit():
    a=get_adapter('daytona')
    assert a.version=='2-cli'
    assert a.version_command()['argv']==['daytona','--version']
    op=a.plan(manifest())[0]
    assert op['argv'][2].endswith('/daytona_cli_bridge.py')
    assert not op['env_keys'] and op['stdin_env_keys']==[]
    assert a.capabilities['logs'] is False
    assert get_adapter('daytona',{'target':{'transport':'sdk'}}).version=='2-sdk'


@pytest.mark.parametrize('operation',['identity','inspect','reconcile'])
def test_identity_projection_and_absence(operation):
    cli=CLI(); result=bridge.execute(operation,config(),cli)
    assert result['account']=='org'
    assert 'private@example.test' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)
    assert all(not mutation for _,mutation in cli.calls)
    if operation!='identity':
        assert result['exists'] is False and result['snapshot_image'].endswith('a'*64)


def test_create_uses_verified_snapshot_and_private_no_secret_argv():
    cli=CLI();out=bridge.execute('create',config(),cli)
    write=[args for args,mutation in cli.calls if mutation]
    assert write==[['create','--name','sandbox','--snapshot','snap-id','--auto-stop','15','--auto-delete','-1','--target','us','--label','onedeploy-managed=true']]
    assert out['id']=='sb-id' and out['public'] is False
    assert 'SECRET' not in json.dumps(out)
    assert cli.calls[-2][0][:2]==['organization','members']


def test_secret_runtime_values_are_rejected():
    for spec in ({'type':'string','secret':True}, {'type':'string'}):
        m=manifest();m['variables']={'KEY':spec}
        with pytest.raises(OnedeployError,match='no secret-safe'):
            get_adapter('daytona').plan(m)
    with pytest.raises(bridge.Failure):bridge.check_runtime(config(),{'KEY':'value'})


def test_api_key_org_command_failure_never_falls_back():
    class Denied(CLI):
        def call(self,args,**kw):raise bridge.Failure()
    with pytest.raises(bridge.Failure) as exc:bridge.execute('identity',config(),Denied())
    assert exc.value.code=='authentication_failed'
    with pytest.raises(OnedeployError,match='API-key-only'):
        get_adapter('daytona').parse('identity','{"error":"authentication_failed"}')


@pytest.mark.parametrize('members',[[],[{}],[{'organizationId':'wrong'}],[{'organizationId':'org'},{'organizationId':'wrong'}]])
def test_identity_requires_exact_nonempty_active_org(members):
    class Bad(CLI):
        def call(self,args,**kw):return json.dumps(members)
    with pytest.raises(bridge.Failure):bridge.execute('identity',config(),Bad())


def test_no_replay_existing_resource():
    cli=CLI();cli.rows=[sandbox()]
    with pytest.raises(bridge.Failure):bridge.execute('create',config(),cli)
    assert not any(mutation for _,mutation in cli.calls)


def test_snapshot_build_receipt_and_following_create():
    cli=CLI();cli.snaps=[];c=config();c['target'].pop('snapshot');c['build']={'snapshot_name':'new-snap'};c.pop('snapshot_receipt')
    observed=bridge.execute('inspect',c,cli);assert observed['snapshot_pending']
    receipt=bridge.execute('snapshot-create',c,cli)
    assert ['snapshot','create','new-snap','--dockerfile','Dockerfile','--context','.'] in [a for a,_ in cli.calls]
    with pytest.raises(bridge.Failure):bridge.execute('create',c,cli)
    c['snapshot_receipt']={k:receipt[k] for k in ('snapshot_id','snapshot_image')}
    assert bridge.execute('create',c,cli)['snapshot_id']=='snap-id'


@pytest.mark.parametrize('field,value',[('imageName','image:latest'),('state','building'),('organizationId','other')])
def test_mutable_or_unready_or_wrong_account_snapshot_rejected(field,value):
    cli=CLI();cli.snaps[0][field]=value
    with pytest.raises(bridge.Failure):bridge.execute('create',config(),cli)
    assert not any(mutation for _,mutation in cli.calls)


def test_existing_volume_resolved_by_id():
    cli=CLI();c=config();c['storage']=[{'resource_id':'cache','mount_path':'/cache'}];c['approved_inspection']['volumes']=[{'id':'v-id','name':'cache','mount_path':'/cache'}]
    bridge.execute('create',c,cli)
    write=[a for a,m in cli.calls if m][0]
    assert write[-2:]==['--volume','v-id:/cache']


def test_wrong_info_identity_is_not_success():
    cli=CLI();cli.rows=[sandbox()]
    with pytest.raises(bridge.Failure):bridge.info(cli,'other','org')


def test_org_switch_after_mutation_is_detected():
    class Switch(CLI):
        def call(self,args,**kw):
            result=super().call(args,**kw)
            if args[0]=='create':self.org='other'
            return result
    cli=Switch()
    with pytest.raises(bridge.Failure):bridge.execute('create',config(),cli)
    assert cli.mutation_attempted


def test_paginated_sandbox_absence_checks_all_pages():
    class Pages(CLI):
        def call(self,args,**kw):
            if args[0]=='list':
                return json.dumps({'items':[],'nextCursor':'next'} if '--cursor' not in args else {'items':[sandbox()],'nextCursor':None})
            return super().call(args,**kw)
    assert bridge.sandbox_list(Pages(),'org')==[sandbox()]


def test_repeated_cursor_and_truncated_snapshot_list_fail_closed(monkeypatch):
    class Pages(CLI):
        def call(self,args,**kw):
            if args[0]=='list':return '{"items":[],"nextCursor":"same"}'
            if args[:2]==['snapshot','list']:
                return json.dumps([{**snapshot(),'id':'snap-'+str(i)} for i in range(100)])
            return super().call(args,**kw)
    with pytest.raises(bridge.Failure):bridge.sandbox_list(Pages(),'org')
    monkeypatch.setattr(bridge,'MAX_PAGES',1)
    with pytest.raises(bridge.Failure):bridge.snapshots(Pages(),'org')


def test_readiness_fixed_probe_and_no_body_capture():
    cli=CLI();cli.rows=[sandbox()];c=config();c.update(port=8000,health_path='/health',ready_path='/ready')
    result=bridge.execute('verify',c,cli)
    assert result=={'process_running':True,'health_verified':True,'readiness_verified':False}
    argv=[a for a,_ in cli.calls if a[0]=='exec'][0]
    assert argv[:7]==['exec','sb-id','--timeout','90','--','python3','-I']
    assert '127.0.0.1' in argv[8] and 'r.read' not in argv[8]


def test_auth_checks_and_logs_are_explicitly_unsupported():
    a=get_adapter('daytona');m=manifest()
    with pytest.raises(OnedeployError,match='no entrypoint logs'):
        a.command(m,'logs')
    m['runtime']={'port':8000};m['checks']=[{'name':'x','path':'/x','safety':'read-only','auth_variable':'TOKEN'}]
    with pytest.raises(OnedeployError,match='authenticated probe'):
        a.command(m,'verify-checks')


def test_cli_bridge_contains_no_sdk_or_credentials_file_access():
    from pathlib import Path
    source=Path(bridge.__file__).read_text()
    assert 'from daytona import' not in source and 'importlib.metadata' not in source
    assert 'config.json' not in source and 'urllib' not in source


def test_approved_snapshot_receipt_required_even_without_build():
    cli=CLI();c=config();c.pop('snapshot_receipt')
    with pytest.raises(bridge.Failure):bridge.execute('create',c,cli)
    assert not cli.mutation_attempted


def test_wrong_volume_binding_or_changed_volume_fails_before_write():
    cli=CLI();c=config();c['storage']=[{'resource_id':'wanted','mount_path':'/cache'}]
    with pytest.raises(bridge.Failure):bridge.execute('create',c,cli)
    assert not cli.mutation_attempted
    c['storage'][0]['resource_id']='cache'
    with pytest.raises(bridge.Failure):bridge.execute('create',c,cli)
    assert not cli.mutation_attempted


def test_start_rejects_replaced_sandbox_identity():
    cli=CLI();cli.rows=[sandbox()];c=config();c['target']['mode']='existing'
    c['approved_inspection']={**bridge.summary(sandbox(),'org'),'id':'prior-sandbox'}
    with pytest.raises(bridge.Failure):bridge.execute('start',c,cli)
    assert not cli.mutation_attempted


def test_core_approved_receipts_are_materialized_not_reselected():
    a=get_adapter('daytona');op=a.plan(manifest())[0]
    approved={'inspection':{'exists':False,'account':'org','snapshot_id':'snap-id','snapshot_image':'image@sha256:'+'a'*64,'volumes':[]}}
    bound=a.bind_preconditions(op,approved)
    data=json.loads(bound['argv'][-1])
    assert data['snapshot_receipt']['snapshot_id']=='snap-id'
    assert data['approved_inspection']==approved['inspection']
    assert 'snapshot_receipt' not in json.loads(op['argv'][-1])


def test_explicit_public_variables_can_use_cli_argv():
    m=manifest();m['variables']={'GREETING':{'type':'string','secret':False}}
    a=get_adapter('daytona');op=a.plan(m)[0]
    assert op['stdin_env_keys']==['GREETING'] and op['stdin_format']=='json'
    c=config();c['public_variable_names']=['GREETING'];cli=CLI()
    bridge.execute('create',c,cli,{'GREETING':'Hello world'})
    assert ['--env','GREETING=Hello world']==[a for a,m in cli.calls if m][0][-2:]
    assert 'Hello world' not in json.dumps(op)


def test_public_value_plan_disclosure_and_redaction(tmp_path):
    import yaml
    from onedeploy import core
    m=manifest();m['variables']={'GREETING':{'type':'string','secret':False,'required':True}}
    p=tmp_path/'onedeploy.yaml';p.write_text(yaml.safe_dump(m))
    plan=core.make_plan(p,allow_dirty=True,env_overrides={'GREETING':'public-example-value'})
    assert any('visible to local process inspection' in w for w in plan['warnings'])
    assert 'public-example-value' not in json.dumps(plan)
    assert 'public-example-value' not in json.dumps(core.redact({'argv':['daytona','create','--env','GREETING=public-example-value']},['public-example-value']))


def test_provider_projection_rejects_nested_or_credential_bearing_fields():
    row=sandbox();row['state']={'SECRET':'unexpected-value'}
    with pytest.raises(bridge.Failure):bridge.summary(row,'org')
    snap=snapshot();snap['imageName']='user:password@registry/image@sha256:'+'a'*64
    with pytest.raises(bridge.Failure):bridge.snapshot_receipt(snap,'org')
