# Parity Workbench & A1111 Metadata

## Design Decisions

### EXIF-first metadata authority
Parity and A1111 bridge select A1111 metadata from EXIF-first sources (exif_data_fresh/exif_data/sidecar/db before merged) and only hydrate missing fields from secondary candidates.

### Regional Prompting forces manual intervention
Parity classification is forced to `needs_manual_intervention` when RP/Regional Prompting signals are detected — these are not yet fully supported for automatic parity matching.

## Key Files
- `app/backend/main.py` — parity logic (large inline sections)
- `app/backend/services/a1111_parser_service.py` — A1111 metadata parsing
- `app/backend/services/metadata_extraction.py` — metadata extraction pipeline

## Gotchas
- Parity logic lives in large inline sections of `main.py` — not yet extracted to a service
- Unsupported features (RP, Regional Prompting) gate the classification to prevent false matches
