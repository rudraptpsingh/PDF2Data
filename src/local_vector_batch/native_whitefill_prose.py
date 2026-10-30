"""Prove invisible white background candidates as source-complete native prose.

No original table, text or raw value is removed. The narrowly classified visual
layout is separate from semantic/legal table interpretation. All eight ordinary
source gates and a fresh registered decoder remain mandatory.
"""
import copy,json,shutil,tempfile
from collections import Counter
from pathlib import Path
from .native_table_feedback import sha,canonical,read_rows
from .proof_identity import proof_configuration,inspect_pdf2data_profile,verify_unchanged_quality_pages

OPERATION='native_whitefill_prose'
def read(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def rules():return read(Path(__file__).with_name('native_whitefill_prose_rules.json'))


def selected_representation(source,native,page,raw,selected,third,policy):
    """Accept only exact native output or a freshly reproduced registered repair."""
    proof={'raw_native_text_sha256':sha(raw),'selected_text_sha256':sha(selected),'repairs':[],
           'source_text_modified':False,'selected_text_modified':False,'basis':'exact_fresh_native_text'}
    if raw==selected:return True,proof
    if not policy.get('allow_registered_source_line_representation',False):return False,proof
    compact=lambda s:Counter(c for c in s if not c.isspace())
    if compact(raw)!=compact(selected):return False,proof
    from .native_consensus import repair_source_lines
    reproduced,repairs=repair_source_lines(source,native,page,raw,third)
    if reproduced!=selected or not repairs:return False,proof
    return True,{**proof,'basis':'fresh_registered_source_line_repair_exact_equality',
                 'repairs':repairs,'reproduced_text_sha256':sha(reproduced)}


def classify_objects(native,policy):
    colors=policy['allowed_exact_white_colors'];rects=native.rects
    white=bool(rects)and all(r.get('fill')and not r.get('stroke')and
        (list(r['non_stroking_color'])if isinstance(r.get('non_stroking_color'),(list,tuple))else[r.get('non_stroking_color')])in colors for r in rects)
    return {'nonempty_white_backgrounds':white,
            'no_ruling_strokes_images_or_curves':not native.images and not native.curves and not native.lines and all(not r.get('stroke')for r in rects)}


def derive(parent,page):
    import pdfplumber
    from .legal_l1 import verify_document
    from .native_verification_session import active_session
    from .native_paint_visibility import analyze
    from .native_consensus import read_source_lines,choose_witness
    from .quality_gate import native_source_raster_evidence,assess_page,load_policy
    parent=Path(parent);verify_document(parent);source=parent/'original.pdf';pdf_sha=sha(source.read_bytes());policy=rules();session=active_session()
    selected=session.page_row(parent/'extraction/pages.jsonl',page)if session else next(r for r in read_rows(parent/'extraction/pages.jsonl')if r['page']==page)
    layout=session.page_row(parent/'layout.jsonl',page)if session else next(r for r in read_rows(parent/'layout.jsonl')if r['page']==page)
    raw_tables=[t for t in read(parent/'extraction/tables.json')if t['page']==page]
    if policy.get('require_recorded_raw_grid_candidates',True)and not raw_tables:raise ValueError('Rule needs retained raw native grid candidates')
    with pdfplumber.open(source)as pdf:
        native=pdf.pages[page-1];predicates=classify_objects(native,policy)
        if not all(predicates.values()):raise ValueError('Rule object predicates did not match: '+canonical(predicates))
        paint=analyze(source,page,[])
        predicates['no_paint_occlusion_or_unsupported_paint']=not paint['unsupported_paints']and all(g['visibility_state']in('retained_visible_or_unproven','retained_raw_whitespace')for g in paint['classified_glyphs'])
        representation_ok,representation=selected_representation(source,native,page,native.extract_text(),selected['text'],layout.get('secondary_layout_text_raw')or'',policy)
        predicates['source_bound_selected_representation']=selected['method']=='pdf_text'and representation_ok and sha(selected['text'])==selected['text_sha256']
        lines=native.extract_text_lines(return_chars=True);gaps=[]
        for index,line in enumerate(lines):
            glyphs=sorted((g for g in line['chars']if g['text'].strip()),key=lambda g:g['x0'])
            for a,b in zip(glyphs,glyphs[1:]):
                if b['x0']-a['x1']>max(policy['source_column_gap_points'],policy['source_column_gap_font_multiplier']*max(a.get('size',0),b.get('size',0))):gaps.append(index)
        predicates['single_column_source_lines']=not gaps
        source_lines=read_source_lines(source,native,page,layout.get('secondary_layout_text_raw'))
        predicates['complete_independent_source_line_coverage']=source_lines.get('status')=='captured'and source_lines.get('glyph_coverage')is True
        if any(predicates.get(name)is not True for name in policy['required_predicates']):raise ValueError('Rule source predicates held: '+canonical(predicates))
        witness,basis=choose_witness(selected['text'],layout.get('secondary_layout_text_raw'),source_lines)
        gate_policy=load_policy();geometry=native_source_raster_evidence(selected,native,pdf_sha,witness,table_count=0,policy=gate_policy)
        quality=assess_page(selected,source_pdf_sha256=pdf_sha,package_pdf_sha256=pdf_sha,
            independent_text=witness,source_geometry=geometry,table_count=0,policy=gate_policy,source_pdf_path=source)
        proof={'contract_version':'pdf2data.native-whitefill-prose-proof.v1','operation':OPERATION,'source_pdf_sha256':pdf_sha,
            'page':page,'page_text_sha256':selected['text_sha256'],'rule_id':policy['rule_id'],'rule_version':policy['version'],
            'rule_config_sha256':sha(canonical(policy)),'predicates':predicates,'classification':'invisible_background_candidates_with_single_column_native_prose',
            'white_rectangles':[{'index':i,'bbox':[r[k]for k in('x0','top','x1','bottom')],'source_object_sha256':sha(canonical(r))}for i,r in enumerate(native.rects)],
            'paint_visibility_proof_sha256':sha(canonical(paint)),'independent_source_lines':source_lines,'independent_basis':basis,
            'selected_representation_proof':representation,
            'source_geometry':geometry,'raw_tables_retained':raw_tables,'raw_table_semantics':'unreviewed_not_removed_or_rewritten',
            'selected_text_modified':False,'legal_review':'not_run','publication_eligible':False}
    return proof,quality


def build_version(parent,source_runtime,page,output,*,external_decoder=False):
    from .legal_l1 import verify_document,write_rows,readiness_ledger
    parent,source_runtime,output=map(lambda p:Path(p).resolve(),(parent,source_runtime,output))
    if output.exists():raise ValueError('New immutable white-background proof directory required')
    verify_document(parent);origin=inspect_pdf2data_profile(source_runtime);source=read(parent/'source-receipt.json')
    if any(source['configuration'].get(k)!=v for k,v in origin['source_configuration_expected'].items()):raise ValueError('Original extraction epoch mismatch')
    if next(q for q in read(parent/'quality.json')['results']if q['page']==page)['outcome']=='auto_accepted':
        raise ValueError('Already accepted source page is retained unchanged')
    output.mkdir(parents=True);before=sha((parent/'l1-package.json').read_bytes())
    try:proof,quality=derive(parent,page)
    except Exception as error:
        policy=rules();result={'status':'held','reason':type(error).__name__+': '+str(error),'page':page,
            'rule_matched':False,'rule_id':policy['rule_id'],'rule_version':policy['version'],
            'rule_config_sha256':sha(canonical(policy)),'publication_eligible':False};save(output/'validation.json',result);return result
    if quality['outcome']!='auto_accepted'or any(c['state']!='pass'for c in quality['components'].values()):
        result={'status':'held','reason':'Matched white-background rule; other source gates remain held','page':page,
            'rule_matched':True,'quality':quality,'proof':proof,'publication_eligible':False};save(output/'validation.json',result);return result
    config=proof_configuration(OPERATION,parent,Path(__file__));config.update(source_runtime=str(source_runtime),page=page)
    dest=output/'packages'/parent.name;dest.parent.mkdir()
    with tempfile.TemporaryDirectory(prefix='.white-prose-',dir=output)as temp:
        stage=Path(temp)/parent.name;shutil.copytree(parent,stage);layout=read_rows(stage/'layout.jsonl')
        for row in layout:
            if row['page']==page:row.update(native_whitefill_prose_proof=proof,quality_repair_configuration=config)
        write_rows(stage/'layout.jsonl',layout);sr=read(stage/'source-receipt.json');sr['quality_repair_configuration']=config;save(stage/'source-receipt.json',sr)
        q=read(stage/'quality.json');q['results']=[quality if r['page']==page else r for r in q['results']]
        q['counts']={state:sum(r['outcome']==state for r in q['results'])for state in('auto_accepted','repair_required','source_blocked')}
        save(stage/'quality.json',q);save(stage/'readiness.json',readiness_ledger(read(stage/'structure.json'),q))
        write_rows(stage/'repair-queue.jsonl',[r for r in read_rows(stage/'repair-queue.jsonl')if r['page']!=page])
        manifest=read(stage/'l1-package.json');manifest['request_sha256']=sha(canonical({'parent':before,'operation':OPERATION,'proof_sha256':sha(canonical(proof))}))
        manifest['artifacts']={name:sha((stage/name).read_bytes())for name in manifest['artifacts']};save(stage/'l1-package.json',manifest);verify_document(stage)
        if(stage/'extraction/pages.jsonl').read_bytes()!=(parent/'extraction/pages.jsonl').read_bytes()or(stage/'extraction/tables.json').read_bytes()!=(parent/'extraction/tables.json').read_bytes():raise ValueError('White-background rule changed selected text/raw tables')
        stage.rename(dest)
    decoder=None if external_decoder else verify_version(dest,source_runtime)
    result={'status':'blocked_pending_registered_source_decoder'if external_decoder else'passed','page':page,
        'rule_matched':True,'new_l1_package_path':str(dest),'new_l1_package_sha256':sha((dest/'l1-package.json').read_bytes()),
        'source_pdf_sha256':proof['source_pdf_sha256'],'rule_id':proof['rule_id'],'rule_version':proof['rule_version'],
        'rule_config_sha256':proof['rule_config_sha256'],'fresh_decoder':decoder,'source_text_modified':False,'raw_tables_preserved':True,
        'answer_publication_eligible':False,'actual_model_calls':0,'remote_operations':0};save(output/'validation.json',result);return result


def verify_version(package,source_runtime):
    from .legal_l1 import verify_document
    package=Path(package);verify_document(package);config=read(package/'source-receipt.json')['quality_repair_configuration'];parent=Path(config['parent_l1_package_path']);verify_document(parent)
    current=proof_configuration(OPERATION,parent,Path(__file__))
    if config['operation']!=OPERATION or sha((parent/'l1-package.json').read_bytes())!=config['parent_manifest_sha256']or current['proof_profile']['source_configuration_expected']!=config['proof_profile']['source_configuration_expected']:raise ValueError('White-background parent/proof profile changed')
    source_config=read(parent/'source-receipt.json')['configuration'];origin=inspect_pdf2data_profile(source_runtime)
    if any(source_config.get(k)!=v for k,v in origin['source_configuration_expected'].items()):raise ValueError('White-background original extraction epoch mismatch')
    page=config['page'];proof,quality=derive(parent,page);stored=next(r for r in read_rows(package/'layout.jsonl')if r['page']==page)['native_whitefill_prose_proof']
    if proof!=stored or quality!=next(q for q in read(package/'quality.json')['results']if q['page']==page):raise ValueError('White-background proof/quality differs from fresh original source')
    if quality['outcome']!='auto_accepted':raise ValueError('White-background fresh complete-page gates remain held')
    verify_unchanged_quality_pages(package,parent,[page])
    for name in('original.pdf','extraction/pages.jsonl','extraction/tables.json'):
        if(package/name).read_bytes()!=(parent/name).read_bytes():raise ValueError('White-background proof altered original source/raw data')
    return {'status':'verified','operation':OPERATION,'source_quality_pages_verified':[page],'fresh_registered_source_decoder':True,
        'rule_id':proof['rule_id'],'rule_config_sha256':proof['rule_config_sha256'],'source_pdf_sha256':proof['source_pdf_sha256'],
        'page_text_sha256':proof['page_text_sha256'],'source_text_modified':False,'publication_eligible':False}
