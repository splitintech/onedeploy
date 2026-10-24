"""Transport selection is explicit, source-bound, and never an auth fallback."""
import importlib.util

import pytest
import yaml

from onedeploy import core, discovery as d
from onedeploy.cli import parser
from onedeploy.errors import OnedeployError
from onedeploy.providers import get_adapter


def manifest(transport=None):
    result = {'version': 1, 'name': 'app', 'provider': 'daytona', 'profile': 'docker-http',
              'source': {'path': '.'}, 'target': {'account': 'owner', 'sandbox': 'sandbox',
                                                'mode': 'create', 'snapshot': 'snapshot'}}
    if transport is not None:
        result['target']['transport'] = transport
    return result


@pytest.mark.parametrize('transport,expected', [(None, 'DaytonaAdapter'), ('cli', 'DaytonaAdapter'), ('sdk', 'DaytonaSDKAdapter')])
def test_daytona_transport_factory_and_core(transport, expected):
    value = manifest(transport)
    assert type(get_adapter('daytona', value)).__name__ == expected
    assert type(core._adapter(value)).__name__ == expected


@pytest.mark.parametrize('provider', ['railway', 'modal'])
@pytest.mark.parametrize('transport', ['cli', 'sdk'])
def test_transport_rejected_for_other_provider(provider, transport):
    with pytest.raises(OnedeployError) as error:
        get_adapter(provider, {'target': {'transport': transport}})
    assert error.value.code == 'invalid_config'


def test_no_invalid_transport_fallback():
    with pytest.raises(OnedeployError):
        get_adapter('daytona', manifest('automatic'))


def test_explicit_sdk_doctor_does_not_require_cli(monkeypatch):
    monkeypatch.setattr(core.shutil, 'which', lambda name: pytest.fail('SDK must not inspect CLI'))
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: object())
    calls = []
    def check(adapter, cwd):
        calls.append(type(adapter).__name__)
        return {'version': adapter.supported_version}
    monkeypatch.setattr(core, '_check_version', check)
    result = core.doctor('daytona', transport='sdk')
    assert result['compatible'] and result['selected_transport'] == 'sdk'
    assert calls == ['DaytonaSDKAdapter']
    assert 'DAYTONA_API_KEY' in ' '.join(result['warnings'])


def test_missing_sdk_does_not_fallback(monkeypatch):
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None)
    monkeypatch.setattr(core, '_check_version', lambda *a: pytest.fail('No installed SDK'))
    result = core.doctor('daytona', transport='sdk')
    assert result['status'] == 'missing' and not result['compatible']


@pytest.mark.parametrize('provider', ['railway', 'modal'])
def test_sdk_doctor_rejects_other_provider(provider):
    with pytest.raises(OnedeployError):
        core.doctor(provider, transport='sdk')


def test_doctor_cli_flag():
    args = parser().parse_args(['doctor', '--provider', 'daytona', '--transport', 'sdk'])
    assert args.transport == 'sdk'
    assert parser().parse_args(['doctor', '--provider', 'daytona']).transport == 'cli'


@pytest.mark.parametrize('transport', ['cli', 'sdk'])
def test_portable_daytona_requires_explicit_transport_even_if_requirements_edited(tmp_path, transport):
    path = tmp_path / 'onedeploy.yaml'
    path.write_text(yaml.safe_dump(manifest(transport)))
    intent = d.export_intent(d.resolve(d.discover(path, 'daytona')))
    assert intent['requirements']['transport'] == transport
    assert 'transport' in intent['requirements']['target_fields']
    supplied = {'trust_code': True, 'target': manifest()['target']}
    result = d.inspect_intent_resolution(intent, path, supplied)
    assert any(q['field'] == 'target.transport' and q['choices'] == ['cli', 'sdk'] for q in result['missing'])
    intent['requirements']['target_fields'].remove('transport')
    intent['requirements'].pop('transport')
    result = d.inspect_intent_resolution(intent, path, supplied)
    assert any(q['field'] == 'target.transport' for q in result['missing'])
    supplied['target']['transport'] = transport
    imported = d.import_intent(intent, path, supplied)
    assert imported['manifest']['target']['transport'] == transport


