from __future__ import annotations

from pathlib import Path

from zf.cli.start import _refresh_pricing_catalog
from zf.core.config.schema import CostConfig, ZfConfig
from zf.core.cost.catalog import PricingCatalogError


def test_start_refreshes_configured_catalog(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[str] = []

    def refresh(self, *, force: bool = False):
        calls.append(self.url)
        return {"status": "updated"}

    monkeypatch.setattr(
        "zf.core.cost.catalog.PricingCatalogSyncService.refresh",
        refresh,
    )
    config = ZfConfig(cost=CostConfig(
        pricing_catalog_url="https://pricing.example.test/catalog.json"
    ))

    _refresh_pricing_catalog(config, tmp_path)

    assert calls == ["https://pricing.example.test/catalog.json"]


def test_start_catalog_failure_uses_last_known_good(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    def refresh(self, *, force: bool = False):
        raise PricingCatalogError("pricing catalog refresh failed: timeout")

    monkeypatch.setattr(
        "zf.core.cost.catalog.PricingCatalogSyncService.refresh",
        refresh,
    )
    config = ZfConfig(cost=CostConfig(
        pricing_catalog_url="https://pricing.example.test/catalog.json"
    ))

    _refresh_pricing_catalog(config, tmp_path)

    assert "using last-known-good pricing" in capsys.readouterr().err
