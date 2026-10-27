import json

from local_vector_batch.cli import main


def test_doctor_json(capsys):
    assert main(["doctor", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert "platform" in result and "torch_installed" in result
