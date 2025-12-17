"""Tests for forensics module."""

import pytest

from swe_cgd.evaluation.forensics import (
    PredictiveMetrics,
    CostAnalysis,
    ForensicsReport,
    InstanceForensics,
    evaluate_decision_gate,
)
from swe_cgd.baseline.generator import CostMetrics


def test_predictive_metrics_p_fail_given_error():
    """Test P(fail | error) calculation."""
    pm = PredictiveMetrics(
        total_instances=100,
        total_with_diagnostic_issues=40,
        total_without_diagnostic_issues=60,
        failed_with_diagnostic_issues=32,  # 80% failure rate
        failed_without_diagnostic_issues=24,  # 40% failure rate
        resolved_with_diagnostic_issues=8,
        resolved_without_diagnostic_issues=36,
    )

    assert pm.p_fail_given_error == pytest.approx(0.8)
    assert pm.p_fail_given_no_error == pytest.approx(0.4)
    assert pm.predictiveness_ratio == pytest.approx(2.0)


def test_predictive_metrics_ceiling():
    """Test ceiling (fraction of failures with errors) calculation."""
    pm = PredictiveMetrics(
        total_instances=100,
        total_with_diagnostic_issues=40,
        total_without_diagnostic_issues=60,
        failed_with_diagnostic_issues=32,
        failed_without_diagnostic_issues=18,
        resolved_with_diagnostic_issues=8,
        resolved_without_diagnostic_issues=42,
    )

    # Ceiling = 32 / (32 + 18) = 0.64
    assert pm.ceiling == pytest.approx(0.64)


def test_predictive_metrics_diagnostic_issue_rate():
    """Test diagnostic issue rate calculation."""
    pm = PredictiveMetrics(
        total_instances=100,
        total_with_diagnostic_issues=40,
        total_without_diagnostic_issues=60,
        failed_with_diagnostic_issues=20,
        failed_without_diagnostic_issues=30,
    )

    assert pm.diagnostic_issue_rate == pytest.approx(0.4)


def test_predictive_metrics_edge_cases():
    """Test edge cases with zero values."""
    pm = PredictiveMetrics(
        total_instances=0,
        total_with_diagnostic_issues=0,
        total_without_diagnostic_issues=0,
    )

    assert pm.p_fail_given_error == 0.0
    assert pm.p_fail_given_no_error == 0.0
    assert pm.predictiveness_ratio == 1.0  # No data = neutral
    assert pm.ceiling == 0.0
    assert pm.diagnostic_issue_rate == 0.0


def test_cost_analysis_cost_per_solved():
    """Test cost per solved calculation."""
    ca = CostAnalysis(
        total_tokens=10000,
        tokens_for_resolved=6000,
        tokens_for_failed=4000,
        instances_resolved=3,
        instances_failed=2,
    )

    assert ca.cost_per_solved == pytest.approx(2000)  # 6000 / 3
    assert ca.cost_per_failed == pytest.approx(2000)  # 4000 / 2
    assert ca.cost_per_instance == pytest.approx(2000)  # 10000 / 5


def test_cost_analysis_no_solved():
    """Test cost per solved when nothing is solved."""
    ca = CostAnalysis(
        total_tokens=5000,
        instances_resolved=0,
        instances_failed=5,
    )

    assert ca.cost_per_solved == float('inf')


def test_instance_forensics_had_diagnostic_issues():
    """Test had_diagnostic_issues property."""
    # No issues
    inst1 = InstanceForensics(
        instance_id="test-1",
        status="resolved",
        had_syntax_errors=False,
        had_type_errors=False,
    )
    assert not inst1.had_diagnostic_issues

    # Syntax errors
    inst2 = InstanceForensics(
        instance_id="test-2",
        status="failed",
        had_syntax_errors=True,
        had_type_errors=False,
    )
    assert inst2.had_diagnostic_issues

    # Type errors
    inst3 = InstanceForensics(
        instance_id="test-3",
        status="failed",
        had_syntax_errors=False,
        had_type_errors=True,
    )
    assert inst3.had_diagnostic_issues


def test_forensics_report_compute_metrics():
    """Test that compute_metrics correctly aggregates instance data."""
    report = ForensicsReport(
        run_id="test-run",
        timestamp="2024-01-01T00:00:00",
        total_instances=0,
        resolved=0,
        failed=0,
        errors=0,
        timeouts=0,
    )

    # Add instances with various states
    report.instances = [
        InstanceForensics(
            instance_id="test-1",
            status="resolved",
            had_syntax_errors=False,
            had_type_errors=False,
            cost_metrics=CostMetrics(total_tokens=1000, llm_calls=1),
        ),
        InstanceForensics(
            instance_id="test-2",
            status="failed",
            had_syntax_errors=True,
            had_type_errors=False,
            cost_metrics=CostMetrics(total_tokens=1500, llm_calls=2),
        ),
        InstanceForensics(
            instance_id="test-3",
            status="failed",
            had_syntax_errors=False,
            had_type_errors=True,
            cost_metrics=CostMetrics(total_tokens=2000, llm_calls=3),
        ),
        InstanceForensics(
            instance_id="test-4",
            status="resolved",
            had_syntax_errors=False,
            had_type_errors=True,
            cost_metrics=CostMetrics(total_tokens=1200, llm_calls=2),
        ),
    ]

    report.compute_metrics()

    pm = report.predictive_metrics
    assert pm.total_instances == 4
    assert pm.total_with_diagnostic_issues == 3  # test-2, test-3, test-4
    assert pm.total_without_diagnostic_issues == 1  # test-1
    assert pm.failed_with_diagnostic_issues == 2  # test-2, test-3
    assert pm.resolved_with_diagnostic_issues == 1  # test-4

    ca = report.cost_analysis
    assert ca.total_tokens == 5700  # 1000 + 1500 + 2000 + 1200
    assert ca.total_llm_calls == 8


