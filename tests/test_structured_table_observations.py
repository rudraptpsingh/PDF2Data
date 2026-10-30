import copy
import unittest
from local_vector_batch.structured_table_observations import financial_year_range,load_rules,table_observations,page_spans

class FinancialYears(unittest.TestCase):
 def test_cross_language_literal_range(self):
  for raw in ('FY 2024-25 to\nFY 2028-29','वि.व. 2024-25 से वि.व. 2028-29','जि.ि. 2024-25 से जि.ि. 2028-29'):
   result=financial_year_range(raw)
   self.assertEqual(result['inclusive_financial_years'],5);self.assertIsNone(result['calendar_interval']);self.assertEqual(result['semantic_state'],'candidate_requires_review')
 def test_calendar_requires_explicit_policy(self):
  result=financial_year_range('FY 2024-25 to FY 2028-29',calendar_policy='india_april_financial_year')
  self.assertEqual(result['calendar_interval']['start'],'2024-04-01');self.assertEqual(result['calendar_interval']['end_inclusive'],'2029-03-31')
  with self.assertRaises(ValueError):financial_year_range('FY 2024-25 to FY 2028-29',calendar_policy='guess')
 def test_rollover_and_single_year_range(self):
  self.assertEqual(financial_year_range('FY 2099-00 to FY 2099-00')['inclusive_financial_years'],1)
 def test_bad_ranges_are_not_interpreted(self):
  for raw in ('FY 2024-26 to FY 2028-29','FY 2028-29 to FY 2024-25','FY 2024-25 to FY 2200-01'):
   with self.assertRaises(ValueError):financial_year_range(raw)
  for raw in ('2024-25 to 2028-29','within 5 years','FY 2024-25 and FY 2028-29','FY 2024-25 to FY 2028-29 unless renewed','FY 2024-25'):
   self.assertIsNone(financial_year_range(raw))
 def test_configurable_prefix_and_no_unicode_loss(self):
  rules=copy.deepcopy(load_rules()[0]);rules['financial_year_range']['prefixes'].append('वित्तीय वर्ष')
  result=financial_year_range('वित्तीय वर्ष 2024-25 से वित्तीय वर्ष 2028-29',rules)
  self.assertIn('वित्तीय',result['raw_text'])
 def fixture(self):
  raw=[['Year','Some station Unit 8'],['FY 2024-25 to FY 2028-29','42.52']]
  cells=[{'id':f'{r}:{c}','row':r,'column':c,'text':text,'bbox':[c*100,100+r*20,c*100+100,120+r*20]}for r,row in enumerate(raw)for c,text in enumerate(row)]
  table={'id':'tableA','page':7,'bbox':[0,100,200,140],'cell_spans':cells,'unresolved_grid_slots':[]}
  lines=[{'text':'(in Rs Lakh/MW)','x0':80,'x1':190,'top':83,'bottom':94}]
  text='(in Rs Lakh/MW)\nYear Some station Unit 8\nFY 2024-25 to FY 2028-29 42.52'
  return table,lines,text
 def extract(self,table,lines,text):return table_observations(table,text,lines,source_pdf_sha256='a'*64)
 def test_same_table_unit_subject_without_totals_or_client_facts(self):
  t,l,text=self.fixture();r=self.extract(t,l,text)[0]
  self.assertEqual(r['quantity']['decimal_value'],'42.52');self.assertEqual(r['quantity']['unit_id'],'INR_lakh_per_MW');self.assertEqual(r['subject']['raw_header'],'Some station Unit 8')
  self.assertIsNone(r['period_total']);self.assertIsNone(r['deadline']);self.assertEqual(r['arithmetic_performed'],[]);self.assertFalse(r['verified_client_fact']);self.assertFalse(r['publication_eligible'])
 def test_missing_or_ambiguous_unit_held(self):
  t,l,text=self.fixture();self.assertEqual(self.extract(t,[],text),[]);self.assertEqual(self.extract(t,l+l,text),[])
 def test_no_unit_borrow_across_page_distance_or_horizontal_scope(self):
  t,l,text=self.fixture();l[0]['bottom']=10;self.assertEqual(self.extract(t,l,text),[])
  l[0].update(bottom=94,x0=300,x1=400);self.assertEqual(self.extract(t,l,text),[])
 def test_blank_header_merged_or_duplicate_cell_rejected(self):
  for kind in ('blank','merged','duplicate','grid'):
   t,l,text=self.fixture()
   if kind=='blank':t['cell_spans'][1]['text']=''
   elif kind=='merged':t['cell_spans'][1]['column_span']=2
   elif kind=='duplicate':t['cell_spans'].append(copy.deepcopy(t['cell_spans'][1]))
   else:t['unresolved_grid_slots']=[{}]
   self.assertEqual(self.extract(t,l,text),[])
 def test_non_numeric_or_percentage_values_not_relabelled(self):
  for raw in ('42.52%','42.52 × 5','-','42,52','42.52 or 45'):
   t,l,text=self.fixture();t['cell_spans'][3]['text']=raw;self.assertEqual(self.extract(t,l,text),[])
 def test_evidence_preserves_exact_discontiguous_page_spans(self):
  result=page_spans('FY 2024-25 to\nFY 2028-29','FY 2024-25 to\n42.52\nFY 2028-29')
  self.assertEqual(result['status'],'exact_unique_segments');self.assertEqual(len(result['spans']),2)
  self.assertEqual(page_spans('42.52','42.52 42.52')['status'],'not_uniquely_locatable')
 def test_parallel_agreement_does_not_claim_translation(self):
  from local_vector_batch.structured_table_observations import compare_parallel_candidates
  t,l,text=self.fixture();a=self.extract(t,l,text)[0];b=copy.deepcopy(a);b['observation_id']='other';b['subject']['raw_header']='अन्य स्तम्भ'
  result=compare_parallel_candidates(a,b);self.assertTrue(result['range_quantity_unit_agree']);self.assertFalse(result['subject_translation_verified']);self.assertFalse(result['legal_equivalence_verified'])
 def test_profile_and_payload_tampering_fails_fresh_verifier(self):
  from local_vector_batch.structured_table_observations import verify_observations
  with self.assertRaises(ValueError):verify_observations({'contract_version':'wrong'},'/nonexistent')
 def test_output_cannot_overwrite_source(self):
  from local_vector_batch.structured_table_observations import write_observations
  with self.assertRaises(ValueError):write_observations('/tmp/l1-source',[1],'/tmp/l1-source/new-observations.json')
if __name__=='__main__':unittest.main()
