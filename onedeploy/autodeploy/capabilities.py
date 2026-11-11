"""Provider-agnostic AutoDeploy discovery and fail-closed capability negotiation.

A registry entry is not an adapter, an account connection, or live qualification.
Native GitHub integration is preferred when inspected, never silently replaced
with a second competing deployment loop. No provider calls occur here.
"""
from dataclasses import asdict, dataclass

from onedeploy.errors import OnedeployError

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    workloads: tuple[str, ...]
    strategy: str
    native_github: str = 'requires_inspection'
    existing_general_adapter: bool = False
    autodeploy_implemented: bool = False
    qualification: str = 'planned'
    hold: str | None = None
    reference: str = ''

    def to_dict(self):
        return {**asdict(self), 'workloads': list(self.workloads),
                'live_qualified': False, 'account_connection_verified': False}


# Native candidates require official product-generation and selected-account
# inspection. Existing general deploy adapters do not imply autodeploy support.
PROVIDERS = (
    ProviderCapability('railway', ('service',), 'native_delegate', existing_general_adapter=True, reference='https://docs.railway.com/cli'),
    ProviderCapability('fly', ('service',), 'orchestrated_adapter', reference='https://fly.io/docs/flyctl/'),
    ProviderCapability('vercel', ('web', 'functions'), 'native_delegate', reference='https://vercel.com/docs/deployments/git'),
    ProviderCapability('replicate', ('model',), 'orchestrated_adapter', reference='https://replicate.com/docs/guides/build/push-a-model'),
    ProviderCapability('skypilot', ('compute_job',), 'orchestrated_adapter', reference='https://docs.skypilot.ai/en/latest/reference/cli.html'),
    ProviderCapability('defang', ('service_stack',), 'orchestrated_adapter', reference='https://docs.defang.io/docs/concepts/deployments'),
    ProviderCapability('render', ('service',), 'native_delegate', reference='https://render.com/docs/deploys'),
    ProviderCapability('heroku', ('service',), 'native_delegate', reference='https://devcenter.heroku.com/articles/github-integration'),
    ProviderCapability('serverless_aws', ('functions', 'infrastructure_stack'), 'orchestrated_adapter', reference='https://www.serverless.com/framework/docs/providers/aws/cli-reference/deploy'),
    ProviderCapability('cloud_run', ('service',), 'native_delegate', reference='https://docs.cloud.google.com/run/docs/continuous-deployment'),
    ProviderCapability('aether', ('coding_task',), 'held', hold='Product identity and noninteractive command contract are unresolved.', reference='https://onaether.dev/docs/quickstart'),
    ProviderCapability('daytona', ('sandbox', 'gpu_inference'), 'orchestrated_adapter', existing_general_adapter=True, autodeploy_implemented=True, qualification='offline_tested', reference='https://www.daytona.io/docs/en/gpu-sandboxes/'),
    ProviderCapability('modal', ('service', 'gpu_inference'), 'orchestrated_adapter', existing_general_adapter=True, reference='https://modal.com/docs/cli/latest/deploy'),
    ProviderCapability('e2b', ('sandbox',), 'orchestrated_adapter', reference='https://e2b.dev/docs'),
    ProviderCapability('coder', ('workspace',), 'not_service_deployment', reference='https://coder.com/docs/reference/cli/create'),
    ProviderCapability('devpod', ('workspace',), 'not_service_deployment', reference='https://devpod.sh/docs/developing-in-workspaces/create-a-workspace'),
    ProviderCapability('gitpod_ona', ('workspace',), 'held', hold='Classic Gitpod versus current Ona product generation requires qualification.', reference='https://ona.com/docs/classic/user/references/gitpod-cli'),
    ProviderCapability('cloudflare', ('web', 'functions'), 'native_delegate', reference='https://developers.cloudflare.com/workers/ci-cd/builds/'),
    ProviderCapability('deno', ('web',), 'held', hold='Current deno deploy contract needs qualification; sunset deployctl/Classic is not supported.', reference='https://docs.deno.com/deploy/migration_guide/'),
    ProviderCapability('koyeb', ('service',), 'native_delegate', reference='https://www.koyeb.com/docs/build-and-deploy/deploy-with-git'),
)


def provider_capabilities(provider=None):
    """Versioned machine-readable inventory; names are not executable plugins."""
    if provider is None:
        return {'schema_version': SCHEMA_VERSION, 'providers': [item.to_dict() for item in PROVIDERS]}
    if not isinstance(provider, str):
        raise OnedeployError('invalid_config', 'Provider ID must be a string.')
    found = next((item for item in PROVIDERS if item.provider == provider), None)
    if found is None:
        raise OnedeployError('unsupported_capability', 'Unknown AutoDeploy provider. Inspect capabilities for canonical IDs.')
    return {'schema_version': SCHEMA_VERSION, **found.to_dict()}


def negotiate(provider, *, workload, required=(), native_observation=None):
    """Plan a strategy, not perform it or rewrite existing deployment digests.

    native_observation is trusted caller evidence from a future account inspector,
    not an authorization token. Only an exact boolean can report an existing
    integration; even then this function cannot certify or operate that service.
    """
    capability = provider_capabilities(provider)
    supported_requirements = {'branch_trigger', 'immutable_artifact', 'candidate', 'promote', 'drain', 'rollback', 'gpu', 'scale_to_zero'}
    if not isinstance(workload, str) or not isinstance(required, (tuple, list)) or any(not isinstance(x, str) or x not in supported_requirements for x in required):
        raise OnedeployError('invalid_config', 'Invalid workload or capability requirement.')
    if native_observation is not None and type(native_observation) is not bool:
        raise OnedeployError('invalid_config', 'Native integration observation must be true, false or unknown.')
    reasons = []
    strategy = capability['strategy']
    if workload not in capability['workloads']:
        reasons.append('workload_not_supported_by_registry_contract')
    if capability['hold']:
        reasons.append('provider_identity_or_generation_hold')
    # Always prefer a confirmed existing native integration, regardless of the
    # initial strategy hint. Never install an additional event listener beside it.
    if native_observation is True:
        strategy = 'native_delegate'
        reasons.append('native_integration_must_be_verified_for_requested_capabilities')
    elif strategy == 'native_delegate':
        reasons.append('native_integration_requires_inspection' if native_observation is None else 'native_integration_setup_not_implemented')
    if strategy == 'not_service_deployment':
        reasons.append('workspace_lifecycle_is_not_service_promotion')
    if not capability['autodeploy_implemented']:
        reasons.append('autodeploy_adapter_not_implemented')
    reasons.append('live_qualification_not_performed')
    operations = {}
    for item in dict.fromkeys(required):
        # Daytona is the first implemented lifecycle, but offline tests are not
        # live provider evidence. GPU inference is distinct from general hosting.
        operations[item] = ('offline_tested' if provider == 'daytona' and strategy == 'orchestrated_adapter' else 'unverified')
    return {'schema_version': SCHEMA_VERSION, 'provider': provider, 'workload': workload,
            'strategy': strategy, 'implemented': capability['autodeploy_implemented'],
            'live_qualified': False, 'execution_authorized': False,
            'native_connection_observed': native_observation,
            'install_additional_trigger': False if strategy == 'native_delegate' else None,
            'requirements': operations, 'gaps': reasons,
            'next_action': 'Inspect existing official integration and qualify requested capabilities.' if strategy == 'native_delegate' else 'Complete provider-specific capability and live qualification gates before unattended release.'}
