-- Migration 003: Add concept deviation fields and attribute checklist JSON
-- to concept_review_assessments.

-- Concept deviations: intentional creative departures from canonical form
-- (body variant, exaggeration, extra features, fusion) — distinct from
-- technical generation anomalies.
ALTER TABLE concept_review_assessments ADD COLUMN deviation_present    BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE concept_review_assessments ADD COLUMN deviation_body_variant BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE concept_review_assessments ADD COLUMN deviation_exaggerated  BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE concept_review_assessments ADD COLUMN deviation_extra_feature BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE concept_review_assessments ADD COLUMN deviation_fusion        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE concept_review_assessments ADD COLUMN deviation_kind   VARCHAR(100);
ALTER TABLE concept_review_assessments ADD COLUMN deviation_degree INTEGER;

-- Attribute checklist: { attribute_concept_id: "present"|"absent"|"not_visible" }
ALTER TABLE concept_review_assessments ADD COLUMN attribute_checks TEXT;
