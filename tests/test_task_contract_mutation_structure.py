from __future__ import annotations

import ast
from pathlib import Path


def test_complete_task_contract_writes_use_authority_service() -> None:
    root = Path(__file__).parents[1] / "src" / "zf"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            keywords = {item.arg for item in node.keywords if item.arg}
            if node.func.attr == "update" and "contract" in keywords:
                violations.append(f"{path.relative_to(root)}:{node.lineno}: update")
            if node.func.attr == "reopen":
                violations.append(f"{path.relative_to(root)}:{node.lineno}: reopen")
            if (
                node.func.attr == "compare_and_update_contract"
                and path.name != "task_contract_authority.py"
            ):
                violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: direct contract CAS"
                )
    assert violations == []
