from local_vector_batch.cli import main, parser


def test_pdf_quality_cli_has_explicit_source_bound_inputs():
    args = parser().parse_args(["pdf-quality", "--package", "parsed", "--source-pdf", "source.pdf",
                                "--output", "quality/report.json", "--pages", "2,4", "--independent", "ocr.jsonl"])
    assert args.pages == [2, 4]
    assert args.source_raster is True
    assert str(args.independent) == "ocr.jsonl"


def test_pdf_quality_cli_refuses_overwriting_extraction_files(tmp_path, capsys):
    result = main(["pdf-quality", "--package", str(tmp_path), "--source-pdf", str(tmp_path / "source.pdf"),
                   "--output", str(tmp_path / "pages.jsonl"), "--json"])
    assert result == 2
    assert "must not overwrite" in capsys.readouterr().err


def test_pipeline_gate_writes_repair_receipt_but_returns_failure(tmp_path, monkeypatch, capsys):
    import local_vector_batch.quality_gate as gate
    report = {"schema_version": 1, "human_review_receipts_created": 0,
              "pages_assessed": 1, "counts": {"repair_required": 1}}
    monkeypatch.setattr(gate, "assess_package", lambda *args, **kwargs: report)
    output = tmp_path / "quality.json"
    result = main(["pdf-quality", "--package", str(tmp_path / "parsed"),
                   "--source-pdf", str(tmp_path / "source.pdf"), "--output", str(output),
                   "--require-accepted", "--json"])
    assert result == 3
    assert output.is_file()
    assert '"repair_required": 1' in capsys.readouterr().out


def test_repair_queue_cli_runs_staged_api_and_status_without_new_work(tmp_path, monkeypatch, capsys):
    import local_vector_batch.repair_pipeline as repairs
    calls = []
    monkeypatch.setattr(repairs, "run_repairs", lambda *args, **kwargs:
                        calls.append((args, kwargs)) or {"jobs_run": 5, "counts": {"repair_required": 5}})
    monkeypatch.setattr(repairs, "repair_status", lambda path: {"jobs": 5})
    output = tmp_path / "repairs"
    assert main(["pdf-repair-queue", "--input", "manifest.jsonl", "--output", str(output),
                 "--workers", "4", "--limit", "5", "--json"]) == 0
    assert calls[0][1] == {"workers": 4, "limit": 5, "config": {}}
    assert main(["pdf-repair-status", "--output", str(output), "--json"]) == 0
    assert len(calls) == 1
    assert '"jobs": 5' in capsys.readouterr().out


def test_repair_queue_cli_rejects_nonobject_config(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text("[]")
    assert main(["pdf-repair-queue", "--input", "manifest.jsonl", "--output", str(tmp_path / "repairs"),
                 "--config", str(config), "--json"]) == 2
    assert "must be a JSON object" in capsys.readouterr().err
