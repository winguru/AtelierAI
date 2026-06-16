# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/concept-framework.md
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Concept,
    ConceptAttributeAuthorityWeight,
    ConceptAttributeTermProfile,
    ConceptReviewEvidence,
    ConceptReviewSession,
    ConceptReviewAssessment,
    ImageModel,
    ImageConceptObservation,
    TagAuthority,
    AuthorityTerm,
)
from schemas import (
    BulkObservationWeightUpdateRequest,
    BulkReviewEvidenceRequest,
    ConceptAttributeAuthorityWeightCreateRequest,
    ConceptAttributeAuthorityWeightResponse,
    ConceptAttributeAuthorityWeightUpdateRequest,
    ConceptAttributeTermProfileCreateRequest,
    ConceptAttributeTermProfileResponse,
    ConceptAttributeTermProfileUpdateRequest,
    ConceptObservationResponse,
    ConceptObservationUpdateRequest,
    ConceptProfileWeightingSummary,
    ConceptScoredImage,
    ConceptScoringConfig,
    ConceptScoringResponse,
    ConceptReviewEvidenceCreateRequest,
    ConceptReviewEvidenceResponse,
    ConceptReviewEvidenceUpdateRequest,
    ConceptReviewSessionCreateRequest,
    ConceptReviewSessionResponse,
    ConceptReviewSessionUpdateRequest,
    ConceptReviewAssessmentUpsertRequest,
    ConceptReviewAssessmentResponse,
)


router = APIRouter(prefix="/concepts", tags=["concept-review"])


def _image_thumbnail_url(image: Optional[ImageModel]) -> Optional[str]:
    """Return a best-effort thumbnail URL for an image record."""
    if image is None:
        return None
    return image.civitai_cdn_url or image.source_url or (f"/api/images/{image.file_hash}/thumb" if image.file_hash else None)


def _session_to_response(session: ConceptReviewSession) -> ConceptReviewSessionResponse:
    return ConceptReviewSessionResponse(
        id=session.id,
        concept_id=session.concept_id,
        concept_name=session.concept.canonical_name if session.concept else None,
        status=session.status,
        notes=session.notes,
        created_at=session.created_at.isoformat() if session.created_at else None,
        updated_at=session.updated_at.isoformat() if session.updated_at else None,
        closed_at=session.closed_at.isoformat() if session.closed_at else None,
        assessment_count=len(session.assessments) if session.assessments else 0,
    )


