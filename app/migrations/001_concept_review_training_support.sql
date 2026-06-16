-- Migration: Concept Review and Training Support Schema
-- Version: 001
-- Date: 2026-06-09
-- Description: Add observation weighting fields and create concept review tables
-- Database: SQLite
--
-- This migration extends the schema to support:
-- 1. Observation weighting for concept training (concept_strength_weight, etc.)
-- 2. Attribute term profiles for fine-grained attribute tracking
-- 3. Authority-specific weights for attribute evidence
-- 4. Human review evidence for validation and learning
--
-- Apply with: sqlite3 image_db.sqlite < migrations/001_concept_review_training_support.sql
-- Note: Backup your database before applying: cp image_db.sqlite image_db.sqlite.bak

-- ============================================================================
-- Part 1: Extend image_concept_observations with observation weighting fields
-- ============================================================================

-- SQLite ADD COLUMN supports adding multiple columns, but we do them one at a time
-- for better error messages and rollback safety.
-- Note: SQLite doesn't support COMMENT clauses - documentation in docstrings instead.

ALTER TABLE image_concept_observations
    ADD COLUMN observation_weight REAL DEFAULT NULL;

ALTER TABLE image_concept_observations
    ADD COLUMN review_confidence REAL DEFAULT NULL;

ALTER TABLE image_concept_observations
    ADD COLUMN training_role TEXT DEFAULT NULL;

ALTER TABLE image_concept_observations
    ADD COLUMN concept_strength_weight REAL DEFAULT NULL;

-- ============================================================================
-- Part 2: Create concept_attribute_term_profiles table
-- ============================================================================

-- This table links concepts to authority_terms for fine-grained attribute tracking.
-- It's separate from concept_attribute_profiles which links concepts to other concepts.

CREATE TABLE concept_attribute_term_profiles (
    concept_id INTEGER NOT NULL,
    attribute_term_id INTEGER NOT NULL,
    consistency_score REAL,
    invariance INTEGER NOT NULL DEFAULT 0,
    attribute_mode TEXT NOT NULL DEFAULT 'boolean',
    attribute_family TEXT,
    cardinality_min INTEGER,
    cardinality_max INTEGER,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    PRIMARY KEY (concept_id, attribute_term_id),
    FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
    FOREIGN KEY (attribute_term_id) REFERENCES authority_terms(id) ON DELETE CASCADE
);

-- Indexes for efficient queries
CREATE INDEX ix_attr_term_profile_concept
    ON concept_attribute_term_profiles(concept_id);

CREATE INDEX ix_attr_term_profile_term
    ON concept_attribute_term_profiles(attribute_term_id);

-- ============================================================================
-- Part 3: Create concept_attribute_authority_weights table
-- ============================================================================

CREATE TABLE concept_attribute_authority_weights (
    concept_id INTEGER NOT NULL,
    attribute_term_id INTEGER NOT NULL,
    authority_id INTEGER NOT NULL,
    base_weight REAL,
    learned_weight REAL,
    updated_at DATETIME,
    PRIMARY KEY (concept_id, attribute_term_id, authority_id),
    FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
    FOREIGN KEY (attribute_term_id) REFERENCES authority_terms(id) ON DELETE CASCADE,
    FOREIGN KEY (authority_id) REFERENCES tag_authorities(id) ON DELETE CASCADE
);

-- Indexes for efficient queries
CREATE INDEX ix_auth_weight_concept_attr
    ON concept_attribute_authority_weights(concept_id, attribute_term_id);

CREATE INDEX ix_auth_weight_concept_auth
    ON concept_attribute_authority_weights(concept_id, authority_id);

-- ============================================================================
-- Part 4: Create concept_review_evidence table
-- ============================================================================

CREATE TABLE concept_review_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id INTEGER NOT NULL,
    image_id INTEGER NOT NULL,
    attribute_term_id INTEGER,
    evidence_kind TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL,
    notes TEXT,
    reviewer TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY (attribute_term_id) REFERENCES authority_terms(id) ON DELETE SET NULL
);

-- Indexes for efficient queries
CREATE INDEX ix_review_ev_concept_image
    ON concept_review_evidence(concept_id, image_id);

CREATE INDEX ix_review_ev_image_attribute
    ON concept_review_evidence(image_id, attribute_term_id);

CREATE INDEX ix_review_ev_created_at
    ON concept_review_evidence(created_at);

-- ============================================================================
-- Part 5: Verify migration
-- ============================================================================

-- Check that image_concept_observations has the new columns
SELECT
    'image_concept_observations extended' as check_name,
    COUNT(*) as column_count
FROM pragma_table_info('image_concept_observations')
WHERE name IN ('observation_weight', 'review_confidence', 'training_role', 'concept_strength_weight');

-- Check that new tables exist with correct column counts
SELECT
    'concept_attribute_term_profiles created' as check_name,
    COUNT(*) as column_count
FROM pragma_table_info('concept_attribute_term_profiles');

SELECT
    'concept_attribute_authority_weights created' as check_name,
    COUNT(*) as column_count
FROM pragma_table_info('concept_attribute_authority_weights');

SELECT
    'concept_review_evidence created' as check_name,
    COUNT(*) as column_count
FROM pragma_table_info('concept_review_evidence');

-- Check that indexes exist
SELECT
    'indexes created' as check_name,
    COUNT(*) as index_count
FROM pragma_index_list('concept_attribute_term_profiles')
WHERE name IN ('ix_attr_term_profile_concept', 'ix_attr_term_profile_term');

-- ============================================================================
-- Post-migration notes
-- ============================================================================
--
-- 1. Backward compatibility: All new fields are nullable with DEFAULT NULL,
--    so existing queries continue to work without modification.
--
-- 2. Training pipeline integration:
--    - observation_weight: Set by training pipeline based on review rubric scores
--    - concept_strength_weight: Calculated from attribute evidence agreement
--    - training_role: Assigned during corpus curation (positive_exemplar, etc.)
--
-- 3. Review workflow integration:
--    - concept_review_evidence: Populated by Review Lab UI
--    - learned weights: Updated from review evidence using feedback loop
--
-- 4. Query optimization:
--    - All indexes support the composite scoring algorithm's query patterns
--    - Covering indexes avoid table lookups for common filter operations
--
-- 5. Data cleanup (optional):
--    - Run VACUUM after migration to reclaim space
--    - Run ANALYZE to update query planner statistics
--
-- ============================================================================