def test_portable_default_cli_and_intentional_transport_switch(tmp_path):
    path = tmp_path / 'onedeploy.yaml'
    path.write_text(yaml.safe_dump(manifest()))
    original = d.resolve(d.discover(path, 'daytona'))
    intent = d.export_intent(original)
    assert intent['requirements']['transport'] == 'cli'
    supplied = {'trust_code': True, 'target': {**manifest()['target'], 'transport': 'sdk'}}
    imported = d.import_intent(intent, path, supplied)
    assert imported['manifest']['target']['transport'] == 'sdk'
    assert imported['id'] != original['id']
    assert type(core._adapter(imported['manifest'])).__name__ == 'DaytonaSDKAdapter'


@pytest.mark.parametrize('inputs', [
    {'variables': {'API_TOKEN': {'source': 'env:API_TOKEN'}}},
    {'variables': {'API_TOKEN': {'source': 'env:API_TOKEN', 'secret': True}}},
    {'variables': {'PROBE_TOKEN': {'source': 'env:PROBE_TOKEN', 'secret': True}},
     'checks': [{'name': 'authenticated', 'path': '/private', 'safety': 'read-only',
                 'auth_variable': 'PROBE_TOKEN', 'unauthenticated_status': 401}]},
])
def test_sensitive_import_offers_transport_then_requires_explicit_sdk(tmp_path, inputs):
    (tmp_path / 'Dockerfile').write_text('FROM scratch\nEXPOSE 8000\n')
    found = d.discover(tmp_path / 'Dockerfile', 'daytona')
    supplied = {'name': 'app', 'trust_code': True, 'runtime': {'port': 8000},
                'snapshot_name': 'snapshot', 'target': {'account': 'owner', 'sandbox': 'sandbox', 'mode': 'create'},
                **inputs}
    result = d.inspect_resolution(found, supplied)
    assert result['status'] == 'input_required'
    question = next(q for q in result['missing'] if q['field'] == 'target.transport')
    assert question['choices'] == ['cli', 'sdk']
    assert 'DAYTONA_API_KEY' in question['reason']
    assert 'transport' not in supplied['target']
    supplied['target']['transport'] = 'cli'
    assert d.inspect_resolution(found, supplied)['status'] == 'unsupported_capability'
    supplied['target']['transport'] = 'sdk'
    profile = d.resolve(found, supplied)
    assert profile['manifest']['target']['transport'] == 'sdk'


def test_compose_secret_discovery_offers_transport_before_resolution(tmp_path):
    (tmp_path / 'Dockerfile').write_text('FROM scratch\n')
    path = tmp_path / 'compose.yaml'
    path.write_text('services:\n  web:\n    build: .\n    environment:\n      API_TOKEN:\n')
    found = d.discover(path, 'daytona')
    assert found['status'] == 'input_required'
    assert any(q['field'] == 'target.transport' for q in found['missing'])
    supplied = {'name': 'app', 'trust_code': True, 'snapshot_name': 'snapshot',
                'target': {'account': 'owner', 'sandbox': 'sandbox', 'mode': 'create', 'transport': 'sdk'}}
    assert d.resolve(found, supplied)['manifest']['target']['transport'] == 'sdk'


def test_public_import_keeps_default_cli_without_extra_choice(tmp_path):
    (tmp_path / 'Dockerfile').write_text('FROM scratch\n')
    supplied = {'name': 'app', 'trust_code': True, 'snapshot_name': 'snapshot',
                'target': {'account': 'owner', 'sandbox': 'sandbox', 'mode': 'create'},
                'variables': {'MODE': {'source': 'env:MODE', 'secret': False}}}
    profile = d.resolve(d.discover(tmp_path / 'Dockerfile', 'daytona'), supplied)
    assert 'transport' not in profile['manifest']['target']
    assert type(core._adapter(profile['manifest'])).__name__ == 'DaytonaAdapter'


def test_native_secret_manifest_requires_source_transport_fix(tmp_path):
    value = manifest()
    value['variables'] = {'API_TOKEN': {'source': 'env:API_TOKEN', 'secret': True}}
    path = tmp_path / 'onedeploy.yaml'
    path.write_text(yaml.safe_dump(value))
    found = d.discover(path, 'daytona')
    assert found['status'] == 'unsupported_capability'
    assert not any(q['field'] == 'target.transport' for q in found['missing'])
