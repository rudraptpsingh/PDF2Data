from local_vector_batch.layout_furniture import furniture_candidates,observed_margins,formatting


def row(text,x,y,size=8):
    return [dict(text=c,bbox=[x+i*4,y,x+i*4+4,y+size],font='Test',size=size) for i,c in enumerate(text)]


def test_repeated_margin_footer_and_separate_page_label_retain_literal_positions():
    pages=[dict(width=200,height=400,glyphs=row('Repeated footer',20,370)+row('Page '+str(n),155,370)) for n in (1,2)]
    repeated=observed_margins(pages)
    candidates=furniture_candidates(pages[0],[],repeated,'Repeated footer\nPage 1')
    assert {r['kind'] for r in candidates}=={'footer','page_number'}
    assert all(r['parsed_text_aligned'] for r in candidates)
    assert next(r for r in candidates if r['kind']=='page_number')['text']=='Page 1'


def test_bottom_body_line_and_left_footnote_marker_are_not_guessed_as_footer():
    page=dict(width=200,height=400,glyphs=row('Ordinary one-off body line',20,360)+row('1',20,390))
    assert furniture_candidates(page,[],observed_margins([page]),'')==[]


def test_unaligned_margin_observation_does_not_claim_a_parsed_span():
    pages=[dict(width=200,height=400,glyphs=row('Repeated footer',20,370)) for _ in (1,2)]
    candidates=furniture_candidates(pages[0],[],observed_margins(pages),'Different source reading')
    assert len(candidates)==1 and candidates[0]['parsed_text_aligned'] is False


def test_source_alignment_records_indents_without_claiming_justification():
    regions=[dict(type='text',bbox=[20,80,180,100]),dict(type='heading',bbox=[60,40,140,50]),dict(type='text',bbox=[110,120,180,130])]
    formatting(regions,200,400)
    assert regions[0]['formatting']['alignment_candidate']=='left_or_justified_unresolved'
    assert regions[1]['formatting']['alignment_candidate']=='center_candidate'
    assert regions[2]['formatting']['alignment_candidate']=='right_candidate'
    assert not regions[0]['formatting']['classification_verified']
