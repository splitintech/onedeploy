"""Portable intent is inert, allowlisted configuration, never portable approval."""
import copy
import json
from pathlib import Path
import shutil
import pytest
import yaml
from jsonschema import Draft202012Validator
from onedeploy import discovery as d
from onedeploy.errors import OnedeployError


def answers(**extra):
    return {'name': 'app', 'target': {'account': 'origin-account', 'project': 'origin-project', 'environment': 'origin-env', 'service': 'origin-service'}, 'runtime': {'port': 8000}, 'trust_code': True, **extra}


def fresh_answers(**extra):
    return {'target': {'account': 'fresh-account', 'project': 'fresh-project', 'environment': 'fresh-env', 'service': 'fresh-service'}, 'trust_code': True, **extra}


@pytest.fixture
def portable(tmp_path):
    origin = tmp_path / 'private-origin'; origin.mkdir()
    (origin / 'Dockerfile').write_text('FROM scratch\nEXPOSE 8000\n')
    (origin / '.env').write_text('API_TOKEN=synthetic-sensitive')
    profile = d.resolve(d.discover(origin, 'railway'), answers(variables={'API_TOKEN': {'source': 'env:APP_TOKEN', 'secret': True, 'required': True}}))
    target = tmp_path / 'destination'; target.mkdir()
    (target / 'Dockerfile').write_bytes((origin / 'Dockerfile').read_bytes())
    return origin, target, profile, d.export_intent(profile)


def test_portable_cross_directory_is_new_bound_profile(portable):
    origin, target, original, intent = portable
    Draft202012Validator(d.intent_schema()).validate(intent)
    encoded = json.dumps(intent)
    for forbidden in (str(origin), 'private-origin', original['id'], original['discovery_id'], original['source']['bundle_sha256'], 'origin-account', 'origin-project', 'origin-env', 'origin-service', 'synthetic-sensitive', 'trust_code'):
        assert forbidden not in encoded
    assert intent['settings']['variables']['API_TOKEN']['source'] == 'env:APP_TOKEN'
    imported = d.import_intent(intent, target, fresh_answers())
    assert imported['root'] == str(target)
    assert imported['id'] != original['id']
    assert imported['manifest']['target'] == fresh_answers()['target']
    assert d.validate_profile(imported)[1] == target
    assert imported['manifest']['variables']['API_TOKEN']['source'] == 'env:APP_TOKEN'


