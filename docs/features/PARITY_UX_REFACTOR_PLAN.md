# Parity Workbench UX Refactor Plan

**Date:** 2026-04-04  
**Scope:** Terminology, API response shape, frontend rendering, and usability improvements  
**Goal:** Transform the parity workbench from a forensic debug tool into an intuitive creative workflow

---

## Change Summary

| # | Change | Files Touched |
|---|--------|---------------|
| 1 | Terminology rename pass (API keys + frontend labels) | main.py, schemas.py, generation-lab.js, generation-lab.html |
| 2 | Actionable classification with `action_items` and `readiness_score` | main.py |
| 3 | Summary card + visual field diff (replace raw JSON panels) | generation-lab.html, generation-lab.js |
| 4 | Merge three comparison views into unified field status | main.py, generation-lab.js |
| 5 | Auto-include all evidence, remove debug flags | main.py, schemas.py, generation-lab.html, generation-lab.js |
| 6 | Flatten endpoint path (alias, not replace) | main.py |
| 7 | Workflow template selector + file upload button | generation-lab.html, generation-lab.js |

---

## 1. Terminology Rename Pass

### API Response Keys (backend)

| Old Key | New Key | Context |
|---------|---------|---------|
| `model_hash_evidence` | `model_verification` | comparison section |
| `confirmation_tier` | `match_confidence` | model verification + workflow buckets |
| `same_source` | `verified` | match_confidence enum value |
| `cross_source` | `probable` | match_confidence enum value |
| `did_not_match` | `local_only` | semantic bucket key |
| `unmatched_expected_parameters` | `workflow_only` | semantic bucket key |
| `match_reason` | `match_basis` | workflow bucket entries |
| `validated_by_external_model_hash_evidence:same_source` | `verified_by_model_verification:verified` | bucket match_basis value |
| `validated_by_external_model_hash_evidence:cross_source` | `verified_by_model_verification:probable` | bucket match_basis value |
| `no_semantic_match_found` | `no_workflow_comparison` | bucket match_basis value |

### Frontend Labels (HTML + JS)

| Old Label | New Label |
|-----------|-----------|
| "Parity Workbench" | "Generation Audit" |
| "Analyze Candidate" | "Analyze" |
| "Candidate Classification" panel | "Classification" panel |
| "Missing & Conflicts" panel | "Issues & Actions" panel |
| "Extracted Fields" panel | "Extracted Fields" (keep) |
| "Normalized Canonical Fields" panel | "Normalized Fields" panel |
| "Workflow Match Buckets" panel | "Workflow Comparison" panel |
| "Model Hash Evidence" panel | "Model Verification" panel |
| "Include non-prefix LoRA Manager model hash evidence (debug)" | *removed* |

### Internal Variable Names (JS)

| Old Name | New Name |
|----------|----------|
| `parityModelHashEvidencePanel` | `parityModelVerificationPanel` |
| `parityIncludeNonPrefixHashEvidenceInput` | *(removed)* |

### Backward Compatibility

- Old endpoint `/generation-prototype/parity-workbench/candidate-audit` stays functional
- New alias `/generation-audit/analyze` added pointing to same handler
- Response shape changes: **breaking** — but this is pre-release, no external consumers

---

## 2. Actionable Classification with `action_items` and `readiness_score`

### New Response Fields

Add to `candidate` object:

```python
"readiness_score": int,  # 0-100, percentage of required fields present × quality modifiers
"action_items": [str],    # Human-readable todo list
```

### Scoring Logic

```
base_score = (present_required_fields / total_required_fields) * 100

Modifiers:
- model verified (match_confidence: verified)  → +10 (cap at 100)
- model probable (match_confidence: probable)   → +5 (cap at 100)
- unsupported features detected                 → -30
- workflow provided + alignment > 80%           → +5
- workflow provided + alignment < 35%           → -10
- has conflicts                                 → -5 per conflict
```

### Action Items Generation

Auto-generate from state:

```python
action_items = []
if missing_fields:
    action_items.append(f"Set {', '.join(missing_fields)} — missing from extracted metadata")
if classification == "needs_manual_intervention":
    action_items.append("Resolve blocking issues above before attempting generation")
if model_verification and not model_verification.get("confirmed_exact_match"):
    action_items.append("Verify model — local checkpoint could not be confirmed by hash")
if unsupported_features:
    action_items.append(f"Unsupported A1111 features detected: {', '.join(unsupported_features)}")
if not workflow_supplied:
    action_items.append("Provide a workflow JSON to enable field alignment checking")
# etc.
```

---

## 3. Summary Card + Visual Field Diff

### New HTML: Summary Card

Insert above the current output grid:

```html
<section id="parity-summary-card" class="parity-summary-card" style="display:none">
  <div class="parity-summary-header">
    <span id="parity-classification-badge" class="parity-badge">—</span>
    <span id="parity-readiness-score" class="parity-score">—</span>
  </div>
  <p id="parity-summary-text" class="parity-summary-text"></p>
  <ul id="parity-action-items" class="parity-action-items"></ul>
</section>
```

