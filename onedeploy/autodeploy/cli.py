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
    for name in ('init', 'validate', 'plan', 'apply', 'status', 'reconcile', 'rollback', 'drain'):
        p = actions.add_parser(name)
        p.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
        if name == 'init':
            p.add_argument('--provider', choices=['daytona'], default='daytona')
            p.add_argument('--output', default='autodeploy.json')
        if name in ('validate', 'plan'):
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
        if name in ('apply', 'status', 'reconcile', 'rollback', 'drain'):
            p.add_argument('--state', required=True, help='Persistent single-controller local SQLite state')
        if name in ('status', 'reconcile', 'rollback', 'drain'):
            p.add_argument('--deployment', required=True)
        if name in ('rollback', 'drain'):
            p.add_argument('--release', required=True)
        if name in ('reconcile', 'rollback', 'drain'):
            p.add_argument('--approve-release')


def live_adapter(config):
    """Only a built-in qualified adapter may be enabled here, never user plugins."""
    raise OnedeployError('unsupported_capability', 'Live AutoDeploy is not enabled: the pinned Daytona GPU transport and authenticated routing require provider qualification. No cloud resources were changed.')


def dispatch(args):
    action = args.autodeploy_action
    if action == 'init':
        # Syntactically valid placeholders: not inspected or authorized provider resources.
        template = {'schema_version': 1, 'deployment_id': 'my-worker', 'repository': 'OWNER/REPOSITORY',
                    'branch': 'deployment', 'target': 'REVIEW_TARGET',
                    'resources': {'cpu': 2, 'memory_gib': 8, 'disk_gib': 20, 'gpu_count': 1, 'gpu_type': 'REVIEW_GPU'},
                    'max_workers': 1, 'budget_usd': 1, 'idle_timeout_seconds': 60, 'candidate_timeout_seconds': 300}
        planner.save_json(template, args.output)
        return {'created': True, 'status': 'template', 'next_actions': ['Replace OWNER/REPOSITORY, REVIEW_TARGET and REVIEW_GPU placeholders; review resources, branch and budget. No provider access was performed.']}
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
        adapter = live_adapter(config)
        from .state import StateStore
        from .reconciler import Reconciler
        reconciler = Reconciler(StateStore(args.state), adapter)
        release = reconciler.plan(config, spec)
        return reconciler.apply(release['release_id'], approved=True)
    if not args.approve_release:
        raise OnedeployError('approval_required', 'Explicit authorization of a reviewed release operation is required.')
    # No unaudited reconcile/rollback/delete bypass of the qualification gate.
    return live_adapter(None)
