"""Closed source-header rule dispatch; every rule must prove original blocks."""


def prove_header_gap(line,native,page,table_boxes,textpage,layout,*,pdfium_page=None,header_tables=None,table_proofs=None):
    from .native_metadata_rows import prove_header_metadata
    from .native_blank_form_fields import prove_blank_form_header
    from .native_repeated_headers import prove_repeated_source_header
    from .native_literal_form_rows import prove_literal_form_row
    common=dict(pdfium_page=pdfium_page,header_tables=header_tables)
    rules={'reference_and_date':lambda:prove_header_metadata(line,native,page,table_boxes,textpage,layout,**common),
           'unfilled_form_field':lambda:prove_blank_form_header(line,native,page,table_boxes,textpage,layout,table_proofs=table_proofs,**common),
           'repeated_source_page_header':lambda:prove_repeated_source_header(line,native,page,table_boxes,textpage,layout,**common),
           'literal_form_label_and_markers':lambda:prove_literal_form_row(line,native,page,table_boxes,textpage,layout,**common)}
    holds={}
    for rule,prove in rules.items():
        try:return prove()
        except ValueError as error:holds[rule]=str(error)
    raise ValueError('No configured source-header proof matched: '+str(holds))
