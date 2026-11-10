"""Offline source-bound plans. No provider imports or code execution."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from onedeploy.errors import OnedeployError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_json(path):
    try:
        p = Path(path)
        if p.is_symlink() or p.stat().st_size > 1024 * 1024:
            raise ValueError()
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        value = json.loads(p.read_text(), object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError):
        raise OnedeployError('invalid_config', 'Expected a bounded UTF-8 JSON object without duplicate keys.') from None


def save_json(value, path):
    """Exclusive creation: never replace a reviewed plan or follow symlinks."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
    except FileExistsError:
        raise OnedeployError('conflict', 'Output already exists; choose a new output path.') from None
    except OSError:
        raise OnedeployError('invalid_config', 'Cannot create the requested output.') from None


def git(source, *args):
    try:
        result = subprocess.run(['git', '-C', str(Path(source).resolve()), *args], capture_output=True, text=True, timeout=15, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise OnedeployError('invalid_config', 'Cannot inspect the source checkout.') from None


def source_identity(source, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit or ''):
        raise OnedeployError('invalid_config', 'An exact lowercase 40-character Git commit is required.')
    head = git(source, 'rev-parse', 'HEAD')
    if head != commit:
        raise OnedeployError('stale_plan', 'Source HEAD does not match the requested commit.')
    if git(source, 'status', '--porcelain', '--untracked-files=normal'):
        raise OnedeployError('stale_plan', 'Source checkout must be clean, including untracked files.')
    return {'commit': head, 'tree': git(source, 'rev-parse', 'HEAD^{tree}')}


def make_plan(config, source, commit, image, model_digest, evidence_digest):
    from .models import DeploymentConfig, ReleaseSpec
    config = DeploymentConfig.from_dict(config).to_dict()
    spec = ReleaseSpec.from_dict({"source_commit": commit, "image_digest": image, "model_digest": model_digest, "evidence_digest": evidence_digest}).to_dict()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[0-9a-f]{64}', image or ''):
        raise OnedeployError('invalid_config', 'Image must be an immutable registry SHA-256 reference.')
    identity = source_identity(source, commit)
    remote = git(source, 'remote', 'get-url', 'origin')
    allowed = {f"https://github.com/{config['repository']}.git", f"https://github.com/{config['repository']}", f"git@github.com:{config['repository']}.git"}
    if remote not in allowed:
        raise OnedeployError('stale_plan', 'Source origin does not match the approved repository.')
    branch = git(source, 'rev-parse', '--abbrev-ref', 'HEAD')
    if branch != config['branch']:
        ref_head = git(source, 'rev-parse', '--verify', 'refs/remotes/origin/' + config['branch'])
        if branch != 'HEAD' or ref_head != commit:
            raise OnedeployError('stale_plan', 'Source branch does not match the approved branch.')
    plan = {'schema_version': 1, 'kind': 'autodeploy-plan', 'config': config,
            'source': identity, 'spec': spec, 'config_digest': digest(config)}
    plan['id'] = digest(plan)
    return plan


def verify_plan(plan, source):
    required = {'schema_version', 'kind', 'config', 'source', 'spec', 'config_digest', 'id'}
    if set(plan) != required or plan.get('kind') != 'autodeploy-plan' or plan.get('schema_version') != 1:
        raise OnedeployError('invalid_config', 'Unsupported AutoDeploy plan.')
    if not isinstance(plan['source'], dict) or set(plan['source']) != {'commit', 'tree'} or not isinstance(plan['config'], dict) or not isinstance(plan['spec'], dict):
        raise OnedeployError('invalid_config', 'Malformed source-bound plan.')
    from .models import DeploymentConfig, ReleaseSpec
    DeploymentConfig.from_dict(plan['config'])
    ReleaseSpec.from_dict(plan['spec'])
    body = {k: v for k, v in plan.items() if k != 'id'}
    if plan['id'] != digest(body) or plan['config_digest'] != digest(plan['config']):
        raise OnedeployError('stale_plan', 'Plan integrity validation failed.')
    rebuilt = make_plan(plan['config'], source, plan['source'].get('commit'), plan['spec']['image_digest'], plan['spec']['model_digest'], plan['spec']['evidence_digest'])
    if rebuilt != plan:
        raise OnedeployError('stale_plan', 'Source or plan changed; create a fresh plan.')
    return plan


def negotiate_capabilities(provider, *, workload, required=(), native_observation=None):
    """Provider-neutral planning preflight; does not mutate legacy plan digests.

    Registry discovery never authorizes execution. Provider-specific planners
    must still bind target/account observations and exact reviewed source.
    """
    from .capabilities import negotiate
    return negotiate(provider, workload=workload, required=required, native_observation=native_observation)
