# Legacy font review

Native readers can agree on Latin character codes while the PDF displays Hindi.
PDF2Data checks the actual source font inventory as well as Unicode script.
The configured exact families include KrutiDev010 and the supported Chanakya
variants. PDF subset prefixes and case are normalized; arbitrary substrings and
words in the page text do not select a font route.

New captures retain the observed `text_script` separately from
`script_verification`. Known font requirements survive quality assessment and
machine-decision application. An unavailable font inventory requires review.
Neither font detection nor agreement decodes the source or grants language,
translation or legal acceptance.

## Real source check

The CERC additional-information request dated 3 August 2026, Petition
935/GT/2025, has 83 nonblank KrutiDev010 glyphs on page 1. The preserved rc15
quality result called its text Latin and passed language. The rc18 reassessment
retains the original text and reports:

```json
{
  "state": "unknown",
  "reason": "native_complex_script_glyph_mapping_unverified",
  "detected_script": "latin",
  "required_scripts": ["devanagari"],
  "source_font_evidence": {
    "observed_font_glyph_counts": {"krutidev010": 83},
    "decoded_text_modified": false
  }
}
```

This is a language-review correction, not a recovered Hindi transcription. The
source also needs separate image, table and reading-order proof. Old packages,
quality receipts and active job pins remain unchanged.

```sh
pdf2data pdf-quality --package "$EXTRACTION" --source-pdf "$ORIGINAL" \
  --pages 1 --output new-quality.json --require-accepted --json
```

The command writes a new report outside the extraction package and exits 3 when
an assessed page remains held. Existing receipts must not be overwritten.

Exact released rc17 decoder custody is registered for the existing supported
native-table and whitefill operations. Replay verifies the captured operation;
it does not promote a frozen quality policy into current language approval.
Consumers must explicitly qualify a new role or quality revision before use.
