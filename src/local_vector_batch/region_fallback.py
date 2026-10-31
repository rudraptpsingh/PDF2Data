"""Optional local text retry. Recognition confidence is not content approval."""
import hashlib
import math
import platform
from pathlib import Path

CONFIDENCE_FLOOR = .8


def vision_identity():
    from .ocr import _vision_binary
    binary = _vision_binary()
    return dict(engine='macos_vision', os=platform.platform(),
                binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                source_sha256=hashlib.sha256(Path(__file__).with_name('vision_ocr.swift').read_bytes()).hexdigest(),
                wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                ocr_sha256=hashlib.sha256(Path(__file__).with_name('ocr.py').read_bytes()).hexdigest(),
                confidence_floor=CONFIDENCE_FLOOR, device_requested='auto',
                device_used='system_managed_unknown', model_weights_accessible=False)


def validate_lines(lines):
    """Reject the entire retry instead of dropping uncertain/invalid lines."""
    if not isinstance(lines,list) or not lines:
        return 'empty_fallback'
    for line in lines:
        if not isinstance(line,dict) or not isinstance(line.get('text'),str) or not line['text'].strip():
            return 'empty_fallback_line'
        confidence=line.get('confidence')
        if type(confidence) not in (int,float) or not math.isfinite(confidence) or not CONFIDENCE_FLOOR<=confidence<=1:
            return 'low_fallback_confidence'
        box=line.get('bbox')
        if (not isinstance(box,list) or len(box)!=4 or
            any(type(v) not in (int,float) or not math.isfinite(v) for v in box) or
            not 0<=box[0]<box[2]<=1 or not 0<=box[1]<box[3]<=1):
            return 'invalid_fallback_geometry'
    return None


def source_coverage(image,lines):
    """Unrecognized ink is unresolved, including rules/noise; never infer text.

    Box coverage cannot establish spelling or semantic completeness. It can
    nevertheless disprove a confident retry that leaves visible source behind.
    """
    import numpy as np
    ink=np.asarray(image.convert('L'))<100
    height,width=ink.shape;covered=np.zeros(ink.shape,dtype=bool)
    for line in lines:
        x0,y0,x1,y1=line['bbox']
        covered[max(0,math.floor(y0*height)-2):min(height,math.ceil(y1*height)+2),
                max(0,math.floor(x0*width)-2):min(width,math.ceil(x1*width)+2)]=True
    total=int(ink.sum());uncovered=int((ink&~covered).sum());limit=max(32,math.floor(total*.01))
    return dict(source_dark_pixels=total,uncovered_dark_pixels=uncovered,
                max_uncovered_pixels=limit,within_limit=uncovered<=limit,
                semantic_coverage_verified=False)


def vision_attempt(path):
    from PIL import Image
    from .ocr import vision_crop_lines
    try:
        with Image.open(path) as image:
            lines=vision_crop_lines(image)
            reason=validate_lines(lines)
            coverage=source_coverage(image,lines) if reason is None else None
            if coverage and not coverage['within_limit']:reason='fallback_uncovered_source_ink'
        return dict(engine='macos_vision',lines=lines,
                    text='\n'.join(line['text'] for line in lines) if reason is None else None,
                    accepted=reason is None,failure_reason=reason,source_coverage=coverage,content_verified=False)
    except Exception as error:
        return dict(engine='macos_vision',accepted=False,failure_reason='fallback_runtime_error',error=str(error)[:400])
