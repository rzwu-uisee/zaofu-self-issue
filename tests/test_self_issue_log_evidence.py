from __future__ import annotations

from pathlib import Path

import pytest

from zf.runtime.self_issue_log_evidence import (
    collect_log_evidence,
    normalize_log_findings,
    verified_log_candidate_map,
)


def test_full_bounded_scan_finds_anomaly_outside_tail_and_preserves_tail(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".state"
    logs = state / "logs"
    logs.mkdir(parents=True)
    (logs / "web.log").write_text(
        "warning: slow web request GET /snapshot/light took 14300ms\n"
        + ("ordinary heartbeat\n" * 400)
        + "final tail context\n",
        encoding="utf-8",
    )

    evidence = collect_log_evidence(state)

    candidate = evidence["log_error_candidates"][0]
    assert candidate["path"] == "logs/web.log"
    assert candidate["line"] == 1
    assert candidate["category"] == "slow"
    assert "slow web request" in candidate["redacted_line"]
    assert "slow web request" not in evidence["log_excerpts"][0]["redacted_tail"]
    assert "final tail context" in evidence["log_excerpts"][0]["redacted_tail"]


def test_log_candidates_and_tails_are_redacted(tmp_path: Path) -> None:
    state = tmp_path / ".state"
    logs = state / "diagnostics"
    logs.mkdir(parents=True)
    (logs / "runtime.log").write_text(
        "ERROR Bearer secret-token-value leaked for person@example.com\n",
        encoding="utf-8",
    )

    evidence = collect_log_evidence(state)
    encoded = str(evidence)

    assert "secret-token-value" not in encoded
    assert "person@example.com" not in encoded
    assert "REDACTED" in encoded


def test_semantic_findings_are_bound_to_verified_candidate_ids(tmp_path: Path) -> None:
    state = tmp_path / ".state"
    logs = state / "logs"
    logs.mkdir(parents=True)
    (logs / "runtime.log").write_text("ERROR worker timed out\n", encoding="utf-8")
    evidence = collect_log_evidence(state)
    candidate_map = verified_log_candidate_map(evidence["log_error_candidates"])
    candidate_id = next(iter(candidate_map))

    finding = normalize_log_findings([{
        "candidate_id": candidate_id,
        "relation": "supports",
        "confidence": "high",
        "reason": "The timeout matches the reported stalled worker.",
    }], allowed_candidate_ids=set(candidate_map))

    assert finding[0]["candidate_id"] == candidate_id
    with pytest.raises(ValueError, match="unknown log candidate"):
        normalize_log_findings([{
            **finding[0], "candidate_id": "logc-not-issued",
        }], allowed_candidate_ids=set(candidate_map))


def test_tampered_log_candidate_digest_is_rejected(tmp_path: Path) -> None:
    state = tmp_path / ".state"
    logs = state / "logs"
    logs.mkdir(parents=True)
    (logs / "runtime.log").write_text("ERROR worker timed out\n", encoding="utf-8")
    evidence = collect_log_evidence(state)
    candidate = dict(evidence["log_error_candidates"][0])
    candidate["redacted_line"] = "ERROR fabricated replacement"

    assert verified_log_candidate_map([candidate]) == {}
