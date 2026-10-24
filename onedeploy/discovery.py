"""Version 1 passive import and explicit resolution contracts.

Discovery never executes source, imports application Python, invokes Git, reads an
implicit .env, or calls a provider. All importers preserve the original source.
Their deliberately small mappings are described by ``importer_matrix``. Saved
artifacts are private, exclusive, local and source-bound, not portable credentials.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import tomllib

import yaml
from jsonschema import Draft202012Validator
from .config import StrictLoader, validate_manifest, schema as manifest_schema, KEY
from .errors import OnedeployError
from .providers import get_adapter

VERSION = 1
MAX_INPUT = 1024 * 1024
MAX_ARTIFACT = 16 * 1024 * 1024
MAX_FILES = 10000
MAX_BYTES = 512 * 1024 * 1024
MAX_DEPTH = 24
MAX_CANDIDATES = 128
MAX_PARSE_NODES = 50000
MAX_SECONDS = 15
EXCLUDED = {'.git', '.onedeploy', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules'}
PROVIDERS = ('railway', 'daytona', 'modal')
SHA = r'^[a-f0-9]{64}$'
SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,254}$')
SECRET_NAME = re.compile(r'(?i)(secret|password|passwd|token|api[_-]?key|credential|private[_-]?key)')
TRUST_WARNING = 'Source is executable application code; static validation is not a security review or deployment qualification.'


def _error(code, message, result=None):
    error = OnedeployError(code, message)
    if result is not None:
        error.result = result
    raise error


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        _error('invalid_config', 'Artifact contains unsupported data')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _id(value):
    return digest({key: item for key, item in value.items() if key != 'id'})


def _path(path, root=None, exists=True):
    if not isinstance(path, (str, Path)) or '\x00' in str(path):
        _error('invalid_config', 'Invalid input path')
    p = Path(path).absolute()
    if any(part.is_symlink() for part in (p, *p.parents)):
        _error('invalid_config', 'Symlink paths are not accepted')
    p = p.resolve()
    if root is not None and not p.is_relative_to(root):
        _error('invalid_config', 'Input paths must remain within the selected project')
    if exists and not p.exists():
        _error('invalid_config', 'Selected input does not exist')
    return p


def _relative(root, value, *, directory=False):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or '..' in Path(value).parts or '\\' in value or '\x00' in value:
        _error('invalid_config', 'Configuration paths must be safe project-relative paths')
    p = _path(root / value, root)
    if directory and not p.is_dir():
        _error('invalid_config', 'Build context must be a local directory')
    return p


def _read_bytes(path, limit=MAX_INPUT):
    p = _path(path)
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                _error('invalid_config', 'Input must be a bounded regular file')
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            _error('stale_plan', 'Input changed while reading; discover again')
        return data
    except OSError:
        _error('invalid_config', 'Input could not be read safely')


def _text(path):
    try:
        return _read_bytes(path).decode('utf-8')
    except UnicodeError:
        _error('invalid_config', 'Configuration input must be UTF-8')


def _bounded(value):
    count = 0
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_PARSE_NODES:
            _error('invalid_config', 'Input structure exceeds safety limits')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                _error('invalid_config', 'Object keys must be strings')
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            _error('invalid_config', 'Unsupported configuration value type')
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _error('invalid_config', 'Duplicate JSON fields are not accepted')
        result[key] = value
    return result


def _json(text):
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: _error('invalid_config', 'Non-finite JSON is not accepted'))
        return _bounded(value)
    except (ValueError, RecursionError):
        _error('invalid_config', 'Invalid JSON input')


def _yaml(text):
    try:
        return _bounded(yaml.load(text, Loader=StrictLoader))
    except (yaml.YAMLError, ValueError, RecursionError):
        _error('invalid_config', 'Invalid strict YAML input')


def _obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def _string():
    return {'type': 'string', 'maxLength': 4096}


def _issue_schema():
    return _obj({'field': _string(), 'type': {'enum': ['string', 'integer', 'boolean', 'object', 'array', 'choice']}, 'reason': _string(), 'choices': {'type': 'array', 'items': {'type': ['string', 'integer', 'boolean']}, 'maxItems': 128}}, ['field', 'type', 'reason'])


def _provenance_schema():
    return _obj({key: _string() for key in ('field', 'path', 'location', 'importer', 'version', 'source_digest', 'mapping', 'origin', 'ambiguity')}, ['field', 'path', 'location', 'importer', 'version', 'source_digest', 'mapping', 'origin', 'ambiguity'])


def _source_schema():
    return _obj({'root': _string(), 'bundle_sha256': {'type': 'string', 'pattern': SHA}, 'files': {'type': 'array', 'maxItems': MAX_FILES, 'items': _obj({'path': _string(), 'size': {'type': 'integer', 'minimum': 0, 'maximum': MAX_BYTES}, 'sha256': {'type': 'string', 'pattern': SHA}, 'executable': {'type': 'boolean'}}, ['path', 'size', 'sha256', 'executable'])}}, ['root', 'bundle_sha256', 'files'])


def _partial_schema():
    result = manifest_schema()
    result.pop('required', None)
    # Partial target/source sections are filled only by typed resolution answers.
    for key in ('target', 'source'):
        result['properties'][key].pop('required', None)
    return result


def discovery_schema():
    issues = {'type': 'array', 'maxItems': 512, 'items': _issue_schema()}
    provenance = {'type': 'array', 'maxItems': 4096, 'items': _provenance_schema()}
    candidate = _obj({'id': {'type': 'string', 'pattern': SHA}, 'format': {'enum': ['manifest', 'dockerfile', 'compose', 'railway', 'modal', 'snapshot', 'unsupported']}, 'path': _string(), 'root': _string(), 'service': {'type': ['string', 'null']}, 'manifest': _partial_schema(), 'missing': issues, 'conflicts': issues, 'unsupported': issues, 'provenance': provenance, 'warnings': {'type': 'array', 'items': _string()}}, ['id', 'format', 'path', 'root', 'service', 'manifest', 'missing', 'conflicts', 'unsupported', 'provenance', 'warnings'])
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **_obj({'version': {'type': 'integer', 'const': VERSION}, 'kind': {'const': 'discovery'}, 'id': {'type': 'string', 'pattern': SHA}, 'provider': {'enum': list(PROVIDERS)}, 'root': _string(), 'selected_path': _string(), 'source': _source_schema(), 'candidates': {'type': 'array', 'maxItems': MAX_CANDIDATES, 'items': candidate}, 'missing': issues, 'conflicts': issues, 'unsupported': issues, 'provenance': provenance, 'warnings': {'type': 'array', 'items': _string()}, 'status': {'enum': ['ready', 'input_required', 'conflict', 'unsupported_capability']}}, ['version', 'kind', 'id', 'provider', 'root', 'selected_path', 'source', 'candidates', 'missing', 'conflicts', 'unsupported', 'provenance', 'warnings', 'status'])}


def answers_schema():
    m = manifest_schema()['properties']
    variables = copy.deepcopy(m['variables'])
    # Answer files carry references, not runtime values, even for nonsecret inputs.
    variables['additionalProperties']['properties'].pop('default', None)
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **_obj({'candidate': {'type': 'string', 'pattern': SHA}, 'name': m['name'], 'profile': m['profile'], 'target': {**m['target'], 'required': []}, 'runtime': m['runtime'], 'resources': m['resources'], 'ingress': m['ingress'], 'variables': variables, 'dependencies': m['dependencies'], 'storage': m['storage'], 'trust_code': {'type': 'boolean'}, 'snapshot': m['target']['properties']['snapshot'], 'snapshot_name': m['build']['properties']['snapshot_name'], 'checks': m['checks'], 'rollback': m['rollback']})}


def profile_schema():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **_obj({'version': {'type': 'integer', 'const': VERSION}, 'kind': {'const': 'profile'}, 'id': {'type': 'string', 'pattern': SHA}, 'manifest': manifest_schema(), 'root': _string(), 'source': _source_schema(), 'discovery_id': {'type': 'string', 'pattern': SHA}, 'discovery': discovery_schema(), 'resolution': _obj({'answers': answers_schema(), 'answers_digest': {'type': 'string', 'pattern': SHA}, 'candidate': {'type': 'string', 'pattern': SHA}, 'normalizer_version': {'const': '1'}}, ['answers', 'answers_digest', 'candidate', 'normalizer_version']), 'provenance': {'type': 'array', 'items': _provenance_schema(), 'maxItems': 4096}, 'warnings': {'type': 'array', 'items': _string()}}, ['version', 'kind', 'id', 'manifest', 'root', 'source', 'discovery_id', 'discovery', 'resolution', 'provenance', 'warnings'])}


def resolution_schema():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **_obj({'status': {'enum': ['ready', 'input_required', 'conflict', 'unsupported_capability']}, 'missing': {'type': 'array', 'items': _issue_schema()}, 'conflicts': {'type': 'array', 'items': _issue_schema()}, 'unsupported': {'type': 'array', 'items': _issue_schema()}, 'profile': profile_schema()}, ['status', 'missing', 'conflicts', 'unsupported'])}


def _validate(value, contract, label):
    _bounded(value)
    if not Draft202012Validator(contract).is_valid(value):
        _error('invalid_config', label + ' does not match its strict version 1 schema')
    return value


def _excluded(name):
    return name in EXCLUDED or name.startswith('.env') or name.endswith(('.pem', '.key', '.p12', '.pfx'))


def inventory(root):
    """Bounded, passive source inventory. Deliberately does not invoke Git."""
    root = _path(root)
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home():
        _error('invalid_config', 'Select a project directory, not a filesystem or home root')
    records = []
    total = 0
    visited = 0
    start = time.monotonic()
    pending = [(root, 0)]
    while pending:
        parent, depth = pending.pop()
        if depth > MAX_DEPTH or time.monotonic() - start > MAX_SECONDS:
            _error('invalid_config', 'Source traversal exceeds safety limits')
        try:
            with os.scandir(parent) as iterator:
                entries = []
                for entry in iterator:
                    visited += 1
                    if visited > MAX_FILES:
                        _error('invalid_config', 'Source file count exceeds safety limits')
                    entries.append(entry)
        except OSError:
            _error('invalid_config', 'Source directory could not be read safely')
        for entry in sorted(entries, key=lambda item: item.name):
            if _excluded(entry.name):
                continue
            if entry.is_symlink():
                _error('invalid_config', 'Source symlinks are not accepted')
            p = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                pending.append((p, depth + 1))
                continue
            if not entry.is_file(follow_symlinks=False):
                _error('invalid_config', 'Source contains non-regular files')
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                _error('invalid_config', 'Source changed while inventorying')
            total += size
            if total > MAX_BYTES:
                _error('invalid_config', 'Source byte count exceeds safety limits')
            content = _read_bytes(p, MAX_BYTES - total + size)
            records.append({'path': p.relative_to(root).as_posix(), 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'executable': bool(p.stat().st_mode & 0o111)})
    records.sort(key=lambda item: item['path'])
    return {'root': str(root), 'files': records, 'bundle_sha256': digest(records)}


def _question(field, reason, typ='string', choices=None):
    result = {'field': field, 'type': typ, 'reason': reason}
    if choices is not None:
        result['choices'] = choices
    return result


def _unsupported(candidate, field, reason):
    candidate['unsupported'].append(_question(field, reason))


def _leaf_fields(value, prefix=''):
    if isinstance(value, dict) and value:
        for key in sorted(value):
            yield from _leaf_fields(value[key], prefix + ('.' if prefix else '') + key)
    else:
        yield prefix


def _provenance(candidate, fields, *, origin='explicit', mapping='Preserved static setting', location=None):
    for field in fields:
        candidate['provenance'].append({'field': field, 'path': candidate['path'], 'location': location or field, 'importer': candidate['format'], 'version': '1', 'source_digest': candidate['_digest'], 'mapping': mapping, 'origin': origin, 'ambiguity': ''})


def _candidate(root, path, format, provider, service=None):
    rel = path.relative_to(root).as_posix() if path != root else '.'
    result = {'id': digest({'path': rel, 'format': format, 'service': service}), 'format': format, 'path': rel, 'root': str(root), 'service': service, 'manifest': {'version': 1, 'provider': provider, 'source': {'path': '.'}}, 'missing': [], 'conflicts': [], 'unsupported': [], 'provenance': [], 'warnings': [TRUST_WARNING], '_digest': hashlib.sha256(_read_bytes(path)).hexdigest() if path.is_file() else digest({'root': str(root)})}
    return result


def _unsafe_literals(manifest):
    # Never collect values from declarative credential slots. Explicitly nonsecret
    # naming does not turn an obvious password/token default into safe evidence.
    for name, spec in manifest.get('variables', {}).items():
        if 'default' in spec and (spec.get('secret', True) or SECRET_NAME.search(name)):
            return True
    for arg in manifest.get('runtime', {}).get('command', []):
        if re.search(r'(?i)(password|secret|token|api[_-]?key)(?:=|\b)', arg):
            return True
    for value in _strings(manifest):
        if re.search(r'(?i)[a-z][a-z0-9+.-]*://[^\s/]+@|[?&](?:token|key|secret|signature|access_token)=', value):
            return True
    return False


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _native(root, path, provider, value=None):
    candidate = _candidate(root, path, 'manifest', provider)
    manifest = validate_manifest(value if value is not None else _yaml(_text(path)))
    if _unsafe_literals(manifest):
        _unsupported(candidate, 'variables', 'Literal credentials are not accepted in imported evidence; use environment references')
        return candidate
    if manifest['provider'] != provider:
        candidate['conflicts'].append(_question('provider', 'The manifest explicitly selects a different provider; choose that provider or a different source'))
        return candidate
    source = _relative(path.parent, manifest['source']['path'], directory=True)
    if not source.is_relative_to(root):
        _error('invalid_config', 'Manifest source escapes the selected project')
    candidate['root'] = str(source)
    candidate['manifest'] = copy.deepcopy(manifest)
    candidate['manifest']['source']['path'] = '.'
    _provenance(candidate, list(_leaf_fields(manifest)), mapping='Strict native manifest; source.path rebased to bound root')
    _check_build_paths(candidate)
    return candidate


def _check_build_paths(candidate):
    root = Path(candidate['root'])
    manifest = candidate['manifest']
    for section, field in (('build', 'context'), ('build', 'dockerfile'), ('source', 'entrypoint')):
        value = manifest.get(section, {}).get(field)
        if value:
            _relative(root, value, directory=field == 'context')


def _docker(root, path, provider, format='dockerfile'):
    candidate = _candidate(root, path, format, provider)
    if provider == 'modal':
        _unsupported(candidate, 'profile', 'Modal requires a reviewed Python ASGI entrypoint; Dockerfile transport is unsupported')
        return candidate
    candidate['root'] = str(path.parent)
    candidate['manifest'].update({'profile': 'docker-http', 'build': {'context': '.', 'dockerfile': path.name}})
    if path.name != 'Dockerfile':
        _unsupported(candidate, 'build.dockerfile', 'Current container adapters require a root file named Dockerfile')
    ports = set()
    lines = _text(path).splitlines()
    if not any(re.match(r'(?i)^\s*FROM\s+\S+', line) for line in lines):
        _unsupported(candidate, 'build', 'A Dockerfile must contain a FROM instruction')
    for line in lines:
        match = re.match(r'(?i)^\s*EXPOSE\s+(.+)', line)
        if match:
            for token in match.group(1).split():
                if re.fullmatch(r'[0-9]+(?:/tcp)?', token) and 1 <= int(token.split('/')[0]) <= 65535:
                    ports.add(int(token.split('/')[0]))
                else:
                    candidate['missing'].append(_question('runtime.port', 'EXPOSE is dynamic or non-TCP; select the actual HTTP listener port', 'integer'))
        match = re.match(r'(?i)^\s*(?:ENV|ARG)\s+([A-Za-z_][A-Za-z0-9_]*)(?:=|\s+)(.+)', line)
        if match and SECRET_NAME.search(match.group(1)) and match.group(2).strip():
            _unsupported(candidate, 'build', 'A credential-shaped build default is embedded in the Dockerfile; remove it and use runtime references')
    if ports:
        candidate['missing'].append(_question('runtime.port', 'EXPOSE is metadata, not proof of the listener; explicitly confirm a port', 'integer', sorted(ports)))
    _provenance(candidate, ['profile'], origin='inferred', mapping='Container HTTP profile suggestion; choose profile explicitly if different')
    _provenance(candidate, ['build.context', 'build.dockerfile'], mapping='Preserve original Dockerfile without building or rewriting')
    return candidate


def _compose(root, path, provider):
    value = _yaml(_text(path))
    if not isinstance(value, dict) or not isinstance(value.get('services'), dict) or not value['services']:
        _error('invalid_config', 'Compose must contain a nonempty services object')
    global_unsupported = set(value) - {'name', 'version', 'services', 'volumes'}
    if 'version' in value and str(value['version']) not in {'3', '3.0', '3.1', '3.2', '3.3', '3.4', '3.5', '3.6', '3.7', '3.8', '3.9'}:
        global_unsupported.add('version')
    if 'name' in value and not isinstance(value['name'], str):
        _error('invalid_config', 'Compose project name must be a string')
    if 'volumes' in value and (not isinstance(value['volumes'], dict) or any(spec not in (None, {}) for spec in value['volumes'].values())):
        global_unsupported.add('volumes')
    candidates = []
    for service, config in sorted(value['services'].items()):
        if not SAFE_ID.fullmatch(service) or not isinstance(config, dict):
            _error('invalid_config', 'Compose service names and definitions must be static objects')
        candidate = _candidate(root, path, 'compose', provider, service)
        candidates.append(candidate)
        candidate['manifest']['profile'] = 'docker-http'
        if provider == 'modal':
            _unsupported(candidate, 'profile', 'Compose containers are not a Modal Python ASGI entrypoint')
        if global_unsupported:
            _unsupported(candidate, 'compose', 'Unsupported top-level Compose semantics are present')
        if set(config) - {'build', 'environment', 'ports', 'expose', 'depends_on', 'volumes'}:
            _unsupported(candidate, 'compose.service', 'Unsupported service fields are present; command, image, networks, privileges and other runtime semantics cannot be silently dropped')
        build = config.get('build')
        if isinstance(build, str):
            build = {'context': build}
        if not isinstance(build, dict) or set(build) - {'context', 'dockerfile'} or not isinstance(build.get('context'), str):
            _unsupported(candidate, 'build', 'Select a static build-backed service; build arguments, remote/image-only builds and extra build settings are unsupported')
        else:
            context = _relative(path.parent, build['context'], directory=True)
            if not context.is_relative_to(root):
                _error('invalid_config', 'Compose build context escapes the selected project')
            dockerfile = build.get('dockerfile', 'Dockerfile')
            docker = _relative(context, dockerfile)
            nested = _docker(root, docker, provider)
            candidate['root'] = str(context)
            candidate['manifest']['build'] = {'context': '.', 'dockerfile': docker.relative_to(context).as_posix()}
            candidate['unsupported'].extend(nested['unsupported'])
            _provenance(candidate, ['build.context', 'build.dockerfile'], location='services.build', mapping='Selected service context rebased; original Dockerfile preserved')
        environment = config.get('environment', {})
        if isinstance(environment, list):
            names = []
            for entry in environment:
                if not isinstance(entry, str) or not KEY.fullmatch(entry):
                    _unsupported(candidate, 'variables', 'Compose environment accepts names only; remove literal values and interpolation, and supply private runtime references')
                    continue
                names.append(entry)
            if len(names) != len(set(names)):
                _error('invalid_config', 'Duplicate Compose environment names')
            environment = dict.fromkeys(names)
        if not isinstance(environment, dict):
            _error('invalid_config', 'Compose environment must be names or an object')
        for name, item in sorted(environment.items()):
            if not KEY.fullmatch(name):
                _error('invalid_config', 'Invalid Compose variable name')
            if item is not None:
                _unsupported(candidate, 'variables', 'Literal environment values and interpolation are unsupported; select private runtime references')
            candidate['manifest'].setdefault('variables', {})[name] = {'type': 'string', 'required': True, 'secret': True, 'source': 'env:' + name}
            _provenance(candidate, ['variables.' + name], location='services.environment', mapping='Variable name only; value never collected')
        ports = set()
        for field in ('ports', 'expose'):
            entries = config.get(field, [])
            if not isinstance(entries, list):
                _error('invalid_config', 'Compose port declarations must be an array')
            for entry in entries:
                text = str(entry) if type(entry) in (str, int) else ''
                match = re.fullmatch(r'(?:[0-9]+:)?([0-9]+)(?:/tcp)?', text)
                if not match or not 1 <= int(match.group(1)) <= 65535:
                    _unsupported(candidate, 'runtime.port', 'Only static single TCP port candidates without explicit host/IP binding are supported; host exposure restrictions cannot be dropped')
                else:
                    ports.add(int(match.group(1)))
        if not ports and isinstance(build, dict) and isinstance(build.get('context'), str) and not (set(build) - {'context', 'dockerfile'}):
            candidate['missing'].extend(nested['missing'])
        if ports:
            candidate['missing'].append(_question('runtime.port', 'Compose host publication is not imported; confirm the container listener port', 'integer', sorted(ports)))
        dependencies = config.get('depends_on', [])
        if dependencies:
            if not isinstance(dependencies, list) or not all(isinstance(name, str) and SAFE_ID.fullmatch(name) for name in dependencies):
                _unsupported(candidate, 'dependencies', 'Conditional or dynamic Compose dependencies are unsupported')
            else:
                candidate['missing'].append(_question('dependencies', 'Map every selected-service dependency to an explicit external dependency with TLS/runtime references', 'array', sorted(dependencies)))
        volumes = config.get('volumes', [])
        if volumes:
            if not isinstance(volumes, list):
                _error('invalid_config', 'Compose volumes must be an array')
            names = []
            for mount in volumes:
                if not isinstance(mount, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*:/[A-Za-z0-9_/-]+(?::(?:ro|rw))?', mount):
                    _unsupported(candidate, 'storage', 'Only named volume requirements can be resolved; bind mounts and dynamic/extended mounts are unsupported')
                else:
                    names.append(mount)
            if names:
                candidate['missing'].append(_question('storage', 'Map every named mount to an explicit existing provider storage resource without changing path/access', 'array', sorted(names)))
        if len(value['services']) > 1:
            candidate['warnings'].append('Only the explicitly selected service is deployed. Other Compose services are not orchestrated.')
    return candidates


def _railway(root, path, provider):
    candidate = _candidate(root, path, 'railway', provider)
    if path.suffix == '.json':
        value = _json(_text(path))
    else:
        try:
            value = _bounded(tomllib.loads(_text(path)))
        except (ValueError, RecursionError):
            _error('invalid_config', 'Invalid Railway TOML input')
    if not isinstance(value, dict):
        _error('invalid_config', 'Railway configuration must be an object')
    if provider != 'railway':
        _unsupported(candidate, 'provider', 'Railway configuration cannot be translated to another provider')
        return candidate
    if set(value) - {'$schema', 'build', 'deploy'}:
        _unsupported(candidate, 'railway', 'Unknown Railway sections are unsupported')
    if '$schema' in value and value['$schema'] != 'https://railway.com/railway.schema.json':
        _unsupported(candidate, 'railway.schema', 'Only the documented Railway schema reference is accepted; no URL is fetched')
    build, deploy = value.get('build', {}), value.get('deploy', {})
    if not isinstance(build, dict) or not isinstance(deploy, dict):
        _error('invalid_config', 'Railway build and deploy settings must be objects')
    if set(build) - {'builder', 'dockerfilePath'} or build.get('builder', 'DOCKERFILE') != 'DOCKERFILE':
        _unsupported(candidate, 'build', 'Only the static DOCKERFILE builder and dockerfilePath are supported')
    if set(deploy) - {'healthcheckPath', 'healthcheckTimeout'}:
        _unsupported(candidate, 'deploy', 'Unmapped Railway commands, replica/resource/lifecycle settings are unsupported')
    dockerfile = build.get('dockerfilePath', 'Dockerfile')
    docker = _relative(path.parent, dockerfile)
    nested = _docker(root, docker, provider)
    candidate['unsupported'].extend(nested['unsupported'])
    candidate['missing'].extend(nested['missing'])
    candidate['root'] = str(path.parent)
    candidate['manifest'].update({'profile': 'docker-http', 'build': {'context': '.', 'dockerfile': dockerfile}})
    for original, normalized in (('healthcheckPath', 'health_path'), ('healthcheckTimeout', 'timeout')):
        if original in deploy:
            candidate['manifest'].setdefault('runtime', {})[normalized] = deploy[original]
            _provenance(candidate, ['runtime.' + normalized], location='deploy.' + original, mapping='Static provider setting retained in original config and used for local verification metadata')
    _provenance(candidate, ['build.context', 'build.dockerfile'], location='build', mapping='Preserved static Dockerfile build')
    return candidate


def _modal_literal(node):
    """Small literal grammar, intentionally not ast.literal_eval on whole input."""
    if isinstance(node, ast.Constant) and type(node.value) in (str, int, float, bool, type(None)):
        return node.value
    return ...


def _modal_image(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    method = node.func.attr
    owner = node.func.value
    if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name) and owner.value.id == 'modal' and owner.attr == 'Image':
        if method != 'debian_slim' or node.args or len({item.arg for item in node.keywords}) != len(node.keywords):
            return False
        return all(item.arg == 'python_version' and isinstance(_modal_literal(item.value), str) and re.fullmatch(r'[0-9]+\.[0-9]+', item.value.value) for item in node.keywords)
    if method in {'pip_install', 'apt_install'} and _modal_image(owner) and not node.keywords and node.args:
        return all(isinstance(_modal_literal(arg), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.<>=!~,+\[\]-]{0,254}', arg.value) for arg in node.args)
    return False


def _modal_function_settings(call, images):
    allowed = {'image', 'cpu', 'memory', 'timeout', 'min_containers', 'max_containers', 'scaledown_window'}
    if call.args or len({item.arg for item in call.keywords}) != len(call.keywords):
        return False
    for item in call.keywords:
        if item.arg not in allowed:
            return False
        if item.arg == 'image':
            if not (isinstance(item.value, ast.Name) and item.value.id in images) and not _modal_image(item.value):
                return False
        elif type(_modal_literal(item.value)) not in (int, float) or not 0 <= _modal_literal(item.value) <= 1048576:
            return False
    return True


def _modal(root, path, provider):
    candidate = _candidate(root, path, 'modal', provider)
    if provider != 'modal':
        _unsupported(candidate, 'provider', 'Modal Python packaging cannot be translated to a container provider')
        return candidate
    try:
        tree = ast.parse(_text(path), filename='<deployment-input>')
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        _error('invalid_config', 'Invalid Python deployment syntax')
    if sum(1 for _ in ast.walk(tree)) > MAX_PARSE_NODES:
        _error('invalid_config', 'Python AST exceeds safety limits')
    app_name = None
    app_var = None
    functions = []
    images = set()
    imported = False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Import) and len(node.names) == 1 and node.names[0].name == 'modal' and node.names[0].asname in (None, 'modal'):
            if imported or app_var:
                _unsupported(candidate, 'source.entrypoint', 'Repeated or out-of-order Modal imports are unsupported')
            imported = True
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == 'modal' and call.func.attr == 'App' and len(call.args) <= 1 and not call.keywords:
                name = _modal_literal(call.args[0]) if call.args else None
                if not imported or app_var is not None or (name is not None and (not isinstance(name, str) or not SAFE_ID.fullmatch(name))):
                    _unsupported(candidate, 'source.entrypoint', 'Only one literal modal.App declaration after import modal is supported')
                else:
                    app_var, app_name = node.targets[0].id, name
                    if app_var == 'modal':
                        _unsupported(candidate, 'source.entrypoint', 'Modal module shadowing is unsupported')
                continue
            if imported and _modal_image(call):
                name = node.targets[0].id
                if name in images or name in {'modal', app_var}:
                    _unsupported(candidate, 'source.entrypoint', 'Image/module/application declaration shadowing is unsupported')
                images.add(name)
                _provenance(candidate, ['source.image'], location='AST image declaration', mapping='Static Debian image/package settings remain owned by original Python; not translated into generic resources')
                continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = False
            asgi = False
            valid = bool(app_var)
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute) or not isinstance(decorator.func.value, ast.Name):
                    valid = False
                    continue
                owner, method = decorator.func.value.id, decorator.func.attr
                if owner == app_var and method == 'function' and not function:
                    function = _modal_function_settings(decorator, images)
                    valid = valid and function
                elif owner == 'modal' and method == 'asgi_app' and not asgi:
                    asgi = (not decorator.args and len(decorator.keywords) <= 1 and all(item.arg == 'requires_proxy_auth' and type(_modal_literal(item.value)) is bool for item in decorator.keywords))
                    valid = valid and asgi
                    if not any(item.arg == 'requires_proxy_auth' and _modal_literal(item.value) is True for item in decorator.keywords):
                        candidate['warnings'].append('The original Modal endpoint does not declare proxy authentication; review application authentication and exposure before deployment.')
                else:
                    valid = False
            args = node.args
            # ASGI factory subset is argument-free. Annotations/defaults evaluate
            # at module import, so reject rather than calling them inert literals.
            if not valid or not (function and asgi) or node.returns is not None or args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg or args.defaults or any(args.kw_defaults):
                _unsupported(candidate, 'source.entrypoint', 'Only argument-free static ASGI factories with reviewed literal function settings are supported; evaluated signatures/decorators are rejected')
            elif node.name in images or node.name in {'modal', app_var} or node.name in functions:
                _unsupported(candidate, 'source.entrypoint', 'Duplicate or shadowed function declarations are unsupported')
            else:
                functions.append(node.name)
                _provenance(candidate, ['source.function'], location='AST function decorators', mapping='Static image/CPU/memory/timeout/container/auth settings preserved in original Python; never silently translated')
            continue
        _unsupported(candidate, 'source.entrypoint', 'Dynamic declarations, top-level imports/effects and computed configuration need reviewed native packaging; Python was not executed')
    if not imported or not app_var or not functions:
        _unsupported(candidate, 'source.entrypoint', 'A supported static Modal App and ASGI function declaration is required')
    candidate['manifest'].update({'profile': 'asgi', 'source': {'path': '.', 'entrypoint': path.relative_to(root).as_posix()}})
    if app_name:
        candidate['manifest'].setdefault('target', {})['app'] = app_name
        _provenance(candidate, ['target.app'], location='modal.App', mapping='Literal original application name')
    if len(functions) == 1:
        candidate['manifest'].setdefault('target', {})['function'] = functions[0]
    elif functions:
        candidate['missing'].append(_question('target.function', 'Choose the ASGI function used for verification; original application is retained', 'choice', sorted(functions)))
    _provenance(candidate, ['profile', 'source.entrypoint'], location='AST', mapping='Static literal declarations only; original Python retained and not imported')
    return candidate


def importer_matrix():
    return {'version': 1, 'qualification': 'experimental/offline-tested', 'manifest': {'version': 1, 'mapping': 'Strict native schema, no unknown keys; source path rebased inside selected scope'}, 'dockerfile': {'version': 1, 'mapping': 'Preserve original root Dockerfile; EXPOSE becomes explicit listener question', 'providers': ['railway', 'daytona']}, 'compose': {'version': 1, 'mapping': 'One selected service: static build context/dockerfile, environment names, TCP port candidates, named storage and dependency requirements', 'rejected': ['image-only', 'literal environment values', 'interpolation', 'include/extends', 'commands', 'privileges', 'networks', 'devices', 'bind mounts', 'explicit host/IP bindings', 'extra fields']}, 'railway': {'version': 1, 'mapping': 'DOCKERFILE builder/dockerfilePath; deploy healthcheckPath/healthcheckTimeout', 'rejected': ['commands', 'other builders', 'resources', 'replicas', 'restart policy', 'unknown fields']}, 'modal': {'version': 1, 'mapping': 'import modal; one literal modal.App; supported literal Debian image/packages, function image/resource settings and ASGI proxy authentication; original argument-free factory body retained', 'rejected': ['computed top-level config', 'external top-level imports', 'evaluated function signatures', 'unknown decorators', 'unsupported image/resource settings', 'secrets', 'shadowed declarations']}, 'snapshot': {'version': 1, 'mapping': 'Explicit Daytona snapshot identifier; provider inspection binds immutable receipt before execution', 'note': 'A snapshot label alone is not evidence of immutability'}, 'unknown': {'mapping': 'Explicit rejection; no Kubernetes, Helm, workflows or arbitrary application YAML import'}}


def _finish(candidate):
    root = Path(candidate['root'])
    if candidate['manifest']['provider'] == 'railway' and candidate['format'] != 'unsupported':
        configs = [name for name in ('railway.toml', 'railway.json') if (root / name).exists()]
        if configs and candidate['format'] != 'railway':
            _unsupported(candidate, 'railway', 'Railway implicitly consumes local Railway configuration; select that configuration explicitly rather than bypass its semantics')
        if len(configs) > 1:
            _unsupported(candidate, 'railway', 'Multiple Railway configuration files are present; resolve their ambiguity in source')
    if candidate['format'] == 'manifest':
        manifest = candidate['manifest']
        if manifest['provider'] == 'railway' and not (root / manifest.get('build', {}).get('dockerfile', 'Dockerfile')).is_file():
            _unsupported(candidate, 'build.dockerfile', 'The native Railway source requires a root Dockerfile that is currently missing')
        if not _required(manifest, 'manifest'):
            try:
                get_adapter(manifest['provider'], manifest).validate(manifest)
            except OnedeployError as error:
                _unsupported(candidate, 'capability', error.message)
    # Ensure every normalized leaf has provenance without leaking raw literals.
    fields = {item['field'] for item in candidate['provenance']}
    _provenance(candidate, [field for field in _leaf_fields(candidate['manifest']) if field not in fields], origin='inferred', mapping='Importer normalization metadata or preserved static declaration')
    candidate.pop('_digest', None)
    _validate(candidate['manifest'], _partial_schema(), 'Imported settings')
    return candidate


def discover(path, provider):
    if provider not in PROVIDERS:
        _error('invalid_config', 'Choose a supported provider')
    selected = _path(path)
    root = selected if selected.is_dir() else selected.parent
    source = inventory(root)
    candidates = []
    explicit = selected.is_file()
    if explicit and _excluded(selected.name):
        _error('invalid_config', 'Secret/environment files are not deployment specifications')
    paths = [selected] if explicit else [root / row['path'] for row in source['files'] if (Path(row['path']).name in {'Dockerfile', 'onedeploy.yaml', 'onedeploy.yml', 'compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml', 'railway.toml', 'railway.json'} or (provider == 'modal' and Path(row['path']).suffix == '.py'))]
    if len(paths) > MAX_CANDIDATES:
        _error('invalid_config', 'Too many possible deployment inputs; select a configuration file')
    for item in sorted(paths):
        name = item.name
        if name in {'compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml'}:
            candidates.extend(_compose(root, item, provider))
        elif name in {'railway.toml', 'railway.json'}:
            candidates.append(_railway(root, item, provider))
        elif name == 'Dockerfile' or (explicit and name.startswith('Dockerfile.')):
            candidates.append(_docker(root, item, provider))
        elif item.suffix == '.py':
            # An explicit .py is attempted. Folder discovery only recognizes a
            # literal Modal import marker; ordinary application Python is source.
            if explicit or re.search(r'^\s*(?:import modal\b|from modal\b)', _text(item), re.M):
                candidates.append(_modal(root, item, provider))
        elif item.suffix in {'.yaml', '.yml', '.json'}:
            value = _json(_text(item)) if item.suffix == '.json' else _yaml(_text(item))
            if isinstance(value, dict) and {'version', 'provider', 'source'} <= set(value):
                candidates.append(_native(root, item, provider, value))
            else:
                candidate = _candidate(root, item, 'unsupported', provider)
                _unsupported(candidate, 'input', 'This is not a native Onedeploy manifest; Kubernetes, Helm, workflow and arbitrary YAML/JSON are not imported')
                candidates.append(candidate)
        elif explicit:
            candidate = _candidate(root, item, 'unsupported', provider)
            _unsupported(candidate, 'input', 'This input format has no supported semantic importer')
            candidates.append(candidate)
    if not candidates and provider == 'daytona' and not explicit:
        candidate = _candidate(root, root, 'snapshot', provider)
        candidate['manifest']['profile'] = 'docker-http'
        candidate['missing'].append(_question('snapshot', 'Select an existing reviewed snapshot; remote inspection must bind its immutable image before deployment'))
        candidates.append(candidate)
    if len(candidates) > MAX_CANDIDATES:
        _error('invalid_config', 'Too many deployment candidates; select a configuration file')
    candidates = [_finish(candidate) for candidate in candidates]
    # Deterministically bind the snapshot after parsing, detecting concurrent edits.
    if inventory(root) != source:
        _error('stale_plan', 'Source changed during discovery; discover again')
    missing, conflicts, unsupported = [], [], []
    if len(candidates) > 1:
        conflicts.append(_question('candidate', 'Multiple deployment inputs or services are present; explicitly choose one candidate', 'choice', [item['id'] for item in candidates]))
    elif candidates:
        missing.extend(candidates[0]['missing'])
        conflicts.extend(candidates[0]['conflicts'])
        unsupported.extend(candidates[0]['unsupported'])
        missing.extend(_required(candidates[0]['manifest'], candidates[0]['format']))
    else:
        unsupported.append(_question('input', 'No supported deployment configuration found; select a native manifest, Dockerfile, Compose, Railway config or Modal entrypoint'))
    result = {'version': VERSION, 'kind': 'discovery', 'provider': provider, 'root': str(root), 'selected_path': str(selected), 'source': source, 'candidates': candidates, 'missing': _unique(missing), 'conflicts': _unique(conflicts), 'unsupported': _unique(unsupported), 'provenance': [record for item in candidates for record in item['provenance']], 'warnings': [TRUST_WARNING, 'Discovery is local and passive; provider identity, destination, costs and live compatibility are not verified.'], 'status': _status(missing, conflicts, unsupported)}
    result['id'] = _id(result)
    return _validate(result, discovery_schema(), 'Discovery')


def _unique(items):
    return list({canonical(item): item for item in items}.values())


def _status(missing, conflicts, unsupported):
    return 'unsupported_capability' if unsupported else 'conflict' if conflicts else 'input_required' if missing else 'ready'


def _required(manifest, format):
    missing = []
    if not manifest.get('name'):
        missing.append(_question('name', 'Choose an explicit application name'))
    for field in sorted(get_adapter(manifest['provider'], manifest).required_fields):
        if not manifest.get('target', {}).get(field):
            missing.append(_question('target.' + field, 'Select the exact provider destination identifier'))
    if format != 'manifest' and manifest.get('source', {}).get('trust_code') is not True:
        missing.append(_question('trust_code', 'Confirm you reviewed and trust the original executable build/startup source', 'boolean'))
    if manifest['provider'] == 'daytona':
        target = manifest.get('target', {})
        sensitive_inputs = any(spec.get('secret', True) is not False for spec in manifest.get('variables', {}).values())
        authenticated_checks = any(check.get('auth_variable') for check in manifest.get('checks', []))
        if format != 'manifest' and 'transport' not in target and (sensitive_inputs or authenticated_checks):
            missing.append(_question('target.transport',
                                     'Explicitly choose a Daytona transport: CLI reuses existing login but cannot safely deliver secrets or authenticated probe credentials; SDK requires DAYTONA_API_KEY and supports these inputs. No automatic fallback.',
                                     'choice', ['cli', 'sdk']))
        if 'mode' not in target:
            missing.append(_question('target.mode', 'Explicitly choose existing or create sandbox behavior', 'choice', ['create', 'existing']))
        if manifest.get('build') and not manifest['build'].get('snapshot_name'):
            missing.append(_question('snapshot_name', 'Choose a new snapshot name for the preserved Dockerfile build'))
        if target.get('mode') == 'create' and not manifest.get('build') and not target.get('snapshot'):
            missing.append(_question('snapshot', 'Select a reviewed existing snapshot'))
    if manifest['provider'] == 'modal' and format != 'manifest' and 'mode' not in manifest.get('target', {}):
        missing.append(_question('target.mode', 'Explicitly choose existing or create application behavior', 'choice', ['existing', 'create']))
    if manifest['provider'] == 'modal' and manifest.get('source', {}).get('trust_code') is not True and not any(item['field'] == 'trust_code' for item in missing):
        missing.append(_question('trust_code', 'Confirm the original Modal Python entrypoint is trusted', 'boolean'))
    return missing


def _check_source(value):
    root = _path(value['root'])
    if str(root) != value['root'] or value['source']['root'] != value['root']:
        _error('invalid_config', 'Artifact root binding is inconsistent')
    if inventory(root) != value['source']:
        _error('stale_plan', 'Source changed since discovery; rediscover and resolve before planning')
    return root


def verify_discovery(value, *, check_source=True):
    _validate(value, discovery_schema(), 'Discovery')
    if value['id'] != _id(value):
        _error('stale_plan', 'Discovery digest does not match its contents')
    root = _path(value['root'])
    selected = _path(value['selected_path'], root)
    if value['source']['bundle_sha256'] != digest(value['source']['files']):
        _error('stale_plan', 'Source inventory digest is inconsistent')
    for candidate in value['candidates']:
        _path(candidate['root'], root)
        if candidate['path'] != '.':
            _relative(root, candidate['path'])
        if candidate['id'] != digest({'path': candidate['path'], 'format': candidate['format'], 'service': candidate['service']}):
            _error('invalid_config', 'Candidate identity is inconsistent')
    if check_source:
        _check_source(value)
        # Re-run the passive parser, not just a caller-editable digest: saved
        # artifacts are data, never trusted permission to invent source semantics.
        fresh = discover(selected, value['provider'])
        if fresh != value:
            _error('stale_plan', 'Discovery no longer matches the current importer and selected inputs')
    return value


def _merge(manifest, answers, provenance, candidate):
    conflicts = []
    for field in ('name', 'profile', 'target', 'runtime', 'resources', 'ingress', 'variables', 'dependencies', 'storage', 'checks', 'rollback'):
        if field not in answers:
            continue
        value = answers[field]
        if isinstance(value, dict):
            for key, item in value.items():
                old = manifest.get(field, {}).get(key)
                # Imported inferred profile can be selected differently. All
                # explicit declarations require source correction, not masking.
                if old is not None and old != item:
                    conflicts.append(_question(field + '.' + key, 'Answer conflicts with an explicit imported setting; correct the source rather than silently replacing it'))
                else:
                    manifest.setdefault(field, {})[key] = copy.deepcopy(item)
        elif field in manifest and manifest[field] != value and not (field == 'profile' and candidate['format'] in {'dockerfile', 'compose', 'railway', 'snapshot'}):
            conflicts.append(_question(field, 'Answer conflicts with an explicit imported setting'))
        else:
            manifest[field] = copy.deepcopy(value)
    if 'trust_code' in answers:
        manifest['source']['trust_code'] = answers['trust_code']
    if 'snapshot' in answers:
        if manifest.get('target', {}).get('snapshot') not in (None, answers['snapshot']):
            conflicts.append(_question('snapshot', 'Snapshot answer conflicts with the imported target'))
        else:
            manifest.setdefault('target', {})['snapshot'] = answers['snapshot']
    if 'snapshot_name' in answers:
        if not manifest.get('build'):
            conflicts.append(_question('snapshot_name', 'A build snapshot name requires a Dockerfile-backed candidate'))
        elif manifest['build'].get('snapshot_name') not in (None, answers['snapshot_name']):
            conflicts.append(_question('snapshot_name', 'Snapshot build name conflicts with the imported setting'))
        else:
            manifest['build']['snapshot_name'] = answers['snapshot_name']
    for field in _leaf_fields(answers):
        provenance.append({'field': field, 'path': '', 'location': 'answers.' + field, 'importer': 'resolver', 'version': '1', 'source_digest': '', 'mapping': 'Explicit typed answer; runtime values are excluded', 'origin': 'user-resolved', 'ambiguity': ''})
    return conflicts


def _requirements(candidate, manifest, answers):
    missing, conflicts = [], []
    for question in candidate['missing']:
        field = question['field']
        parts = field.split('.')
        value = answers
        for part in parts:
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            missing.append(question)
            continue
        choices = question.get('choices', [])
        if field in ('runtime.port', 'target.function') and choices and value not in choices:
            conflicts.append(_question(field, 'Selected value conflicts with the static source candidates', question['type'], choices))
        if field == 'dependencies':
            if not isinstance(value, list) or {item['name'] for item in value} != set(choices):
                conflicts.append(_question(field, 'Every declared Compose dependency must be mapped exactly once', 'array'))
        if field == 'storage':
            mounts = []
            for entry in choices:
                name, path, *mode = entry.split(':')
                mounts.append((name, path, 'read-only' if mode == ['ro'] else 'read-write'))
            actual = [(item['name'], item['mount_path'], item['access']) for item in value]
            if sorted(actual) != sorted(mounts):
                conflicts.append(_question(field, 'Every Compose named volume must retain its declared name, mount path and access', 'array'))
    return missing, conflicts


def inspect_resolution(discovery, answers=None):
    if not isinstance(discovery, dict):
        discovery = read_discovery(discovery)
    else:
        verify_discovery(discovery)
    answers = {} if answers is None else answers
    _validate(answers, answers_schema(), 'Resolution answers')
    if _unsafe_literals({'variables': answers.get('variables', {}), 'runtime': answers.get('runtime', {}), 'target': answers.get('target', {})}):
        _error('invalid_config', 'Answers must contain typed settings and environment references, never credential values')
    candidates = discovery['candidates']
    candidate_id = answers.get('candidate')
    selected = [item for item in candidates if item['id'] == candidate_id] if candidate_id else candidates
    if len(selected) != 1:
        issue = _question('candidate', 'Select exactly one discovered input/service candidate', 'choice', [item['id'] for item in candidates])
        return {'status': 'conflict' if candidates else 'unsupported_capability', 'missing': [], 'conflicts': [issue] if candidates else [], 'unsupported': discovery['unsupported'] if not candidates else []}
    candidate = selected[0]
    manifest = copy.deepcopy(candidate['manifest'])
    provenance = copy.deepcopy(candidate['provenance'])
    conflicts = copy.deepcopy(candidate['conflicts'])
    unsupported = copy.deepcopy(candidate['unsupported'])
    conflicts.extend(_merge(manifest, answers, provenance, candidate))
    missing, requirement_conflicts = _requirements(candidate, manifest, answers)
    conflicts.extend(requirement_conflicts)
    missing.extend(_required(manifest, candidate['format']))
    missing = _unique(missing)
    if unsupported or conflicts or missing:
        return {'status': _status(missing, conflicts, unsupported), 'missing': missing, 'conflicts': conflicts, 'unsupported': unsupported}
    validate_manifest(manifest)
    if _unsafe_literals(manifest):
        _error('invalid_config', 'Resolved profile contains unsupported literal credentials')
    try:
        get_adapter(manifest['provider'], manifest).validate(manifest)
    except OnedeployError as error:
        return {'status': 'unsupported_capability', 'missing': [], 'conflicts': [], 'unsupported': [_question('capability', error.message)]}
    profile = {'version': VERSION, 'kind': 'profile', 'manifest': manifest, 'root': candidate['root'], 'source': copy.deepcopy(discovery['source']), 'discovery_id': discovery['id'], 'discovery': copy.deepcopy(discovery), 'resolution': {'answers': copy.deepcopy(answers), 'answers_digest': digest(answers), 'candidate': candidate['id'], 'normalizer_version': '1'}, 'provenance': provenance, 'warnings': list(dict.fromkeys(discovery['warnings'] + candidate['warnings'] + ['Profile contains operational identifiers, not credentials. Local source binding is not a portable deployment authorization.']))}
    profile['id'] = _id(profile)
    _validate(profile, profile_schema(), 'Profile')
    return {'status': 'ready', 'missing': [], 'conflicts': [], 'unsupported': [], 'profile': profile}


def resolve(discovery, answers=None):
    result = inspect_resolution(discovery, answers)
    if result['status'] != 'ready':
        _error(result['status'], {'input_required': 'Explicit input is required before creating a profile', 'conflict': 'Conflicting or ambiguous inputs require resolution', 'unsupported_capability': 'The selected import or provider capability is unsupported'}[result['status']], result)
    return result['profile']


def validate_profile(profile):
    if not isinstance(profile, dict):
        profile = _read_artifact(profile)
    _validate(profile, profile_schema(), 'Profile')
    if profile['id'] != _id(profile) or profile['discovery_id'] != profile['discovery']['id'] or profile['source'] != profile['discovery']['source'] or profile['resolution']['answers_digest'] != digest(profile['resolution']['answers']):
        _error('stale_plan', 'Profile binding or resolution digest is inconsistent')
    rebuilt = resolve(profile['discovery'], profile['resolution']['answers'])
    if rebuilt != profile:
        _error('stale_plan', 'Profile differs from deterministic source-bound resolution')
    root = _path(profile['root'], Path(profile['discovery']['root']))
    _check_build_paths({'root': str(root), 'manifest': profile['manifest']})
    return copy.deepcopy(profile['manifest']), root


def _read_artifact(path):
    p = _path(path)
    if os.name == 'posix' and p.stat().st_mode & 0o077:
        _error('invalid_config', 'Saved input artifacts must have private permissions (chmod 600)')
    try:
        return _json(_read_bytes(p, MAX_ARTIFACT).decode('utf-8'))
    except UnicodeError:
        _error('invalid_config', 'Saved artifact must be UTF-8 JSON')


def read_discovery(path):
    return verify_discovery(_read_artifact(path))


def read_profile(path):
    profile = _read_artifact(path)
    validate_profile(profile)
    return profile


def read_answers(path):
    return _validate(_read_artifact(path), answers_schema(), 'Resolution answers')


def _save(value, path):
    output = _path(path, exists=False)
    root = Path(value['source']['root'])
    if output.is_relative_to(root) and '.onedeploy' not in output.relative_to(root).parts:
        _error('invalid_config', 'Save artifacts outside project source or inside .onedeploy to avoid changing the bound source')
    data = canonical(value) + b'\n'
    if len(data) > MAX_ARTIFACT:
        _error('invalid_config', 'Artifact exceeds size limits')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = _path(output, exists=False)
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        _error('conflict', 'Artifact output already exists; choose a new path, originals are never overwritten')
    except OSError:
        _error('invalid_config', 'Artifact output could not be created safely')
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        try:
            output.unlink()
        except OSError:
            pass
        _error('invalid_config', 'Artifact save did not complete')
    return {'id': value['id'], 'path': str(output)}


def save_discovery(discovery, path):
    verify_discovery(discovery)
    return _save(discovery, path)


def save_profile(profile, path):
    validate_profile(profile)
    return _save(profile, path)


# Portable intent deliberately has no source inventory, digest, root, credentials,
# provider identity receipt, or approval. It is not executable or a saved profile.
def _portable_relative_schema():
    return {'type': 'string', 'minLength': 1, 'maxLength': 1024,
            'pattern': r'^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9_. /-]+$'}


def _portable_variable_schema():
    spec = copy.deepcopy(answers_schema()['properties']['variables'])
    spec['additionalProperties']['properties'].pop('required_if', None)
    return spec


def _portable_storage_schema():
    spec = copy.deepcopy(manifest_schema()['properties']['storage'])
    spec['items']['properties'].pop('resource_id')
    spec['items']['required'].remove('resource_id')
    return spec


def _portable_manifest_schema():
    spec = copy.deepcopy(_partial_schema())
    props = spec['properties']
    props.pop('target')
    props['source'] = _obj({'entrypoint': _portable_relative_schema()})
    props['build'] = _obj({'context': _portable_relative_schema(), 'dockerfile': _portable_relative_schema()})
    props['runtime']['properties'].pop('command')
    props['runtime']['properties'].pop('ready_url')
    props['variables'] = _portable_variable_schema()
    props['storage'] = _portable_storage_schema()
    return spec


def _portable_settings_schema():
    spec = copy.deepcopy(answers_schema())
    props = spec['properties']
    for key in ('candidate', 'trust_code', 'target', 'snapshot', 'snapshot_name', 'storage'):
        props.pop(key)
    props['runtime']['properties'].pop('command')
    props['runtime']['properties'].pop('ready_url')
    props['variables'] = _portable_variable_schema()
    return spec


def _portable_question_schema():
    spec = _issue_schema()
    spec['properties']['field'] = {'enum': ['runtime.port', 'dependencies', 'storage', 'snapshot', 'target.function']}
    spec['properties']['reason'] = {'enum': [
        'EXPOSE is dynamic or non-TCP; select the actual HTTP listener port',
        'EXPOSE is metadata, not proof of the listener; explicitly confirm a port',
        'Compose host publication is not imported; confirm the container listener port',
        'Map every selected-service dependency to an explicit external dependency with TLS/runtime references',
        'Map every named mount to an explicit existing provider storage resource without changing path/access',
        'Choose the ASGI function used for verification; original application is retained',
        'Select an existing reviewed snapshot; remote inspection must bind its immutable image before deployment'
    ]}
    return spec


def intent_schema():
    """Portable non-executable intent v1, not a local profile/plan schema."""
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **_obj({
        'version': {'type': 'integer', 'const': VERSION}, 'kind': {'const': 'intent'},
        'provider': {'enum': list(PROVIDERS)},
        'selection': _obj({'format': {'enum': ['manifest', 'dockerfile', 'compose', 'railway', 'modal', 'snapshot']},
                           'path': _portable_relative_schema(), 'source_path': _portable_relative_schema(),
                           'service': {'type': ['string', 'null'], 'pattern': SAFE_ID.pattern}},
                          ['format', 'path', 'source_path', 'service']),
        'expected': _obj({'manifest': _portable_manifest_schema(),
                          'questions': {'type': 'array', 'items': _portable_question_schema(), 'maxItems': 512}},
                         ['manifest', 'questions']),
        'settings': _portable_settings_schema(),
        'requirements': _obj({'target_fields': {'type': 'array', 'uniqueItems': True, 'maxItems': 11,
                                               'items': {'enum': ['account', 'project', 'environment', 'service', 'sandbox', 'snapshot', 'region', 'app', 'function', 'mode', 'transport']}},
                              'transport': {'enum': ['cli', 'sdk']},
                              'storage': _portable_storage_schema(),
                              'snapshot': {'enum': ['none', 'existing', 'build']},
                              'ready_url': {'type': 'boolean'}},
                             ['target_fields', 'storage', 'snapshot', 'ready_url']),
        'warnings': {'type': 'array', 'items': {'enum': [
            'Portable intent is non-executable and contains no source identity or approval.',
            'Review the newly discovered code and explicitly confirm the new provider destination, storage and runtime secret sources.',
            'Only transported static settings are compared; changed application code requires fresh review and a newly approved plan.'
        ]}, 'minItems': 3, 'maxItems': 3, 'uniqueItems': True}
    }, ['version', 'kind', 'provider', 'selection', 'expected', 'settings', 'requirements', 'warnings'])}


def _portable_manifest(manifest):
    """Allowlist, rather than heuristic redaction, for transported settings."""
    allowed = _portable_manifest_schema()['properties']
    result = {key: copy.deepcopy(value) for key, value in manifest.items() if key in allowed}
    result['source'] = {key: value for key, value in result.get('source', {}).items() if key == 'entrypoint'}
    if 'build' in result:
        result['build'] = {key: value for key, value in result['build'].items() if key in {'context', 'dockerfile'}}
    if 'runtime' in result:
        result['runtime'] = {key: value for key, value in result['runtime'].items() if key not in {'command', 'ready_url'}}
    if 'variables' in result:
        result['variables'] = {name: {key: value for key, value in spec.items() if key in {'type', 'secret', 'required', 'source'}} for name, spec in result['variables'].items()}
    if 'storage' in result:
        result['storage'] = [{key: value for key, value in item.items() if key != 'resource_id'} for item in result['storage']]
    return result


def _portable_expected(candidate):
    return {'manifest': _portable_manifest(candidate['manifest']),
            'questions': copy.deepcopy(candidate['missing'])}


def _portable_settings(answers):
    allowed = _portable_settings_schema()['properties']
    result = {key: copy.deepcopy(value) for key, value in answers.items() if key in allowed}
    if 'runtime' in result:
        result['runtime'] = {key: value for key, value in result['runtime'].items() if key not in {'command', 'ready_url'}}
    if 'variables' in result:
        result['variables'] = {name: {key: value for key, value in spec.items() if key in {'type', 'secret', 'required', 'source'}} for name, spec in result['variables'].items()}
    return result


def _validate_intent(intent):
    _validate(intent, intent_schema(), 'Portable intent')
    for field in ('path', 'source_path'):
        value = intent['selection'][field]
        if Path(value).is_absolute() or '..' in Path(value).parts or '\\' in value:
            _error('invalid_config', 'Portable selections must use safe relative paths')
    for section, keys in (('source', ('entrypoint',)), ('build', ('context', 'dockerfile'))):
        for field in keys:
            value = intent['expected']['manifest'].get(section, {}).get(field)
            if value is not None and (Path(value).is_absolute() or '..' in Path(value).parts or '\\' in value):
                _error('invalid_config', 'Portable source/build settings must use safe relative paths')
    for question in intent['expected']['questions']:
        field = question['field']
        for choice in question.get('choices', []):
            if field == 'runtime.port':
                valid = type(choice) is int and 1 <= choice <= 65535
            elif field == 'storage':
                valid = isinstance(choice, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*:/[A-Za-z0-9_/-]+(?::(?:ro|rw))?', choice)
            elif field in {'dependencies', 'target.function'}:
                valid = isinstance(choice, str) and SAFE_ID.fullmatch(choice)
            else:
                valid = False
            if not valid:
                _error('invalid_config', 'Portable question choices must be supported static references')
    if intent['expected']['manifest'].get('provider', intent['provider']) != intent['provider']:
        _error('invalid_config', 'Portable intent provider settings conflict')
    if _unsafe_literals(intent['expected']['manifest']) or _unsafe_literals(intent['settings']):
        _error('invalid_config', 'Portable intent must not contain literal credentials')
    return intent


def export_intent(profile):
    """Extract reusable settings only; never export local source or approval."""
    if not isinstance(profile, dict):
        profile = read_profile(profile)
    else:
        validate_profile(profile)
    original_answers = profile['resolution']['answers']
    if any('required_if' in spec for spec in original_answers.get('variables', {}).values()):
        _error('unsupported_capability', 'Answer-defined conditional variable behavior cannot be exported without values; declare it in reviewed source and rediscover')
    if original_answers.get('runtime', {}).get('command'):
        _error('unsupported_capability', 'Answer-defined startup commands cannot be transported; retain reviewed startup behavior in source')
    candidate = next(item for item in profile['discovery']['candidates'] if item['id'] == profile['resolution']['candidate'])
    manifest = profile['manifest']
    source_path = Path(candidate['root']).relative_to(Path(profile['discovery']['root'])).as_posix()
    result = {'version': VERSION, 'kind': 'intent', 'provider': manifest['provider'],
              'selection': {'format': candidate['format'], 'path': candidate['path'], 'service': candidate['service'], 'source_path': source_path},
              'expected': _portable_expected(candidate),
              'settings': _portable_settings(profile['resolution']['answers']),
              'requirements': {'target_fields': sorted(manifest['target']),
                               'storage': [{key: copy.deepcopy(value) for key, value in volume.items() if key != 'resource_id'} for volume in manifest.get('storage', [])],
                               'snapshot': 'build' if manifest.get('build', {}).get('snapshot_name') else 'existing' if manifest.get('target', {}).get('snapshot') else 'none',
                               'ready_url': bool(manifest.get('runtime', {}).get('ready_url'))},
              'warnings': ['Portable intent is non-executable and contains no source identity or approval.',
                           'Review the newly discovered code and explicitly confirm the new provider destination, storage and runtime secret sources.',
                           'Only transported static settings are compared; changed application code requires fresh review and a newly approved plan.']}
    if manifest['provider'] == 'daytona':
        result['requirements']['transport'] = manifest['target'].get('transport', 'cli')
        result['requirements']['target_fields'] = sorted(set(result['requirements']['target_fields']) | {'transport'})
    return _validate_intent(result)


def read_intent(path):
    return _validate_intent(_read_artifact(path))


def save_intent(intent, path, *, source_root=None):
    """Private exclusive write. Intent is inert but may contain variable names."""
    _validate_intent(intent)
    output = _path(path, exists=False)
    if source_root is not None:
        root = _path(source_root)
        if not root.is_dir():
            _error('invalid_config', 'Intent export source must be a project directory')
        if output.is_relative_to(root) and '.onedeploy' not in output.relative_to(root).parts:
            _error('invalid_config', 'Export intent outside source or inside .onedeploy to preserve the current profile binding')
    data = canonical(intent) + b'\n'
    if len(data) > MAX_INPUT:
        _error('invalid_config', 'Portable intent exceeds the input size limit')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = _path(output, exists=False)
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        _error('conflict', 'Intent output already exists; originals are never overwritten')
    except OSError:
        _error('invalid_config', 'Intent output could not be created safely')
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except OSError:
        try:
            output.unlink()
        except OSError:
            pass
        _error('invalid_config', 'Intent save did not complete')
    return {'path': str(output), 'kind': 'intent', 'version': VERSION}


def _intent_inputs(intent, discovery):
    intent = _validate_intent(intent) if isinstance(intent, dict) else read_intent(intent)
    if isinstance(discovery, dict):
        verify_discovery(discovery)
    else:
        path = _path(discovery)
        # Public inspect/import helpers accept a fresh project/config path, not
        # an ambiguous JSON file that might itself be an executable profile.
        discovery = discover(path, intent['provider'])
    return intent, discovery


def inspect_intent_resolution(intent, discovery, answers=None):
    """Fresh source and explicit fresh target/trust are mandatory on every call."""
    intent, discovery = _intent_inputs(intent, discovery)
    answers = {} if answers is None else answers
    _validate(answers, answers_schema(), 'Fresh import answers')
    if discovery['provider'] != intent['provider']:
        return {'status': 'conflict', 'missing': [], 'conflicts': [_question('provider', 'Intent and freshly discovered provider differ; rediscover with the intended provider')], 'unsupported': []}
    selector = intent['selection']
    candidates = [candidate for candidate in discovery['candidates']
                  if candidate['format'] == selector['format'] and candidate['path'] == selector['path'] and candidate['service'] == selector['service']
                  and Path(candidate['root']).relative_to(Path(discovery['root'])).as_posix() == selector['source_path']]
    if len(candidates) != 1:
        return {'status': 'conflict', 'missing': [], 'conflicts': [_question('candidate', 'The fresh project does not contain the unique relative input/service selected by this intent')], 'unsupported': []}
    candidate = candidates[0]
    if candidate['unsupported']:
        return {'status': 'unsupported_capability', 'missing': [], 'conflicts': [], 'unsupported': copy.deepcopy(candidate['unsupported'])}
    if _portable_expected(candidate) != intent['expected']:
        return {'status': 'conflict', 'missing': [], 'conflicts': [_question('source', 'Transported static configuration differs from the fresh source; rediscover and review without reusing this intent')], 'unsupported': []}
    merged = copy.deepcopy(intent['settings'])
    for key, value in answers.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(copy.deepcopy(value))
        else:
            merged[key] = copy.deepcopy(value)
    merged['candidate'] = candidate['id']
    missing, conflicts = [], []
    if answers.get('trust_code') is not True:
        missing.append(_question('trust_code', 'Review and explicitly trust this destination project; portable intent never carries source approval', 'boolean'))
    required_fields = set(intent['requirements']['target_fields']) | get_adapter(intent['provider'], {'target': merged.get('target', {})}).required_fields | set(candidate['manifest'].get('target', {}))
    if intent['provider'] in {'modal', 'daytona'}:
        required_fields.add('mode')
    if intent['provider'] == 'daytona':
        required_fields.add('transport')
    for field in sorted(required_fields):
        if field == 'snapshot' and 'snapshot' in answers:
            continue
        if field not in answers.get('target', {}):
            choices = ['existing', 'create'] if field == 'mode' else ['cli', 'sdk'] if field == 'transport' else None
            if field == 'transport':
                previous = intent['requirements'].get('transport', candidate['manifest'].get('target', {}).get('transport', 'cli'))
                missing.append(_question('target.transport', 'Explicitly confirm the Daytona transport; previously selected ' + previous + '; no automatic backend switch', 'choice', choices))
                continue
            missing.append(_question('target.' + field, 'Explicitly confirm this fresh provider destination; no target identifier is transported', 'choice' if choices else 'string', choices))
    # Intent is editable configuration, not an authorization record. Never let
    # mutable requirement flags suppress bindings independently present in the
    # freshly parsed source. Transported constraints and source constraints must
    # agree before any profile can be produced.
    source_manifest = candidate['manifest']
    source_storage = [{key: copy.deepcopy(value) for key, value in volume.items() if key != 'resource_id'}
                      for volume in source_manifest.get('storage', [])]
    expected_storage = intent['requirements']['storage']
    if source_storage and source_storage != expected_storage:
        conflicts.append(_question('storage', 'Portable storage requirements contradict fresh source-owned mounts; regenerate the intent without removing required bindings', 'array'))
    if source_storage:
        expected_storage = source_storage
    if expected_storage:
        if 'storage' not in answers:
            choices = [item['name'] + ':' + item['mount_path'] + (':ro' if item['access'] == 'read-only' else ':rw') for item in expected_storage]
            missing.append(_question('storage', 'Select fresh provider storage resource IDs for every preserved mount', 'array', choices))
        elif [{key: value for key, value in item.items() if key != 'resource_id'} for item in answers['storage']] != expected_storage:
            conflicts.append(_question('storage', 'Imported storage must preserve its reviewed mount/access/writer settings; only resource IDs are selected afresh', 'array'))
    snapshot = intent['requirements']['snapshot']
    source_snapshot = ('build' if source_manifest.get('build', {}).get('snapshot_name')
                       else 'existing' if source_manifest.get('target', {}).get('snapshot') else 'none')
    if source_snapshot != 'none' and source_snapshot != snapshot:
        conflicts.append(_question('snapshot', 'Portable snapshot requirements contradict the fresh source; regenerate the intent without removing required bindings'))
    if source_snapshot != 'none':
        snapshot = source_snapshot
    if snapshot == 'build' and 'snapshot_name' not in answers:
        missing.append(_question('snapshot_name', 'Choose a fresh build snapshot name; intent never transports provider snapshot identifiers'))
    if snapshot == 'existing' and 'snapshot' not in answers and 'snapshot' not in answers.get('target', {}):
        missing.append(_question('snapshot', 'Choose a fresh reviewed snapshot; remote inspection must verify its immutable image'))
    source_ready_url = bool(source_manifest.get('runtime', {}).get('ready_url'))
    if source_ready_url and not intent['requirements']['ready_url']:
        conflicts.append(_question('runtime.ready_url', 'Portable readiness requirements contradict the fresh source; regenerate the intent without removing the origin binding'))
    if (intent['requirements']['ready_url'] or source_ready_url) and 'ready_url' not in answers.get('runtime', {}):
        missing.append(_question('runtime.ready_url', 'Select the fresh credential-free provider readiness origin'))
    if 'candidate' in answers and answers['candidate'] != candidate['id']:
        conflicts.append(_question('candidate', 'Fresh candidate answer does not match the portable selection'))
    if missing or conflicts:
        return {'status': _status(missing, conflicts, []), 'missing': _unique(missing), 'conflicts': conflicts, 'unsupported': []}
    return inspect_resolution(discovery, merged)


def resolve_intent(discovery, intent, answers=None):
    """Resolve an intent against an already freshly discovered project."""
    if not isinstance(discovery, dict):
        discovery = read_discovery(discovery)
    result = inspect_intent_resolution(intent, discovery, answers)
    if result['status'] != 'ready':
        _error(result['status'], 'Portable intent needs fresh source review and explicit destination resolution', result)
    return result['profile']


def import_intent(intent, path, answers=None):
    """Import inert intent at an explicitly selected destination project path."""
    result = inspect_intent_resolution(intent, path, answers)
    if result['status'] != 'ready':
        _error(result['status'], 'Portable intent needs fresh source review and explicit destination resolution', result)
    return result['profile']


def prepare_intent_import(intent, path):
    """UI preparation only. Continue through inspect_intent_resolution."""
    intent, discovery = _intent_inputs(intent, path)
    return {'discovery': discovery, 'answers': copy.deepcopy(intent['settings']),
            'resolution': inspect_intent_resolution(intent, discovery, {})}
