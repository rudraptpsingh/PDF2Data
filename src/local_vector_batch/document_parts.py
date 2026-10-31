"""Physical labels and explicit bundle-part markers, without inventing scopes."""
from collections import defaultdict
import re

from .document_blocks import digest
from .document_metadata import DIGITS, normalize_date
from .layout_furniture import margin_segments
from .table_contexts import text_evidence

PAGE_LABEL = re.compile(r'^(?:(?:page|पृष्ठ|पृष्‍ठ)\s*[:|.-]?\s*)?(\d+|[ivxlcdm]+)(?:\s*(?:of|/|में से)\s*(\d+))?$',re.I)
PART = re.compile(r'^\s*(Annexure|Annex|Appendix|Schedule|अनुलग्नक|परिशिष्ट|अनुसूची)\s*[-–:]?\s*([A-Za-z0-9०-९ivxlcdm]+)\s*[:.-]?\s*$',re.I)
LANGUAGE = re.compile(r'^\s*(English|Hindi|अंग्रेजी|हिंदी|हिन्दी)\s*(?:version|translation|text|अनुवाद|पाठ)?\s*$',re.I)


def build_document_parts(data, metadata, elements):
    pages={p['page']:p for p in data['pages']};layouts={p['page']:p for p in data.get('layout',[])}
    labels=[];markers=[];clusters=[]
    for number,layout in layouts.items():
        width,height=layout.get('width'),layout.get('height')
        if not width or not height:continue
        tables=[t['bbox']for t in data.get('tables',[])if t['page']==number and t.get('bbox')]
        for segment in margin_segments(layout):
            raw=segment['text'];match=PAGE_LABEL.fullmatch(raw.strip())
            if not match:continue
            b=segment['bbox'];center=(b[0]+b[2])/2
            if not(re.match(r'^(?:page|पृष्ठ|पृष्‍ठ)\b',raw,re.I) or center<width*.18 or center>width*.82 or abs(center-width/2)<width*.05):continue
            if any(min(b[2],t[2])>max(b[0],t[0]) and min(b[3],t[3])>max(b[1],t[1])for t in tables):continue
            value=match[1].translate(DIGITS)
            record=dict(physical_page=number,bbox=b,printed_label=value,
                printed_number=int(value)if value.isdecimal()else None,
                printed_total=int(match[2].translate(DIGITS))if match[2]else None,
                evidence_basis='native_margin_glyphs',**text_evidence(raw,pages[number]['text']),
                numbering_identity_verified=False)
            record['id']='page-label-'+digest(record)[:24];labels.append(record)
    for element in elements:
        if element.get('story')!='body' or element.get('quote_id') or element.get('role_candidate')=='quotation':continue
        raw=element['text'];match=PART.fullmatch(raw.strip());language=LANGUAGE.fullmatch(raw.strip())
        if not match and not language:continue
        record=dict(element_id=element['id'],physical_page=element['source_spans'][0]['page'],
                    kind='annexure_or_schedule_marker'if match else 'language_marker',
                    literal_label=match[2]if match else language[1],
                    source_spans=element['source_spans'],bbox=element.get('bbox'),
                    status='explicit_marker_candidate',end_boundary=None,semantic_scope_verified=False)
        record['id']='part-marker-'+digest(record)[:24];markers.append(record)
    fields=defaultdict(list)
    for field in metadata['fields']:
        box=field.get('geometry',{} ) or {}
        # Repeated top headers still carry identity. Margin position excludes
        # footers; repetition must not manufacture a new instrument boundary.
        if not box.get('bbox_normalized') or box['bbox_normalized'][3]>.5:continue
        if field['role_candidate']in ('document_reference','document_date','decision_date','case_number','appeal_number'):
            # Labels already reject narrative prefixes. Exclude table cells too.
            b=box['bbox'];number=field['source_span']['page']
            anchor=field['source_span']
            if any((e.get('quote_id')or e.get('role_candidate')=='quotation') and any(s['page']==number and s['char_start']<=anchor['char_start']<s['char_start']+s['char_length']for s in e['source_spans'])for e in elements):continue
            if field['role_candidate']in ('document_reference','case_number','appeal_number'):
                text=pages[number]['text'];start=anchor['char_start'];line_start=text.rfind('\n',0,start)+1
                if text[line_start:start].strip():continue
            if any(t['page']==number and t.get('bbox') and min(b[2],t['bbox'][2])>max(b[0],t['bbox'][0]) and min(b[3],t['bbox'][3])>max(b[1],t['bbox'][1])for t in data.get('tables',[])):continue
            fields[number].append(field)
    previous=None
    for number,items in sorted(fields.items()):
        identifiers=[f for f in items if f['role_candidate']in ('document_reference','case_number','appeal_number')]
        dates=[f for f in items if f['role_candidate']in ('document_date','decision_date')]
        for identifier in identifiers:
            b=identifier['geometry']['bbox'];near=[d for d in dates if abs(d['geometry']['bbox'][1]-b[1])<=48]
            if not near:continue
            normalized_dates={d.get('normalization',{}).get('iso')for d in near}
            key=re.sub(r'\s+','',identifier['value'].translate(DIGITS)).casefold()
            record=dict(physical_page=number,identifier_field_id=identifier['id'],date_field_ids=[d['id']for d in near],
                literal_identifier=identifier['value'],date_state='unambiguous_candidate'if len(normalized_dates)==1 and None not in normalized_dates else 'unresolved_or_conflicting',
                date_candidate=next(iter(normalized_dates))if len(normalized_dates)==1 and None not in normalized_dates else None,
                previous_header_candidate_id=previous['id']if previous else None,
                relationship_to_previous='first_header_candidate',source_field_ids=[identifier['id']]+[d['id']for d in near],
                status='header_cluster_candidate',semantic_scope_verified=False,end_boundary=None)
            if previous:
                previous_key=re.sub(r'\s+','',previous['literal_identifier'].translate(DIGITS)).casefold()
                record['relationship_to_previous']='different_literal_identifier_unresolved_bundle_boundary'if key!=previous_key else 'same_identifier_conflicting_or_unknown_date'if not record['date_candidate'] or record['date_candidate']!=previous['date_candidate'] else 'repeated_identity_candidate'
            record['id']='header-cluster-'+digest(record)[:24];clusters.append(record);previous=record
    declared=data.get('source',{}).get('declared',{});checks=[]
    for cluster in clusters:
        if declared.get('document_reference'):
            expected=re.sub(r'\s+','',str(declared['document_reference']).translate(DIGITS)).casefold()
            actual=re.sub(r'\s+','',cluster['literal_identifier'].translate(DIGITS)).casefold()
            checks.append(dict(header_candidate_id=cluster['id'],catalog_field='document_reference',
                               catalog_literal=declared['document_reference'],status='literal_match_candidate'if actual==expected else 'literal_conflict_requires_review'))
        if declared.get('document_date'):
            normalized=normalize_date(str(declared['document_date'])).get('iso')
            checks.append(dict(header_candidate_id=cluster['id'],catalog_field='document_date',catalog_literal=declared['document_date'],
                               status='not_comparable'if not normalized or not cluster['date_candidate'] else 'normalized_match_candidate'if normalized==cluster['date_candidate']else 'date_conflict_requires_review'))
    return dict(schema_version='pdf2data.document-parts.v1',source_pdf_sha256=data['source_pdf_sha256'],
                printed_page_labels=labels,explicit_markers=markers,header_clusters=clusters,catalog_checks=checks,
                semantic_parts=[],scope_policy='explicit_marker_and_header_candidates_only_no_automatic_page_ranges',
                bilingual_text_equivalence_verified=False,publication_eligible=False)
