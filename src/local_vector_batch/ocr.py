"""Optional OCR through local macOS Vision or an installed Tesseract binary."""
from __future__ import annotations

import csv
from functools import lru_cache
import io
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import uuid


@lru_cache(maxsize=2)
def _rapidocr_reader(recognition_model: str = "en"):
    """Use a real language-specific PP-OCRv5 recognizer, cached per worker."""
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    models = {"en": LangRec.EN, "devanagari": LangRec.DEVANAGARI}
    if recognition_model not in models:
        raise ValueError(f"unsupported RapidOCR recognition model: {recognition_model}")
    return RapidOCR(params={"Rec.lang_type": models[recognition_model],
                            "Rec.ocr_version": OCRVersion.PPOCRV5,
                            "Rec.model_type": ModelType.MOBILE,
                            "EngineConfig.onnxruntime.intra_op_num_threads": 2})


def _rapidocr_lines(image, recognition_model: str = "en") -> tuple[list[dict], int] | None:
    """Independent candidate reader for visibly nonblank Vision-empty pages.

    RapidOCR output is still unverified OCR. Word boxes preserve source geometry
    for the separate, review-gated ruled-table extractor; they do not establish
    that a cell value or row label is correct.
    """
    started = time.monotonic()
    try:
        reader = _rapidocr_reader(recognition_model)
    except ImportError:
        return None
    result = reader(image, return_word_box=True)
    lines = []
    polygons = result.boxes if result.boxes is not None else ()
    values = result.txts if result.txts is not None else ()
    scores = result.scores if result.scores is not None else ()
    word_groups = getattr(result, "word_results", ()) or ()
    for index, (polygon, value, score) in enumerate(zip(polygons, values, scores)):
        if not str(value).strip():
            continue
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        words = []
        if index < len(word_groups):
            for candidate in word_groups[index]:
                if not isinstance(candidate, (tuple, list)) or len(candidate) != 3:
                    continue
                word, _word_score, box = candidate
                if not str(word).strip() or box is None:
                    continue
                word_xs = [float(point[0]) for point in box]
                word_ys = [float(point[1]) for point in box]
                words.append({"text": str(word),
                              "bbox": [min(word_xs)/image.width, min(word_ys)/image.height,
                                       max(word_xs)/image.width, max(word_ys)/image.height]})
        lines.append({"text": str(value), "confidence": float(score),
                      "bbox": [min(xs)/image.width, min(ys)/image.height,
                               max(xs)/image.width, max(ys)/image.height],
                      "words": words})
    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    return lines, round((time.monotonic() - started) * 1000)


def _rapidocr_version() -> str | None:
    try:
        return version("rapidocr")
    except PackageNotFoundError:
        return None


@lru_cache(maxsize=2)
def _rapidocr_model_sha256(recognition_model: str) -> str | None:
    """Bind OCR provenance to the actual local recognition model weights."""
    try:
        import rapidocr
        path = Path(rapidocr.__file__).parent / "models" / f"{recognition_model}_PP-OCRv5_rec_mobile.onnx"
        if not path.is_file():
            return None
        with path.open("rb") as model_file:
            return hashlib.file_digest(model_file, "sha256").hexdigest()
    except (ImportError, OSError):
        return None


OCR_BASE_DPI = 180
OCR_MAX_PIXELS = 12_000_000


def bounded_ocr_dpi(width_points: float, height_points: float,
                    base_dpi: int = OCR_BASE_DPI, max_pixels: int = OCR_MAX_PIXELS) -> int:
    """Bound an oversized scan's render before it consumes excessive memory.

    The caller records the actual DPI. A reduced render remains subject to
    source review; this function does not imply that its text is complete.
    """
    if width_points <= 0 or height_points <= 0 or base_dpi < 1 or max_pixels < 1:
        raise ValueError("invalid OCR page dimensions or rendering budget")
    dpi = min(base_dpi, max(1, math.floor(72 * math.sqrt(max_pixels / (width_points * height_points)))))
    while math.ceil(width_points * dpi / 72) * math.ceil(height_points * dpi / 72) > max_pixels and dpi > 1:
        dpi -= 1
    if math.ceil(width_points * dpi / 72) * math.ceil(height_points * dpi / 72) > max_pixels:
        raise ValueError("OCR page exceeds render budget even at 1 DPI")
    return dpi


def _run_vision(binary: Path, image_path: Path, device: str,
                languages: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, int, int]:
    """Serialize system-managed Vision work across processes, but not PDF parsing."""
    from filelock import FileLock
    queued = time.monotonic()
    with FileLock(str(binary.parent / "vision-ocr.lock"), timeout=600):
        wait_ms = round((time.monotonic() - queued) * 1000)
        started = time.monotonic()
        command = [str(binary), str(image_path), device]
        if languages:
            command.append(",".join(languages))
        result = subprocess.run(command,
                                capture_output=True, text=True, timeout=120)
        run_ms = round((time.monotonic() - started) * 1000)
    return result, wait_ms, run_ms


