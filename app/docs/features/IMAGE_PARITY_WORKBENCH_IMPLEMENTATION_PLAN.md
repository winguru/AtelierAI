# Image Parity Workbench Implementation Plan

## Goal
Create a single, workflow-first interface for validating and improving generation metadata parity for gallery images, with emphasis on A1111-to-Comfy mapping and iterative visual matching.

## Phase 0 - Foundations and Definitions
1. Define parity status model:
   - Matched: best similarity >= threshold.
   - Not Matched: best similarity < threshold.
   - Fundamental Generation Issue: not matched or generation failed/no outputs.
2. Define image eligibility classes:
   - Generatable now.
   - Generatable with inference.
   - Needs manual intervention.
   - Non-generatable now (missing generation data, composite/hybrid, video/complex media).
3. Define canonical parameter schema for parity work:
   - Prompts, model/checkpoint, sampler/scheduler, steps, cfg, seed, size, denoise, clip skip, LoRAs.

## Phase A - Candidate Audit + Unified Workbench Shell
1. Backend candidate audit endpoint:
   - Input: local `file_hash`, optional `comfy_workflow_json`.
   - Output:
     - extracted fields
     - normalized canonical fields
     - missing fields
     - conflicts/warnings
     - mapping notes (e.g., sampler aliases)
     - readiness classification
2. Add `Parity Workbench` panel/tab in Generation Lab:
   - Source hash + optional workflow JSON
   - Analyze button
   - Panels for extracted/normalized/missing/conflicts/readiness
3. Keep existing tabs as advanced tooling:
   - Template Studio
   - A1111 Bridge
   - Generate Image
4. Start with local-image scope; CivitAI-side work can remain integrated via existing extraction pipeline.

## Phase A.5 - Expected Workflow Diff (Match Buckets)
1. Add expected workflow JSON diff view:
   - Input: optional expected `comfy_workflow_json` pasted by user.
   - Output buckets:
     - matched
     - mismatched
     - did_not_match
2. Provide clear field-level diagnostics:
   - matched fields with value and path samples
   - mismatched scalar paths with expected vs actual value samples
   - candidate fields that did not match any expected workflow value
   - unmatched expected workflow parameters (for example KSampler scheduler or VAELoader VAE name) that have no local candidate match
3. Use semantic matching before classifying:
   - sampler alias normalization (for example `Euler a` -> `euler_ancestral`)
   - model name normalization and model hash prefix matching
   - scheduler normalization and node-context-aware expected parameter extraction
4. Expose summary counters for quick triage:
   - matched count
   - mismatched count
   - did_not_match count
   - unmatched_expected_parameters count
5. Keep this phase focused on explainability and visibility:
   - no auto-repair logic yet
   - no rule promotion yet

## Phase B - Guided Mapping and Rule Engine v1
1. Add rule-driven normalization layer:
   - Sampler alias mapping (A1111 -> Comfy)
   - Scheduler canonicalization
   - Denoise handling hints by stage role
   - Prompt fallback precedence
2. Return rule provenance for each normalized field:
   - extracted | inferred | defaulted | override
3. Add conflict diagnostics:
   - conflicting seed/model identifiers
   - denoise mismatch risk
   - missing required fields for selected template

## Phase C - Iterative Tuning Loop and Attempt Intelligence
1. Integrate existing attempt log into workbench flow:
   - show attempts inline for current image
   - show tweak label, parameters changed, result similarity, issue class
2. Add suggested next-step tuning actions:
   - missing required fields first
   - high-impact parameter sweeps second
3. Add structured tweak presets:
   - sampler variants
   - cfg/steps micro-sweeps
   - denoise and clip-skip candidates

## Phase D - Template-Aware Auto-Repair
1. Add template binding diagnostics:
   - unresolved tokens
   - weak/defaulted token usage
2. Add one-click auto-repair:
   - apply best-known mapping defaults
   - fill missing critical fields from inferred candidates
3. Keep image-specific overrides separate from global rules.

## Phase E - Learning and Persistence
1. Promote successful tweak patterns to reusable rules:
   - global rules for general cases
   - image-specific overrides for exceptions
2. Store learned mapping confidence and evidence:
   - similarity outcomes and attempt counts
3. Add rule introspection UI with rollback support.

## Phase F - Validation, QA, and Scale
1. Curate regression fixtures:
   - Comfy-native images
   - A1111-mapped images
   - known hard/non-generatable cases
2. Add acceptance metrics:
   - matched rate by source type
   - average attempts to match
   - fundamental issue rate
