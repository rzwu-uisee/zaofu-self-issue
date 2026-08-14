"""Tests for zf cost CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.cost.billing import BillingReconciliationStore
from zf.core.cost.tracker import CostTracker


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])
    return tmp_path


class TestCostCLI:
    def test_no_data(self, project_dir: Path, capsys):
        result = main(["cost"])
        assert result == 0
        captured = capsys.readouterr()
        assert "no cost" in captured.out.lower()

    def test_with_data(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 10000, 5000)
        result = main(["cost"])
        assert result == 0
        captured = capsys.readouterr()
        assert "dev" in captured.out
        assert "$" in captured.out

    def test_budget_within(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500)
        result = main(["cost", "--budget", "100"])
        assert result == 0
        captured = capsys.readouterr()
        assert "WITHIN" in captured.out

    def test_budget_exceeded(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 1_000_000, 1_000_000, "default")
        result = main(["cost", "--budget", "0.001"])
        assert result == 0
        captured = capsys.readouterr()
        assert "EXCEEDED" in captured.out

    def test_doctor_reports_projection_health(self, project_dir: Path, capsys):
        tracker = CostTracker(project_dir / ".zf" / "cost.jsonl")
        tracker.record_usage("dev", 1000, 500, usage_sample_id="sample-1")

        result = main(["cost", "--doctor"])

        assert result == 0
        captured = capsys.readouterr()
        assert "Cost Projection Doctor" in captured.out
        assert "duplicate_entries: 0" in captured.out

    def test_subscription_reconciliation_is_explicitly_unavailable(
        self, project_dir: Path, capsys
    ):
        result = main([
            "cost",
            "--reconcile", "openai",
            "--accounting-mode", "subscription",
            "--start", "2026-08-01T00:00:00Z",
            "--end", "2026-08-02T00:00:00Z",
        ])

        assert result == 0
        assert "not_available" in capsys.readouterr().out

    def test_refresh_without_configured_url_is_disabled(
        self, project_dir: Path, capsys
    ):
        assert main(["cost", "--refresh-pricing"]) == 0
        assert "disabled" in capsys.readouterr().out

    def test_json_separates_estimate_reported_billed_and_provenance(
        self, project_dir: Path, capsys
    ):
        state_dir = project_dir / ".zf"
        CostTracker(state_dir / "cost.jsonl").record_usage(
            "dev",
            1_000_000,
            100_000,
            model="claude-opus-4-8",
            backend="claude-code",
            provider_cost_usd=7.25,
        )
        BillingReconciliationStore(state_dir).persist({
            "schema_version": "billing-reconciliation.v1",
            "provider": "anthropic",
            "accounting_mode": "api",
            "window_start": "2026-08-01T00:00:00+00:00",
            "window_end": "2026-08-02T00:00:00+00:00",
            "project_id": "test",
            "status": "reconciled",
            "billed_usd": "7.5",
            "estimated_usd": "7",
            "variance_usd": "0.5",
        })

        assert main(["cost", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)["data"]

        assert data["precision"]["estimated_usd"] == 7.5
        assert data["precision"]["provider_reported_usd"] == 7.25
        assert data["precision"]["catalogs"][0]["digest"].startswith("sha256:")
        assert data["reconciliation"]["billed_usd"] == "7.5"
