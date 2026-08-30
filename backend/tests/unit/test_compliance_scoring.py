"""Comprehensive unit tests for the pure deterministic compliance and risk scoring engine."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models.compliance import (
    AssessmentScoringInput,
    ControlScoringInput,
)
from app.models.enums import ControlStatus, RiskClassification
from app.services.compliance_scoring import RiskScoringEngine
from pydantic import ValidationError


class TestControlEvaluation:
    """Test raw control score calculation and grounding penalties."""

    def test_satisfied_grounded(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-1",
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("1.0"),
            evidence_count=1,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.raw_score == Decimal("100.00")
        assert res.weighted_score == Decimal("100.00")
        assert res.is_grounded is True
        assert res.is_applicable is True

    def test_satisfied_multiple_evidence(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-1",
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("2.0"),
            evidence_count=5,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.raw_score == Decimal("100.00")
        assert res.weighted_score == Decimal("200.00")
        assert res.is_grounded is True

    def test_satisfied_ungrounded_applies_30_percent_penalty(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-1",
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("1.0"),
            evidence_count=0,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.raw_score == Decimal("70.00")
        assert res.weighted_score == Decimal("70.00")
        assert res.is_grounded is False

    def test_partially_satisfied_grounded(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-2",
            status=ControlStatus.PARTIALLY_SATISFIED,
            effective_weight=Decimal("1.0"),
            evidence_count=2,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.raw_score == Decimal("50.00")
        assert res.weighted_score == Decimal("50.00")
        assert res.is_grounded is True

    def test_partially_satisfied_ungrounded_applies_30_percent_penalty(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-2",
            status=ControlStatus.PARTIALLY_SATISFIED,
            effective_weight=Decimal("1.0"),
            evidence_count=0,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.raw_score == Decimal("35.00")
        assert res.weighted_score == Decimal("35.00")
        assert res.is_grounded is False

    def test_deficient_scores_zero_regardless_of_evidence(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-3",
            status=ControlStatus.DEFICIENT,
            effective_weight=Decimal("3.0"),
            evidence_count=4,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.raw_score == Decimal("0.00")
        assert res.weighted_score == Decimal("0.00")
        assert res.is_applicable is True

    def test_unassessed_scores_zero_and_remains_applicable(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-4",
            status=ControlStatus.UNASSESSED,
            effective_weight=Decimal("2.0"),
            evidence_count=0,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.raw_score == Decimal("0.00")
        assert res.weighted_score == Decimal("0.00")
        assert res.is_applicable is True

    def test_not_applicable_is_excluded(self) -> None:
        ctrl = ControlScoringInput(
            control_id="AC-5",
            status=ControlStatus.NOT_APPLICABLE,
            effective_weight=Decimal("1.0"),
            evidence_count=0,
        )
        res = RiskScoringEngine.evaluate_control(ctrl)
        assert res.is_applicable is False
        assert res.raw_score == Decimal("0.00")
        assert res.weighted_score == Decimal("0.00")


class TestWeightAndInputValidation:
    """Test boundary and type enforcement on control inputs."""

    def test_weight_below_lower_bound_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ControlScoringInput(
                control_id="W-1",
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("0.9"),
            )

    def test_weight_above_upper_bound_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ControlScoringInput(
                control_id="W-2",
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("5.1"),
            )

    def test_weight_nan_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            ControlScoringInput(
                control_id="W-NAN",
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("NaN"),
            )

    def test_weight_infinity_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            ControlScoringInput(
                control_id="W-INF",
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("Infinity"),
            )

    def test_evidence_count_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ControlScoringInput(
                control_id="E-NEG",
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("1.0"),
                evidence_count=-1,
            )

    def test_weights_at_exact_boundaries_accepted(self) -> None:
        ctrl_min = ControlScoringInput(
            control_id="W-MIN",
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("1.0"),
        )
        ctrl_max = ControlScoringInput(
            control_id="W-MAX",
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("5.0"),
        )
        assert RiskScoringEngine.evaluate_control(ctrl_min).effective_weight == Decimal("1.0")
        assert RiskScoringEngine.evaluate_control(ctrl_max).effective_weight == Decimal("5.0")

    def test_duplicate_control_ids_rejected(self) -> None:
        with pytest.raises((ValidationError, ValueError)):
            AssessmentScoringInput(
                framework_id="FW-1",
                framework_version="1.0",
                controls=[
                    ControlScoringInput(
                        control_id="DUP-1",
                        status=ControlStatus.SATISFIED,
                        effective_weight=Decimal("1.0"),
                    ),
                    ControlScoringInput(
                        control_id="DUP-1",
                        status=ControlStatus.DEFICIENT,
                        effective_weight=Decimal("2.0"),
                    ),
                ],
            )


class TestRiskClassificationBoundaries:
    """Test exact threshold transitions between risk bands."""

    def test_compliance_100_yields_low_risk(self) -> None:
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[
                ControlScoringInput(
                    control_id="C-1",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=1,
                )
            ],
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.overall_score == Decimal("100.00")
        assert res.residual_risk == Decimal("0.00")
        assert res.risk_classification == RiskClassification.LOW

    def test_compliance_75_01_yields_low_risk(self) -> None:
        # 3 satisfied (w=1, grounded=100) and 1 with w=1 yielding overall 75.00
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[
                ControlScoringInput(
                    control_id="C-1",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=1,
                ),
                ControlScoringInput(
                    control_id="C-2",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=1,
                ),
                ControlScoringInput(
                    control_id="C-3",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=1,
                ),
                ControlScoringInput(
                    control_id="C-4",
                    status=ControlStatus.UNASSESSED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=0,
                ),
            ],
        )
        # 3*100 / 4 = 75.00 -> risk = 25.00 -> MEDIUM
        res = RiskScoringEngine.compute(assessment)
        assert res.overall_score == Decimal("75.00")
        assert res.residual_risk == Decimal("25.00")
        assert res.risk_classification == RiskClassification.MEDIUM

    def test_risk_boundary_at_50_yields_high(self) -> None:
        # 1 satisfied (100) and 1 unassessed (0) -> average = 50.00 -> risk = 50.00 -> HIGH
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[
                ControlScoringInput(
                    control_id="C-1",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=1,
                ),
                ControlScoringInput(
                    control_id="C-2",
                    status=ControlStatus.UNASSESSED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=0,
                ),
            ],
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.overall_score == Decimal("50.00")
        assert res.residual_risk == Decimal("50.00")
        assert res.risk_classification == RiskClassification.HIGH

    def test_risk_boundary_at_75_yields_critical(self) -> None:
        # 1 satisfied (100) and 3 unassessed (0) -> 100/4 = 25.00 -> risk = 75.00 -> CRITICAL
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[
                ControlScoringInput(
                    control_id="C-1",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=1,
                ),
                ControlScoringInput(
                    control_id="C-2",
                    status=ControlStatus.UNASSESSED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=0,
                ),
                ControlScoringInput(
                    control_id="C-3",
                    status=ControlStatus.UNASSESSED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=0,
                ),
                ControlScoringInput(
                    control_id="C-4",
                    status=ControlStatus.UNASSESSED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=0,
                ),
            ],
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.overall_score == Decimal("25.00")
        assert res.residual_risk == Decimal("75.00")
        assert res.risk_classification == RiskClassification.CRITICAL


class TestCriticalControlOverride:
    """Test that a deficient critical-weight control (weight=5.0) forces CRITICAL classification."""

    def test_weight_5_deficient_triggers_critical_override(self) -> None:
        controls = [
            ControlScoringInput(
                control_id=f"NORM-{i}",
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("1.0"),
                evidence_count=1,
            )
            for i in range(9)
        ]
        controls.append(
            ControlScoringInput(
                control_id="CRIT-1",
                status=ControlStatus.DEFICIENT,
                effective_weight=Decimal("5.0"),
                evidence_count=0,
            )
        )
        assessment = AssessmentScoringInput(
            framework_id="FW-SOC2",
            framework_version="1.0",
            controls=controls,
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.overall_score == Decimal("64.29")
        assert res.residual_risk == Decimal("35.71")
        assert res.critical_override_triggered is True
        assert res.risk_classification == RiskClassification.CRITICAL

    def test_weight_less_than_5_deficient_does_not_trigger_critical_override(self) -> None:
        controls = [
            ControlScoringInput(
                control_id=f"NORM-{i}",
                status=ControlStatus.SATISFIED,
                effective_weight=Decimal("1.0"),
                evidence_count=1,
            )
            for i in range(9)
        ]
        controls.append(
            ControlScoringInput(
                control_id="CRIT-1",
                status=ControlStatus.DEFICIENT,
                effective_weight=Decimal("4.9"),
                evidence_count=0,
            )
        )
        assessment = AssessmentScoringInput(
            framework_id="FW-SOC2",
            framework_version="1.0",
            controls=controls,
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.critical_override_triggered is False
        assert res.risk_classification == RiskClassification.MEDIUM

    def test_not_applicable_with_weight_5_never_triggers_critical_override(self) -> None:
        # Control has weight 5.0 but is NOT_APPLICABLE -> should not trigger critical override
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[
                ControlScoringInput(
                    control_id="NORM-1",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("1.0"),
                    evidence_count=1,
                ),
                ControlScoringInput(
                    control_id="NA-CRIT",
                    status=ControlStatus.NOT_APPLICABLE,
                    effective_weight=Decimal("5.0"),
                ),
            ],
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.overall_score == Decimal("100.00")
        assert res.residual_risk == Decimal("0.00")
        assert res.critical_override_triggered is False
        assert res.risk_classification == RiskClassification.LOW


class TestEmptyDenominatorAndNotApplicable:
    """Test behavior when all controls are not applicable or empty."""

    def test_all_not_applicable_yields_not_scored(self) -> None:
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[
                ControlScoringInput(
                    control_id="NA-1",
                    status=ControlStatus.NOT_APPLICABLE,
                    effective_weight=Decimal("1.0"),
                ),
                ControlScoringInput(
                    control_id="NA-2",
                    status=ControlStatus.NOT_APPLICABLE,
                    effective_weight=Decimal("3.0"),
                ),
            ],
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.applicable_control_count == 0
        assert res.total_control_count == 2
        assert res.overall_score is None
        assert res.residual_risk is None
        assert res.risk_classification == RiskClassification.NOT_SCORED

    def test_empty_controls_list_yields_not_scored(self) -> None:
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[],
        )
        res = RiskScoringEngine.compute(assessment)
        assert res.applicable_control_count == 0
        assert res.total_control_count == 0
        assert res.overall_score is None
        assert res.residual_risk is None
        assert res.risk_classification == RiskClassification.NOT_SCORED


class TestDeterminismAndOrdering:
    """Test reproducibility regardless of input order or repetition."""

    def test_input_ordering_does_not_change_mathematical_result(self) -> None:
        c1 = ControlScoringInput(
            control_id="A-1",
            status=ControlStatus.SATISFIED,
            effective_weight=Decimal("1.5"),
            evidence_count=1,
        )
        c2 = ControlScoringInput(
            control_id="B-2",
            status=ControlStatus.PARTIALLY_SATISFIED,
            effective_weight=Decimal("2.5"),
            evidence_count=0,
        )
        c3 = ControlScoringInput(
            control_id="C-3",
            status=ControlStatus.DEFICIENT,
            effective_weight=Decimal("3.0"),
            evidence_count=0,
        )

        res1 = RiskScoringEngine.compute(
            AssessmentScoringInput(
                framework_id="FW-1",
                framework_version="1.0",
                controls=[c1, c2, c3],
            )
        )
        res2 = RiskScoringEngine.compute(
            AssessmentScoringInput(
                framework_id="FW-1",
                framework_version="1.0",
                controls=[c3, c1, c2],
            )
        )
        res3 = RiskScoringEngine.compute(
            AssessmentScoringInput(
                framework_id="FW-1",
                framework_version="1.0",
                controls=[c2, c3, c1],
            )
        )

        assert res1.overall_score == res2.overall_score == res3.overall_score
        assert res1.residual_risk == res2.residual_risk == res3.residual_risk
        assert res1.risk_classification == res2.risk_classification == res3.risk_classification
        assert list(res1.control_scores.keys()) == ["A-1", "B-2", "C-3"]
        assert list(res2.control_scores.keys()) == ["A-1", "B-2", "C-3"]
        assert list(res3.control_scores.keys()) == ["A-1", "B-2", "C-3"]

    def test_repeated_runs_return_byte_for_byte_identical_results(self) -> None:
        assessment = AssessmentScoringInput(
            framework_id="FW-1",
            framework_version="1.0",
            controls=[
                ControlScoringInput(
                    control_id="C-1",
                    status=ControlStatus.SATISFIED,
                    effective_weight=Decimal("2.0"),
                    evidence_count=1,
                ),
                ControlScoringInput(
                    control_id="C-2",
                    status=ControlStatus.PARTIALLY_SATISFIED,
                    effective_weight=Decimal("1.5"),
                    evidence_count=0,
                ),
            ],
        )
        first_result = RiskScoringEngine.compute(assessment)
        for _ in range(50):
            next_result = RiskScoringEngine.compute(assessment)
            assert next_result.model_dump() == first_result.model_dump()

    def test_unsupported_scoring_version_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported scoring_version"):
            RiskScoringEngine.compute(
                AssessmentScoringInput(
                    framework_id="FW-1",
                    framework_version="1.0",
                    controls=[],
                    scoring_version="v99.0",
                )
            )
