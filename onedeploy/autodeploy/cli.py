"""AutoDeploy CLI: offline preview is useful; unqualified live transport fails closed."""
from pathlib import Path
import argparse

from onedeploy.errors import OnedeployError
from .models import DeploymentConfig, ReleaseSpec
from . import planner


def register(sub):
    group = sub.add_parser('autodeploy', help='Opt-in immutable GPU release planning')
    group.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
    actions = group.add_subparsers(dest='autodeploy_action', required=True)
    for name in ('init', 'validate', 'plan', 'apply', 'status', 'reconcile', 'rollback', 'drain', 'serve', 'setup', 'capabilities'):
        p = actions.add_parser(name)
        p.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
        if name == 'capabilities':
            p.add_argument('--provider')
            p.add_argument('--workload')
            p.add_argument('--require', action='append', default=[])
        if name == 'init':
            p.add_argument('--provider', choices=['daytona'], default='daytona')
            p.add_argument('--output', default='autodeploy.json')
        if name in ('validate', 'plan', 'setup'):
            p.add_argument('--config', required=True)
        if name == 'plan':
            p.add_argument('--commit', required=True)
            p.add_argument('--image', required=True)
            p.add_argument('--model-digest', required=True)
            p.add_argument('--evidence-digest', required=True)
            p.add_argument('--out', required=True)
        if name in ('plan', 'apply'):
            p.add_argument('--source', default='.')
        if name == 'apply':
            p.add_argument('--plan', required=True)
            p.add_argument('--approve-plan')
        if name in ('apply', 'status', 'reconcile', 'rollback', 'drain', 'serve'):
            p.add_argument('--state', required=True, help='Persistent single-controller local SQLite state')
        if name in ('status', 'reconcile', 'rollback', 'drain', 'serve'):
            p.add_argument('--deployment', required=True)
        if name in ('reconcile', 'rollback', 'drain'):
            p.add_argument('--release', required=True)
        if name in ('reconcile', 'rollback', 'drain'):
            p.add_argument('--approve-release')
        if name in ('apply', 'reconcile', 'rollback', 'drain', 'serve', 'setup'):
            p.add_argument('--runtime', help='Reviewed fixed worker runtime JSON')
            p.add_argument('--policy', help='Explicit JSON resource-rate and budget policy')
            p.add_argument('--approve-policy', help='Exact reviewed policy digest')
        if name == 'serve':
            p.add_argument('--host', default='127.0.0.1')
            p.add_argument('--port', type=int, default=8766)
            p.add_argument('--config', required=True)


def read_runtime(path):
    from .transport import RuntimeConfig
    from .daytona import DaytonaContractError
    try:
        return RuntimeConfig.from_dict(planner.read_json(path)).to_dict()
    except DaytonaContractError:
        raise OnedeployError('invalid_config', 'Worker runtime configuration is invalid.') from None


