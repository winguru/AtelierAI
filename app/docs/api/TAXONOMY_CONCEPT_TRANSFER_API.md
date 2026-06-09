# AtelierAI Taxonomy Concept Transfer API

This document specifies export/import endpoints and merge contracts for transferring concept trees and associated authority tags between instances.

## Goals

- Export concept hierarchy with associated tags and aliases in a portable JSON format.
- Import a concept tree using name/slug matching, not internal IDs.
- Preserve local taxonomy as authoritative during collisions.
- Preserve current single-association rule: one authority term can map to at most one concept.

## Non-Goals

- No automatic reparenting of existing local concepts unless a future explicit mode is added.
- No multi-concept tag association in this version.

## Base Routes

- `GET /taxonomy/concepts/export`
- `POST /taxonomy/concepts/import`
- `POST /taxonomy/concepts/import_file`

## Shared Modes And Policies

### Dry Run

Supported by import endpoints.

- `dry_run=true`: validate and compute merge/report only, no DB writes.
- `dry_run=false`: execute merge.

### Root Creation Policy

Import behavior is policy-controlled.

- `strict` (default): do not auto-create new root concepts.
- `permissive`: allow creating imported root concepts when there is no local overlap.

Notes:
- `strict` aligns with the current project rule that roots are never auto-created by background/import flows.
- `permissive` is optional for controlled migrations.

### Local Authority Rule

Local tree and local tag associations are authoritative.

- Concept collisions: import branch is grafted into local match.
- Tag collision (same authority + term already bound to another concept): skip imported tag link and report conflict.

## Canonical Transfer Format

### Export Response Shape

```json
{
  "version": 1,
  "exported_at": "2026-05-16T12:00:00Z",
  "authority_order": ["civitai", "danbooru", "prompt", "user"],
  "roots": [
    {
      "name": "subject",
      "slug": "subject",
      "description": null,
      "status": "active",
      "aliases": ["subjects"],
      "tags": {
        "civitai": [],
        "danbooru": [],
        "prompt": [],
        "user": []
      },
      "children": [
        {
          "name": "1girl",
          "slug": "1girl",
          "description": null,
          "status": "active",
          "aliases": ["one girl", "1 girl"],
          "tags": {
            "civitai": ["1girl"],
            "danbooru": ["1girl"],
            "prompt": ["1girl", "1 girl", "one girl"],
            "user": ["1girl"]
          },
          "children": []
        }
      ]
    }
  ]
}
```

### Node Contract

Each concept node in `roots[]` and `children[]`:

- `name` (string, required): canonical concept display name.
- `slug` (string, required): stable merge key candidate.
- `description` (string|null, optional).
- `status` (string, optional; default `active`).
- `aliases` (string[], optional).
- `tags` (object, required): keys are authority names; values are string arrays of authority-term names.
- `children` (node[], required).

## Export Endpoint

### `GET /taxonomy/concepts/export`

Returns canonical transfer JSON.

Query params:

- `include_aliases` (bool, default `true`)
- `include_descriptions` (bool, default `true`)
- `authorities` (csv list, optional; default all authorities)

Response:

- `200`: transfer document.

## Import Endpoint (JSON Body)

### `POST /taxonomy/concepts/import`

Request body:

```json
{
  "document": {
    "version": 1,
    "authority_order": ["civitai", "danbooru", "prompt", "user"],
    "roots": []
  },
  "mode": "graft",
  "root_policy": "strict",
  "dry_run": true
}
```

Body fields:

- `document` (object, required): canonical transfer document.
- `mode` (string, required): currently only `graft`.
- `root_policy` (string, optional): `strict` or `permissive`.
- `dry_run` (bool, optional): default `true`.

Response (`200`):

```json
{
  "ok": true,
  "dry_run": true,
  "summary": {
    "roots_processed": 2,
    "roots_created": 0,
    "roots_skipped_by_policy": 1,
    "concepts_matched": 12,
    "concepts_created": 6,
    "branches_grafted": 8,
    "aliases_added": 4,
    "tag_links_added": 27,
    "tag_conflicts_skipped": 3,
    "validation_errors": 0
  },
  "conflicts": [
    {
      "type": "tag_conflict",
      "authority": "civitai",
      "term": "1girl",
      "existing_concept": "subject/1girl",
      "incoming_concept": "characters/1girl",
      "action": "skipped"
    }
  ],
  "actions": [
    {
      "type": "graft_existing",
      "incoming_path": "characters/1girl",
      "local_path": "subject/1girl"
    }
  ]
}
```

Status codes:

- `200`: successful validation/import with summary.
- `400`: malformed document.
- `409`: unsupported mode/policy or incompatible version.
- `422`: semantic validation failure (for example cyclic tree in input).

## Import Endpoint (File Upload)

### `POST /taxonomy/concepts/import_file`

Multipart form fields:

- `file` (required): JSON file following canonical transfer format.
- `mode` (optional, default `graft`).
- `root_policy` (optional, default `strict`).
- `dry_run` (optional, default `true`).

Response:

- Same response shape/status codes as `POST /taxonomy/concepts/import`.

## Merge Algorithm Contract (Normative)

1. Root handling:
- For each imported root node, attempt local match by `slug`, then normalized `name`, then optional alias.
- If no match exists:
- `strict`: skip root and report `roots_skipped_by_policy`.
- `permissive`: create root and continue.

2. Recursive child handling:
- For each imported child under the current resolved local parent:
- If local child collision exists, recurse into existing local child.
- Else create child under current local parent and recurse.

3. Tag association handling:
- For each `authority -> term` on current imported node:
- If term has no local concept association, associate to resolved local concept.
- If term already associated to this same concept, no-op.
- If term associated to a different concept, skip and add `tag_conflicts_skipped` record.

4. Alias handling:
- Add missing aliases to resolved local concept.
- Ignore duplicates by normalized alias uniqueness.

5. Structural authority:
- Existing local parent-child structure is authoritative in collisions.
- Import never force-reparents an existing local node in `graft` mode.

## Matching Rules

Given imported node `N`, resolve local concept in this order:

1. Exact `slug` match.
2. Normalized canonical-name match.
3. Optional normalized alias match (if exactly one candidate).

Ambiguous matches should return a validation conflict entry and skip that branch unless future `resolve_strategy` support is added.

## Versioning

- `document.version` is required.
- Import must reject unsupported future versions (`409`).
- Backward-compatible extensions should be additive.

## Forward Compatibility Notes

Future optional enhancements:

- `allow_multi_tag_association` mode for many-to-many term-concept links.
- explicit `reparent` mode.
- branch-level conflict resolution strategy controls.
