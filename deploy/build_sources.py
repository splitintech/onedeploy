"""Build the Python distribution and an auditable locked-dependency inventory.

Adapted from Ozzy's artifact-first build approach; no inference assets or secrets.
This inventory is not a third-party license audit or a signed attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def inventory(root: Path, output: Path) -> None:
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    components = []
    for line in (root / 'requirements-dev.lock').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9.+-]+)', line)
        if not match:
            raise ValueError('Unpinned or unsupported lock entry')
        name, version = match.groups()
        components.append({'type': 'library', 'name': name, 'version': version,
                           'purl': f'pkg:pypi/{name.lower()}@{version}',
                           'scope': 'optional'})
    output.mkdir(parents=True, exist_ok=True)
    (output / 'sbom.cdx.json').write_text(json.dumps({
        'bomFormat': 'CycloneDX', 'specVersion': '1.5', 'version': 1,
        'metadata': {'component': {'type': 'application', 'name': project['name'],
                                  'version': project['version']}},
        'components': components,
    }, indent=2) + '\n')
    (output / 'NOTICE.txt').write_text(
        'OneDeploy source and dependency inventory\n\n'
        'Includes the development/test lock, not a full deployed GPU-image SBOM.\n'
        'Review upstream licenses and GPU/model licenses before distribution.\n\n'
        + (root / 'LICENSE').read_text())
    files = sorted(p for p in output.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
    (output / 'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in files))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'sources')
    parser.add_argument('--inventory-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Use an empty output directory to avoid stale release artifacts')
    output.mkdir(parents=True, exist_ok=True)
    if not args.inventory_only:
        subprocess.run([sys.executable, '-m', 'build', '--no-isolation',
                        '--outdir', str(output)], cwd=ROOT, check=True)
    inventory(ROOT, output)


if __name__ == '__main__':
    main()
