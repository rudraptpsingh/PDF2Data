# PDF2Data benchmark comparison

Scores use the same frozen source PDFs and external rule labels. Raw and coverage-adjusted scores are separate. Extraction and evaluator times are separate. OS caches were not cleared.

| Label | Profile | Page body | Raw macro | Coverage adjusted | Extraction wall seconds | Evaluation seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| structured-release | layout-vlm-source-tables | tables | 71.75% | 71.75% | 1015.7 | 83.8 |
| recovery-whitespace-v1 | layout-vlm | tables | 71.93% | 71.93% | 49.5 | 81.5 |
| recovery-whitespace-v2-reconciled | layout-vlm-source-tables | tables | 71.93% | 71.93% | 43.9 | 85.5 |
| recovery-guarded-release | layout-vlm-source-tables | tables | 71.93% | 71.93% | 26.4 | 81.3 |

## Per category

| Category | structured-release | recovery-whitespace-v1 | recovery-whitespace-v2-reconciled | recovery-guarded-release |
| --- | ---: | ---: | ---: | ---: |
| arxiv_math | 81.63% | 81.63% | 81.63% | 81.63% |
| baseline | 85.71% | 87.14% | 87.14% | 87.14% |
| headers_footers | 96.88% | 96.88% | 96.88% | 96.88% |
| long_tiny_text | 94.44% | 94.44% | 94.44% | 94.44% |
| multi_column | 65.91% | 65.91% | 65.91% | 65.91% |
| old_scans | 22.41% | 22.41% | 22.41% | 22.41% |
| old_scans_math | 57.35% | 57.35% | 57.35% | 57.35% |
| table_tests | 69.64% | 69.64% | 69.64% | 69.64% |

Tables are additive extracted candidates; inline placement and duplicate suppression are not established by this score. The text ablation reuses the same extraction and must not be timed as a fresh parser run.

The baseline category is separate. A public full-suite claim requires complete_public_dataset=true in the frozen suite. Custom probes are not an independent legal accuracy benchmark.