3. Add dashboard/reporting endpoints for parity progress.

## Immediate Deliverables (Now)
1. Implement Phase A backend candidate audit endpoint.
2. Add Phase A Parity Workbench tab and panel in Generation Lab.
3. Wire analysis call + visual display of extracted/normalized/missing/conflicts/readiness.
4. Preserve existing tabs for advanced/manual workflows.

## Progress Update (2026-04-01)
1. Phase A implementation status: completed.
   - Candidate audit endpoint is live and returns extracted fields, canonical fields, conflicts, warnings, readiness class, and mapping notes.
   - Generation Lab includes the Parity Workbench panel with local hash input, optional workflow JSON input, and analysis rendering.
2. Phase A.5 implementation status: completed (initial) and enhanced.
   - Workflow match buckets are implemented and rendered in the Parity Workbench output.
   - Backend now returns semantic buckets with:
     - matched
     - mismatched
     - did_not_match
     - unmatched_expected_parameters
   - Frontend prefers semantic bucket payloads and falls back to legacy scalar buckets when needed.

## Implemented Enhancements
1. Semantic field matching improvements:
   - Sampler alias normalization supports A1111 -> Comfy equivalence (for example `Euler a` -> `euler_ancestral`).
   - Scheduler values are normalized before compare.
   - Model matching uses normalized model-name comparison with tolerant containment checks.
   - Model hash matching supports prefix compatibility across truncated and full hash tokens.
2. Expected workflow parameter extraction improvements:
   - Extracts KSampler sampler/scheduler/denoise expected values.
   - Extracts CheckpointLoader model expected value.
   - Extracts VAELoader vae_name expected value.
   - Surfaces unmatched expected parameters when no local candidate value exists.
3. Prompt comparison reliability improvements:
   - Prompt matching now normalizes unicode and whitespace to reduce false negatives.
   - Prompt mismatch diagnostics now report:
     - closest expected workflow path
     - raw/normalized equality flags
     - first differing character and code points
   - Prompt matching now tolerates inline LoRA tag representation differences (`<lora:...>`) when the remaining normalized prompt text matches.

## Related Model Reference Lab Enhancements
1. Download action behavior:
   - "Download with LoRA Manager" is shown when there is no exact local model/version match, not only when fully missing.
2. CivitAI source URL behavior:
   - CivitAI Model Source now resolves from authoritative reference model/version ids instead of best-match fallback URLs.

## Current Findings and Open Work
1. Verified for image hash `9d2897df0761865a95422e9bd500ccc2e51b187e45c9386c55fda5fc1ffca2ef`:
   - CivitAI generation metadata payload contains model hash token `e31a2563f0`.
   - This confirms hash evidence is available from CivitAI metadata for this sample.
2. Local catalog hash evidence currently does not prove an exact hash-prefix match for this sample:
   - Available local catalog checkpoint hashes for the same model name do not prefix-match `e31a2563f0`.
3. Model hash evidence wiring is in progress:
   - Parity response now includes `comparison.model_hash_evidence` for debugging and future promotion to first-class exact-match confidence.
   - Additional hardening is still needed so validated cross-source hash evidence can reliably promote `model_hash` to matched in all expected cases.

## Next Steps
1. Finalize model hash exact-match promotion rules:
   - Promote `model_hash` to matched when model id/version context and hash-prefix evidence are jointly satisfied.
   - Keep a strict safety gate to avoid false positives from name-only matches.
   - **Completed (2026-04-04)**: Two-tier confirmation system implemented:
     - Tier 1 (same_source): Single source has both `model_name_compatible` + `hash_prefix_match`.
     - Tier 2 (cross_source): One source provides `hash_prefix_match` and another provides matching CivitAI model/version IDs, or name_compat + IDs from one source + hash_match from another.
     - CivitAI metadata now extracts `modelId`, `modelVersionId` from `resources[*]`, URN `Model` field, and `civitaiResources[*]`.
     - Frontend now shows a dedicated "Model Hash Evidence" panel with `confirmation_tier` and `cross_source_detail`.
2. Reuse Model Reference Lab data paths where applicable:
   - Prefer authoritative CivitAI model/version ids and known catalog match basis when deriving parity confidence.
3. Add small regression fixtures for Phase A.5 semantics:
   - Prompt with inline LoRA tags vs workflow prompt text.
   - Truncated hash vs full hash prefix matching.
   - Scheduler/vae unmatched expected parameter detection.
