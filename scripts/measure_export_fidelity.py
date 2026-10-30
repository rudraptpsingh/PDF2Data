"""Compare CLI export artifacts; never reads/parses a PDF independently."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


def read(root, name):
    folder = root / name
    return json.loads((folder / 'data.json').read_text()), (folder / 'document.md').read_text()


def metrics(data, md, name):
    positions = [m.start() for m in re.finditer('<table', md)]
    if name == 'sample':
        inline = int(len(positions) == 1 and md.index('3. Outputs') < positions[0] < md.index('4. Review'))
        duplicate = int('Format Purpose' in md)
    elif name == 'october-em':
        inline = sum((positions[0] < md.index('Illustration 2:'), positions[1] < md.index('(a) Suppose'))) if len(positions) == 2 else 0
        duplicate = max(0, md.count('Not effective') - 1)
    else:
        inline = duplicate = 0
    raw = '\n'.join(p['text'] for p in data['pages'])
    return {
        'source_pdf_sha256': data['source_pdf_sha256'],
        'page_count': len(data['pages']),
        'raw_text_sha256': sha256(raw.encode()).hexdigest(),
        'raw_character_count': sum(len(p['text']) for p in data['pages']),
        'raw_page_hashes_valid': all(sha256(p['text'].encode()).hexdigest() == p['text_sha256'] for p in data['pages']),
        'table_count': len(data['tables']),
        'structured_cell_count': sum(len(row) for t in data['tables'] for row in t['cells']),
        'inline_tables': inline,
        'flattened_table_duplicate_probes': duplicate,
        'page5_footer_joins_next_condition': 'Page 5(a)' in md,
        'cross_page_optional_rejoinder_preserved': 'if any' in md and 'within two weeks thereafter' in md if name == 'rop' else None,
        'publication_eligible': data['publication_eligible'],
        'unresolved_tables': len(data['document_blocks']['unresolved_tables']) if 'document_blocks' in data else None,
        'document_blocks_available': 'document_blocks' in data,
    }


def compare(before, after):
    cases = {}
    for name in ('sample', 'october-em', 'rop'):
        old, old_md = read(before, name)
        new, new_md = read(after, name)
        a, b = metrics(old, old_md, name), metrics(new, new_md, name)
        assert a['source_pdf_sha256'] == b['source_pdf_sha256']
        assert a['raw_text_sha256'] == b['raw_text_sha256']
        assert [t['cells'] for t in old['tables']] == [t['cells'] for t in new['tables']]
        assert a['raw_page_hashes_valid'] and b['raw_page_hashes_valid']
        assert not a['publication_eligible'] and not b['publication_eligible']
        cases[name] = {'before': a, 'after': b, 'raw_text_identical': True, 'structured_table_values_identical': True}
    assert cases['sample']['after']['inline_tables'] == 1
    assert cases['october-em']['after']['inline_tables'] == 2
    assert cases['october-em']['after']['structured_cell_count'] == 36
    assert cases['october-em']['before']['page5_footer_joins_next_condition']
    assert not cases['october-em']['after']['page5_footer_joins_next_condition']
    assert cases['rop']['before']['cross_page_optional_rejoinder_preserved']
    assert cases['rop']['after']['cross_page_optional_rejoinder_preserved']
    return {'schema_version': 'pdf2data.fidelity-comparison.v1', 'before_commit': 'c8328d18e1352b09d43de6b8bc144e15267431a6',
            'after_commit': '11de3b800bcb7c97a941db3b94115dbf58b2502c', 'cases': cases,
            'limits': ['Three selected PDFs; no corpus-wide accuracy claim.',
                       'Duplicate probes are explicit literals, not all possible duplication.',
                       'Character conservation and candidate structure do not certify OCR or legal meaning.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(compare(args.before, args.after), indent=2) + '\n')