def test_intent_never_is_profile_or_discovery(portable, tmp_path):
    intent = portable[3]
    for action in (lambda: d.validate_profile(intent), lambda: d.verify_discovery(intent), lambda: d.resolve(intent, fresh_answers())):
        with pytest.raises(OnedeployError) as error: action()
        assert error.value.code == 'invalid_config'
    path = tmp_path / 'intent.json'
    d.save_intent(intent, path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert d.read_intent(path) == intent
    with pytest.raises(OnedeployError): d.read_profile(path)
    with pytest.raises(OnedeployError) as error: d.save_intent(intent, path)
    assert error.value.code == 'conflict'


def test_fresh_target_and_trust_cannot_be_inherited(portable):
    _, target, _, intent = portable
    prepared = d.prepare_intent_import(intent, target)
    assert not {'target', 'trust_code', 'snapshot', 'snapshot_name', 'storage'} & set(prepared['answers'])
    assert prepared['resolution']['status'] == 'input_required'
    missing = {item['field'] for item in prepared['resolution']['missing']}
    assert missing >= {'target.account', 'target.project', 'target.environment', 'target.service', 'trust_code'}
    result = d.inspect_intent_resolution(intent, prepared['discovery'], {'target': {'account': 'fresh-account'}, 'trust_code': True})
    assert result['status'] == 'input_required'
    result = d.inspect_intent_resolution(intent, target, {'target': fresh_answers()['target']})
    assert {q['field'] for q in result['missing']} == {'trust_code'}
    result = d.inspect_intent_resolution(intent, target, fresh_answers(trust_code=False))
    assert result['status'] == 'input_required'


def test_static_configuration_mismatch_rejected(portable):
    _, target, _, intent = portable
    (target / 'Dockerfile').write_text('FROM scratch\nEXPOSE 9090\n')
    with pytest.raises(OnedeployError) as error: d.import_intent(intent, target, fresh_answers())
    assert error.value.code == 'conflict'
    assert error.value.result['conflicts'][0]['field'] == 'source'


def test_changed_code_is_fresh_review_not_ported_identity(portable):
    _, target, original, intent = portable
    (target / 'Dockerfile').write_text('FROM scratch\nEXPOSE 8000\nCMD ["/different-app"]\n')
    with pytest.raises(OnedeployError) as error: d.import_intent(intent, target, {'target': fresh_answers()['target']})
    assert error.value.code == 'input_required'
    imported = d.import_intent(intent, target, fresh_answers())
    assert imported['source']['bundle_sha256'] != original['source']['bundle_sha256']
    assert any('changed application code requires fresh review' in item for item in intent['warnings'])


def test_wrong_provider_and_missing_selector(portable, tmp_path):
    _, target, _, intent = portable
    wrong = d.discover(target, 'daytona')
    assert d.inspect_intent_resolution(intent, wrong, fresh_answers())['status'] == 'conflict'
    (target / 'Dockerfile').rename(target / 'Dockerfile.other')
    assert d.inspect_intent_resolution(intent, target, fresh_answers())['status'] == 'conflict'


def test_fresh_unsupported_source_cannot_reuse_intent(portable):
    _, target, _, intent = portable
    (target / 'Dockerfile').write_text('FROM scratch\nEXPOSE 8000\nENV PASSWORD=synthetic-sensitive\n')
    with pytest.raises(OnedeployError) as error: d.import_intent(intent, target, fresh_answers())
    assert error.value.code == 'unsupported_capability'
    assert 'synthetic-sensitive' not in json.dumps(error.value.result)


@pytest.mark.parametrize('mutation', [
    lambda i: i.update(root='/private/owner/project'),
    lambda i: i.update(id='a' * 64),
    lambda i: i.update(approval=True),
    lambda i: i['settings'].update(trust_code=True),
    lambda i: i['settings'].update(target={'account': 'origin-account'}),
    lambda i: i['settings'].update(variables={'TOKEN': {'default': 'synthetic-sensitive'}}),
    lambda i: i['settings'].update(runtime={'command': ['/private/bin', 'synthetic-sensitive']}),
    lambda i: i['settings'].update(runtime={'ready_url': 'https://user:synthetic-sensitive@example.invalid'}),
    lambda i: i['selection'].update(path='/private/source/Dockerfile'),
    lambda i: i['selection'].update(source_path='../outside'),
    lambda i: i['selection'].update(path='a/../../outside'),
    lambda i: i['expected']['manifest'].update(source={'entrypoint': '/private/app.py'}),
    lambda i: i['expected']['manifest'].update(build={'context': '../private'}),
    lambda i: i['expected']['manifest'].update(source={'revision': 'a' * 40}),
    lambda i: i['requirements'].update(storage=[{'resource_id': 'private-volume'}]),
])
def test_unsafe_intent_allowlist_rejects_values_and_paths(portable, mutation, tmp_path):
    _, target, _, intent = portable
    mutation(intent)
    with pytest.raises(OnedeployError) as error: d.import_intent(intent, target, fresh_answers())
    assert error.value.code == 'invalid_config'
    assert 'synthetic-sensitive' not in str(error.value)


def test_native_default_runtime_origin_storage_ids_not_exported(tmp_path):
    root = tmp_path / 'origin'; root.mkdir()
    (root / 'Dockerfile').write_text('FROM scratch\n')
    manifest = {'version': 1, 'name': 'app', 'provider': 'railway', 'profile': 'docker-http', 'source': {'path': '.'}, 'target': answers()['target'],
                'runtime': {'ready_url': 'https://origin-name.up.railway.app', 'ready_path': '/ready'},
                'variables': {'MODE': {'secret': False, 'default': 'private-runtime-default'}},
                'storage': [{'name': 'data', 'resource_id': 'private-storage-id', 'mount_path': '/data', 'kind': 'volume', 'access': 'read-write', 'single_writer': True, 'attachment': 'existing'}]}
    path = root / 'onedeploy.yaml'; path.write_text(yaml.safe_dump(manifest))
    intent = d.export_intent(d.resolve(d.discover(path, 'railway')))
    encoded = json.dumps(intent)
    for secret in ('private-runtime-default', 'private-storage-id', 'origin-name.up.railway.app', 'origin-account', 'origin-project'):
        assert secret not in encoded
    assert intent['requirements']['ready_url'] is True
    target = tmp_path / 'target'; shutil.copytree(root, target)
    manifest['target'] = fresh_answers()['target']
    manifest['runtime']['ready_url'] = 'https://fresh-name.up.railway.app'
    manifest['storage'][0]['resource_id'] = 'fresh-volume'
    (target / 'onedeploy.yaml').write_text(yaml.safe_dump(manifest))
    result = d.inspect_intent_resolution(intent, target / 'onedeploy.yaml', fresh_answers())
    assert {item['field'] for item in result['missing']} >= {'storage', 'runtime.ready_url'}
    imported = d.import_intent(intent, target / 'onedeploy.yaml', fresh_answers(storage=manifest['storage'], runtime={'ready_url': manifest['runtime']['ready_url']}))
    assert imported['manifest']['storage'][0]['resource_id'] == 'fresh-volume'
    assert imported['manifest']['variables']['MODE']['default'] == 'private-runtime-default'  # Re-read from new source, never transported.


def test_snapshot_identifier_not_portable_and_fresh_mode_required(tmp_path):
    origin = tmp_path / 'origin'; origin.mkdir()
    profile = d.resolve(d.discover(origin, 'daytona'), {'name': 'app', 'trust_code': True, 'snapshot': 'private-origin-snapshot', 'target': {'account': 'old', 'sandbox': 'old-sandbox', 'mode': 'create'}})
    intent = d.export_intent(profile)
    assert 'private-origin-snapshot' not in json.dumps(intent)
    target = tmp_path / 'target'; target.mkdir()
    result = d.inspect_intent_resolution(intent, target, {'trust_code': True, 'target': {'account': 'new', 'sandbox': 'new-sandbox', 'mode': 'create'}})
    assert any(item['field'] in {'snapshot', 'target.snapshot'} for item in result['missing'])
    imported = d.import_intent(intent, target, {'trust_code': True, 'target': {'account': 'new', 'sandbox': 'new-sandbox', 'mode': 'create', 'transport': 'cli'}, 'snapshot': 'fresh-snapshot'})
    assert imported['manifest']['target']['snapshot'] == 'fresh-snapshot'


def test_current_profile_changes_block_export(portable):
    origin, _, profile, _ = portable
    (origin / 'Dockerfile').write_text('FROM scratch\nEXPOSE 9000\n')
    with pytest.raises(OnedeployError) as error: d.export_intent(profile)
    assert error.value.code == 'stale_plan'


def test_local_discovery_api_and_private_files(portable, tmp_path):
    _, target, _, intent = portable
    fresh = d.discover(target, 'railway')
    assert d.resolve_intent(fresh, intent, fresh_answers())['root'] == str(target)
    ipath = tmp_path / 'intent.json'; d.save_intent(intent, ipath)
    dpath = target / '.onedeploy' / 'discovery.json'; d.save_discovery(fresh, dpath)
    assert d.resolve_intent(dpath, ipath, fresh_answers())['root'] == str(target)
    ipath.chmod(0o644)
    with pytest.raises(OnedeployError): d.read_intent(ipath)


def test_answer_defined_condition_is_explicitly_unsupported(portable):
    origin = portable[0]
    profile = d.resolve(d.discover(origin, 'railway'), answers(variables={
        'ENABLE': {'type': 'boolean', 'secret': False, 'required': False},
        'TOKEN': {'type': 'string', 'secret': True, 'required': False,
                  'required_if': {'variable': 'ENABLE', 'equals': True}}}))
    with pytest.raises(OnedeployError) as error:
        d.export_intent(profile)
    assert error.value.code == 'unsupported_capability'
    assert 'conditional' in str(error.value)


@pytest.mark.parametrize('mutation', [
    lambda i: i['expected']['questions'][0].update(reason='synthetic-sensitive'),
    lambda i: i['expected']['questions'][0].update(choices=['/private/source']),
    lambda i: i['expected']['questions'][0].update(field='secret-source-path'),
    lambda i: i['settings'].update(variables={'FLAG': {'type': 'boolean', 'required_if': {'variable': 'OTHER', 'equals': 'synthetic-sensitive'}}}),
])
def test_portable_questions_and_variables_are_allowlisted(portable, mutation):
    _, target, _, intent = portable
    mutation(intent)
    with pytest.raises(OnedeployError) as error: d.import_intent(intent, target, fresh_answers())
    assert error.value.code == 'invalid_config'
    assert 'synthetic-sensitive' not in str(error.value)


def test_portable_compose_service_mapping_preserved(tmp_path):
    origin = tmp_path / 'origin'; origin.mkdir()
    (origin / 'Dockerfile').write_text('FROM scratch\nEXPOSE 8000\n')
    (origin / 'compose.yaml').write_text('services:\n  web:\n    build: .\n  worker:\n    build: .\n')
    found = d.discover(origin / 'compose.yaml', 'railway')
    candidate = next(item['id'] for item in found['candidates'] if item['service'] == 'worker')
    profile = d.resolve(found, answers(candidate=candidate))
    intent = d.export_intent(profile)
    target = tmp_path / 'target'; shutil.copytree(origin, target)
    imported = d.import_intent(intent, target / 'compose.yaml', fresh_answers())
    assert next(item['service'] for item in imported['discovery']['candidates'] if item['id'] == imported['resolution']['candidate']) == 'worker'
    (target / 'compose.yaml').write_text('services:\n  web:\n    build: .\n')
    with pytest.raises(OnedeployError) as error: d.import_intent(intent, target / 'compose.yaml', fresh_answers())
    assert error.value.code == 'conflict'


def test_intent_save_does_not_overwrite_symlink(portable, tmp_path):
    path = tmp_path / 'original'; path.write_text('unchanged')
    link = tmp_path / 'intent-link'; link.symlink_to(path)
    with pytest.raises(OnedeployError): d.save_intent(portable[3], link)
    assert path.read_text() == 'unchanged'


def test_export_source_destination_guard_preserves_profile(portable):
    origin, _, profile, intent = portable
    with pytest.raises(OnedeployError) as error:
        d.save_intent(intent, origin / 'intent.json', source_root=origin)
    assert error.value.code == 'invalid_config'
    assert not (origin / 'intent.json').exists()
    d.save_intent(intent, origin / '.onedeploy' / 'intent.json', source_root=origin)
    assert d.validate_profile(profile)[1] == origin


@pytest.mark.parametrize('removed', ['storage', 'ready_url'])
def test_editable_intent_cannot_remove_native_source_bindings(tmp_path, removed):
    root = tmp_path / 'project'; root.mkdir()
    (root / 'Dockerfile').write_text('FROM scratch\n')
    manifest = {'version': 1, 'name': 'app', 'provider': 'railway', 'profile': 'docker-http',
                'source': {'path': '.'}, 'target': fresh_answers()['target'],
                'runtime': {'ready_url': 'https://fresh.up.railway.app'},
                'storage': [{'name': 'data', 'resource_id': 'fresh-volume', 'mount_path': '/data', 'kind': 'volume',
                             'access': 'read-write', 'single_writer': True, 'attachment': 'existing'}]}
    path = root / 'onedeploy.yaml'; path.write_text(yaml.safe_dump(manifest))
    intent = d.export_intent(d.resolve(d.discover(path, 'railway')))
    intent['requirements'][removed] = [] if removed == 'storage' else False
    supplied = fresh_answers()
    if removed == 'storage':
        supplied['runtime'] = {'ready_url': manifest['runtime']['ready_url']}
    else:
        supplied['storage'] = manifest['storage']
    result = d.inspect_intent_resolution(intent, path, supplied)
    assert result['status'] == 'conflict'
    wanted = 'storage' if removed == 'storage' else 'runtime.ready_url'
    assert wanted in {item['field'] for item in result['missing']}
    assert wanted in {item['field'] for item in result['conflicts']}
    with pytest.raises(OnedeployError) as error:
        d.import_intent(intent, path, supplied)
    assert error.value.code == 'conflict'
    assert 'fresh-volume' not in json.dumps(error.value.result)


@pytest.mark.parametrize('build', [True, False])
def test_editable_intent_cannot_remove_native_snapshot_binding(tmp_path, build):
    root = tmp_path / 'project'; root.mkdir()
    manifest = {'version': 1, 'name': 'app', 'provider': 'daytona', 'profile': 'docker-http', 'source': {'path': '.'},
                'target': {'account': 'fresh-owner', 'sandbox': 'fresh-sandbox', 'mode': 'create'}}
    if build:
        (root / 'Dockerfile').write_text('FROM scratch\n')
        manifest['build'] = {'context': '.', 'dockerfile': 'Dockerfile', 'snapshot_name': 'source-snapshot-name'}
    else:
        manifest['target']['snapshot'] = 'source-snapshot-id'
    path = root / 'onedeploy.yaml'; path.write_text(yaml.safe_dump(manifest))
    intent = d.export_intent(d.resolve(d.discover(path, 'daytona')))
    intent['requirements']['snapshot'] = 'none'
    intent['requirements']['target_fields'] = ['account', 'sandbox', 'mode']
    supplied = {'trust_code': True, 'target': {'account': 'fresh-owner', 'sandbox': 'fresh-sandbox', 'mode': 'create'}}
    result = d.inspect_intent_resolution(intent, path, supplied)
    assert result['status'] == 'conflict'
    assert any(item['field'] == 'snapshot' for item in result['conflicts'])
    assert any(item['field'] == ('snapshot_name' if build else 'target.snapshot') for item in result['missing'])
    assert 'source-snapshot' not in json.dumps(result)
