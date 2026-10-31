"""Reproducible bilingual keyword frequencies from verified immutable L1 packages."""
from collections import Counter
from pathlib import Path
import csv,hashlib,json,re,tempfile
from .jsonl import jsonl_physical_lines
from .document_metadata import extract_metadata

KEYWORDS={
 'reference_label':{'en':r'\b(?:File\s+No\.?|Ref(?:erence)?\.?|No\.)','hi':r'संदर्भ|(?:फाइल|फ़ाइल|पत्र)\s*(?:संख्या|सं\.)|संख्या|सं०|सं\.'},
 'dated':{'en':r'\bDated\b','hi':r'दिनांक|तारीख|तिथि'},
 'order_date':{'en':r'\bDate\s+of\s+(?:Order|Judg(?:e)?ment|Decision)\b','hi':r'(?:आदेश|निर्णय)\s*(?:की\s*)?(?:तिथि|तारीख|दिनांक)'},
 'hearing_date':{'en':r'\bDate\s+of\s+Hearing\b','hi':r'सुनवाई\s*(?:की\s*)?(?:तिथि|तारीख|दिनांक)'},
 'notification':{'en':r'\bnotification\b','hi':r'अधिसूचना'},
 'draft':{'en':r'\bdraft\b','hi':r'प्रारूप|मसौदा'},
 'notice':{'en':r'\b(?:public\s+)?notice\b','hi':r'सार्वजनिक\s*सूचना|नोटिस'},
 'memorandum':{'en':r'\bexplanatory\s+memorandum\b','hi':r'व्याख्यात्मक\s*(?:ज्ञापन|विवरण)'},
 'statement_of_reasons':{'en':r'\bstatement\s+of\s+(?:(?:objects?\s*(?:and|&)\s*)?reasons)\b','hi':r'कारणों\s*का\s*(?:विवरण|कथन)|उद्देश्यों\s*और\s*कारणों'},
 'coram':{'en':r'\bcoram\b','hi':r'पीठ|समक्ष'},
 'petitioner':{'en':r'\bpetitioner\b','hi':r'याचिकाकर्ता'},
 'respondent':{'en':r'\brespondents?\b','hi':r'प्रतिवादी|उत्तरदाता'},
 'judgment':{'en':r'\bjudg(?:e)?ment\b','hi':r'निर्णय'},
 'proceedings':{'en':r'\brecord\s+of\s+proceedings\b','hi':r'कार्यवाही\s*का\s*(?:अभिलेख|विवरण)'},
 'commencement':{'en':r'\bcommencement\b|come\s+into\s+force','hi':r'प्रवर्तन|प्रारंभ|लागू\s*होग'},
 'waiver':{'en':r'\bwaivers?\b','hi':r'छूट|माफी'},
 'charge':{'en':r'\bcharges?\b','hi':r'शुल्क|प्रभार'},
 'rate':{'en':r'\brates?\b','hi':r'(?<![\u0900-\u097F])दर(?:ें|ों)?(?![\u0900-\u097F])'},
 'table':{'en':r'\btable\s*\d*','hi':r'सारणी|तालिका'},
 'illustration':{'en':r'\b(?:illustration|example)\s*\d*','hi':r'उदाहरण|दृष्टांत'},
 'comments':{'en':r'\bcomments?\b|\bobjections?\b','hi':r'टिप्पणि|आपत्ति'},
}


