# Correction ledger

One JSON file per notice, named by the Dashen feed id, listing every human
decision about that notice's OCR text. The format, the freshness rule and the
`crop` / `verify` commands are documented in the repository
[README](../../../README.md#the-correction-ledger).

```bash
python -m tools.cli verify          # every entry against the raw OCR
python -m tools.cli crop <notice> <image> <block>
```

The raw engine output under `derived/ocr/dashen/` is immutable; a correction
records the reading itself, so the ledger can be re-applied at any time.
