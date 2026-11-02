"""Benign subprocess configuration probes; no compiled-file payloads."""
import json
from pathlib import Path
import subprocess

import pytest

from local_vector_batch import native_table_review_queue, proof_relocation, targeted_reproof


@pytest.mark.parametrize('launcher', ['custody', 'targeted', 'capture'])
@pytest.mark.parametrize('failure', [False, True])
def test_workers_use_unique_private_cache_with_isolated_mode_and_cleanup(tmp_path, monkeypatch, launcher, failure):
    old = tmp_path / 'existing-cache'
    old.mkdir()
    (old / 'sentinel.txt').write_text('existing caller cache')
    monkeypatch.setenv('PYTHONPYCACHEPREFIX', str(old))
    monkeypatch.setattr(__import__('sys'), 'pycache_prefix', str(old))
    real_run = subprocess.run
    observed = []

    def probe(args, **kwargs):
        # Run the launcher's actual Python switches, replacing only the proof
        # workload with a stdlib introspection probe. No PDF processing occurs.
        offset = args.index('-X')
        prefix = Path(args[offset + 1].removeprefix('pycache_prefix='))
        assert prefix.is_dir() and not list(prefix.iterdir())
        assert prefix != old and prefix not in observed
        observed.append(prefix)
        code = ('import json,sys; print(json.dumps({"status":"verified",'
                '"cache":sys.pycache_prefix,"isolated":sys.flags.isolated,'
                '"write_disabled":sys.dont_write_bytecode}));'
                + ('print("benign probe failure",file=sys.stderr);raise SystemExit(3)' if failure else ''))
        actual = real_run([*args[:offset + 2], '-c', code], capture_output=True, text=True)
        row = json.loads(actual.stdout)
        assert row == {'status': 'verified', 'cache': str(prefix), 'isolated': 1, 'write_disabled': True}
        assert not list(prefix.iterdir())
        return actual

    monkeypatch.setattr(subprocess, 'run', probe)
    child = tmp_path / 'child'
    child.mkdir()
    (child / 'l1-package.json').write_text('{}')
    mapping = tmp_path / 'mapping.json'
    mapping.write_text('{}')

    def launch():
        if launcher == 'custody':
            return proof_relocation.replay(child, tmp_path, tmp_path, mapping)
        if launcher == 'targeted':
            return targeted_reproof._worker('verify', {})
        return native_table_review_queue._capture(child, tmp_path, tmp_path, 1)

    for _ in range(2):
        if failure:
            with pytest.raises(ValueError):
                launch()
        else:
            assert launch()['status'] == 'verified'
        assert all(not path.exists() for path in observed)
    assert len(observed) == 2 and observed[0] != observed[1]
    assert (old / 'sentinel.txt').read_text() == 'existing caller cache'
