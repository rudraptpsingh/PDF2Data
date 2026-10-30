# PDF2Data benchmark comparison

Scores use the same frozen source PDFs and external rule labels. Raw and coverage-adjusted scores are separate. Extraction and evaluator times are separate. OS caches were not cleared.

| Label | Profile | Page body | Raw macro | Coverage adjusted | Extraction wall seconds | Evaluation seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| native-release | native | tables | 27.68% | 15.91% | 11.5 | 30.9 |
| auto-tables-release | auto | tables | 21.53% | 21.53% | 40.8 | 32.3 |
| layout-vlm-v1 | layout-vlm | tables | 65.65% | 65.65% | 1252.8 | 103.0 |
| layout-vlm-v2 | layout-vlm | tables | 71.75% | 71.75% | 1015.7 | 112.4 |
| structured-release | layout-vlm-source-tables | tables | 71.75% | 71.75% | 1015.7 | 83.8 |

## Per category

| Category | native-release | auto-tables-release | layout-vlm-v1 | layout-vlm-v2 | structured-release |
| --- | ---: | ---: | ---: | ---: | ---: |
| arxiv_math | 0.00% | 0.00% | 67.35% | 81.63% | 81.63% |
| baseline | 64.29% | 81.43% | 81.43% | 85.71% | 85.71% |
| headers_footers | 25.00% | 25.00% | 87.50% | 96.88% | 96.88% |
| long_tiny_text | 0.00% | 12.96% | 94.44% | 94.44% | 94.44% |
| multi_column | 2.27% | 6.82% | 56.82% | 65.91% | 65.91% |
| old_scans | 0.00% | 10.34% | 22.41% | 22.41% | 22.41% |
| old_scans_math | 0.00% | 0.00% | 45.59% | 57.35% | 57.35% |
| table_tests | 35.71% | 35.71% | 69.64% | 69.64% | 69.64% |

Tables are additive extracted candidates; inline placement and duplicate suppression are not established by this score. The text ablation reuses the same extraction and must not be timed as a fresh parser run.

The baseline category is separate. A public full-suite claim requires complete_public_dataset=true in the frozen suite. Custom probes are not an independent legal accuracy benchmark.