def live_adapter(config, *, state, policy_path, approve_policy, runtime_path, pool_transport=False):
    """Built-in SDK and fixed authenticated worker protocol; no dynamic plugins."""
    import os
    from .budget import BudgetPolicy, BudgetLedger
    if not policy_path:
        raise OnedeployError('approval_required', 'An explicit reviewed resource budget policy is required.')
    policy = BudgetPolicy.from_dict(planner.read_json(policy_path), config)
    if policy.concurrency_per_worker != 1:
        raise OnedeployError('unsupported_capability', 'The supplied Whisper worker supports one concurrent job per GPU worker.')
    if approve_policy != policy.id:
        raise OnedeployError('approval_required', 'Explicit approval of this exact budget policy is required.')
    if not runtime_path:
        raise OnedeployError('invalid_config', 'Reviewed worker runtime configuration is required.')
    runtime = read_runtime(runtime_path)
    if planner.digest(runtime) != policy.runtime_digest:
        raise OnedeployError('stale_plan', 'Worker runtime does not match the approved budget policy.')
    api_key = os.environ.get('DAYTONA_API_KEY')
    if not api_key:
        raise OnedeployError('authentication_failed', 'DAYTONA_API_KEY must be configured in the isolated SDK environment.')
    from .daytona import sdk_adapter, worker_control, DaytonaContractError
    ledger = BudgetLedger(state, policy)
    resources = config.resources
    try:
        adapter = sdk_adapter(account=config.target, deployment=config.deployment_id,
            resources={'cpu': resources['cpu'], 'memory': resources['memory_gib'], 'disk': resources['disk_gib'],
                       'gpu': resources['gpu_count'], 'gpu_type': resources['gpu_type']},
            probe=lambda worker: worker_control(worker, 'qualify'),
            drainer=lambda worker: worker_control(worker, 'drain'),
            api_key=api_key, timeout=min(config.candidate_timeout_seconds, 3600),
            idle_minutes=max(1, min(60, (config.idle_timeout_seconds + 59) // 60)),
            authorize_operation=ledger.authorize, inspect_status=lambda worker, operation: worker_control(worker, operation + '-status'), worker_ttl_seconds=policy.max_worker_seconds)
        from .transport import DaytonaPoolTransport
        transport = DaytonaPoolTransport(adapter, runtime, worker_ttl_seconds=policy.max_worker_seconds, job_timeout_seconds=policy.job_timeout_seconds, ledger=ledger)
        return (transport, policy, ledger) if pool_transport else transport.release_adapter()
    except (ImportError, DaytonaContractError):
        raise OnedeployError('unsupported_capability', 'The isolated Daytona 0.231.0 SDK or worker transport is unavailable; no rollout was started.') from None


def approved_adapter(args, config):
    return live_adapter(config, state=args.state, policy_path=args.policy, approve_policy=args.approve_policy, runtime_path=args.runtime)


def bind_adapter_runtime(adapter, release):
    """The built-in release adapter carries the exact already-approved ledger."""
    ledger = getattr(getattr(adapter, 'transport', None), 'ledger', None)
    if ledger is None:
        raise OnedeployError('unsupported_capability', 'Live releases require durable runtime binding.')
    ledger.bind_runtime(release)


def dispatch(args):
    action = args.autodeploy_action
    if action == 'capabilities':
        from .capabilities import provider_capabilities
        if args.workload:
            if not args.provider:
                raise OnedeployError('invalid_config', 'Capability negotiation requires a provider.')
            return planner.negotiate_capabilities(args.provider, workload=args.workload, required=args.require)
        if args.require:
            raise OnedeployError('invalid_config', 'Requirements need a workload and provider.')
        return provider_capabilities(args.provider)
    if action == 'init':
        # Syntactically valid placeholders: not inspected or authorized provider resources.
        template = {'schema_version': 1, 'deployment_id': 'my-worker', 'repository': 'OWNER/REPOSITORY',
                    'branch': 'deployment', 'target': 'REVIEW_TARGET',
                    'resources': {'cpu': 2, 'memory_gib': 8, 'disk_gib': 20, 'gpu_count': 1, 'gpu_type': 'REVIEW_GPU'},
                    'max_workers': 1, 'budget_usd': 1, 'idle_timeout_seconds': 60, 'candidate_timeout_seconds': 300}
        planner.save_json(template, args.output)
        return {'created': True, 'status': 'template', 'next_actions': ['Replace OWNER/REPOSITORY, REVIEW_TARGET and REVIEW_GPU placeholders; review resources, branch and budget. No provider access was performed.']}
    if action == 'setup':
        from .budget import BudgetPolicy
        if not args.runtime or not args.policy:
            raise OnedeployError('invalid_config', 'Setup requires configuration, runtime and budget policy files.')
        config = DeploymentConfig.from_dict(planner.read_json(args.config))
        runtime = read_runtime(args.runtime)
        policy = BudgetPolicy.from_dict(planner.read_json(args.policy), config)
        if policy.concurrency_per_worker != 1:
            raise OnedeployError('unsupported_capability', 'The supplied Whisper worker supports one concurrent job per GPU worker.')
        if policy.runtime_digest != planner.digest(runtime):
            raise OnedeployError('stale_plan', 'Worker runtime does not match budget policy.')
        return {'validated': True, 'config_digest': planner.digest(config.to_dict()),
                'runtime_digest': planner.digest(runtime), 'policy_digest': policy.id,
                'provider_inspected': False, 'resources_created': False,
                'next_actions': ['Review rates, account, model volume and policy. Configure DAYTONA_API_KEY in the isolated SDK environment; approve exact plan and policy for apply.']}
    if action in ('validate', 'plan'):
        config = DeploymentConfig.from_dict(planner.read_json(args.config)).to_dict()
        if action == 'validate':
            return {'valid': True, 'config_digest': planner.digest(config), 'provider_inspected': False}
        plan = planner.make_plan(config, args.source, args.commit, args.image, args.model_digest, args.evidence_digest)
        planner.save_json(plan, args.out)
        return plan
    if action == 'status':
        path = Path(args.state)
        if not path.is_file() or path.is_symlink():
            raise OnedeployError('invalid_config', 'A pre-existing controller state file is required.')
        # Read-only SQLite connection: status must not initialize or migrate storage.
        import sqlite3, json
        try:
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                row = db.execute('SELECT generation,desired FROM deployments WHERE id=?', (args.deployment,)).fetchone()
                records = db.execute('SELECT data FROM releases WHERE deployment=? ORDER BY generation DESC', (args.deployment,)).fetchall()
            return {'deployment_id': args.deployment, 'generation': row[0] if row else 0,
                    'desired_release': row[1] if row else None, 'releases': [json.loads(x[0]) for x in records]}
        except (sqlite3.Error, ValueError):
            raise OnedeployError('invalid_config', 'Cannot read valid AutoDeploy state.') from None
    if action == 'apply':
        plan = planner.verify_plan(planner.read_json(args.plan), args.source)
        if args.approve_plan != plan['id']:
            raise OnedeployError('approval_required', 'Explicit authorization of this exact source-bound plan is required.')
        config = DeploymentConfig.from_dict(plan['config'])
        spec = ReleaseSpec.from_dict(plan['spec'])
        adapter = approved_adapter(args, config)
        from .state import StateStore
        from .reconciler import Reconciler
        reconciler = Reconciler(StateStore(args.state), adapter)
        release = reconciler.plan(config, spec)
        bind_adapter_runtime(adapter, release)
        return reconciler.apply(release['release_id'], approved=True)
    if action == 'serve':
        config = DeploymentConfig.from_dict(planner.read_json(args.config))
        if config.deployment_id != args.deployment:
            raise OnedeployError('invalid_config', 'Controller deployment must match reviewed configuration.')
        # Validate all local policy before creating a provider client or server.
        if args.host not in ('127.0.0.1', 'localhost') or not 1 <= args.port <= 65535:
            raise OnedeployError('invalid_config', 'Controller binds loopback only; place an authenticated TLS proxy in front.')
        import os
        token = os.environ.get('ONEDEPLOY_CONTROLLER_TOKEN', '')
        if len(token) < 32:
            raise OnedeployError('authentication_failed', 'Set a strong ONEDEPLOY_CONTROLLER_TOKEN of at least 32 characters.')
        transport, policy, ledger = live_adapter(config, state=args.state, policy_path=args.policy,
            approve_policy=args.approve_policy, runtime_path=args.runtime, pool_transport=True)
        from .controller import Controller, serve
        from .state import StateStore
        from .pool import PoolPolicy
        pool_policy = PoolPolicy(max_workers=config.max_workers,
            concurrency_per_worker=policy.concurrency_per_worker,
            idle_timeout_seconds=config.idle_timeout_seconds,
            worker_ttl_seconds=policy.max_worker_seconds,
            startup_timeout_seconds=config.candidate_timeout_seconds,
            job_timeout_seconds=policy.job_timeout_seconds, total_hourly_rate=policy.total_hourly_rate)
        pool_policy.validate()
        store = StateStore(args.state)
        existing = store.deployment(config.deployment_id)
        for release in existing['releases']:
            if release['release_id'] == existing['desired_release'] or (release.get('worker_id') and not release.get('worker_deleted')):
                ledger.bind_runtime(release)
        controller = Controller(store, config.deployment_id, transport,
                                pool_policy, budget_usd=policy.max_total_usd)
        serve(controller, host=args.host, port=args.port, token=token)
        return {'stopped': True, 'deployment_id': config.deployment_id}
    if args.approve_release != args.release:
        raise OnedeployError('approval_required', 'Explicit authorization of the exact release operation is required.')
    from .state import StateStore
    from .reconciler import Reconciler
    if not Path(args.state).is_file() or Path(args.state).is_symlink():
        raise OnedeployError('invalid_config', 'Existing persistent controller state is required.')
    store = StateStore(args.state)
    release = store.get(args.release)
    if release['deployment_id'] != args.deployment:
        raise OnedeployError('invalid_config', 'Release does not belong to requested deployment.')
    config = DeploymentConfig.from_dict(release['config'])
    adapter = approved_adapter(args, config)
    bind_adapter_runtime(adapter, release)
    reconciler = Reconciler(store, adapter)
    if action == 'reconcile':
        return reconciler.apply(args.release, approved=True)
    if action == 'rollback':
        return reconciler.rollback(args.deployment, args.release, approved=True)
    if action == 'drain':
        return reconciler.cleanup(args.release, approved=True)
    raise OnedeployError('invalid_config', 'Unsupported AutoDeploy operation.')
