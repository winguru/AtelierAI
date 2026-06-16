-- Migration: Review sessions, structured assessments, and image style override
-- Version: 002
-- Date: 2026-06-12
-- Database: SQLite

-- ============================================================================
-- Part 1: Extend images with review-editable style metadata
-- ============================================================================

ALTER TABLE images ADD COLUMN user_image_style_concept_id INTEGER NULL;
ALTER TABLE images ADD COLUMN user_image_style_source TEXT NULL;
ALTER TABLE images ADD COLUMN user_image_style_confidence REAL NULL;

CREATE INDEX IF NOT EXISTS ix_images_user_style_concept
    ON images(user_image_style_concept_id);

-- ============================================================================
-- Part 2: Create concept_review_sessions table
-- ============================================================================

CREATE TABLE IF NOT EXISTS concept_review_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    closed_at DATETIME,
    FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_review_sessions_concept
    ON concept_review_sessions(concept_id);

CREATE INDEX IF NOT EXISTS ix_review_sessions_status
    ON concept_review_sessions(status);

CREATE INDEX IF NOT EXISTS ix_review_sessions_created
    ON concept_review_sessions(created_at);

-- ============================================================================
-- Part 3: Create concept_review_assessments table
-- ============================================================================

CREATE TABLE IF NOT EXISTS concept_review_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    concept_id INTEGER NOT NULL,
    image_id INTEGER NOT NULL,

    predominance_rating INTEGER,
    quality_rating INTEGER,
    accuracy_rating INTEGER,
    attribute_support_rating INTEGER,

    context_incongruent INTEGER NOT NULL DEFAULT 0,
    context_anachronistic INTEGER NOT NULL DEFAULT 0,
    context_anatopismic INTEGER NOT NULL DEFAULT 0,
    context_nonsensical INTEGER NOT NULL DEFAULT 0,
    context_anomalous_form INTEGER NOT NULL DEFAULT 0,

    anomaly_present INTEGER NOT NULL DEFAULT 0,
    anomaly_kind TEXT,
    anomaly_degree INTEGER,

    image_style_concept_id INTEGER,
    image_style_source TEXT,
    image_style_confidence REAL,

    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,

    FOREIGN KEY (session_id) REFERENCES concept_review_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY (image_style_concept_id) REFERENCES concepts(id) ON DELETE SET NULL,
    UNIQUE(session_id, image_id)
);

CREATE INDEX IF NOT EXISTS ix_review_assessment_session
    ON concept_review_assessments(session_id);

CREATE INDEX IF NOT EXISTS ix_review_assessment_concept_image
    ON concept_review_assessments(concept_id, image_id);

CREATE INDEX IF NOT EXISTS ix_review_assessment_style_concept
    ON concept_review_assessments(image_style_concept_id);