def test_decision_gate_passes():
    """Test decision gate passes when criteria are met."""
    pm = PredictiveMetrics(
        total_instances=100,
        total_with_diagnostic_issues=50,
        total_without_diagnostic_issues=50,
        failed_with_diagnostic_issues=35,  # 70% failure rate with errors
        failed_without_diagnostic_issues=20,  # 40% failure rate without
        resolved_with_diagnostic_issues=15,
        resolved_without_diagnostic_issues=30,
    )

    report = ForensicsReport(
        run_id="test",
        timestamp="",
        total_instances=100,
        resolved=45,
        failed=55,
        errors=0,
        timeouts=0,
        predictive_metrics=pm,
    )

    gate = evaluate_decision_gate(report)

    # Ceiling = 35/(35+20) = 0.636 >= 0.30 ✓
    # Predictiveness = 0.70/0.40 = 1.75 >= 1.2 ✓
    assert gate["gate_passed"]
    assert "PROCEED" in gate["recommendation"]


def test_decision_gate_fails_low_ceiling():
    """Test decision gate fails when ceiling is too low."""
    pm = PredictiveMetrics(
        total_instances=100,
        total_with_diagnostic_issues=10,  # Only 10% have issues
        total_without_diagnostic_issues=90,
        failed_with_diagnostic_issues=8,  # 80% failure rate with errors
        failed_without_diagnostic_issues=36,  # 40% failure rate without
        resolved_with_diagnostic_issues=2,
        resolved_without_diagnostic_issues=54,
    )

    report = ForensicsReport(
        run_id="test",
        timestamp="",
        total_instances=100,
        resolved=56,
        failed=44,
        errors=0,
        timeouts=0,
        predictive_metrics=pm,
    )

    gate = evaluate_decision_gate(report)

    # Ceiling = 8/(8+36) = 0.18 < 0.30 ✗
    # Predictiveness = 0.80/0.40 = 2.0 >= 1.2 ✓
    assert not gate["gate_passed"]
    assert gate["criteria"]["errors_common_in_failures"]["passed"] is False
    assert "LIMITED HEADROOM" in gate["recommendation"]


def test_decision_gate_fails_not_predictive():
    """Test decision gate fails when errors aren't predictive."""
    pm = PredictiveMetrics(
        total_instances=100,
        total_with_diagnostic_issues=50,
        total_without_diagnostic_issues=50,
        failed_with_diagnostic_issues=20,  # 40% failure rate with errors
        failed_without_diagnostic_issues=22,  # 44% failure rate without
        resolved_with_diagnostic_issues=30,
        resolved_without_diagnostic_issues=28,
    )

    report = ForensicsReport(
        run_id="test",
        timestamp="",
        total_instances=100,
        resolved=58,
        failed=42,
        errors=0,
        timeouts=0,
        predictive_metrics=pm,
    )

    gate = evaluate_decision_gate(report)

    # Ceiling = 20/(20+22) = 0.476 >= 0.30 ✓
    # Predictiveness = 0.40/0.44 = 0.91 < 1.2 ✗
    assert not gate["gate_passed"]
    assert gate["criteria"]["errors_predictive_of_failure"]["passed"] is False
    assert "WEAK SIGNAL" in gate["recommendation"]


def test_decision_gate_fails_both():
    """Test decision gate fails when both criteria fail."""
    pm = PredictiveMetrics(
        total_instances=100,
        total_with_diagnostic_issues=10,
        total_without_diagnostic_issues=90,
        failed_with_diagnostic_issues=5,  # 50% failure rate with errors
        failed_without_diagnostic_issues=45,  # 50% failure rate without
        resolved_with_diagnostic_issues=5,
        resolved_without_diagnostic_issues=45,
    )

    report = ForensicsReport(
        run_id="test",
        timestamp="",
        total_instances=100,
        resolved=50,
        failed=50,
        errors=0,
        timeouts=0,
        predictive_metrics=pm,
    )

    gate = evaluate_decision_gate(report)

    # Ceiling = 5/(5+45) = 0.10 < 0.30 ✗
    # Predictiveness = 0.50/0.50 = 1.0 < 1.2 ✗
    assert not gate["gate_passed"]
    assert "DO NOT PROCEED" in gate["recommendation"]
