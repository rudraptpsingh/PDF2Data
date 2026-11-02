# Ordered operators and layout whitespace

Multiword operators match the same configured words separated by source
whitespace. `shall not`, `shall  not` and `shall\nnot` have the same operator
identity. Literal characters and codepoint spans remain unchanged. Only
whitespace between the existing words is tolerated; missing words, changed
negation, punctuation, joined words and operator reordering remain differences.
Numbers continue through their separate, unchanged gate.

For an existing frozen native L1 package, derive a separate comparison report:

```sh
pdf2data pdf-quality --package ./native-l1 \
  --source-pdf ./native-l1/original.pdf --output ./operator-candidate.json \
  --cached-native-operators --no-source-raster --require-accepted --json
```

This mode verifies the package and reads its exact cached selected/independent
texts. It performs no content extraction, native geometry reproof, OCR or model
call. The new output must be outside the immutable package and must not exist.
Alternate readers, policies, reference files and page selections are refused.

The original quality `results`, components and page outcomes are retained
exactly. `operator_comparison.results` contains the separate candidate token
comparison and exact selected-source/independent-reader spans. Independent
offsets refer to the frozen layout reader text, not the selected page text.
Unsupported Unicode span projection remains held rather than receiving guessed
offsets. Source/package/reading hashes and matcher implementation are bound.

The RERC trial isolates eight false operator disagreements caused by doubled
layout spaces, including `shall  not`, `at  least` and
`whichever  is  higher`. All eight candidate token sequences agree after this
change. All 54 original pages remain held, and `--require-accepted` still exits
`3`. This comparison does not establish semantic equivalence, language, layout,
raster completeness, legal effect or permission to calculate. The separate
[native raster component](native-scoped-raster.md) grants none of those claims.
The [development receipt](validation/rerc158-scoped-native-001.json) keeps the
eight-page comparison separate from raster and linguistic acceptance.
