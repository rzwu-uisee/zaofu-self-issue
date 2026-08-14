from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config


def _write(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "zf.yaml"
    path.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: cost-config\n"
        f"{extra}",
        encoding="utf-8",
    )
    return path


def test_cost_config_loads_catalog_and_accounting_modes(tmp_path: Path):
    config = load_config(_write(tmp_path, """
cost:
  pricing_catalog_url: https://pricing.example.test/catalog.json
  pricing_refresh_ttl_seconds: 3600
  pricing_refresh_timeout_seconds: 3.5
  backend_accounting_modes:
    codex: subscription
    claude-code: api
"""))

    assert config.cost.pricing_catalog_url.endswith("catalog.json")
    assert config.cost.pricing_refresh_ttl_seconds == 3600
    assert config.cost.pricing_refresh_timeout_seconds == 3.5
    assert config.cost.backend_accounting_modes == {
        "codex": "subscription",
        "claude-code": "api",
    }


@pytest.mark.parametrize("value", ["free", "", "metered-ish"])
def test_cost_config_rejects_unknown_accounting_mode(
    tmp_path: Path,
    value: str,
):
    with pytest.raises(ConfigError, match="accounting_modes"):
        load_config(_write(tmp_path, f"""
cost:
  backend_accounting_modes:
    codex: {value!r}
"""))


def test_cost_config_rejects_unknown_key(tmp_path: Path):
    with pytest.raises(ConfigError, match="Unknown key"):
        load_config(_write(tmp_path, """
cost:
  pricing_magic: true
"""))
