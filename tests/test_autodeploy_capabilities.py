import json
import pytest

from onedeploy import cli
from onedeploy.autodeploy.capabilities import provider_capabilities, negotiate
from onedeploy.errors import OnedeployError


def test_inventory_truthful_twenty():
    inventory = provider_capabilities()
    assert inventory['schema_version'] == 1
    rows = inventory['providers']
    assert len(rows) == 20
    assert len({row['provider'] for row in rows}) == 20
    assert all(row['live_qualified'] is False for row in rows)
    assert [row['provider'] for row in rows if row['autodeploy_implemented']] == ['daytona']
    assert all(row['account_connection_verified'] is False for row in rows)


def test_native_strategy_no_duplicate():
    result = negotiate('railway', workload='service', required=['branch_trigger', 'rollback'])
    assert result['strategy'] == 'native_delegate'
    assert result['install_additional_trigger'] is False
    assert result['execution_authorized'] is False
    assert 'native_integration_requires_inspection' in result['gaps']
    assert set(result['requirements'].values()) == {'unverified'}


def test_confirmed_native_takes_priority_even_for_adapter_hint():
    result = negotiate('modal', workload='service', native_observation=True)
    assert result['strategy'] == 'native_delegate'
    assert result['install_additional_trigger'] is False
    assert result['live_qualified'] is False


def test_daytona_first_offline_implementation_not_live_certification():
    result = negotiate('daytona', workload='gpu_inference', required=['gpu', 'candidate', 'promote'])
    assert result['implemented'] is True
    assert result['strategy'] == 'orchestrated_adapter'
    assert result['requirements'] == dict(gpu='offline_tested', candidate='offline_tested', promote='offline_tested')
    assert result['gaps'] == ['live_qualification_not_performed']
    assert result['execution_authorized'] is False


def test_workspace_not_service():
    result = negotiate('coder', workload='service', required=['promote'])
    assert 'workload_not_supported_by_registry_contract' in result['gaps']
    assert 'workspace_lifecycle_is_not_service_promotion' in result['gaps']


@pytest.mark.parametrize('provider', ['aether', 'gitpod_ona', 'deno'])
def test_held_providers(provider):
    row = provider_capabilities(provider)
    assert row['hold']
    assert row['strategy'] == 'held'
    result = negotiate(provider, workload=row['workloads'][0])
    assert 'provider_identity_or_generation_hold' in result['gaps']


def test_unknown_and_bad_requirements_fail():
    with pytest.raises(OnedeployError):
        provider_capabilities('unknown')
    with pytest.raises(OnedeployError):
        negotiate('daytona', workload='sandbox', required=['run-any-shell'])
    with pytest.raises(OnedeployError):
        negotiate('railway', workload='service', native_observation='yes')


def test_cli_inventory_and_negotiation(capsys):
    assert cli.main(['autodeploy', 'capabilities', '--json']) == 0
    assert len(json.loads(capsys.readouterr().out)['result']['providers']) == 20
    assert cli.main(['autodeploy', 'capabilities', '--provider', 'railway', '--workload', 'service', '--require', 'branch_trigger', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['result']['install_additional_trigger'] is False
    assert cli.main(['autodeploy', 'capabilities', '--workload', 'service', '--json']) == 2
