"""U20:审角色报告证据观测门(r6.1 凭证复核 finding 13)。

实弹:第 12 轮 review 报告声称跑了 Playwright 运行时探针,但
evidence_refs=0——判决靠信任(经地面真值核验恰好全对,机制上裸奔)。
F7 诚实门只覆盖 dev 完成事件;此门补审角色一侧:review/verify 家族的
子报告若无任何证据引用,发观测事件(与 P3-3 同哲学,不阻塞;一轮
实弹后再议 fail-closed)。
"""

from __future__ import annotations

from typing import Any

REPORT_EVIDENCE_MISSING_EVENT = "stage.report.evidence_missing"
_VERIFICATION_STAGE_MARKERS = ("review", "verify", "judge", "test")
_EVIDENCE_KEYS = (
    "evidence_refs",
    "runtime_evidence_refs",
    "evidence",
    "artifact_refs",
    "probes",
)
_NESTED_EVIDENCE_COLLECTIONS = (
    "findings",
    "requirement_results",
    "requirement_coverage_matrix",
    "probe_receipts",
)


def is_verification_stage(*, stage_id: str, event_type: str) -> bool:
    stage = str(stage_id or "").lower()
    etype = str(event_type or "").lower()
    return any(
        marker in stage or etype.startswith(f"{marker}.")
        for marker in _VERIFICATION_STAGE_MARKERS
    )


def report_evidence_gap(report: Any) -> str:
    """返回缺口描述("" = 报告带证据或无报告可核)。"""
    if not isinstance(report, dict):
        return ""
    has_verdict = bool(
        report.get("status")
        or report.get("recommendation")
        or report.get("verdict")
    )
    if not has_verdict:
        return ""
    if _has_evidence(report):
        return ""
    for collection in _NESTED_EVIDENCE_COLLECTIONS:
        items = report.get(collection)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    if _has_evidence(item):
                        return ""
    return (
        "verification report carries a verdict but no evidence refs "
        f"(checked keys: {', '.join(_EVIDENCE_KEYS)})"
    )


def _has_evidence(value: dict[str, Any]) -> bool:
    for key in _EVIDENCE_KEYS:
        evidence = value.get(key)
        if isinstance(evidence, list):
            if any(str(item or "").strip() for item in evidence):
                return True
        elif isinstance(evidence, str) and evidence.strip():
            return True
    return False


__all__ = [
    "REPORT_EVIDENCE_MISSING_EVENT",
    "is_verification_stage",
    "report_evidence_gap",
]
