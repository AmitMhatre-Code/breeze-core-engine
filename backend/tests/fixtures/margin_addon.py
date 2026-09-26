"""Make the portal's ICICI margin add-on current for a test.

Every SPAN-file margin path fails closed to ICICI's margin_calculator when no add-on has been
received from the portal (docs/design-decisions.md #48). Tests that exercise a SPAN-file path
wrap themselves in `active_margin_addon()`; with the default zero rates the add-on is 0, so
their expected figures are exactly the SPAN file's own.
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator
from unittest.mock import patch

from icici_breeze_backend.app.services import margin_addon, margin_source_prefs
from icici_breeze_backend.app.services.margin_addon import MarginAddonRates, compute_addon

ZERO_RATES = MarginAddonRates(
    version="test-zero",
    index_rate=0.0,
    index_deep_otm_rate=0.0,
    index_deep_otm_threshold=0.10,
    stock_rate=0.0,
    stock_deep_otm_rate=0.0,
    stock_deep_otm_threshold=0.30,
    expiry_day_extra_rate=0.0,
)


@contextlib.contextmanager
def active_margin_addon(
    rates: MarginAddonRates = ZERO_RATES, *, spot: float = 100.0, is_index: bool = True
) -> Iterator[MarginAddonRates]:
    """`rates` are current; the add-on prices off a fixed `spot` instead of the SPAN file's."""

    def _addon_for_underlying(exchange_code: str, stock_code: str, legs: Any, *, rates: MarginAddonRates, spot_: Any = None, **_: Any):
        return {
            "amount": compute_addon(rates, list(legs), spot=spot, is_index=is_index),
            "version": rates.version,
            "spot": spot,
            "is_index": is_index,
        }

    with patch.object(margin_addon, "get_active_rates", return_value=rates), patch.object(
        margin_source_prefs, "get_active_rates", return_value=rates
    ), patch.object(margin_addon, "addon_for_underlying", side_effect=_addon_for_underlying):
        yield rates
