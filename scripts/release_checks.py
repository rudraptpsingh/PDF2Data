"""Check versions and distributable wheel contents without importing the source tree."""
from pathlib import Path
import argparse
import json
import re
import zipfile
import hashlib
import tarfile

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
        'field_checks.py', 'multipart_fields.py', 'semantic_fields.py',
        'extraction_rules/cerc-sharing-supplement-v1.json',
        'extraction_rules/cerc-sharing-supplement-v1.checks.json']
    wheels = []
    if dist:
        for source in Path(dist).glob('*.tar.gz'):
            with tarfile.open(source) as archive:
                for member in archive.getmembers():
                    parts = Path(member.name).parts
                    if any(p in {'.qa', '.local', '.venv', 'node_modules'} or p.startswith('.venv-') for p in parts):
                        raise ValueError('Private/runtime directory included in source archive')
                    if Path(member.name).is_absolute() or '..' in parts:
                        raise ValueError('Unsafe source archive member')
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