def _vision_binary() -> Path:
    """Reuse the versioned local Vision executable for page or crop OCR."""
    if platform.system() != "Darwin" or not shutil.which("swiftc"):
        raise RuntimeError("macOS Vision OCR requires macOS and Xcode command line tools")
    source = Path(__file__).with_name("vision_ocr.swift")
    compiler = Path(shutil.which("swiftc")).resolve()
    key = hashlib.sha256(source.read_bytes() + f"{platform.platform()}:{compiler.stat().st_mtime_ns}".encode()).hexdigest()[:20]
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "local-vector-batch" / "ocr"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = cache / f"vision-{key}"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        temporary = cache / f".{key}-{uuid.uuid4().hex}.tmp"
        try:
            result = subprocess.run([str(compiler), str(source), "-o", str(temporary)],
                                    capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(f"Vision OCR compilation failed: {result.stderr[-300:]}")
            temporary.replace(binary)
        finally:
            temporary.unlink(missing_ok=True)
    return binary


def vision_crop_lines(image) -> list[dict]:
    """Read an exact source crop through a distinct macOS Vision recognizer."""
    with tempfile.TemporaryDirectory(prefix="lvb-vision-crop-") as directory:
        path = Path(directory) / "crop.png"
        image.save(path)
        result, _, _ = _run_vision(_vision_binary(), path, "auto")
        if result.returncode:
            raise RuntimeError(f"Vision crop OCR failed: {result.stderr[-300:]}")
        return json.loads(result.stdout)["lines"]


class LocalOCR:
    def __init__(self, engine: str = "auto", language: str = "eng", device: str = "auto",
                 render_dpi: int = OCR_BASE_DPI):
        if engine not in {"auto", "off", "vision", "tesseract", "rapidocr"}:
            raise ValueError("OCR engine must be auto, off, vision, tesseract, or rapidocr")
        if not language.replace("+", "").replace("_", "").isalnum():
            raise ValueError("invalid OCR language")
        if device not in {"auto", "cpu", "gpu"}:
            raise ValueError("OCR device must be auto, cpu, or gpu")
        if type(render_dpi) is not int or not 72 <= render_dpi <= 300:
            raise ValueError("OCR render DPI must be 72..300")
        vision = platform.system() == "Darwin" and shutil.which("swiftc")
        tesseract = shutil.which("tesseract")
        self.engine = ("vision" if vision else "tesseract" if tesseract else "off") if engine == "auto" else engine
        self.engine_requested = engine
        if self.engine == "vision" and not vision:
            raise RuntimeError("macOS Vision OCR requires macOS and Xcode command line tools")
        if self.engine == "tesseract" and not tesseract:
            raise RuntimeError("Tesseract OCR is not installed or is not on PATH")
        if self.engine == "rapidocr":
            if language not in {"eng", "en", "hin", "hi", "eng+hin", "hin+eng"}:
                raise ValueError("RapidOCR supports eng, hin, or eng+hin with the configured PP-OCRv5 recognizers")
            try:
                import rapidocr  # noqa: F401
            except ImportError as error:
                raise RuntimeError("RapidOCR is not installed; install local-vector-batch[ocr]") from error
        if device == "gpu":
            raise ValueError("forced GPU OCR is unsupported: Vision does not report which accelerator ran the request")
        self.language = language
        self.render_dpi_requested = render_dpi
        self.rapidocr_model = ("devanagari" if language in {"hin", "hi", "eng+hin", "hin+eng"} else "en")
        # Vision uses BCP-47 language identifiers. Keep the historical English
        # default on system automatic detection; opt into bilingual recognition
        # only when requested. Other Tesseract packs are not assumed supported
        # by Vision and must fail clearly rather than silently becoming English.
        vision_languages = {"eng": (), "en": (), "hin": ("hi-IN", "en-IN"),
                            "hi": ("hi-IN", "en-IN"),
                            "eng+hin": ("hi-IN", "en-IN"),
                            "hin+eng": ("hi-IN", "en-IN")}
        if self.engine == "vision" and language not in vision_languages:
            raise ValueError(f"Vision OCR language {language!r} is unsupported; use eng, hin or eng+hin")
        self.recognition_languages = (vision_languages.get(language, ()) if self.engine == "vision" else
                                      ("hi", "en") if self.engine == "rapidocr" and self.rapidocr_model == "devanagari" else
                                      ("en",) if self.engine == "rapidocr" else ())
        self.device_requested = device
        self.device_used = ("none" if self.engine == "off" else
                            "cpu" if self.engine in {"tesseract", "rapidocr"} else
                            "cpu_requested_unverified" if device == "cpu" else
                            "system_managed_unreported")
        self.workspace = tempfile.TemporaryDirectory(prefix="lvb-ocr-")
        self.binary: Path | None = None
        self.last_queue_wait_ms = 0
        self.last_rendered_image = None
        self.fallback_pages = 0

    def close(self) -> None:
        self.workspace.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def recognize(self, page) -> dict | None:
        if self.engine == "off":
            return None
        render_dpi = bounded_ocr_dpi(float(page.width), float(page.height),
                                     base_dpi=self.render_dpi_requested)
        image = page.to_image(resolution=render_dpi).original
        self.last_rendered_image = image
        image_path = Path(self.workspace.name) / "page.png"
        image.save(image_path)
        queue_wait_ms = 0
        fallback = None
        if self.engine == "vision":
            if self.binary is None:
                self.binary = _vision_binary()
            result, queue_wait_ms, execution_ms = _run_vision(
                self.binary, image_path, self.device_requested, self.recognition_languages)
            self.last_queue_wait_ms = queue_wait_ms
            if result.returncode:
                raise RuntimeError(f"Vision OCR failed: {result.stderr[-300:]}")
            lines = json.loads(result.stdout)["lines"]
            if (not lines and self.engine_requested == "auto" and
                    self.language in {"eng", "en"} and
                    image.convert("L").getextrema()[0] < 250):
                alternative = _rapidocr_lines(image, "en")
                if alternative is not None and alternative[0]:
                    lines, alternate_ms = alternative
                    self.fallback_pages += 1
                    alternate_text = "\n".join(line["text"] for line in lines)
                    fallback = {"primary_engine": "vision", "primary_text": "",
                                "primary_text_sha256": hashlib.sha256(b"").hexdigest(),
                                "primary_line_count": 0, "alternate_engine": "rapidocr",
                                "alternate_text_sha256": hashlib.sha256(alternate_text.encode()).hexdigest(),
                                "alternate_package_version": _rapidocr_version(),
                                "alternate_recognition_model": "en_PP-OCRv5_rec_mobile",
                                "numeric_agreement_verified": False,
                                "requires_source_review": True,
                                "trigger": "vision_empty_visible_ink",
                                "primary_execution_ms": execution_ms,
                                "alternate_execution_ms": alternate_ms}
                    execution_ms += alternate_ms
        elif self.engine == "rapidocr":
            alternative = _rapidocr_lines(image, self.rapidocr_model)
            if alternative is None:
                raise RuntimeError("RapidOCR is not installed; install local-vector-batch[ocr]")
            lines, execution_ms = alternative
        else:
            started = time.monotonic()
            result = subprocess.run(["tesseract", str(image_path), "stdout", "-l", self.language, "tsv"],
                                    capture_output=True, text=True, timeout=120)
            execution_ms = round((time.monotonic() - started) * 1000)
            if result.returncode:
                raise RuntimeError(f"Tesseract OCR failed: {result.stderr[-300:]}")
            groups: dict[tuple, list[dict]] = {}
            for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
                if row.get("level") != "5" or not row.get("text", "").strip():
                    continue
                key = tuple(row[field] for field in ("block_num", "par_num", "line_num"))
                groups.setdefault(key, []).append(row)
            lines = []
            for words in groups.values():
                left = min(int(word["left"]) for word in words)
                top = min(int(word["top"]) for word in words)
                right = max(int(word["left"])+int(word["width"]) for word in words)
                bottom = max(int(word["top"])+int(word["height"]) for word in words)
                lines.append({"text": " ".join(word["text"] for word in words),
                              "confidence": sum(float(word["conf"]) for word in words)/len(words)/100,
                              "bbox": [left/image.width, top/image.height, right/image.width, bottom/image.height],
                              "words": [{"text": word["text"],
                                         "bbox": [int(word["left"])/image.width,
                                                  int(word["top"])/image.height,
                                                  (int(word["left"])+int(word["width"]))/image.width,
                                                  (int(word["top"])+int(word["height"]))/image.height]}
                                        for word in words]})
        selected_engine = "rapidocr" if fallback else self.engine
        return {"engine": selected_engine, "device_requested": self.device_requested,
                "device_used": "cpu" if fallback else self.device_used,
                "engine_package_version": _rapidocr_version() if selected_engine == "rapidocr" else None,
                "recognition_model": (f"{self.rapidocr_model}_PP-OCRv5_rec_mobile" if self.engine == "rapidocr" else
                                      "en_PP-OCRv5_rec_mobile" if fallback else None),
                "recognition_model_sha256": (_rapidocr_model_sha256("en" if fallback else self.rapidocr_model)
                                               if selected_engine == "rapidocr" else None),
                "render_dpi": render_dpi, "render_pixels": image.width * image.height,
                "render_capped": render_dpi < self.render_dpi_requested,
                "recognition_languages": (["en"] if fallback else list(self.recognition_languages)) if self.engine == "vision" else
                (["hi", "en"] if self.rapidocr_model == "devanagari" else ["en"])
                if self.engine == "rapidocr" else [self.language],
                "queue_wait_ms": queue_wait_ms, "execution_ms": execution_ms,
                "text": "\n".join(line["text"] for line in lines), "lines": lines,
                "mean_confidence": sum(line["confidence"] for line in lines)/len(lines) if lines else 0,
                "bbox_coordinates": "normalized top-left",
                **({"fallback": fallback} if fallback else {})}