def profile_documents(manifest,output):
    from .legal_l1 import verify_document
    manifest=Path(manifest).resolve();output=Path(output).absolute()
    if output.exists() or output.is_symlink():raise ValueError('profile output must be a new directory')
    config=json.loads(manifest.read_text())
    if config.get('schema_version')!='pdf2data.profile-input.v1' or not isinstance(config.get('packages'),list) or not config['packages']:raise ValueError('invalid profile input manifest')
    sources=[];seen=set();skipped=[]
    for item in config['packages']:
        package=(manifest.parent/item['package']).resolve()
        if output.resolve()==package or package in output.resolve().parents:raise ValueError('profile output must not overwrite source packages')
        verify_document(package)
        source=json.loads((package/'source-receipt.json').read_text());sha=source['actual_sha256']
        if sha in seen:skipped.append(dict(id=item.get('id'),source_pdf_sha256=sha,reason='duplicate_pdf_hash'));continue
        seen.add(sha)
        def records(path):return [json.loads(row) for row in jsonl_physical_lines(path.read_text()) if row.strip(' \t\r')]
        pages=records(package/'extraction/pages.jsonl')
        data=dict(source_pdf_sha256=sha,source=source,pages=pages,layout=records(package/'layout.jsonl'),structure=json.loads((package/'structure.json').read_text()))
        counts={concept:{lang:0 for lang in aliases} for concept,aliases in KEYWORDS.items()}
        page1={concept:{lang:0 for lang in aliases} for concept,aliases in KEYWORDS.items()}
        for page in pages:
            for concept,aliases in KEYWORDS.items():
                for lang,pattern in aliases.items():
                    n=len(list(re.finditer(pattern,page['text'],re.I)));counts[concept][lang]+=n
                    if page['page']==1:page1[concept][lang]+=n
        quality=json.loads((package/'quality.json').read_text())
        sources.append(dict(id=item.get('id',source['declared']['id']),source_pdf_sha256=sha,
                            declared_document_type=source['declared']['family'],pages=len(pages),
                            page_text_sha256=[p['text_sha256'] for p in pages],
                            whitespace_tokens=sum(len(re.findall(r'\S+',p['text'])) for p in pages),
                            quality_flagged_pages=sum(q['outcome']!='auto_accepted' for q in quality['results']),
                            keyword_counts_including_furniture=counts,first_page_keyword_counts=page1,
                            metadata=extract_metadata(data)))
    groups=[]
    for family in sorted({s['declared_document_type'] for s in sources}):
        docs=[s for s in sources if s['declared_document_type']==family];tokens=sum(s['whitespace_tokens'] for s in docs);keywords={}
        for concept,aliases in KEYWORDS.items():
            keywords[concept]={}
            for lang in aliases:
                occurrences=sum(s['keyword_counts_including_furniture'][concept][lang] for s in docs)
                keywords[concept][lang]=dict(documents_with_keyword=sum(s['keyword_counts_including_furniture'][concept][lang]>0 for s in docs),
                    documents_in_type=len(docs),occurrences_including_furniture=occurrences,
                    first_page_occurrences=sum(s['first_page_keyword_counts'][concept][lang] for s in docs),
                    occurrences_per_1000_whitespace_tokens=round(1000*occurrences/tokens,3) if tokens else None)
        groups.append(dict(document_type=family,documents=len(docs),pages=sum(s['pages'] for s in docs),keywords=keywords,
                           primary_field_candidate_documents={role:sum(s['metadata']['primary_fields'][role]['status']=='candidate' for s in docs) for role in docs[0]['metadata']['primary_fields']}))
    report=dict(schema_version='pdf2data.document-profiles.v1',input_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                unique_documents=len(sources),physical_pages=sum(s['pages'] for s in sources),duplicate_sources_skipped=skipped,
                counting_policy='English/Hindi regex alias occurrences, including furniture; document frequency is binary per unique source hash',
                learning_policy='descriptive sample frequencies; no automatic rule training, type classification or date-role approval',
                supported_aliases=KEYWORDS,by_document_type=groups,
                documents=[{k:v for k,v in s.items() if k!='metadata'} for s in sources],publication_eligible=False)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pdf2data-profiles-',dir=output.parent) as tmp:
        stage=Path(tmp)/'bundle';stage.mkdir();(stage/'metadata').mkdir()
        (stage/'profiles.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        for s in sources:(stage/'metadata'/f'{s["source_pdf_sha256"]}.json').write_text(json.dumps(s['metadata'],ensure_ascii=False,indent=2)+'\n')
        with (stage/'keyword-frequencies.csv').open('w',newline='') as stream:
            writer=csv.writer(stream,lineterminator='\n');writer.writerow(['document_type','concept','language','documents_with_keyword','documents_in_type','occurrences_including_furniture','first_page_occurrences','occurrences_per_1000_whitespace_tokens'])
            for group in groups:
                for concept,langs in group['keywords'].items():
                    for lang,stats in langs.items():writer.writerow([group['document_type'],concept,lang,*stats.values()])
        rows=['# Local document keyword profiles','',f'{len(sources)} unique PDFs · {report["physical_pages"]} physical pages. Source-declared types; keyword observations are not extraction accuracy.','',
              '| Type | Documents | Pages | Reference candidate | Document date candidate | Decision date candidate | Hearing date candidate |','| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
        for g in groups:
            f=g['primary_field_candidate_documents'];rows.append(f'| {g["document_type"]} | {g["documents"]} | {g["pages"]} | {f["document_reference"]} | {f["document_date"]} | {f["decision_date"]} | {f["hearing_date"]} |')
        rows+=['','`keyword-frequencies.csv` and `profiles.json` contain English and Hindi counts separately. Repeated footers affect occurrence counts, but not unique-document counts. Small and uneven type samples and OCR language gaps limit conclusions. Missing Hindi matches do not establish absence in the PDF.','',
               'Each metadata file retains original language, source spans, geometry, date ambiguities and bilingual correlation candidates. No raw text or review status was changed.']
        (stage/'report.md').write_text('\n'.join(rows)+'\n')
        if output.exists():raise ValueError('profile output appeared during generation')
        stage.rename(output)
    return dict(output=str(output),unique_documents=len(sources),pages=report['physical_pages'],document_types=len(groups),profiles=str(output/'profiles.json'),frequencies=str(output/'keyword-frequencies.csv'),report=str(output/'report.md'),recognition_rerun=False,publication_eligible=False)