### New HTML: Visual Field Diff

Replace 6 raw JSON panels with:

```html
<section id="parity-field-diff" class="parity-field-diff" style="display:none">
  <h3>Field Status</h3>
  <table id="parity-field-table" class="parity-field-table">
    <thead>
      <tr><th>Field</th><th>Status</th><th>Local Value</th><th>Workflow Value</th><th>Detail</th></tr>
    </thead>
    <tbody id="parity-field-tbody"></tbody>
  </table>
</section>
```

### Collapsible Raw Details

Keep existing JSON panels but wrap in collapsible `<details>`:

```html
<details class="parity-raw-details">
  <summary>Raw JSON Details</summary>
  <!-- existing 6 panels here -->
</details>
```

### Frontend Rendering

New `renderParitySummary()` function:
1. Build classification badge (color: green/yellow/red/gray)
2. Render readiness score as percentage
3. Build natural language summary sentence
4. Render action_items as checklist

New `renderParityFieldDiff()` function:
1. Iterate canonical_fields
2. For each field, determine status from unified comparison (matched/mismatched/local_only/workflow_only)
3. Build table rows with ✅/⚠️/❌/➖ indicators

---

## 4. Merge Three Comparison Views into Unified Field Status

### Current Problem

Three overlapping views: `field_alignment`, `structural`, `workflow_match_buckets_semantic`  
A field can appear in multiple with different verdicts.

### Solution

Add new `unified_field_status` dict to comparison response:

```python
"unified_field_status": {
    "model_hash": {
        "status": "verified",        # verified | mismatched | local_only | workflow_only | not_checked
        "local_value": "67ab2fd8ec",
        "workflow_value": "67ab2fd8ec",
        "confidence": "verified",     # from model verification
        "detail": "Hash confirmed via civitai_metadata (same source)"
    },
    "sampler_name": {
        "status": "matched",
        "local_value": "euler_ancestral",
        "workflow_value": "euler_ancestral",
        "confidence": null,
        "detail": "Normalized from 'Euler a'"
    },
    "prompt_positive": {
        "status": "local_only",
        "local_value": "score_9, score_8_up, ...",
        "workflow_value": null,
        "confidence": null,
        "detail": "No workflow comparison available"
    }
}
```

### Merge Logic (backend)

1. Start with all canonical_fields → status `not_checked`
2. If workflow was provided:
   - Apply semantic bucket results: matched → `matched`, mismatched → `mismatched`
   - Apply model verification to `model_hash` field specifically
   - Fields in local_only bucket → `local_only`
   - Fields in workflow_only bucket → `workflow_only`
3. Override model_hash status with verification tier if available:
   - confirmed_exact_match + verified → `verified`
   - confirmed_exact_match + probable → `verified` (with confidence detail)

Keep the original three views in response for debugging, but frontend uses `unified_field_status`.

---

## 5. Auto-Include All Evidence, Remove Debug Flags

### Changes

- **Remove** `include_non_prefix_local_reference_hash_evidence` from request schema
- **Always include** all evidence sources (prefix and non-prefix)
- **Mark** each source entry with `hash_match_type: "prefix" | "full" | "none"` for frontend filtering
- **Remove** `include_generation_payload` from request schema (replace with query param `?debug=true`)
- **Remove** the debug checkbox from HTML

---

## 6. Flatten Endpoint Path

### New Alias

Add new route pointing to same handler:

```python
@app.post("/generation-audit/analyze", response_model=dict)
# delegates to analyze_parity_candidate logic
```

Keep old route for backward compatibility. Document new route as preferred.

---

## 7. Workflow Template Selector + File Upload

### HTML Additions

```html
<div class="control-row">
  <button id="parity-load-template-btn" type="button">Load from Templates</button>
  <button id="parity-upload-workflow-btn" type="button">Upload File</button>
  <input id="parity-workflow-file" type="file" accept=".json" style="display:none">
</div>
<select id="parity-template-select" style="display:none">
  <option value="">— Select a template —</option>
</select>
```

### JS Logic

- "Load from Templates" → fetch `/generation-templates/list`, populate dropdown
- On template select → fetch `/generation-templates/get/{id}`, populate workflow textarea
- "Upload File" → trigger hidden file input, read file, populate textarea

---

## Execution Order

1. **Terminology + action_items + readiness_score** (backend) — foundational, everything else depends on new key names
2. **Unified field status** (backend) — new response shape
3. **Remove debug flags** (backend + schema)
4. **Flatten endpoint** (backend)
5. **Frontend: summary card + field diff** (HTML + JS)
6. **Frontend: terminology renames** (HTML + JS)
7. **Frontend: template selector + file upload** (HTML + JS)
8. **Update implementation plan + API docs**

---

## Risk Assessment

- **Breaking change:** Response shape changes are breaking for any existing consumers. Since this is pre-release with no external API consumers, risk is low.
- **Testing:** After each phase, re-run the test image (`e024a7b83f226e1e...`) and verify response.
- **Rollback:** All changes are in a cohesive branch; revert is clean.
