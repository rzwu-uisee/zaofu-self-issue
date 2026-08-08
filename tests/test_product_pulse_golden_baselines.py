from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "e2e" / "fixtures" / "product-pulse"


def _git_blob_digest(body: bytes) -> str:
    return hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()


def test_independent_product_flow_baselines_match_golden_manifests() -> None:
    for flow_kind in ("issue", "refactor"):
        root = FIXTURES / f"{flow_kind}-baseline"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        expected = manifest["git_blobs"]
        actual_paths = {
            path.relative_to(root).as_posix()
            for path in (root / "app").rglob("*")
            if path.is_file()
        }

        assert manifest["schema_version"] == "product-pulse-golden-baseline.v1"
        assert manifest["flow_kind"] == flow_kind
        assert len(manifest["provenance"]["product_commit"]) == 40
        assert actual_paths == set(expected)
        assert {
            relative: _git_blob_digest((root / relative).read_bytes())
            for relative in sorted(expected)
        } == expected
