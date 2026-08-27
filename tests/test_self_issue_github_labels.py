from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]


def test_github_self_issue_label_workflow_is_allowlisted_and_least_privilege() -> None:
    path = REPO / ".github" / "workflows" / "self-issue-labels.yml"
    raw = path.read_text(encoding="utf-8")
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)

    assert workflow["on"] == {"issues": {"types": ["opened"]}}
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert "secrets.GITHUB_TOKEN" in raw
    assert "pull_request_target" not in raw
    assert "zf-self-issue:[0-9a-f]{64}" in raw
    for label in (
        "runtime", "kernel/state", "provider/integration", "web/ui",
        "configuration", "security", "performance", "test/regression",
        "unknown",
    ):
        assert f'"{label}"' in raw
    assert "P[0-3]" in raw
    assert "severity.group(1).lower()" in raw
