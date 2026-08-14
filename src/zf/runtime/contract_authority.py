"""kanban 契约作为 verification 的唯一权威源(avbs-r4 F4)。

r4 实证的三向真相分叉:`task.contract.update` 修订 kanban 契约后,
writer briefing 读 plan-synth workdir 的 task_map 副本、reviewer 读
candidate 树内副本,两处都是旧命令——工人按旧契约诚实卡死、reviewer
按旧命令判拒,operator 被迫机械对齐两份副本。

治理原则:派发 payload 组装时,若 kanban 存在 canonical 任务且其契约
带 verification,则以契约覆盖 task_map 工件值;工件副本降级为历史记录
与无 canonical 任务时的回退。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from zf.runtime.task_contract_authority import task_execution_binding


def apply_contract_authority(task_item: dict[str, Any], task_store) -> dict[str, Any]:
    """返回以 kanban 契约为准修订后的 task_item(无 canonical 任务时原样)。"""
    task_id = str(task_item.get("task_id") or task_item.get("id") or "")
    if not task_id:
        return task_item
    try:
        task = task_store.get(task_id)
    except Exception:
        return task_item
    contract = getattr(task, "contract", None) if task is not None else None
    if contract is None:
        return task_item
    updated = dict(task_item)
    canonical = asdict(contract)
    binding = asdict(task_execution_binding(task))
    authority_fields = {
        "canonical_contract": canonical,
        "contract_authority_revision": str(
            getattr(task, "contract_authority_revision", "") or ""
        ),
        "contract_authority_sequence": int(
            getattr(task, "contract_authority_sequence", 0) or 0
        ),
        "execution_binding": binding,
    }
    updated.update(authority_fields)
    task_scope = task_item.get("scope")
    semantic_fields = {
        "verification": str(contract.verification or ""),
        "verification_tiers": list(contract.verification_tiers or []),
        "validation": dict(contract.validation or {}),
        "behavior": str(contract.behavior or ""),
        "allowed_paths": list(contract.scope or []),
        "scope": (
            task_scope
            if isinstance(task_scope, str) and task_scope.strip()
            else list(contract.scope or [])
        ),
        "acceptance": str(contract.acceptance or ""),
        "acceptance_criteria": list(contract.acceptance_criteria or []),
        "exclusions": list(contract.exclusions or []),
        "explicit_non_goals": list(contract.explicit_non_goals or []),
        "task_map_generation": str(
            (contract.evidence_contract or {}).get("task_map_generation")
            or ((contract.evidence_contract or {}).get("source_refs") or {}).get(
                "task_map_generation"
            )
            or ""
        ),
        "verification_source": "kanban_contract",
    }
    updated.update(semantic_fields)
    raw_task = updated.get("raw_task")
    if isinstance(raw_task, dict):
        raw_updated = dict(raw_task)
        raw_updated["canonical_contract"] = canonical
        for key in (
            "behavior",
            "verification",
            "verification_tiers",
            "validation",
            "allowed_paths",
            "scope",
            "acceptance",
            "acceptance_criteria",
            "exclusions",
            "explicit_non_goals",
        ):
            if authority_fields["contract_authority_revision"]:
                value = semantic_fields.get(key)
                if key == "scope" and isinstance(raw_updated.get(key), str):
                    continue
                raw_updated[key] = value
        updated["raw_task"] = raw_updated
    return updated
