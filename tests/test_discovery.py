"""Independent synthetic importer fixtures; no application code is executed."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import yaml
import pytest
from jsonschema import Draft202012Validator
from onedeploy import discovery as d
from onedeploy.errors import OnedeployError


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'Dockerfile').write_text('FROM scratch\nEXPOSE 8000\nCMD ["/app"]\n')
    return root


def railway_answers(**extra):
    return {'name': 'example', 'target': {'account': 'owner', 'project': 'project', 'environment': 'staging', 'service': 'web'}, 'runtime': {'port': 8000}, 'trust_code': True, **extra}


def daytona_answers(**extra):
    return {'name': 'example', 'target': {'account': 'owner', 'sandbox': 'sandbox', 'mode': 'create'}, 'runtime': {'port': 8000}, 'trust_code': True, 'snapshot_name': 'reviewed-build', **extra}


def select(result, format, service=None):
    return next(item['id'] for item in result['candidates'] if item['format'] == format and item['service'] == service)


def write_compose(root, content):
    path = root / 'compose.yaml'
    path.write_text(content)
    return path


def test_passive_discovery_and_resolution(project, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('Discovery executed a process or used a network')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    before = {p.name: p.read_bytes() for p in project.iterdir()}
    found = d.discover(project, 'railway')
    profile = d.resolve(found, railway_answers())
    assert profile['manifest']['source'] == {'path': '.', 'trust_code': True}
    assert profile['manifest']['build'] == {'context': '.', 'dockerfile': 'Dockerfile'}
    assert d.validate_profile(profile) == (profile['manifest'], project)
    assert {p.name: p.read_bytes() for p in project.iterdir()} == before
    assert d.resolve(found, railway_answers()) == profile
    assert d.discover(project, 'railway') == found
    assert all({'field', 'path', 'location', 'importer', 'version', 'source_digest', 'mapping', 'origin', 'ambiguity'} == set(item) for item in profile['provenance'])


def test_schema_contracts(project):
    found = d.discover(project, 'railway')
    profile = d.resolve(found, railway_answers())
    for schema, value in ((d.discovery_schema(), found), (d.profile_schema(), profile), (d.answers_schema(), railway_answers()), (d.resolution_schema(), d.inspect_resolution(found, {}))):
        Draft202012Validator(schema).validate(value)
        value = copy.deepcopy(value)
        value['unexpected'] = 'not-allowed'
        assert not Draft202012Validator(schema).is_valid(value)


def test_missing_typed_no_default_target(project):
    found = d.discover(project, 'railway')
    assert found['status'] == 'input_required'
    fields = {item['field']: item for item in found['missing']}
    assert fields['runtime.port']['type'] == 'integer'
    assert fields['runtime.port']['choices'] == [8000]
    assert fields['trust_code']['type'] == 'boolean'
    with pytest.raises(OnedeployError) as error:
        d.resolve(found)
    assert error.value.code == 'input_required'
    assert error.value.result['missing']
    assert not error.value.mutation_possible


@pytest.mark.parametrize('field,value', [('runtime', {'port': '8000'}), ('trust_code', 'true'), ('variables', {'SECRET': {'default': 'synthetic-sensitive'}}), ('unknown', 'synthetic-sensitive'), ('target', {'account': 'user:synthetic-sensitive@host'})])
def test_answers_fail_closed_secret_safe(project, field, value):
    answers = railway_answers(**{field: value})
    with pytest.raises(OnedeployError) as error:
        d.resolve(d.discover(project, 'railway'), answers)
    assert 'synthetic-sensitive' not in str(error.value)


def test_secret_sources_not_read_or_echoed(project, monkeypatch):
    (project / '.env').write_text('PASSWORD=synthetic-sensitive')
    (project / 'private.key').write_text('synthetic-sensitive')
    (project / '.onedeploy').mkdir()
    (project / '.onedeploy' / 'private.json').write_text('synthetic-sensitive')
    original = d._read_bytes
    def guarded(path, *args, **kwargs):
        assert not str(path).endswith(('.env', 'private.key', 'private.json'))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(d, '_read_bytes', guarded)
    result = d.discover(project, 'railway')
    assert 'synthetic-sensitive' not in json.dumps(result)
    assert {item['path'] for item in result['source']['files']} == {'Dockerfile'}


def test_ambiguous_candidates_never_first(project):
    compose = write_compose(project, 'services:\n  web:\n    build: .\n  worker:\n    build: .\n')
    found = d.discover(project, 'railway')
    assert found['status'] == 'conflict'
    with pytest.raises(OnedeployError) as error:
        d.resolve(found, railway_answers())
    assert error.value.code == 'conflict'
    profile = d.resolve(found, railway_answers(candidate=select(found, 'compose', 'web')))
    assert profile['resolution']['candidate'] == select(found, 'compose', 'web')
    assert any('Only the explicitly selected service' in item for item in profile['warnings'])
    selected = d.discover(compose, 'railway')
    assert len(selected['candidates']) == 2


def test_single_compose_rebase_and_env_names(project):
    app = project / 'backend'; app.mkdir()
    (app / 'Dockerfile').write_text('FROM scratch\nEXPOSE 8000\n')
    path = write_compose(project, 'services:\n  web:\n    build:\n      context: ./backend\n      dockerfile: Dockerfile\n    environment:\n      API_TOKEN:\n      MODE:\n    ports: ["8080:8000"]\n')
    found = d.discover(path, 'railway')
    profile = d.resolve(found, railway_answers())
    assert profile['root'] == str(app)
    assert profile['manifest']['variables']['API_TOKEN'] == {'type': 'string', 'required': True, 'secret': True, 'source': 'env:API_TOKEN'}
    assert d.validate_profile(profile)[1] == app
    assert path.read_text().startswith('services:')


@pytest.mark.parametrize('addition', [
    '    image: example\n',
    '    command: ["echo", "synthetic-sensitive"]\n',
    '    privileged: true\n',
    '    network_mode: host\n',
    '    devices: ["/dev/kvm"]\n',
    '    env_file: .env\n',
    '    extends: elsewhere\n',
    '    labels: {secret: synthetic-sensitive}\n',
    '    environment: {TOKEN: synthetic-sensitive}\n',
    '    environment: ["TOKEN=synthetic-sensitive"]\n',
    '    environment: {TOKEN: "${TOKEN}"}\n',
    '    volumes: ["./private:/data"]\n',
    '    ports: ["127.0.0.1:8080:8000"]\n',
    '    ports: ["0.0.0.0:8080:8000"]\n',
    '    ports: ["8000/udp"]\n',
    '    depends_on: {db: {condition: service_healthy}}\n',
])
def test_compose_rejected_semantics_no_values(project, addition):
    path = write_compose(project, 'services:\n  web:\n    build: .\n' + addition)
    found = d.discover(path, 'railway')
    assert found['unsupported']
    assert 'synthetic-sensitive' not in json.dumps(found)
    with pytest.raises(OnedeployError) as error:
        d.resolve(found, railway_answers())
    assert error.value.code == 'unsupported_capability'


@pytest.mark.parametrize('content', ['services:\n  app:\n    image: busybox\n', 'services:\n  app:\n    build:\n      context: .\n      args: {PASSWORD: synthetic-sensitive}\n', 'include: elsewhere\nservices:\n  app:\n    build: .\n', 'services:\n  app:\n    build: .\nvolumes:\n  data:\n    external: true\n'])
def test_compose_additional_unsupported(project, content):
    found = d.discover(write_compose(project, content), 'railway')
    assert found['unsupported']
    assert 'synthetic-sensitive' not in json.dumps(found)


def test_compose_dependencies_and_storage_cannot_drop(project):
    path = write_compose(project, 'services:\n  web:\n    build: .\n    environment: [DB_URL]\n    depends_on: [db]\n    volumes: ["data:/data:rw"]\nvolumes:\n  data: {}\n')
    found = d.discover(path, 'railway')
    assert {q['field'] for q in found['missing']} >= {'dependencies', 'storage'}
    incomplete = d.inspect_resolution(found, railway_answers(dependencies=[], storage=[]))
    assert incomplete['status'] == 'conflict'
    complete = railway_answers(dependencies=[{'name': 'db', 'kind': 'postgres', 'variable': 'DB_URL', 'tls': True, 'required': True}], storage=[{'name': 'data', 'resource_id': 'volume-id', 'mount_path': '/data', 'kind': 'volume', 'access': 'read-write', 'single_writer': True, 'attachment': 'existing'}])
    assert d.resolve(found, complete)['manifest']['storage'][0]['resource_id'] == 'volume-id'
    complete['storage'][0]['mount_path'] = '/other'
    assert d.inspect_resolution(found, complete)['status'] == 'conflict'


def test_dockerfile_only_expose_in_compose_needs_confirmation(project):
    found = d.discover(write_compose(project, 'services:\n  web:\n    build: .\n'), 'railway')
    assert next(q for q in found['missing'] if q['field'] == 'runtime.port')['choices'] == [8000]


@pytest.mark.parametrize('suffix,content', [('.toml', '[build]\nbuilder = "DOCKERFILE"\ndockerfilePath = "Dockerfile"\n[deploy]\nhealthcheckPath = "/healthz"\nhealthcheckTimeout = 60\n'), ('.json', '{"build":{"builder":"DOCKERFILE","dockerfilePath":"Dockerfile"},"deploy":{"healthcheckPath":"/healthz","healthcheckTimeout":60}}')])
def test_railway_static_subset(project, suffix, content):
    path = project / ('railway' + suffix); path.write_text(content)
    found = d.discover(path, 'railway')
    profile = d.resolve(found, railway_answers())
    assert profile['manifest']['runtime'] == {'health_path': '/healthz', 'timeout': 60, 'port': 8000}
    assert any(row['location'] == 'deploy.healthcheckPath' for row in profile['provenance'])
    assert path.read_text() == content


@pytest.mark.parametrize('content', ['[build]\nbuilder="NIXPACKS"\n', '[deploy]\nstartCommand="synthetic-sensitive"\n', '[deploy]\nrestartPolicyType="NEVER"\n', '[deploy]\npreDeployCommand="synthetic-sensitive"\n', '[deploy]\nnumReplicas=2\n', '[env.production]\nx=1\n'])
def test_railway_unsupported_and_sibling_bypass(project, content):
    path = project / 'railway.toml'; path.write_text(content)
    found = d.discover(path, 'railway')
    assert found['unsupported']
    assert 'synthetic-sensitive' not in json.dumps(found)
    alternate = d.discover(project / 'Dockerfile', 'railway')
    assert alternate['unsupported']
    with pytest.raises(OnedeployError) as error:
        d.resolve(alternate, railway_answers())
    assert error.value.code == 'unsupported_capability'


def test_multiple_railway_files_and_provider_conflict(project):
    (project / 'railway.toml').write_text('[build]\nbuilder="DOCKERFILE"\n')
    (project / 'railway.json').write_text('{}')
    assert d.discover(project / 'railway.toml', 'railway')['unsupported']
    assert d.discover(project / 'railway.toml', 'daytona')['unsupported']


def test_native_manifest_source_rebase(project):
    app = project / 'app'; app.mkdir()
    (app / 'Dockerfile').write_text('FROM scratch\n')
    manifest = {'version': 1, 'name': 'native', 'provider': 'railway', 'profile': 'docker-http', 'source': {'path': 'app'}, 'target': railway_answers()['target']}
    path = project / 'arbitrary-name.yaml'; path.write_text(yaml.safe_dump(manifest))
    found = d.discover(path, 'railway')
    profile = d.resolve(found)
    assert profile['manifest']['source']['path'] == '.'
    assert d.validate_profile(profile)[1] == app
    assert yaml.safe_load(path.read_text()) == manifest
    assert d.inspect_resolution(found, {'target': {'service': 'other'}})['status'] == 'conflict'
    assert d.discover(path, 'daytona')['conflicts']


def test_native_unknown_secret_and_provider_capabilities(project):
    manifest = {'version': 1, 'name': 'native', 'provider': 'railway', 'profile': 'docker-http', 'source': {'path': '.'}, 'target': railway_answers()['target']}
    path = project / 'onedeploy.yaml'
    bad = {**manifest, 'unknown': 'synthetic-sensitive'}
    path.write_text(yaml.safe_dump(bad))
    with pytest.raises(OnedeployError) as error: d.discover(path, 'railway')
    assert 'synthetic-sensitive' not in str(error.value)
    manifest['variables'] = {'API_TOKEN': {'secret': False, 'default': 'synthetic-sensitive'}}
    path.write_text(yaml.safe_dump(manifest))
    found = d.discover(path, 'railway')
    assert found['unsupported'] and 'synthetic-sensitive' not in json.dumps(found)
    manifest.pop('variables'); manifest['resources'] = {'cpu': 1}
    path.write_text(yaml.safe_dump(manifest))
    assert d.inspect_resolution(d.discover(path, 'railway'))['status'] == 'unsupported_capability'


def test_daytona_build_and_snapshot(project, tmp_path):
    build = d.resolve(d.discover(project, 'daytona'), daytona_answers())
    assert build['manifest']['build']['snapshot_name'] == 'reviewed-build'
    empty = tmp_path / 'snapshot-project'; empty.mkdir()
    found = d.discover(empty, 'daytona')
    answers = daytona_answers(snapshot='reviewed-snapshot')
    answers.pop('snapshot_name')
    snapshot = d.resolve(found, answers)
    assert snapshot['manifest']['target']['snapshot'] == 'reviewed-snapshot'
    assert 'build' not in snapshot['manifest']
    assert any('immutable' in q['reason'] for q in found['missing'] if q['field'] == 'snapshot')
    invalid = daytona_answers(target={'account': 'owner', 'sandbox': 'sandbox', 'mode': 'existing'})
    assert d.inspect_resolution(d.discover(project, 'daytona'), invalid)['status'] == 'unsupported_capability'


def test_modal_actual_template_passive(tmp_path, monkeypatch):
    root = tmp_path / 'modal'; root.mkdir()
    source = Path(d.__file__).parent / 'templates' / 'asgi' / 'modal_app.py'
    path = root / 'modal_app.py'; path.write_bytes(source.read_bytes())
    def forbidden(*a, **kw): raise AssertionError('Must not execute Python')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    found = d.discover(path, 'modal')
    assert not found['unsupported']
    answers = {'name': 'example-asgi', 'target': {'account': 'owner', 'environment': 'staging', 'mode': 'existing'}, 'trust_code': True}
    profile = d.resolve(found, answers)
    assert profile['manifest']['target']['app'] == 'example-asgi'
    assert profile['manifest']['target']['function'] == 'web'
    assert profile['manifest']['source']['entrypoint'] == 'modal_app.py'
    assert d.validate_profile(profile)[1] == root
    assert path.read_bytes() == source.read_bytes()


@pytest.mark.parametrize('source', [
    'import os\nimport modal\napp=modal.App("x")\n',
    'import modal\napp=modal.App(get_name())\n',
    'import modal\napp=modal.App("x")\n@app.function()\n@modal.asgi_app()\ndef web(x: __import__("os").system("echo synthetic-sensitive")):\n return None\n',
    'import modal\napp=modal.App("x")\n@app.function(timeout=get_timeout())\n@modal.asgi_app()\ndef web():\n return None\n',
    'import modal\napp=modal.App("x")\n@app.function(secrets=[modal.Secret.from_dict({"TOKEN":"synthetic-sensitive"})])\n@modal.asgi_app()\ndef web():\n return None\n',
    'import modal\napp=modal.App("x")\nopen("executed", "w").write("bad")\n',
    'import modal\napp=modal.App("x")\n@app.function()\n@modal.asgi_app()\ndef web() -> run():\n return None\n',
])
def test_dynamic_modal_rejected_without_execution(tmp_path, source):
    path = tmp_path / 'entry.py'; path.write_text(source)
    found = d.discover(path, 'modal')
    assert found['unsupported']
    assert 'synthetic-sensitive' not in json.dumps(found)
    assert not (tmp_path / 'executed').exists()


def test_modal_multiple_functions_need_selection(tmp_path):
    body = 'import modal\napp = modal.App("app")\n'
    for name in ('one', 'two'):
        body += '@app.function()\n@modal.asgi_app(requires_proxy_auth=True)\ndef ' + name + '():\n return None\n'
    path = tmp_path / 'entry.py'; path.write_text(body)
    found = d.discover(path, 'modal')
    answers = {'name': 'app', 'target': {'account': 'owner', 'environment': 'test', 'mode': 'existing'}, 'trust_code': True}
    assert any(q['field'] == 'target.function' for q in d.inspect_resolution(found, answers)['missing'])
    answers['target']['function'] = 'two'
    assert d.resolve(found, answers)['manifest']['target']['function'] == 'two'


@pytest.mark.parametrize('name,content', [('deployment.yaml', 'apiVersion: apps/v1\nkind: Deployment\n'), ('Chart.yaml', 'apiVersion: v2\nname: example\n'), ('workflow.yml', 'jobs: {}\n'), ('app.conf', 'arbitrary data'), ('bad.yaml', '!!python/object/apply:os.system ["echo synthetic-sensitive"]'), ('bad.json', '{"x":1,"x":2}')])
def test_unknown_inputs_and_unsafe_yaml(project, name, content):
    path = project / name; path.write_text(content)
    try:
        found = d.discover(path, 'railway')
        assert found['unsupported']
    except OnedeployError as error:
        assert error.code == 'invalid_config'
        assert 'synthetic-sensitive' not in str(error)


def test_duplicate_alias_and_nested_yaml(project):
    for content in ('services:\n  app:\n    build: .\n    build: other\n', 'services: &x {app: {build: .}}\nother: *x\n', 'a: ' + '[' * 30 + '1' + ']' * 30):
        path = write_compose(project, content)
        with pytest.raises(OnedeployError): d.discover(path, 'railway')


@pytest.mark.parametrize('path_value', ['..', '../outside', '/tmp', '${CONTEXT}', 'https://example.invalid/repo', './link'])
def test_unsafe_compose_paths(project, tmp_path, path_value):
    (project / 'link').symlink_to(tmp_path, target_is_directory=True)
    path = write_compose(project, 'services:\n  app:\n    build: ' + path_value + '\n')
    with pytest.raises(OnedeployError): d.discover(path, 'railway')


def test_size_depth_file_and_input_bounds(project, monkeypatch):
    monkeypatch.setattr(d, 'MAX_FILES', 1)
    (project / 'extra').write_text('x')
    with pytest.raises(OnedeployError): d.discover(project, 'railway')
    monkeypatch.setattr(d, 'MAX_FILES', 10000)
    monkeypatch.setattr(d, 'MAX_BYTES', 4)
    with pytest.raises(OnedeployError): d.discover(project, 'railway')


def test_source_changes_and_new_files_invalidate(project):
    found = d.discover(project, 'railway')
    profile = d.resolve(found, railway_answers())
    (project / 'new.py').write_text('print("different")')
    for action in (lambda: d.resolve(found, railway_answers()), lambda: d.validate_profile(profile)):
        with pytest.raises(OnedeployError) as error: action()
        assert error.value.code == 'stale_plan'


def test_recomputed_digest_cannot_authorize_changed_candidate(project):
    found = d.discover(project, 'railway')
    found['candidates'][0]['manifest']['runtime'] = {'port': 6666}
    found['id'] = d._id(found)
    with pytest.raises(OnedeployError) as error: d.resolve(found, railway_answers())
    assert error.value.code == 'stale_plan'
    profile = d.resolve(d.discover(project, 'railway'), railway_answers())
    profile['manifest']['target']['account'] = 'different'
    profile['id'] = d._id(profile)
    with pytest.raises(OnedeployError) as error: d.validate_profile(profile)
    assert error.value.code == 'stale_plan'


def test_private_exclusive_artifact_roundtrip(project, tmp_path):
    found = d.discover(project, 'railway')
    profile = d.resolve(found, railway_answers())
    discovery_path = project / '.onedeploy' / 'discovery.json'
    profile_path = tmp_path / 'profile.json'
    d.save_discovery(found, discovery_path)
    d.save_profile(profile, profile_path)
    assert discovery_path.stat().st_mode & 0o777 == 0o600
    assert profile_path.stat().st_mode & 0o777 == 0o600
    assert d.read_discovery(discovery_path) == found
    assert d.read_profile(profile_path) == profile
    with pytest.raises(OnedeployError) as error: d.save_profile(profile, profile_path)
    assert error.value.code == 'conflict'
    with pytest.raises(OnedeployError): d.save_profile(profile, project / 'profile.json')
    assert not (project / 'profile.json').exists()
    profile_path.chmod(0o644)
    with pytest.raises(OnedeployError): d.read_profile(profile_path)


def test_artifact_input_unknown_fields_and_secret_answers(tmp_path):
    path = tmp_path / 'answers.json'
    for raw in ('{"variables":{"SECRET":{"value":"synthetic-sensitive"}}}', '{"name":"one","name":"two"}', '{"runtime":{"port":NaN}}', '{"target":' + '[' * 30 + '1' + ']' * 30 + '}'):
        path.write_text(raw); path.chmod(0o600)
        with pytest.raises(OnedeployError) as error: d.read_answers(path)
        assert 'synthetic-sensitive' not in str(error.value)


def test_save_symlink_and_input_source_symlink_rejected(project, tmp_path):
    found = d.discover(project, 'railway')
    outside = tmp_path / 'outside.json'; outside.write_text('original')
    link = tmp_path / 'link.json'; link.symlink_to(outside)
    with pytest.raises(OnedeployError): d.save_discovery(found, link)
    assert outside.read_text() == 'original'
    (project / 'unsafe').symlink_to(outside)
    with pytest.raises(OnedeployError): d.discover(project, 'railway')


def test_docker_secret_defaults_are_not_echoed(project):
    (project / 'Dockerfile').write_text('FROM scratch\nENV API_TOKEN=synthetic-sensitive\n')
    found = d.discover(project, 'railway')
    assert found['unsupported']
    assert 'synthetic-sensitive' not in json.dumps(found)


def test_imported_modal_requires_explicit_mode(tmp_path):
    path = tmp_path / 'app.py'
    path.write_text('import modal\napp=modal.App("app")\n@app.function()\n@modal.asgi_app(requires_proxy_auth=True)\ndef web():\n return None\n')
    result = d.inspect_resolution(d.discover(path, 'modal'), {'name': 'app', 'trust_code': True, 'target': {'account': 'owner', 'environment': 'test'}})
    assert result['status'] == 'input_required'
    assert next(item for item in result['missing'] if item['field'] == 'target.mode')['choices'] == ['existing', 'create']


def test_modal_static_package_chain_preserved(tmp_path):
    path = tmp_path / 'app.py'
    source = ('import modal\napp=modal.App("app")\n'
              'image=modal.Image.debian_slim(python_version="3.12").pip_install("starlette==0.46.2").apt_install("ffmpeg")\n'
              '@app.function(image=image, cpu=1.0, memory=512, timeout=60, min_containers=0, max_containers=1, scaledown_window=30)\n'
              '@modal.asgi_app(requires_proxy_auth=True)\ndef web():\n return None\n')
    path.write_text(source)
    found = d.discover(path, 'modal')
    assert not found['unsupported']
    profile = d.resolve(found, {'name': 'app', 'trust_code': True, 'target': {'account': 'owner', 'environment': 'test', 'mode': 'existing'}})
    assert 'resources' not in profile['manifest'] and 'build' not in profile['manifest']
    assert any(row['field'] == 'source.image' for row in profile['provenance'])
    assert path.read_text() == source


def test_explicit_discovery_provider_switch_invalidates_binding(project):
    first = d.discover(project, 'railway')
    second = d.discover(project, 'daytona')
    assert first['id'] != second['id']
    profile = d.resolve(first, railway_answers())
    profile['manifest']['provider'] = 'daytona'
    profile['id'] = d._id(profile)
    with pytest.raises(OnedeployError):
        d.validate_profile(profile)


def test_json_duplicate_railway_key_rejected(project):
    path = project / 'railway.json'
    path.write_text('{"build":{"builder":"DOCKERFILE","builder":"synthetic-sensitive"}}')
    with pytest.raises(OnedeployError) as error: d.discover(path, 'railway')
    assert 'synthetic-sensitive' not in str(error.value)


def test_nested_time_and_ast_bounds(project, monkeypatch):
    monkeypatch.setattr(d, 'MAX_DEPTH', 1)
    nested = project / 'one' / 'two'; nested.mkdir(parents=True)
    (nested / 'source').write_text('x')
    with pytest.raises(OnedeployError): d.discover(project, 'railway')
    monkeypatch.setattr(d, 'MAX_DEPTH', 24)
    counter = iter([0, 0, 999, 999])
    monkeypatch.setattr(d.time, 'monotonic', lambda: next(counter, 999))
    with pytest.raises(OnedeployError): d.inventory(project)
