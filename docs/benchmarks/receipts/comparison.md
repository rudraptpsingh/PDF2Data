# PDF2Data benchmark comparison

Scores use the same frozen source PDFs and external rule labels. Raw and coverage-adjusted scores are separate. Extraction and evaluator times are separate. OS caches were not cleared.

| Label | Profile | Page body | Raw macro | Coverage adjusted | Extraction wall seconds | Evaluation seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| native-before | native | tables | 27.50% | 15.73% | 12.5 | 31.6 |
| native-release | native | tables | 27.68% | 15.91% | 11.5 | 30.9 |
| auto-tables-release | auto | tables | 21.53% | 21.53% | 40.8 | 32.3 |
| auto-text-release | auto | text | 16.68% | 16.68% | 40.8 | 33.0 |

## Per category

| Category | native-before | native-release | auto-tables-release | auto-text-release |
| --- | ---: | ---: | ---: | ---: |
| arxiv_math | 0.00% | 0.00% | 0.00% | 0.00% |
| baseline | 62.86% | 64.29% | 81.43% | 81.43% |
| headers_footers | 25.00% | 25.00% | 25.00% | 21.88% |
| long_tiny_text | 0.00% | 0.00% | 12.96% | 12.96% |
| multi_column | 2.27% | 2.27% | 6.82% | 6.82% |
| old_scans | 0.00% | 0.00% | 10.34% | 10.34% |
| old_scans_math | 0.00% | 0.00% | 0.00% | 0.00% |
| table_tests | 35.71% | 35.71% | 35.71% | 0.00% |

Tables are additive extracted candidates; inline placement and duplicate suppression are not established by this score. The text ablation reuses the same extraction and must not be timed as a fresh parser run.

The baseline category is separate. A public full-suite claim requires complete_public_dataset=true in the frozen suite. Custom probes are not an independent legal accuracy benchmark.
