"""Pure deterministic compliance and risk scoring engine.

This module contains NO framework dependencies, database sessions, or network calls.
Calculations are pure, versioned, and mathematically reproducible.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.models.compliance import (
    AssessmentScoreResult,
    AssessmentScoringInput,
    ControlScoreOutput,
    ControlScoringInput,
)
from app.models.enums import ControlStatus, RiskClassification

SCORING_VERSION_V1 = "v1.0"
TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal("100.00")
ZERO = Decimal("0.00")

# Multipliers for control statuses
STATUS_MULTIPLIERS: dict[ControlStatus, Decimal] = {
    ControlStatus.SATISFIED: Decimal("1.0"),
    ControlStatus.PARTIALLY_SATISFIED: Decimal("0.5"),
    ControlStatus.DEFICIENT: Decimal("0.0"),
    ControlStatus.UNASSESSED: Decimal("0.0"),
    ControlStatus.NOT_APPLICABLE: Decimal("0.0"),
}

UNGROUNDED_PENALTY_MULTIPLIER = Decimal("0.70")
CRITICAL_WEIGHT_THRESHOLD = Decimal("5.0")


class RiskScoringEngine:
    """Deterministic mathematical engine for compliance and residual risk scoring."""

    @classmethod
    def evaluate_control(cls, input_data: ControlScoringInput) -> ControlScoreOutput:
        """Calculate the raw and weighted score for a single control input."""
        status = input_data.status
        weight = Decimal(str(input_data.effective_weight))
        if weight.is_nan() or weight.is_infinite():
            raise ValueError(f"effective_weight must be a finite decimal, got {weight}")
        if weight < Decimal("1.0") or weight > Decimal("5.0"):
            raise ValueError(f"effective_weight must be between 1.0 and 5.0, got {weight}")

        is_applicable = status != ControlStatus.NOT_APPLICABLE
        evidence_count = input_data.evidence_count
        if evidence_count < 0:
            raise ValueError(f"evidence_count must be >= 0, got {evidence_count}")
        is_grounded = evidence_count >= 1

        if not is_applicable:
            raw_score = ZERO
            weighted_score = ZERO
        else:
            base_multiplier = STATUS_MULTIPLIERS[status]
            base_score = base_multiplier * Decimal("100.0")

            if status in (ControlStatus.SATISFIED, ControlStatus.PARTIALLY_SATISFIED):
                if not is_grounded:
                    raw_score = base_score * UNGROUNDED_PENALTY_MULTIPLIER
                else:
                    raw_score = base_score
            else:
                raw_score = ZERO

            weighted_score = raw_score * weight

        return ControlScoreOutput(
            control_id=input_data.control_id,
            status=status,
            effective_weight=weight,
            is_applicable=is_applicable,
            evidence_count=evidence_count,
            is_grounded=is_grounded,
            raw_score=raw_score.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            weighted_score=weighted_score.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
        )

    @classmethod
    def compute(cls, assessment_input: AssessmentScoringInput) -> AssessmentScoreResult:
        """Compute the deterministic compliance score and risk classification.

        Invariant:
        same scoring_version + same framework/control snapshot + same statuses/applicability
        + same effective weights + same validated evidence IDs => identical result.
        """
        scoring_version = assessment_input.scoring_version or SCORING_VERSION_V1
        if scoring_version != SCORING_VERSION_V1:
            raise ValueError(f"Unsupported scoring_version: {scoring_version}")

        # Check for duplicate control IDs
        seen_ids: set[str] = set()
        for c in assessment_input.controls:
            if c.control_id in seen_ids:
                raise ValueError(f"Duplicate control_id found: {c.control_id}")
            seen_ids.add(c.control_id)

        # Deterministic ordering by control_id
        sorted_inputs = sorted(assessment_input.controls, key=lambda c: c.control_id)

        control_scores: dict[str, ControlScoreOutput] = {}
        raw_scores: dict[str, Decimal] = {}

        applicable_count = 0
        total_count = len(sorted_inputs)
        sum_weighted_scores = Decimal("0.0")
        sum_weights = Decimal("0.0")
        critical_override_triggered = False

        for ctrl_input in sorted_inputs:
            ctrl_output = cls.evaluate_control(ctrl_input)
            control_scores[ctrl_input.control_id] = ctrl_output
            raw_scores[ctrl_input.control_id] = ctrl_output.raw_score

            if ctrl_output.is_applicable:
                applicable_count += 1
                sum_weighted_scores += ctrl_output.weighted_score
                sum_weights += ctrl_output.effective_weight

                # Check critical weight override: weight 5.0 + DEFICIENT => CRITICAL
                if (
                    ctrl_output.effective_weight >= CRITICAL_WEIGHT_THRESHOLD
                    and ctrl_output.status == ControlStatus.DEFICIENT
                ):
                    critical_override_triggered = True

        # Handle zero applicable controls
        if applicable_count == 0 or sum_weights == Decimal("0.0"):
            overall_score = None
            residual_risk = None
            risk_classification = RiskClassification.NOT_SCORED
        else:
            raw_average = sum_weighted_scores / sum_weights
            overall_score = raw_average.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            residual_risk = (HUNDRED - overall_score).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

            # Map residual risk to classification bands
            if residual_risk < Decimal("25.00"):
                risk_classification = RiskClassification.LOW
            elif Decimal("25.00") <= residual_risk < Decimal("50.00"):
                risk_classification = RiskClassification.MEDIUM
            elif Decimal("50.00") <= residual_risk < Decimal("75.00"):
                risk_classification = RiskClassification.HIGH
            else:
                risk_classification = RiskClassification.CRITICAL

            # Apply critical override if triggered
            if critical_override_triggered:
                risk_classification = RiskClassification.CRITICAL

        component_breakdown: dict[str, Any] = {
            "scoring_version": scoring_version,
            "total_controls": total_count,
            "applicable_controls": applicable_count,
            "sum_weights": str(sum_weights),
            "sum_weighted_scores": str(sum_weighted_scores),
            "critical_override_triggered": critical_override_triggered,
        }

        return AssessmentScoreResult(
            scoring_version=scoring_version,
            framework_id=assessment_input.framework_id,
            framework_version=assessment_input.framework_version,
            applicable_control_count=applicable_count,
            total_control_count=total_count,
            overall_score=overall_score,
            residual_risk=residual_risk,
            risk_classification=risk_classification,
            critical_override_triggered=critical_override_triggered,
            control_scores=control_scores,
            raw_scores=raw_scores,
            component_breakdown=component_breakdown,
        )

    @classmethod
    def classify_score(cls, score: Decimal) -> RiskClassification:
        """Deterministically map a normalized 0.00..100.00 score to RiskClassification."""
        if score.is_nan() or score.is_infinite():
            raise ValueError(f"score must be a finite decimal, got {score}")
        if score < ZERO or score > HUNDRED:
            raise ValueError(f"score must be between 0.00 and 100.00, got {score}")

        norm_score = score.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        residual_risk = (HUNDRED - norm_score).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        if residual_risk < Decimal("25.00"):
            return RiskClassification.LOW
        elif Decimal("25.00") <= residual_risk < Decimal("50.00"):
            return RiskClassification.MEDIUM
        elif Decimal("50.00") <= residual_risk < Decimal("75.00"):
            return RiskClassification.HIGH
        else:
            return RiskClassification.CRITICAL
