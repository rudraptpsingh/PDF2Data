"""Select an actual OCR model from source-bound independent reading evidence.

This configures a new candidate only. Script/angle decisions do not replace
normal independent literal, numeric/operator, source-raster and layout gates.
"""
import hashlib,json
from pathlib import Path


def rules():return json.loads(Path(__file__).with_name('ocr_selection_rules.json').read_text())


def select(independent,*,source_pdf_sha256,page,source_image_sha256,orientation_review=None,policy=None):
    policy=policy or rules();text=independent['text']
    if (independent.get('source_pdf_sha256')!=source_pdf_sha256 or independent.get('page')!=page or
        independent.get('source_image_sha256')!=source_image_sha256 or
        independent.get('text_sha256')!=hashlib.sha256(text.encode()).hexdigest()):
        raise ValueError('Independent OCR selection evidence is stale/unbound')
    latin=sum(c.isascii()and c.isalpha()for c in text);deva=sum('\u0900'<=c<='\u097f'for c in text)
    observed='devanagari_or_mixed'if deva>=policy['minimum_script_characters']else'latin'if latin>=policy['minimum_latin_characters']else'unknown'
    language=policy['language_by_observed_script'].get(observed)
    confidence=independent.get('mean_confidence',0)
    needs_review=observed=='unknown'or confidence<policy['minimum_reader_confidence']
    angle=None;basis='provider_default_no_source_orientation_claim'
    if orientation_review is not None:
        if (orientation_review.get('source_pdf_sha256')!=source_pdf_sha256 or orientation_review.get('page')!=page or
            orientation_review.get('source_image_sha256')!=source_image_sha256 or
            orientation_review.get('origin')not in policy['orientation_review_origins']or
            orientation_review.get('source_orientation_degrees')!=0 or
            orientation_review.get('human_approval')is not False):
            raise ValueError('Orientation review does not bind this exact upright source image')
        angle=False;basis='actual_original_raster_upright_model_assisted_review_no_human_claim'
    else:
        candidate=policy.get('upright_candidate_from_independent_reader_geometry',{})
        dimensions=independent.get('source_image_dimensions');rotation=independent.get('source_pdf_rotation_degrees')
        long=[];horizontal=[]
        if isinstance(dimensions,list)and len(dimensions)==2 and all(type(v)is int and v>0 for v in dimensions):
            for line in independent.get('lines',[]):
                if len(line.get('text','').strip())<candidate.get('minimum_line_characters',20):continue
                box=line.get('bbox')
                if (not isinstance(box,list)or len(box)!=4 or any(type(v)not in(int,float)or not 0<=v<=1 for v in box)or
                    box[0]>=box[2]or box[1]>=box[3]):
                    horizontal=[];long=[];break
                long.append(line)
                if (box[2]-box[0])*dimensions[0]>=(box[3]-box[1])*dimensions[1]*candidate.get('minimum_horizontal_pixel_aspect',3):horizontal.append(line)
        if (candidate.get('enabled')is True and type(rotation)is int and rotation==candidate['required_declared_PDF_Rotate']and
            confidence>=candidate['minimum_confidence']and len(long)>=candidate['minimum_long_lines']and
            len(horizontal)/len(long)>=candidate['minimum_horizontal_fraction']):
            angle=False;basis='unverified_upright_candidate_from_independent_reader_geometry_requires_all8_normal_gates'
    return {'contract_version':policy['contract_version'],'observed_script':observed,
        'observed_character_counts':{'latin':latin,'devanagari':deva},'independent_confidence':confidence,
        'ocr_language':language,'ocr_use_angle_classifier':angle,'orientation_basis':basis,
        'orientation_proved':orientation_review is not None,
        'requires_script_exception_review':needs_review,'quality_approval':False,
        'source_pdf_sha256':source_pdf_sha256,'page':page,'source_image_sha256':source_image_sha256,
        'independent_text_sha256':independent['text_sha256'],
        'rules_sha256':hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
