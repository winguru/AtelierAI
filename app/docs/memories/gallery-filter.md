# Gallery Filter (unified filter pipeline)

Scope: `app/backend/services/gallery_filter_service.py`,
`app/backend/services/gallery_query.py`, and the unified query sites in
`app/backend/main.py` that consume them.

## Design Decisions

### Status filters vs the active-image base filter
Every gallery query starts from a base filter restricting rows to "active"
images (`image_status IS NULL OR 'active'`) before unified filter terms are
applied. Some status terms target rows the base filter excludes — e.g.
`status:civitai_deleted` tombstones live on rows with
`image_status='placeholder'` — so the base must be conditionally relaxed.

- `_STATUS_FILTERS` maps status term → SQLAlchemy clause
  (`civitai_deleted` → `civitai_deleted_at IS NOT NULL`).
- `_INACTIVE_STATUS_VALUES` (`frozenset({"civitai_deleted"})`) lists status
  values whose rows are outside the standard active set. Extend it when a new
  status term targets placeholder/hidden rows.
- `relaxed_active_image_filter()` admits `'placeholder'` rows;
  `status_terms_include_inactive(parsed)` reports whether any **included**
  status term intersects `_INACTIVE_STATUS_VALUES`.
- Canonical pattern (parse first, then choose base):

  ```python
  parsed = parse_gallery_filter(included, excluded, hidden, missing)
  base_clause = (
      relaxed_active_image_filter()
      if status_terms_include_inactive(parsed)
      else _active_image_filter()
  )
  images_query = db.query(ImageModel).filter(base_clause)
  ```

- Only **included** terms relax the base. Excluded terms never widen it
  (excluding `civitai_deleted` from the active set must not drag placeholders
  into an unfiltered gallery).

## Gotchas

- **`GalleryQuery._resolve_filter` ordering**: the relaxed-vs-standard base
  decision (and the `_last_base_image_filter` assignment) must execute BEFORE
  the cache-check early returns, or cached pages replay the wrong base filter.
- **`GalleryQuery.__init__` initializes `_last_base_image_filter`** to
  `active_image_filter()` so direct `_fetch_image_page` callers (which never
  ran `_resolve_filter`) still have a valid base; the base query and the
  sibling backfill (`missing_ids`) query must both filter on it.
- `_search_cache` has a 30s TTL — use a `&_bust=N` dummy param when
  curl-verifying filter changes.
- Endpoint slash sensitivity: GET `/api/images/` needs the trailing slash;
  GET `/api/images/state` and `/api/images/keys` must NOT have one (else 307).
  POST `/api/query` response items live under `images`, not `items`.
- Tombstone rows have no local file — gallery tiles 404 on their
  `civitai-unavailable-*.placeholder` thumbnails by design.
