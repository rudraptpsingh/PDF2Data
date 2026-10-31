"""Bounded inventory selection, immutable review focuses and terminal handling."""
import copy
import json
from pathlib import Path
import pytest
from local_vector_batch import native_table_review_queue as queue


def test_layout_only_first_then_source_diverse_and_multi_gate_budget():
    rows=[{'source_pdf_sha256':s,'page':p,'only_layout_gate_remaining':easy}
          for s,p,easy in [('a',1,True),('a',2,True),('a',3,True),('b',1,True),('c',1,False)]]
    actual=queue.ordered_candidates(rows,4)
    assert[(r['source_pdf_sha256'],r['page'])for r in actual[:3]]==[('a',1),('b',1),('c',1)]
    assert rows[1]['page']==2


def test_exact_feedback_focus_includes_geometry_source_and_both_readings():
    table={'table_version_id':'table-v2'}
    cell={'id':'cell','selected_cell_sha256':'a','independent_cell_sha256':'b','bbox':[1,2,3,4]}
    key=queue.cell_key('source',1,table,cell)
    feedback={'source_pdf_sha256':'source','page':1,'table_version_id':'table-v2','cell_id':'cell',
              'selected_cell_sha256':'a','independent_cell_sha256':'b','bbox':[1,2,3,4]}
    assert queue.feedback_key(feedback)==key
    for change in [{'page':2},{'bbox':[1,2,3,5]},{'independent_cell_sha256':'changed'},
                   {'source_pdf_sha256':'different'},{'table_version_id':'table-v1'}]:
        assert queue.feedback_key({**feedback,**change})!=key


@pytest.mark.parametrize('cell,want',[
    ({'text':'A\nB','independent_text':'A B'},'spacing_or_line_wrap'),
    ({'text':'Feeder-\nMinutes','independent_text':'Feeder\x02Minutes'},'source_control_hyphen'),
    ({'text':'','independent_text':'g'},'empty_reader_or_cell_boundary'),
    ({'text':'1 2 3','independent_text':'1 3 2'},'reading_order_or_content_conflict')])
def test_patterns_are_observations_not_rewriting_or_approvals(cell,want):
    original=copy.deepcopy(cell);assert queue.pattern(cell)==want;assert cell==original


def test_invalid_limits_and_existing_output_fail_before_mutation(tmp_path):
    with pytest.raises(ValueError,match='Bounded'):queue.generate_queue('missing',[],'missing',tmp_path/'new',limit=0)
    assert not(tmp_path/'new').exists()
    with pytest.raises(ValueError,match='immutable'):queue.generate_queue('missing',[],'missing',tmp_path)


def test_page_failures_continue_and_zero_questions_are_real_empty_queue(tmp_path,monkeypatch):
    held=tmp_path/'held.jsonl';held.write_text(''.join(json.dumps({'source_pdf_sha256':str(n),'page':1,
          'category':'native_table_layout','only_layout_gate_remaining':True})+'\n'for n in range(3)))
    monkeypatch.setattr(queue,'inspect_pdf2data_profile',lambda p:{'profile_sha256':'profile','source_configuration_expected':{}})
    def fail(*a):raise ValueError('source manifest changed')
    monkeypatch.setattr(queue,'_checked_page',fail)
    out=tmp_path/'output';result=queue.generate_queue(held,[tmp_path/'origin'],tmp_path/'proof',out,limit=8,scan_limit=2)
    assert result['counts']['failed_pages']==2
    assert(out/'review-manifest.jsonl').read_text()==''
    assert len(queue.rows(out/'queue-audit.jsonl'))==2
    assert result['page_quality_cleared']is False and result['remote_operations']==0


def test_no_saved_choice_never_forges_answer(tmp_path):
    manifest=tmp_path/'questions.jsonl';manifest.write_text(json.dumps({'target_cell':{'focus_key':'f'},'question_id':'q'})+'\n')
    feedback=tmp_path/'feedback.jsonl';feedback.write_text('')
    result=queue.consume_answers(manifest,feedback,tmp_path/'output')
    assert result['saved_answers_consumed']==0 and result['pending_questions']==1
    assert not list((tmp_path/'output').glob('question-*/feedback.jsonl'))


def test_conflicting_answer_is_terminal_rejected_not_last_write_wins(tmp_path):
    actual={'kind':'extraction_table_cell_feedback','source_pdf_sha256':'source','page':1,
            'table_version_id':'t','cell_id':'c','selected_cell_sha256':'a',
            'independent_cell_sha256':'b','bbox':[1,2,3,4],'decision':'selected'}
    key=queue.feedback_key(actual)
    manifest=tmp_path/'questions.jsonl';manifest.write_text(json.dumps({'target_cell':{'focus_key':key},'question_id':'q'})+'\n')
    feedback=tmp_path/'feedback.jsonl';feedback.write_text(json.dumps(actual)+'\n'+json.dumps({**actual,'decision':'unclear'})+'\n')
    result=queue.consume_answers(manifest,feedback,tmp_path/'output')
    assert result['jobs'][0]['status']=='rejected'
    assert result['jobs'][0]['reason']=='conflicting_saved_choices'
    assert result['page_quality_cleared']is False


def test_multiple_saved_cells_share_one_terminal_source_revalidation(tmp_path,monkeypatch):
    from types import SimpleNamespace
    package=tmp_path/'source';package.mkdir();(package/'l1-package.json').write_text('{}')
    questions=[];answers=[]
    for i in range(2):
        actual={'kind':'extraction_table_cell_feedback','source_pdf_sha256':'source','page':1,
                'table_version_id':'t','cell_id':str(i),'selected_cell_sha256':'a',
                'independent_cell_sha256':'b','bbox':[1,2,3,4],'decision':'selected'}
        target={**actual,'focus_key':queue.feedback_key(actual),'proof_runtime':'proof','source_runtime':'origin',
                'source_fingerprint':{'proof_profile_sha256':'profile','parent_manifest_sha256':queue.sha(b'{}')},
                'render_path':'source.png','render_sha256':'raster','crop_source_bbox':[0,0,5,6],'render_dpi':240}
        questions.append({'target_cell':target,'question_id':str(i),'source_pdf_sha256':'source','page':1,'l1_package':str(package)})
        answers.append(actual)
    manifest=tmp_path/'questions.jsonl';manifest.write_text(''.join(json.dumps(q)+'\n'for q in questions))
    feedback=tmp_path/'feedback.jsonl';feedback.write_text(''.join(json.dumps(a)+'\n'for a in answers))
    monkeypatch.setattr(queue,'inspect_pdf2data_profile',lambda p:{'profile_sha256':'profile'})
    calls=[]
    def execute(args,**kwargs):
        calls.append(args)
        assert len(queue.rows(args[7]))==2  # one group feedback file, not one isolated decision
        return SimpleNamespace(returncode=0,stdout=json.dumps({'cell_status':'validated','page_proof_status':'held',
            'reason':'outside prose remains unverified','page_quality_cleared':False,'answer_publication_eligible':False}))
    monkeypatch.setattr(queue.subprocess,'run',execute)
    result=queue.consume_answers(manifest,feedback,tmp_path/'output')
    assert len(calls)==1 and result['saved_answers_consumed']==2
    assert result['jobs'][0]['status']=='held'and result['pages_source_quality_cleared']==0
    assert queue.rows(tmp_path/'output/page-001/feedback.jsonl')[0]['decision']=='selected'
