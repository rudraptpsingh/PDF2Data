"""Check versions and distributable wheel contents without importing the source tree."""
from pathlib import Path
import argparse
import json
import re
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]


def check(dist=None):
    pyproject = (ROOT / 'pyproject.toml').read_text()
    python_version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    npm_version = json.loads((ROOT / 'npm/package.json').read_text())['version']
    if python_version != re.sub(r'-rc\.(\d+)$', r'rc\1', npm_version):
        raise ValueError('Python and npm versions differ')
    namespace = (ROOT / 'src/local_vector_batch/__init__.py').read_text()
    if f'__version__ = "{python_version}"' not in namespace:
        raise ValueError('Engine __version__ differs')
    required = ['cli.py', 'mcp_server.py', 'workspace.html', 'ui.css', 'overlays.js',
        'overlays.css', 'viewer.css', 'quality_policy.json', 'document_profiles.json',
        'document_model.py', 'formula_observations.py', 'source_locations.py', 'native_visuals.py',
        'field_checks.py']
    wheels = []
    if dist:
        for wheel in Path(dist).glob('*.whl'):
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
                missing = [n for n in required if 'local_vector_batch/' + n not in names]
                if missing: raise ValueError('Wheel assets missing: ' + ', '.join(missing))
            wheels.append(dict(file=wheel.name, sha256=hashlib.sha256(wheel.read_bytes()).hexdigest()))
        if len(wheels) != 1: raise ValueError('Expected one built Python wheel')
    return dict(python_version=python_version, npm_version=npm_version, tag='v' + npm_version, wheels=wheels)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--dist', type=Path)
    args = parser.parse_args(); print(json.dumps(check(args.dist), indent=2))
