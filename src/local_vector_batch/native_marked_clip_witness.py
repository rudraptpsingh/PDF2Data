"""Exact rectangular clip contexts for tagged native text, not guessed scopes.

Only flat, unrotated pages with identity content transforms are supported.
Unknown clip paths, forms, optional content and unbalanced source stacks hold.
No text or path is removed, and this is not a whole-page raster proof.
"""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def marked_clip_contexts(operations, page_height):
    if len(operations) > 100000 or not math.isfinite(page_height) or page_height <= 0:
        raise ValueError('Unsupported source content bounds')
    clips = []; graphics = []; marks = []; path = []; pending = False
    path_supported = True; identity = True; contexts = defaultdict(list)
    for index, (args, op) in enumerate(operations):
        if op == b'q':
            graphics.append((list(clips), identity))
        elif op == b'Q':
            if not graphics:
                raise ValueError('Unbalanced source graphics stack')
            clips, identity = graphics.pop()
        elif op == b'cm':
            # Do not approximate transformed clipping or import form-local IDs.
            identity = identity and len(args) == 6 and tuple(map(float, args)) == (1., 0., 0., 1., 0., 0.)
        elif op == b'Do':
            raise ValueError('Marked clip witness does not support source XObjects')
        elif op == b're':
            if len(args) != 4 or not identity:
                path_supported = False
                continue
            x, y, width, height = map(float, args)
            if not all(math.isfinite(v) for v in (x, y, width, height)) or not width or not height:
                path_supported = False
                continue
            path.append([min(x, x + width), page_height - max(y, y + height),
                         max(x, x + width), page_height - min(y, y + height)])
        elif op in (b'm', b'l', b'c', b'v', b'y', b'h'):
            path_supported = False
        elif op in (b'W', b'W*'):
            pending = True
        elif op in (b'n', b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*'):
            if pending:
                clips.append({'operator_index': index, 'bbox': path[0] if path_supported and len(path) == 1 else None})
            path = []; pending = False; path_supported = True
        elif op == b'BDC':
            if len(args) != 2 or not isinstance(args[1], dict) or str(args[0]) == '/OC':
                marks.append({'mcid': None, 'supported': False})
            else:
                mcid = args[1].get('/MCID')
                marks.append({'mcid': int(mcid) if type(mcid).__name__ in ('NumberObject', 'int') and 0 <= int(mcid) < 2**31 else None,
                              'supported': True})
        elif op == b'BMC':
            marks.append({'mcid': None, 'supported': bool(args) and str(args[0]) != '/OC'})
        elif op == b'EMC':
            if not marks:
                raise ValueError('Unbalanced source marked-content stack')
            marks.pop()
        elif op in (b'Tj', b'TJ', b"'", b'"'):
            mcid = next((m['mcid'] for m in reversed(marks) if m['mcid'] is not None), None)
            if mcid is not None:
                contexts[mcid].append({'operator_index': index, 'clips': list(clips),
                                      'supported': identity and all(m['supported'] for m in marks)})
    if graphics or marks or pending:
        raise ValueError('Incomplete source graphics/marked-content scope')
    return dict(contexts)


def prove_marked_glyph_clips(contexts, glyphs, *, tolerance=0.0001):
    """All observed text operations for each MCID must contain each glyph.

    MCIDs identify source scopes, not a chosen nearest box. Reused/ambiguous
    scopes with incompatible clips reject the candidate. No clip is ignored
    because its geometry happens to look unrelated.
    """
    records = []
    for glyph in glyphs:
        mcid = glyph.get('mcid'); bounds = glyph['bbox']
        if (len(bounds) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds)
                or not bounds[0] < bounds[2] or not bounds[1] < bounds[3]):
            return None
        if type(mcid) is not int or mcid not in contexts:
            return None
        scopes = contexts[mcid]
        if not scopes or any(not scope['supported'] for scope in scopes):
            return None
        for scope in scopes:
            for clip in scope['clips']:
                rectangle = clip['bbox']
                if rectangle is None or not (rectangle[0] - tolerance <= bounds[0] <= bounds[2] <= rectangle[2] + tolerance
                        and rectangle[1] - tolerance <= bounds[1] <= bounds[3] <= rectangle[3] + tolerance):
                    return None
        records.append({'source_glyph_index': glyph['source_glyph_index'], 'mcid': mcid,
                        'bbox': bounds, 'text_operator_indexes': [s['operator_index'] for s in scopes],
                        'active_clips': [c for s in scopes for c in s['clips']]})
    return {'contract_version': 'pdf2data.source-mcid-rectangular-clip-witness.v1',
            'glyphs': records, 'coordinate_system': 'unrotated PDF points, top-left',
            'precision_tolerance_points': tolerance, 'contexts_sha256': sha(canonical(contexts)),
            'implementation_sha256': sha(Path(__file__).read_bytes()),
            'source_content_modified': False, 'page_quality_cleared': False}