def _assessment_to_response(assessment: ConceptReviewAssessment) -> ConceptReviewAssessmentResponse:
    return ConceptReviewAssessmentResponse(
        id=assessment.id,
        session_id=assessment.session_id,
        concept_id=assessment.concept_id,
        concept_name=assessment.concept.canonical_name if assessment.concept else None,
        image_id=assessment.image_id,
        image_file_name=assessment.image.file_name if assessment.image else None,
        image_thumbnail_url=_image_thumbnail_url(assessment.image),
        predominance_rating=assessment.predominance_rating,
        quality_rating=assessment.quality_rating,
        accuracy_rating=assessment.accuracy_rating,
        attribute_support_rating=assessment.attribute_support_rating,
        context_incongruent=assessment.context_incongruent,
        context_anachronistic=assessment.context_anachronistic,
        context_anatopismic=assessment.context_anatopismic,
        context_nonsensical=assessment.context_nonsensical,
        context_anomalous_form=assessment.context_anomalous_form,
        anomaly_present=assessment.anomaly_present,
        anomaly_kind=assessment.anomaly_kind,
        anomaly_degree=assessment.anomaly_degree,
        deviation_present=assessment.deviation_present,
        deviation_body_variant=assessment.deviation_body_variant,
        deviation_exaggerated=assessment.deviation_exaggerated,
        deviation_extra_feature=assessment.deviation_extra_feature,
        deviation_fusion=assessment.deviation_fusion,
        deviation_kind=assessment.deviation_kind,
        deviation_degree=assessment.deviation_degree,
        image_style_concept_id=assessment.image_style_concept_id,
        image_style_concept_name=assessment.image_style_concept.canonical_name if assessment.image_style_concept else None,
        image_style_source=assessment.image_style_source,
        image_style_confidence=assessment.image_style_confidence,
        attribute_checks=assessment.attribute_checks,
        notes=assessment.notes,
        created_at=assessment.created_at.isoformat() if assessment.created_at else None,
        updated_at=assessment.updated_at.isoformat() if assessment.updated_at else None,
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _evidence_from_observation(is_present: Optional[bool], confidence: Optional[float]) -> float:
    conf = _clamp01(confidence if confidence is not None else 0.5)
    if is_present is True:
        return conf
    if is_present is False:
        return 0.0
    return 0.5


def _evidence_from_verdict(verdict: str) -> float:
    if verdict == "supports":
        return 1.0
    if verdict == "contradicts":
        return 0.0
    return 0.5


def _compute_identity_score(observations: list[ImageConceptObservation]) -> float:
    if not observations:
        return 0.0

    best = 0.0
    for obs in observations:
        components = [_evidence_from_observation(obs.is_present, obs.confidence)]
        if obs.observation_weight is not None:
            components.append(_clamp01(obs.observation_weight))
        if obs.review_confidence is not None:
            components.append(_clamp01(obs.review_confidence))
        if obs.concept_strength_weight is not None:
            components.append(_clamp01(obs.concept_strength_weight))
        score = sum(components) / len(components)
        if score > best:
            best = score

    return _clamp01(best)


def _compute_attribute_score(
    observations: list[ImageConceptObservation],
    profiles: list[ConceptAttributeTermProfile],
    authority_weight_map: dict[tuple[int, int], float],
    epsilon: float,
) -> float:
    if not profiles:
        return 1.0

    evidence_by_attr: dict[int, float] = {}
    family_max: dict[str, float] = {}

    for profile in profiles:
        term_obs = [o for o in observations if o.authority_term_id == profile.attribute_term_id]
        if not term_obs:
            evidence = 0.5
        else:
            weighted_sum = 0.0
            total_weight = 0.0
            for obs in term_obs:
                authority_id = obs.authority_id if obs.authority_id is not None else -1
                authority_weight = authority_weight_map.get((profile.attribute_term_id, authority_id), 1.0)
                weighted_sum += authority_weight * _evidence_from_observation(obs.is_present, obs.confidence)
                total_weight += authority_weight
            evidence = weighted_sum / total_weight if total_weight > 0 else 0.5

        evidence = _clamp01(evidence)
        evidence_by_attr[profile.attribute_term_id] = evidence
        if profile.attribute_family:
            family_max[profile.attribute_family] = max(family_max.get(profile.attribute_family, 0.0), evidence)

    log_sum = 0.0
    weight_sum = 0.0

    for profile in profiles:
        evidence = evidence_by_attr.get(profile.attribute_term_id, 0.5)

        cardinality_term = 1.0
        has_cardinality = profile.cardinality_min is not None or profile.cardinality_max is not None
        if profile.attribute_mode == "countable" and has_cardinality:
            cardinality_term = 0.5

        family_term = 1.0
        if profile.attribute_family:
            fam_max = family_max.get(profile.attribute_family)
            if fam_max and fam_max > 0:
                family_term = _clamp01(evidence / fam_max)

        adjusted = _clamp01(evidence * cardinality_term * family_term)

        attribute_weight = 1.0 if profile.invariance else 0.5
        if profile.consistency_score is not None:
            attribute_weight *= max(0.1, _clamp01(profile.consistency_score))

        log_sum += attribute_weight * math.log(max(epsilon, adjusted))
        weight_sum += attribute_weight

    if weight_sum <= 0:
        return 1.0

    return _clamp01(math.exp(log_sum / weight_sum))


def _compute_anomaly_penalty(evidence_records: list[ConceptReviewEvidence]) -> float:
    if not evidence_records:
        return 1.0

    weighted_sum = 0.0
    total_weight = 0.0
    for item in evidence_records:
        weighted_sum += (item.confidence if item.confidence is not None else 0.5) * _evidence_from_verdict(item.verdict)
        total_weight += item.confidence if item.confidence is not None else 0.5

    if total_weight <= 0:
        return 1.0

    anomaly_signal = weighted_sum / total_weight
    return _clamp01(max(0.05, 1.0 - (0.7 * anomaly_signal)))


# ============================================================================
# Concept Attribute Term Profiles (concept → authority_term)
# ============================================================================


@router.get("/{concept_id}/attribute-term-profiles", response_model=list[ConceptAttributeTermProfileResponse])
def get_concept_attribute_term_profiles(
    concept_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """Get all attribute term profiles for a concept."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    profiles = (
        db.query(ConceptAttributeTermProfile)
        .join(AuthorityTerm, ConceptAttributeTermProfile.attribute_term_id == AuthorityTerm.id)
        .filter(ConceptAttributeTermProfile.concept_id == concept_id)
        .all()
    )

    return [
        ConceptAttributeTermProfileResponse(
            concept_id=p.concept_id,
            attribute_term_id=p.attribute_term_id,
            consistency_score=p.consistency_score,
            invariance=p.invariance,
            attribute_mode=p.attribute_mode,
            attribute_family=p.attribute_family,
            cardinality_min=p.cardinality_min,
            cardinality_max=p.cardinality_max,
            created_at=p.created_at.isoformat() if p.created_at else None,
            updated_at=p.updated_at.isoformat() if p.updated_at else None,
            concept_name=concept.canonical_name,
            attribute_term_name=p.attribute_term.external_name if p.attribute_term else None,
            authority_name=p.attribute_term.authority.name if p.attribute_term and p.attribute_term.authority else None,
        )
        for p in profiles
    ]


@router.get("/{concept_id}/attribute-term-profiles/{attribute_term_id}", response_model=ConceptAttributeTermProfileResponse)
def get_concept_attribute_term_profile(
    concept_id: int,
    attribute_term_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """Get a specific attribute term profile."""
    profile = (
        db.query(ConceptAttributeTermProfile)
        .join(AuthorityTerm, ConceptAttributeTermProfile.attribute_term_id == AuthorityTerm.id)
        .filter(
            and_(
                ConceptAttributeTermProfile.concept_id == concept_id,
                ConceptAttributeTermProfile.attribute_term_id == attribute_term_id,
            )
        )
        .first()
    )

    if not profile:
        raise HTTPException(status_code=404, detail="Attribute term profile not found")

    concept = db.query(Concept).filter(Concept.id == concept_id).first()

    return ConceptAttributeTermProfileResponse(
        concept_id=profile.concept_id,
        attribute_term_id=profile.attribute_term_id,
        consistency_score=profile.consistency_score,
        invariance=profile.invariance,
        attribute_mode=profile.attribute_mode,
        attribute_family=profile.attribute_family,
        cardinality_min=profile.cardinality_min,
        cardinality_max=profile.cardinality_max,
        created_at=profile.created_at.isoformat() if profile.created_at else None,
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
        concept_name=concept.canonical_name if concept else None,
        attribute_term_name=profile.attribute_term.external_name if profile.attribute_term else None,
        authority_name=profile.attribute_term.authority.name if profile.attribute_term and profile.attribute_term.authority else None,
    )


@router.put("/{concept_id}/attribute-term-profiles/{attribute_term_id}", response_model=ConceptAttributeTermProfileResponse)
def upsert_concept_attribute_term_profile(
    concept_id: int,
    attribute_term_id: int,
    request: ConceptAttributeTermProfileCreateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Create or update an attribute term profile for a concept."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    # Verify attribute term exists
    attribute_term = db.query(AuthorityTerm).filter(AuthorityTerm.id == attribute_term_id).first()
    if not attribute_term:
        raise HTTPException(status_code=404, detail="Attribute term not found")

    # Check if profile already exists
    profile = (
        db.query(ConceptAttributeTermProfile)
        .filter(
            and_(
                ConceptAttributeTermProfile.concept_id == concept_id,
                ConceptAttributeTermProfile.attribute_term_id == attribute_term_id,
            )
        )
        .first()
    )

    if profile:
        # Update existing profile
        profile.consistency_score = request.consistency_score
        profile.invariance = request.invariance
        profile.attribute_mode = request.attribute_mode
        profile.attribute_family = request.attribute_family
        profile.cardinality_min = request.cardinality_min
        profile.cardinality_max = request.cardinality_max
    else:
        # Create new profile
        profile = ConceptAttributeTermProfile(
            concept_id=concept_id,
            attribute_term_id=attribute_term_id,
            consistency_score=request.consistency_score,
            invariance=request.invariance,
            attribute_mode=request.attribute_mode,
            attribute_family=request.attribute_family,
            cardinality_min=request.cardinality_min,
            cardinality_max=request.cardinality_max,
        )
        db.add(profile)

    db.commit()
    db.refresh(profile)

    return ConceptAttributeTermProfileResponse(
        concept_id=profile.concept_id,
        attribute_term_id=profile.attribute_term_id,
        consistency_score=profile.consistency_score,
        invariance=profile.invariance,
        attribute_mode=profile.attribute_mode,
        attribute_family=profile.attribute_family,
        cardinality_min=profile.cardinality_min,
        cardinality_max=profile.cardinality_max,
        created_at=profile.created_at.isoformat() if profile.created_at else None,
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
        concept_name=concept.canonical_name,
        attribute_term_name=attribute_term.external_name,
        authority_name=attribute_term.authority.name if attribute_term.authority else None,
    )


@router.patch("/{concept_id}/attribute-term-profiles/{attribute_term_id}", response_model=ConceptAttributeTermProfileResponse)
def update_concept_attribute_term_profile(
    concept_id: int,
    attribute_term_id: int,
    request: ConceptAttributeTermProfileUpdateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Update specific fields of an attribute term profile."""
    profile = (
        db.query(ConceptAttributeTermProfile)
        .filter(
            and_(
                ConceptAttributeTermProfile.concept_id == concept_id,
                ConceptAttributeTermProfile.attribute_term_id == attribute_term_id,
            )
        )
        .first()
    )

    if not profile:
        raise HTTPException(status_code=404, detail="Attribute term profile not found")

    # Update only provided fields
    if request.consistency_score is not None:
        profile.consistency_score = request.consistency_score
    if request.invariance is not None:
        profile.invariance = request.invariance
    if request.attribute_mode is not None:
        profile.attribute_mode = request.attribute_mode
    if request.attribute_family is not None:
        profile.attribute_family = request.attribute_family
    if request.cardinality_min is not None:
        profile.cardinality_min = request.cardinality_min
    if request.cardinality_max is not None:
        profile.cardinality_max = request.cardinality_max

    db.commit()
    db.refresh(profile)

    concept = db.query(Concept).filter(Concept.id == concept_id).first()

    return ConceptAttributeTermProfileResponse(
        concept_id=profile.concept_id,
        attribute_term_id=profile.attribute_term_id,
        consistency_score=profile.consistency_score,
        invariance=profile.invariance,
        attribute_mode=profile.attribute_mode,
        attribute_family=profile.attribute_family,
        cardinality_min=profile.cardinality_min,
        cardinality_max=profile.cardinality_max,
        created_at=profile.created_at.isoformat() if profile.created_at else None,
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
        concept_name=concept.canonical_name if concept else None,
        attribute_term_name=profile.attribute_term.external_name if profile.attribute_term else None,
        authority_name=profile.attribute_term.authority.name if profile.attribute_term and profile.attribute_term.authority else None,
    )


@router.delete("/{concept_id}/attribute-term-profiles/{attribute_term_id}", status_code=204)
def delete_concept_attribute_term_profile(
    concept_id: int,
    attribute_term_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Delete an attribute term profile."""
    profile = (
        db.query(ConceptAttributeTermProfile)
        .filter(
            and_(
                ConceptAttributeTermProfile.concept_id == concept_id,
                ConceptAttributeTermProfile.attribute_term_id == attribute_term_id,
            )
        )
        .first()
    )

    if not profile:
        raise HTTPException(status_code=404, detail="Attribute term profile not found")

    db.delete(profile)
    db.commit()

    return Response(status_code=204)


# ============================================================================
# Concept Attribute Authority Weights
# ============================================================================


@router.get("/{concept_id}/attribute-authority-weights", response_model=list[ConceptAttributeAuthorityWeightResponse])
def get_concept_attribute_authority_weights(
    concept_id: int,
    attribute_term_id: Optional[int] = Query(None, description="Filter by attribute term"),
    authority_id: Optional[int] = Query(None, description="Filter by authority"),
    db: Session = Depends(get_db),
) -> Any:
    """Get authority-specific attribute weights for a concept."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    query = (
        db.query(ConceptAttributeAuthorityWeight)
        .join(AuthorityTerm, ConceptAttributeAuthorityWeight.attribute_term_id == AuthorityTerm.id)
        .filter(ConceptAttributeAuthorityWeight.concept_id == concept_id)
    )

    if attribute_term_id:
        query = query.filter(ConceptAttributeAuthorityWeight.attribute_term_id == attribute_term_id)
    if authority_id:
        query = query.filter(ConceptAttributeAuthorityWeight.authority_id == authority_id)

    weights = query.all()

    return [
        ConceptAttributeAuthorityWeightResponse(
            concept_id=w.concept_id,
            attribute_term_id=w.attribute_term_id,
            authority_id=w.authority_id,
            base_weight=w.base_weight,
            learned_weight=w.learned_weight,
            updated_at=w.updated_at.isoformat() if w.updated_at else None,
            concept_name=concept.canonical_name,
            attribute_term_name=w.attribute_term.external_name if w.attribute_term else None,
            authority_name=w.authority.name if w.authority else None,
            effective_weight=w.learned_weight if w.learned_weight is not None else w.base_weight,
        )
        for w in weights
    ]


@router.put("/{concept_id}/attribute-authority-weights/{attribute_term_id}/{authority_id}", response_model=ConceptAttributeAuthorityWeightResponse)
def upsert_concept_attribute_authority_weight(
    concept_id: int,
    attribute_term_id: int,
    authority_id: int,
    request: ConceptAttributeAuthorityWeightCreateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Create or update authority-specific weights for an attribute."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    # Verify attribute term exists
    attribute_term = db.query(AuthorityTerm).filter(AuthorityTerm.id == attribute_term_id).first()
    if not attribute_term:
        raise HTTPException(status_code=404, detail="Attribute term not found")

    # Verify authority exists
    authority = db.query(TagAuthority).filter(TagAuthority.id == authority_id).first()
    if not authority:
        raise HTTPException(status_code=404, detail="Authority not found")

    # Check if weight record already exists
    weight = (
        db.query(ConceptAttributeAuthorityWeight)
        .filter(
            and_(
                ConceptAttributeAuthorityWeight.concept_id == concept_id,
                ConceptAttributeAuthorityWeight.attribute_term_id == attribute_term_id,
                ConceptAttributeAuthorityWeight.authority_id == authority_id,
            )
        )
        .first()
    )

    if weight:
        # Update existing weight
        weight.base_weight = request.base_weight
        weight.learned_weight = request.learned_weight
    else:
        # Create new weight record
        weight = ConceptAttributeAuthorityWeight(
            concept_id=concept_id,
            attribute_term_id=attribute_term_id,
            authority_id=authority_id,
            base_weight=request.base_weight,
            learned_weight=request.learned_weight,
        )
        db.add(weight)

    db.commit()
    db.refresh(weight)

    return ConceptAttributeAuthorityWeightResponse(
        concept_id=weight.concept_id,
        attribute_term_id=weight.attribute_term_id,
        authority_id=weight.authority_id,
        base_weight=weight.base_weight,
        learned_weight=weight.learned_weight,
        updated_at=weight.updated_at.isoformat() if weight.updated_at else None,
        concept_name=concept.canonical_name,
        attribute_term_name=attribute_term.external_name,
        authority_name=authority.name,
        effective_weight=weight.learned_weight if weight.learned_weight is not None else weight.base_weight,
    )


@router.patch("/{concept_id}/attribute-authority-weights/{attribute_term_id}/{authority_id}", response_model=ConceptAttributeAuthorityWeightResponse)
def update_concept_attribute_authority_weight(
    concept_id: int,
    attribute_term_id: int,
    authority_id: int,
    request: ConceptAttributeAuthorityWeightUpdateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Update authority-specific weights."""
    weight = (
        db.query(ConceptAttributeAuthorityWeight)
        .filter(
            and_(
                ConceptAttributeAuthorityWeight.concept_id == concept_id,
                ConceptAttributeAuthorityWeight.attribute_term_id == attribute_term_id,
                ConceptAttributeAuthorityWeight.authority_id == authority_id,
            )
        )
        .first()
    )

    if not weight:
        raise HTTPException(status_code=404, detail="Authority weight not found")

    # Update only provided fields
    if request.base_weight is not None:
        weight.base_weight = request.base_weight
    if request.learned_weight is not None:
        weight.learned_weight = request.learned_weight

    db.commit()
    db.refresh(weight)

    concept = db.query(Concept).filter(Concept.id == concept_id).first()

    return ConceptAttributeAuthorityWeightResponse(
        concept_id=weight.concept_id,
        attribute_term_id=weight.attribute_term_id,
        authority_id=weight.authority_id,
        base_weight=weight.base_weight,
        learned_weight=weight.learned_weight,
        updated_at=weight.updated_at.isoformat() if weight.updated_at else None,
        concept_name=concept.canonical_name if concept else None,
        attribute_term_name=weight.attribute_term.external_name if weight.attribute_term else None,
        authority_name=weight.authority.name if weight.authority else None,
        effective_weight=weight.learned_weight if weight.learned_weight is not None else weight.base_weight,
    )


@router.delete("/{concept_id}/attribute-authority-weights/{attribute_term_id}/{authority_id}", status_code=204)
def delete_concept_attribute_authority_weight(
    concept_id: int,
    attribute_term_id: int,
    authority_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Delete authority-specific weights."""
    weight = (
        db.query(ConceptAttributeAuthorityWeight)
        .filter(
            and_(
                ConceptAttributeAuthorityWeight.concept_id == concept_id,
                ConceptAttributeAuthorityWeight.attribute_term_id == attribute_term_id,
                ConceptAttributeAuthorityWeight.authority_id == authority_id,
            )
        )
        .first()
    )

    if not weight:
        raise HTTPException(status_code=404, detail="Authority weight not found")

    db.delete(weight)
    db.commit()

    return Response(status_code=204)


# ============================================================================
# Concept Review Evidence
# ============================================================================


@router.get("/{concept_id}/review-evidence", response_model=list[ConceptReviewEvidenceResponse])
def get_concept_review_evidence(
    concept_id: int,
    image_id: Optional[int] = Query(None, description="Filter by image"),
    attribute_term_id: Optional[int] = Query(None, description="Filter by attribute term"),
    evidence_kind: Optional[str] = Query(None, description="Filter by evidence kind"),
    verdict: Optional[str] = Query(None, description="Filter by verdict"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Any:
    """Get review evidence for a concept."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    query = (
        db.query(ConceptReviewEvidence)
        .filter(ConceptReviewEvidence.concept_id == concept_id)
    )

    if image_id:
        query = query.filter(ConceptReviewEvidence.image_id == image_id)
    if attribute_term_id:
        query = query.filter(ConceptReviewEvidence.attribute_term_id == attribute_term_id)
    if evidence_kind:
        query = query.filter(ConceptReviewEvidence.evidence_kind == evidence_kind)
    if verdict:
        query = query.filter(ConceptReviewEvidence.verdict == verdict)

    query = query.order_by(desc(ConceptReviewEvidence.created_at)).offset(offset).limit(limit)

    evidence_records = query.all()

    return [
        ConceptReviewEvidenceResponse(
            id=e.id,
            concept_id=e.concept_id,
            image_id=e.image_id,
            attribute_term_id=e.attribute_term_id,
            evidence_kind=e.evidence_kind,
            verdict=e.verdict,
            confidence=e.confidence,
            notes=e.notes,
            reviewer=e.reviewer,
            created_at=e.created_at.isoformat() if e.created_at else None,
            concept_name=concept.canonical_name,
            image_file_name=e.image.file_name if e.image else None,
            image_thumbnail_url=_image_thumbnail_url(e.image),
            attribute_term_name=e.attribute_term.external_name if e.attribute_term else None,
        )
        for e in evidence_records
    ]


@router.get("/{concept_id}/review-evidence/{evidence_id}", response_model=ConceptReviewEvidenceResponse)
def get_concept_review_evidence_by_id(
    concept_id: int,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """Get a specific review evidence record."""
    evidence = (
        db.query(ConceptReviewEvidence)
        .filter(
            and_(
                ConceptReviewEvidence.concept_id == concept_id,
                ConceptReviewEvidence.id == evidence_id,
            )
        )
        .first()
    )

    if not evidence:
        raise HTTPException(status_code=404, detail="Review evidence not found")

    concept = db.query(Concept).filter(Concept.id == concept_id).first()

    return ConceptReviewEvidenceResponse(
        id=evidence.id,
        concept_id=evidence.concept_id,
        image_id=evidence.image_id,
        attribute_term_id=evidence.attribute_term_id,
        evidence_kind=evidence.evidence_kind,
        verdict=evidence.verdict,
        confidence=evidence.confidence,
        notes=evidence.notes,
        reviewer=evidence.reviewer,
        created_at=evidence.created_at.isoformat() if evidence.created_at else None,
        concept_name=concept.canonical_name if concept else None,
        image_file_name=evidence.image.file_name if evidence.image else None,
        image_thumbnail_url=_image_thumbnail_url(evidence.image),
        attribute_term_name=evidence.attribute_term.external_name if evidence.attribute_term else None,
    )


@router.post("/{concept_id}/review-evidence", response_model=ConceptReviewEvidenceResponse, status_code=201)
def create_concept_review_evidence(
    concept_id: int,
    request: ConceptReviewEvidenceCreateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Create a new review evidence record."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    # Verify image exists
    image = db.query(ImageModel).filter(ImageModel.id == request.image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Verify attribute term exists (if provided)
    if request.attribute_term_id:
        attribute_term = db.query(AuthorityTerm).filter(AuthorityTerm.id == request.attribute_term_id).first()
        if not attribute_term:
            raise HTTPException(status_code=404, detail="Attribute term not found")

    evidence = ConceptReviewEvidence(
        concept_id=concept_id,
        image_id=request.image_id,
        attribute_term_id=request.attribute_term_id,
        evidence_kind=request.evidence_kind,
        verdict=request.verdict,
        confidence=request.confidence,
        notes=request.notes,
        reviewer=request.reviewer,
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)

    return ConceptReviewEvidenceResponse(
        id=evidence.id,
        concept_id=evidence.concept_id,
        image_id=evidence.image_id,
        attribute_term_id=evidence.attribute_term_id,
        evidence_kind=evidence.evidence_kind,
        verdict=evidence.verdict,
        confidence=evidence.confidence,
        notes=evidence.notes,
        reviewer=evidence.reviewer,
        created_at=evidence.created_at.isoformat() if evidence.created_at else None,
        concept_name=concept.canonical_name,
        image_file_name=image.file_name,
        image_thumbnail_url=_image_thumbnail_url(image),
        attribute_term_name=evidence.attribute_term.external_name if evidence.attribute_term else None,
    )


@router.patch("/{concept_id}/review-evidence/{evidence_id}", response_model=ConceptReviewEvidenceResponse)
def update_concept_review_evidence(
    concept_id: int,
    evidence_id: int,
    request: ConceptReviewEvidenceUpdateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Update a review evidence record."""
    evidence = (
        db.query(ConceptReviewEvidence)
        .filter(
            and_(
                ConceptReviewEvidence.concept_id == concept_id,
                ConceptReviewEvidence.id == evidence_id,
            )
        )
        .first()
    )

    if not evidence:
        raise HTTPException(status_code=404, detail="Review evidence not found")

    # Update only provided fields
    if request.verdict is not None:
        evidence.verdict = request.verdict
    if request.confidence is not None:
        evidence.confidence = request.confidence
    if request.notes is not None:
        evidence.notes = request.notes

    db.commit()
    db.refresh(evidence)

    concept = db.query(Concept).filter(Concept.id == concept_id).first()

    return ConceptReviewEvidenceResponse(
        id=evidence.id,
        concept_id=evidence.concept_id,
        image_id=evidence.image_id,
        attribute_term_id=evidence.attribute_term_id,
        evidence_kind=evidence.evidence_kind,
        verdict=evidence.verdict,
        confidence=evidence.confidence,
        notes=evidence.notes,
        reviewer=evidence.reviewer,
        created_at=evidence.created_at.isoformat() if evidence.created_at else None,
        concept_name=concept.canonical_name if concept else None,
        image_file_name=evidence.image.file_name if evidence.image else None,
        image_thumbnail_url=_image_thumbnail_url(evidence.image),
        attribute_term_name=evidence.attribute_term.external_name if evidence.attribute_term else None,
    )


@router.delete("/{concept_id}/review-evidence/{evidence_id}", status_code=204)
def delete_concept_review_evidence(
    concept_id: int,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Delete a review evidence record."""
    evidence = (
        db.query(ConceptReviewEvidence)
        .filter(
            and_(
                ConceptReviewEvidence.concept_id == concept_id,
                ConceptReviewEvidence.id == evidence_id,
            )
        )
        .first()
    )

    if not evidence:
        raise HTTPException(status_code=404, detail="Review evidence not found")

    db.delete(evidence)
    db.commit()

    return Response(status_code=204)


@router.post("/review-evidence/bulk", status_code=201)
def create_bulk_review_evidence(
    request: BulkReviewEvidenceRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Bulk create review evidence records."""
    created_count = 0
    errors = []

    for evidence_req in request.evidence_records:
        try:
            # Verify concept exists
            concept = db.query(Concept).filter(Concept.id == evidence_req.concept_id).first()
            if not concept:
                errors.append({"concept_id": evidence_req.concept_id, "error": "Concept not found"})
                continue

            # Verify image exists
            image = db.query(ImageModel).filter(ImageModel.id == evidence_req.image_id).first()
            if not image:
                errors.append({"image_id": evidence_req.image_id, "error": "Image not found"})
                continue

            # Verify attribute term exists (if provided)
            if evidence_req.attribute_term_id:
                attribute_term = db.query(AuthorityTerm).filter(AuthorityTerm.id == evidence_req.attribute_term_id).first()
                if not attribute_term:
                    errors.append({"attribute_term_id": evidence_req.attribute_term_id, "error": "Attribute term not found"})
                    continue

            evidence = ConceptReviewEvidence(
                concept_id=evidence_req.concept_id,
                image_id=evidence_req.image_id,
                attribute_term_id=evidence_req.attribute_term_id,
                evidence_kind=evidence_req.evidence_kind,
                verdict=evidence_req.verdict,
                confidence=evidence_req.confidence,
                notes=evidence_req.notes,
                reviewer=evidence_req.reviewer,
            )
            db.add(evidence)
            created_count += 1
        except Exception as e:
            errors.append({"error": str(e)})

    db.commit()

    return {"created_count": created_count, "error_count": len(errors), "errors": errors}


# ============================================================================
# Concept Observations (with weighting fields)
# ============================================================================


@router.get("/{concept_id}/observations", response_model=list[ConceptObservationResponse])
def get_concept_observations(
    concept_id: int,
    weighted_only: bool = Query(False, description="Filter to only observations with weights"),
    training_role: Optional[str] = Query(None, description="Filter by training role"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Any:
    """Get observations for a concept with weighting fields."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    query = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.concept_id == concept_id)
    )

    if weighted_only:
        query = query.filter(ImageConceptObservation.observation_weight.isnot(None))
    if training_role:
        query = query.filter(ImageConceptObservation.training_role == training_role)

    query = query.order_by(desc(ImageConceptObservation.created_at)).offset(offset).limit(limit)

    observations = query.all()

    return [
        ConceptObservationResponse(
            id=obs.id,
            image_id=obs.image_id,
            concept_id=obs.concept_id,
            authority_id=obs.authority_id,
            authority_term_id=obs.authority_term_id,
            tool_id=obs.tool_id,
            analysis_data_id=obs.analysis_data_id,
            source_type=obs.source_type,
            certainty_label=obs.certainty_label,
            is_present=obs.is_present,
            is_curated=obs.is_curated,
            confidence=obs.confidence,
            observation_weight=obs.observation_weight,
            review_confidence=obs.review_confidence,
            training_role=obs.training_role,
            concept_strength_weight=obs.concept_strength_weight,
            created_at=obs.created_at.isoformat() if obs.created_at else None,
            updated_at=obs.updated_at.isoformat() if obs.updated_at else None,
            concept_name=concept.canonical_name,
            image_file_name=obs.image.file_name if obs.image else None,
            image_thumbnail_url=_image_thumbnail_url(obs.image),
            authority_name=obs.authority.name if obs.authority else None,
        )
        for obs in observations
    ]


@router.get("/observations/{observation_id}", response_model=ConceptObservationResponse)
def get_observation_by_id(
    observation_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """Get a specific observation by ID."""
    observation = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.id == observation_id)
        .first()
    )

    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")

    concept = db.query(Concept).filter(Concept.id == observation.concept_id).first()

    return ConceptObservationResponse(
        id=observation.id,
        image_id=observation.image_id,
        concept_id=observation.concept_id,
        authority_id=observation.authority_id,
        authority_term_id=observation.authority_term_id,
        tool_id=observation.tool_id,
        analysis_data_id=observation.analysis_data_id,
        source_type=observation.source_type,
        certainty_label=observation.certainty_label,
        is_present=observation.is_present,
        is_curated=observation.is_curated,
        confidence=observation.confidence,
        observation_weight=observation.observation_weight,
        review_confidence=observation.review_confidence,
        training_role=observation.training_role,
        concept_strength_weight=observation.concept_strength_weight,
        created_at=observation.created_at.isoformat() if observation.created_at else None,
        updated_at=observation.updated_at.isoformat() if observation.updated_at else None,
        concept_name=concept.canonical_name if concept else None,
        image_file_name=observation.image.file_name if observation.image else None,
        image_thumbnail_url=_image_thumbnail_url(observation.image),
        authority_name=observation.authority.name if observation.authority else None,
    )


@router.patch("/observations/{observation_id}", response_model=ConceptObservationResponse)
def update_observation(
    observation_id: int,
    request: ConceptObservationUpdateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Update observation weighting fields."""
    observation = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.id == observation_id)
        .first()
    )

    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")

    # Update only provided fields
    if request.observation_weight is not None:
        observation.observation_weight = request.observation_weight
    if request.review_confidence is not None:
        observation.review_confidence = request.review_confidence
    if request.training_role is not None:
        observation.training_role = request.training_role
    if request.concept_strength_weight is not None:
        observation.concept_strength_weight = request.concept_strength_weight

    db.commit()
    db.refresh(observation)

    concept = db.query(Concept).filter(Concept.id == observation.concept_id).first()

    return ConceptObservationResponse(
        id=observation.id,
        image_id=observation.image_id,
        concept_id=observation.concept_id,
        authority_id=observation.authority_id,
        authority_term_id=observation.authority_term_id,
        tool_id=observation.tool_id,
        analysis_data_id=observation.analysis_data_id,
        source_type=observation.source_type,
        certainty_label=observation.certainty_label,
        is_present=observation.is_present,
        is_curated=observation.is_curated,
        confidence=observation.confidence,
        observation_weight=observation.observation_weight,
        review_confidence=observation.review_confidence,
        training_role=observation.training_role,
        concept_strength_weight=observation.concept_strength_weight,
        created_at=observation.created_at.isoformat() if observation.created_at else None,
        updated_at=observation.updated_at.isoformat() if observation.updated_at else None,
        concept_name=concept.canonical_name if concept else None,
        image_file_name=observation.image.file_name if observation.image else None,
        image_thumbnail_url=_image_thumbnail_url(observation.image),
        authority_name=observation.authority.name if observation.authority else None,
    )


@router.post("/observations/bulk-update", response_model=dict[str, Any])
def bulk_update_observations(
    request: BulkObservationWeightUpdateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Bulk update observation weights."""
    updated_count = 0
    errors = []

    for update in request.observation_updates:
        try:
            obs_id = update.get("id")
            if not obs_id:
                errors.append({"update": update, "error": "Missing observation ID"})
                continue

            observation = (
                db.query(ImageConceptObservation)
                .filter(ImageConceptObservation.id == obs_id)
                .first()
            )

            if not observation:
                errors.append({"observation_id": obs_id, "error": "Observation not found"})
                continue

            # Update only provided fields
            if "observation_weight" in update and update["observation_weight"] is not None:
                observation.observation_weight = update["observation_weight"]
            if "review_confidence" in update and update["review_confidence"] is not None:
                observation.review_confidence = update["review_confidence"]
            if "training_role" in update and update["training_role"] is not None:
                observation.training_role = update["training_role"]
            if "concept_strength_weight" in update and update["concept_strength_weight"] is not None:
                observation.concept_strength_weight = update["concept_strength_weight"]

            updated_count += 1
        except Exception as e:
            errors.append({"error": str(e)})

    db.commit()

    return {"updated_count": updated_count, "error_count": len(errors), "errors": errors}


# ============================================================================
# Concept Profile Weighting Summary
# ============================================================================


@router.get("/{concept_id}/weighting-summary", response_model=ConceptProfileWeightingSummary)
def get_concept_weighting_summary(
    concept_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """Get aggregated weighting statistics for a concept."""
    # Verify concept exists
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    # Total observations
    total_observations = (
        db.query(func.count(ImageConceptObservation.id))
        .filter(ImageConceptObservation.concept_id == concept_id)
        .scalar() or 0
    )

    # Weighted observations (have observation_weight)
    weighted_observations = (
        db.query(func.count(ImageConceptObservation.id))
        .filter(
            and_(
                ImageConceptObservation.concept_id == concept_id,
                ImageConceptObservation.observation_weight.isnot(None),
            )
        )
        .scalar() or 0
    )

    # Count by training role
    training_role_counts = (
        db.query(
            ImageConceptObservation.training_role,
            func.count(ImageConceptObservation.id),
        )
        .filter(
            and_(
                ImageConceptObservation.concept_id == concept_id,
                ImageConceptObservation.training_role.isnot(None),
            )
        )
        .group_by(ImageConceptObservation.training_role)
        .all()
    )

    role_counts = {role: count for role, count in training_role_counts}

    # Averages
    avg_observation_weight = (
        db.query(func.avg(ImageConceptObservation.observation_weight))
        .filter(
            and_(
                ImageConceptObservation.concept_id == concept_id,
                ImageConceptObservation.observation_weight.isnot(None),
            )
        )
        .scalar()
    )

    avg_concept_strength = (
        db.query(func.avg(ImageConceptObservation.concept_strength_weight))
        .filter(
            and_(
                ImageConceptObservation.concept_id == concept_id,
                ImageConceptObservation.concept_strength_weight.isnot(None),
            )
        )
        .scalar()
    )

    avg_review_confidence = (
        db.query(func.avg(ImageConceptObservation.review_confidence))
        .filter(
            and_(
                ImageConceptObservation.concept_id == concept_id,
                ImageConceptObservation.review_confidence.isnot(None),
            )
        )
        .scalar()
    )

    return ConceptProfileWeightingSummary(
        concept_id=concept_id,
        concept_name=concept.canonical_name,
        total_observations=total_observations,
        weighted_observations=weighted_observations,
        positive_exemplars=role_counts.get("positive_exemplar", 0),
        hard_negatives=role_counts.get("hard_negative", 0),
        style_refs=role_counts.get("style_ref", 0),
        context_refs=role_counts.get("context_ref", 0),
        anomalies=role_counts.get("anomaly", 0),
        avg_observation_weight=float(avg_observation_weight) if avg_observation_weight else None,
        avg_concept_strength=float(avg_concept_strength) if avg_concept_strength else None,
        avg_review_confidence=float(avg_review_confidence) if avg_review_confidence else None,
    )


@router.get("/{concept_id}/scored-images", response_model=ConceptScoringResponse)
def get_concept_scored_images(
    concept_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    alpha_identity: float = Query(0.55, ge=0.0, le=1.0),
    alpha_attribute: float = Query(0.25, ge=0.0, le=1.0),
    alpha_context: float = Query(0.15, ge=0.0, le=1.0),
    alpha_style: float = Query(0.05, ge=0.0, le=1.0),
    epsilon: float = Query(0.05, ge=0.000001, le=0.5),
    db: Session = Depends(get_db),
) -> Any:
    """Compute concept training scores from observations and review evidence."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    image_id_rows = (
        db.query(ImageConceptObservation.image_id)
        .filter(ImageConceptObservation.concept_id == concept_id)
        .group_by(ImageConceptObservation.image_id)
        .order_by(func.max(ImageConceptObservation.created_at).desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    image_ids = [row[0] for row in image_id_rows]

    if not image_ids:
        return ConceptScoringResponse(
            concept_id=concept_id,
            concept_name=concept.canonical_name,
            total_images=0,
            scoring=ConceptScoringConfig(
                alpha_identity=alpha_identity,
                alpha_attribute=alpha_attribute,
                alpha_context=alpha_context,
                alpha_style=alpha_style,
                epsilon=epsilon,
            ),
            results=[],
        )

    observations = (
        db.query(ImageConceptObservation)
        .filter(
            and_(
                ImageConceptObservation.concept_id == concept_id,
                ImageConceptObservation.image_id.in_(image_ids),
            )
        )
        .all()
    )

    observations_by_image: dict[int, list[ImageConceptObservation]] = {}
    for obs in observations:
        observations_by_image.setdefault(obs.image_id, []).append(obs)

    profiles = (
        db.query(ConceptAttributeTermProfile)
        .filter(ConceptAttributeTermProfile.concept_id == concept_id)
        .all()
    )

    authority_weights = (
        db.query(ConceptAttributeAuthorityWeight)
        .filter(ConceptAttributeAuthorityWeight.concept_id == concept_id)
        .all()
    )
    authority_weight_map: dict[tuple[int, int], float] = {
        (row.attribute_term_id, row.authority_id): (row.learned_weight if row.learned_weight is not None else row.base_weight)
        for row in authority_weights
    }

    anomaly_evidence = (
        db.query(ConceptReviewEvidence)
        .filter(
            and_(
                ConceptReviewEvidence.concept_id == concept_id,
                ConceptReviewEvidence.image_id.in_(image_ids),
                ConceptReviewEvidence.evidence_kind == "anomaly",
            )
        )
        .all()
    )
    anomaly_by_image: dict[int, list[ConceptReviewEvidence]] = {}
    for item in anomaly_evidence:
        anomaly_by_image.setdefault(item.image_id, []).append(item)

    image_rows = db.query(ImageModel).filter(ImageModel.id.in_(image_ids)).all()
    images_by_id = {img.id: img for img in image_rows}

    results: list[ConceptScoredImage] = []
    for image_id in image_ids:
        image_observations = observations_by_image.get(image_id, [])
        if not image_observations:
            continue

        identity_score = _compute_identity_score(image_observations)
        attribute_score = _compute_attribute_score(image_observations, profiles, authority_weight_map, epsilon)
        context_score = 1.0
        style_score = 1.0
        anomaly_penalty = _compute_anomaly_penalty(anomaly_by_image.get(image_id, []))

        final_score = (
            (max(epsilon, identity_score) ** alpha_identity)
            * (max(epsilon, attribute_score) ** alpha_attribute)
            * (max(epsilon, context_score) ** alpha_context)
            * (max(epsilon, style_score) ** alpha_style)
            * anomaly_penalty
        )

        image = images_by_id.get(image_id)
        results.append(
            ConceptScoredImage(
                image_id=image_id,
                image_file_name=image.file_name if image else None,
                image_file_hash=image.file_hash if image else None,
                image_thumbnail_url=_image_thumbnail_url(image),
                image_style_concept_id=image.user_image_style_concept_id if image else None,
                image_style_concept_name=(image.user_image_style_concept.canonical_name if image and image.user_image_style_concept else None),
                image_style_source=image.user_image_style_source if image else None,
                image_style_confidence=image.user_image_style_confidence if image else None,
                observation_count=len(image_observations),
                identity_score=identity_score,
                attribute_score=attribute_score,
                context_score=context_score,
                style_score=style_score,
                anomaly_penalty=anomaly_penalty,
                final_score=_clamp01(final_score),
            )
        )

    results.sort(key=lambda item: item.final_score, reverse=True)

    return ConceptScoringResponse(
        concept_id=concept_id,
        concept_name=concept.canonical_name,
        total_images=len(results),
        scoring=ConceptScoringConfig(
            alpha_identity=alpha_identity,
            alpha_attribute=alpha_attribute,
            alpha_context=alpha_context,
            alpha_style=alpha_style,
            epsilon=epsilon,
        ),
        results=results,
    )


@router.get("/{concept_id}/review-sessions", response_model=list[ConceptReviewSessionResponse])
def get_concept_review_sessions(
    concept_id: int,
    status: Optional[str] = Query(None, description="Filter by session status"),
    db: Session = Depends(get_db),
) -> Any:
    """List review sessions for a concept."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    query = db.query(ConceptReviewSession).filter(ConceptReviewSession.concept_id == concept_id)
    if status:
        query = query.filter(ConceptReviewSession.status == status)

    sessions = query.order_by(desc(ConceptReviewSession.created_at)).all()
    return [_session_to_response(session) for session in sessions]


@router.post("/{concept_id}/review-sessions", response_model=ConceptReviewSessionResponse, status_code=201)
def create_concept_review_session(
    concept_id: int,
    request: ConceptReviewSessionCreateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Create a new process-oriented review session for a concept."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    session = ConceptReviewSession(
        concept_id=concept_id,
        status="open",
        notes=request.notes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_to_response(session)


@router.get("/review-sessions/{session_id}", response_model=ConceptReviewSessionResponse)
def get_review_session(
    session_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """Get one review session by ID."""
    session = db.query(ConceptReviewSession).filter(ConceptReviewSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")
    return _session_to_response(session)


@router.patch("/review-sessions/{session_id}", response_model=ConceptReviewSessionResponse)
def update_review_session(
    session_id: int,
    request: ConceptReviewSessionUpdateRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Update review session status/notes."""
    session = db.query(ConceptReviewSession).filter(ConceptReviewSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    if request.status is not None:
        session.status = request.status
        if request.status in {"completed", "abandoned"}:
            session.closed_at = datetime.utcnow()
        else:
            session.closed_at = None
    if request.notes is not None:
        session.notes = request.notes

    session.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return _session_to_response(session)


@router.get("/review-sessions/{session_id}/assessments", response_model=list[ConceptReviewAssessmentResponse])
def get_review_session_assessments(
    session_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """List all image assessments in one review session."""
    session = db.query(ConceptReviewSession).filter(ConceptReviewSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    assessments = (
        db.query(ConceptReviewAssessment)
        .filter(ConceptReviewAssessment.session_id == session_id)
        .order_by(desc(ConceptReviewAssessment.updated_at), desc(ConceptReviewAssessment.created_at))
        .all()
    )
    return [_assessment_to_response(row) for row in assessments]


@router.put("/review-sessions/{session_id}/assessments/{image_id}", response_model=ConceptReviewAssessmentResponse)
def upsert_review_assessment(
    session_id: int,
    image_id: int,
    request: ConceptReviewAssessmentUpsertRequest,
    db: Session = Depends(get_db),
) -> Any:
    """Upsert one image assessment in a review session."""
    if request.image_id != image_id:
        raise HTTPException(status_code=400, detail="Path image_id must match request.image_id")

    session = db.query(ConceptReviewSession).filter(ConceptReviewSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    image = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if request.image_style_concept_id is not None:
        style_concept = db.query(Concept).filter(Concept.id == request.image_style_concept_id).first()
        if not style_concept:
            raise HTTPException(status_code=404, detail="Style concept not found")

    assessment = (
        db.query(ConceptReviewAssessment)
        .filter(
            and_(
                ConceptReviewAssessment.session_id == session_id,
                ConceptReviewAssessment.image_id == image_id,
            )
        )
        .first()
    )

    now = datetime.utcnow()
    if assessment is None:
        assessment = ConceptReviewAssessment(
            session_id=session_id,
            concept_id=session.concept_id,
            image_id=image_id,
            created_at=now,
        )
        db.add(assessment)

    assessment.predominance_rating = request.predominance_rating
    assessment.quality_rating = request.quality_rating
    assessment.accuracy_rating = request.accuracy_rating
    assessment.attribute_support_rating = request.attribute_support_rating
    assessment.context_incongruent = request.context_incongruent
    assessment.context_anachronistic = request.context_anachronistic
    assessment.context_anatopismic = request.context_anatopismic
    assessment.context_nonsensical = request.context_nonsensical
    assessment.context_anomalous_form = request.context_anomalous_form
    assessment.anomaly_present = request.anomaly_present
    assessment.anomaly_kind = request.anomaly_kind
    assessment.anomaly_degree = request.anomaly_degree
    assessment.deviation_present = request.deviation_present
    assessment.deviation_body_variant = request.deviation_body_variant
    assessment.deviation_exaggerated = request.deviation_exaggerated
    assessment.deviation_extra_feature = request.deviation_extra_feature
    assessment.deviation_fusion = request.deviation_fusion
    assessment.deviation_kind = request.deviation_kind
    assessment.deviation_degree = request.deviation_degree
    assessment.image_style_concept_id = request.image_style_concept_id
    assessment.image_style_source = request.image_style_source
    assessment.image_style_confidence = request.image_style_confidence
    assessment.attribute_checks = request.attribute_checks
    assessment.notes = request.notes
    assessment.updated_at = now

    # Persist image-level style override from reviewed assessment.
    if request.image_style_concept_id is not None:
        image.user_image_style_concept_id = request.image_style_concept_id
        image.user_image_style_source = request.image_style_source or "review"
        image.user_image_style_confidence = request.image_style_confidence

    db.commit()
    db.refresh(assessment)
    return _assessment_to_response(assessment)
