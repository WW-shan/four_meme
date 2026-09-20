"""Join Four.meme launch-curve paths with post-graduation DEX OHLCV paths."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence


def _timestamp(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None
    if number is not None and math.isfinite(number):
        return number
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sorted_curve_points(lifecycle: Mapping[str, Any]) -> list[dict[str, Any]]:
    points = []
    for row in lifecycle.get("price_history") or []:
        if not isinstance(row, Mapping):
            continue
        timestamp = _timestamp(row.get("timestamp"))
        price = _price(row.get("price"))
        if timestamp is None or price is None:
            continue
        points.append({"timestamp": timestamp, "price": price, "type": row.get("type", "curve")})
    return sorted(points, key=lambda row: (row["timestamp"], str(row.get("type") or "")))


def _sorted_dex_bars(dex_row: Mapping[str, Any]) -> list[tuple[float, float, float, float, float, float]]:
    bars = []
    for row in dex_row.get("bars") or dex_row.get("ohlcv") or []:
        if not isinstance(row, Sequence) or len(row) < 6:
            continue
        values = [_timestamp(row[0])] + [_price(value) for value in row[1:5]] + [_number(row[5])]
        if any(value is None for value in values):
            continue
        timestamp, opening, high, low, close, volume = values
        if timestamp is None or opening is None or high is None or low is None or close is None or volume is None:
            continue
        if volume < 0.0:
            continue
        bars.append((timestamp, opening, high, low, close, volume))
    return sorted(set(bars), key=lambda row: row[0])


def build_cross_boundary_lifecycle(
    lifecycle: Mapping[str, Any],
    dex_row: Mapping[str, Any],
    *,
    as_of_timestamp: float | None = None,
) -> dict[str, Any] | None:
    """Return a lifecycle whose price path continues through a DEX boundary.

    DEX OHLCV providers can quote the token in USD, WBNB, or another asset.
    The path is therefore scaled at the first DEX bar on/after graduation to
    the last curve price.  Returns after the boundary remain invariant under
    this scale; the bridge is explicitly marked approximate in the output.
    """
    graduation = _timestamp(lifecycle.get("graduate_time"))
    if graduation is None:
        return None
    curve = _sorted_curve_points(lifecycle)
    pre_curve = [point for point in curve if point["timestamp"] <= graduation]
    if not pre_curve:
        return None
    dex_bars = _sorted_dex_bars(dex_row)
    if as_of_timestamp is not None:
        dex_bars = [bar for bar in dex_bars if bar[0] <= float(as_of_timestamp)]
    dex_bars = [bar for bar in dex_bars if bar[0] >= graduation]
    if not dex_bars:
        return None
    bridge_price = _price(dex_bars[0][4])
    curve_price = _price(pre_curve[-1]["price"])
    if bridge_price is None or curve_price is None:
        return None
    scale = curve_price / bridge_price
    has_curve_at_graduation = any(point["timestamp"] == graduation for point in pre_curve)
    dex_points = [
        {
            "timestamp": float(bar[0]),
            "price": float(bar[4] * scale),
            "type": "dex_close",
            "dex_open": float(bar[1] * scale),
            "dex_high": float(bar[2] * scale),
            "dex_low": float(bar[3] * scale),
            "dex_volume": float(bar[5]),
        }
        for bar in dex_bars
        if not (has_curve_at_graduation and bar[0] == graduation)
    ]
    if not dex_points:
        return None
    merged = dict(lifecycle)
    # Keep the final curve quote at the graduation second.  Some tokens have
    # all of their curve trades in that second; dropping it would make the
    # bridged path appear to start from DEX data and corrupt early entries.
    merged["price_history"] = [point for point in curve if point["timestamp"] <= graduation] + dex_points
    merged["last_update"] = max(
        float(_timestamp(lifecycle.get("last_update")) or 0.0),
        max(point["timestamp"] for point in dex_points),
    )
    merged["cross_boundary"] = {
        "graduation_timestamp": float(graduation),
        "dex_first_timestamp": float(dex_bars[0][0]),
        "dex_last_timestamp": float(dex_bars[-1][0]),
        "dex_bar_count": len(dex_points),
        "dex_source_bar_count": len(dex_bars),
        "price_scale_at_bridge": float(scale),
        "quote_side": dex_row.get("quote_side", dex_row.get("token_side")),
        "pool_address": dex_row.get("pool_address") or dex_row.get("pair_address"),
        "source_url": dex_row.get("source_url"),
        "approximate_price_bridge": True,
    }
    return merged


__all__ = ["build_cross_boundary_lifecycle"]
