"""OCR language requests must select a recognizer trained for that script."""

import sys
from types import ModuleType, SimpleNamespace

from local_vector_batch.ocr import LocalOCR, _rapidocr_reader


def test_rapidocr_language_routes_to_named_models(monkeypatch):
    fake = ModuleType("rapidocr")
    fake.RapidOCR = lambda params: SimpleNamespace(cfg=params)
    typings = ModuleType("rapidocr.utils.typings")
    typings.LangRec = SimpleNamespace(EN="en", DEVANAGARI="devanagari")
    typings.ModelType = SimpleNamespace(MOBILE="mobile")
    typings.OCRVersion = SimpleNamespace(PPOCRV5="PP-OCRv5")
    monkeypatch.setitem(sys.modules, "rapidocr", fake)
    monkeypatch.setitem(sys.modules, "rapidocr.utils", ModuleType("rapidocr.utils"))
    monkeypatch.setitem(sys.modules, "rapidocr.utils.typings", typings)
    _rapidocr_reader.cache_clear()
    try:
        assert _rapidocr_reader("en").cfg["Rec.lang_type"] == "en"
        assert _rapidocr_reader("devanagari").cfg["Rec.lang_type"] == "devanagari"
        assert _rapidocr_reader("en").cfg["Rec.ocr_version"] == "PP-OCRv5"
        for language, expected in (("eng", "en"), ("hin", "devanagari"),
                                   ("eng+hin", "devanagari")):
            with LocalOCR(engine="rapidocr", language=language) as reader:
                assert reader.rapidocr_model == expected
    finally:
        _rapidocr_reader.cache_clear()
