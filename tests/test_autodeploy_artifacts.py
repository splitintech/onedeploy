import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_sources', ROOT / 'deploy/build_sources.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_locked_inventory_and_hashes(tmp_path):
    module.inventory(ROOT, tmp_path)
    sbom = json.loads((tmp_path / 'sbom.cdx.json').read_text())
    assert sbom['metadata']['component']['name'] == 'onedeploy'
    assert any(c['name'] == 'pytest' for c in sbom['components'])
    for line in (tmp_path / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split('  ', 1)
        assert checksum == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    assert 'not a full deployed GPU-image SBOM' in (tmp_path / 'NOTICE.txt').read_text()


def test_reusable_workflow_is_opt_in_and_no_gpu_credentials():
    import yaml
    text = (ROOT / '.github/workflows/autodeploy.yml').read_text()
    workflow = yaml.safe_load(text)
    triggers = workflow.get('on', workflow.get(True))
    assert set(triggers) == {'workflow_call'}
    job = workflow['jobs']['build-plan']
    assert "github.event_name == 'push'" in job['if']
    assert workflow['concurrency']['cancel-in-progress'] is False
    assert 'DAYTONA_API_KEY' not in text
    assert 'autodeploy apply' not in text
    assert "plan['id']" in text
    for step in job['steps']:
        if 'uses' in step:
            assert len(step['uses'].rsplit('@', 1)[1]) == 40
