from pathlib import Path
import shutil
import pytest
from local_vector_batch.proof_identity import inspect_pdf2data_profile


def test_installed_and_checkout_code_profiles_use_identical_source_bytes(tmp_path):
    source = Path(__file__).parents[1]
    original = inspect_pdf2data_profile(source)
    library = tmp_path / 'lib/python3.12'
    installed = library / 'site-packages/local_vector_batch'
    installed.mkdir(parents=True)
    for file in (source / 'src/local_vector_batch').iterdir():
        if file.suffix in {'.py', '.json'}: shutil.copyfile(file, installed / file.name)
    profile = inspect_pdf2data_profile(library)
    assert profile['source_configuration_expected'] == original['source_configuration_expected']
    assert profile['source_files'] == original['source_files']
    with pytest.raises(ValueError, match='not found'):
        inspect_pdf2data_profile(tmp_path / 'not-a-runtime')
