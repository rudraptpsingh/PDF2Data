# Source-image field witnesses

`pdf2data field-witness` retains independent readings of bounded original-PDF regions next to configured-field v2 observations. It proposes field corrections without rewriting native text, page quality, source spans or legal status. This is useful when a Hindi text layer is corrupt while the rendered record remains readable.

## Workflow

1. `prepare --package PACKAGE --fields-bundle FIELDS --rules RULES --output NEW` verifies the native package and configured fields, renders selected physical-page regions and binds each field to its exact native observation and source geometry.
2. `ollama --request REQUEST --host http://127.0.0.1:11434 --model INSTALLED_VISION_TAG --output NEW` sends only the source image and a fixed transcription prompt to the explicitly selected loopback model. It does not pull a model. Raw replies, model digest, completion status and request hashes are retained.
3. `import --request REQUEST --readings READINGS --metadata METADATA --output NEW` imports another reader's exact bytes and declared execution metadata. This is an operator import, not proof that an inference occurred. Every region must match the prepared image hash.
4. `compare --request REQUEST --reading FIRST --reading SECOND --required-reader FIRST_ID --required-reader SECOND_ID --output NEW` compares uniquely selected, complete readings. Required qualifier tokens must survive even when both readers agree.
5. `verify --bundle BUNDLE` verifies hashes, rerenders original regions, rederives native bindings and replays comparisons without calling models.

For a separate source-object/raster component, use
[`native-raster` and `verify-native-raster`](native-scoped-raster.md). These
commands do not establish transcription, language, layout or page acceptance.

Each command supports `--json`. Outputs must be new paths outside immutable inputs. Optional PDF rendering dependencies are needed to prepare or verify; inference requires a separately installed local vision model. Imported readers may use other local OCR engines.

## Rules

Rules use schema `pdf2data.field-witness-rules.v1`, an `id`, `version`, render `dpi` from 120 through 360, a bounded `regions` array and a bounded `fields` array. A region has an `id`, physical `page` and `bbox_pdf_points` in top-left PDF coordinates. A field has its own `id`, `native_field_id`, optional exact `native_observation_id`, `region_id`, a literal selector and optional `required_tokens`. Selectors are `first_line` or `between` with exact `prefix` and `suffix`; ambiguous selections remain unresolved. Rotated pages require an explicit frame mapping and are currently rejected.

External reading JSON contains `request_sha256` and `regions` with `region_id`, `image_sha256`, exact `text` and Boolean `complete`. Reader metadata contains `reader_id`, `engine`, `version` and `source_text_provided: false`; record model or language-weight hashes where available.

## Interpretation

The three comparison states are `consensus_matches_native_literal`, `consensus_correction_candidate` and `reader_conflict_or_incomplete`. NFC and whitespace normalization support comparison; retained proposals remain original reader literals. Reader-local character ranges are not native page offsets. Distinct reader fingerprints do not prove statistical independence. A correct selected title does not validate every number elsewhere in its image. Source quality, translation approval, entity identity and legal applicability require their own review gates.

The feature's replay validates retained provenance and deterministic comparison. It cannot cryptographically prove model execution or the truth of operator-supplied metadata. Do not use reader agreement to clear a source-quality hold automatically.
