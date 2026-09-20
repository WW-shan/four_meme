"""Audit a frozen research universe without claiming simulated trades were filled.

Manager metadata acquired later can disprove an unconditional BNB assumption,
but is not a replacement for state and FX observed at the historical decision.
All monetary values below are explicitly price/FDV proxies in the quote asset.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence


NATIVE_QUOTE = "0x" + "0" * 40
USDT = "0x55d398326f99059ff775485246999027b3197955"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _integer(value: Any) -> int | None:
    number = _decimal(value)
    if number is None or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def _timestamp(value: Any) -> Decimal | None:
    numeric = _decimal(value)
    if numeric is not None:
        return numeric if numeric > 0 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    # A timezone-less timestamp cannot establish a causal boundary.
    if parsed.tzinfo is None:
        return None
    return _decimal(parsed.timestamp())


def _address(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.lower()
    if len(value) != 42 or not value.startswith("0x"):
        return None
    try:
        int(value[2:], 16)
    except ValueError:
        return None
    return value


def audit_profitability_inputs(
    prior_report: Mapping[str, Any],
    lifecycles: Sequence[Mapping[str, Any]],
    quote_snapshot: Mapping[str, Any],
    asset_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Retain every old candidate, including missing metadata and invalid rows."""
    lifecycle_by_token = {
        str(row.get("token_address") or row.get("token") or "").lower(): row
        for row in lifecycles
        if isinstance(row, Mapping)
    }
    info_by_token: dict[str, Mapping[str, Any]] = {}
    for row in quote_snapshot.get("rows", []):
        if not isinstance(row, Mapping):
            continue
        token = _address(row.get("token"))
        info = row.get("info")
        if token and isinstance(info, Mapping) and _address(info.get("base")) == token:
            info_by_token[token] = info
    decimals: dict[str, int] = {NATIVE_QUOTE: 18}
    symbols: dict[str, str] = {NATIVE_QUOTE: "BNB"}
    for row in asset_snapshot.get("rows", []):
        if not isinstance(row, Mapping):
            continue
        address = _address(row.get("token"))
        if not address:
            continue
        if row.get("method") == "decimals()":
            value = _integer(row.get("value"))
            if value is not None and value <= 255:
                decimals[address] = value
        elif row.get("method") == "symbol()" and isinstance(row.get("value"), str):
            symbols[address] = row["value"]

    rows: list[dict[str, Any]] = []
    for index, raw_old in enumerate(prior_report.get("rows", [])):
        old = raw_old if isinstance(raw_old, Mapping) else {}
        token = str(old.get("token") or "").lower()
        lifecycle = lifecycle_by_token.get(token, {})
        info = info_by_token.get(token, {})
        quote = _address(info.get("quote"))
        quote_decimals = decimals.get(quote) if quote else None
        base_decimals = decimals.get(token)
        flags = {"execution_unverified", "historical_state_unverified", "no_recorded_x_signal"}
        if not isinstance(raw_old, Mapping) or not _address(token):
            flags.add("invalid_candidate_row")
        if not lifecycle:
            flags.add("missing_lifecycle")
        if quote is None:
            flags.add("unknown_quote")
        elif quote != NATIVE_QUOTE:
            flags.update({"nonnative_quote_labeled_bnb", "unsupported_by_current_native_executor"})
        if quote_decimals is None or base_decimals is None:
            flags.add("unknown_decimals")

        # The old exporter divided both token and quote integers by 10**18.
        # Quote-unit FDV is invariant to the base decimals: p_saved*supply_raw/10**dq.
        supply = _decimal(lifecycle.get("total_supply"))
        saved_price = _decimal(old.get("confirmation_price", old.get("entry_price_causal")))
        fdv_quote = None
        if saved_price is not None and saved_price > 0 and supply is not None and supply > 0 and quote_decimals is not None:
            fdv_quote = saved_price * supply / (Decimal(10) ** quote_decimals)
        if _decimal(info.get("totalSupply")) != supply:
            flags.add("supply_snapshot_mismatch")
        if quote == USDT and fdv_quote is not None and fdv_quote < 50_000:
            flags.add("usdt_fdv_proxy_below_50000_quote_units")

        confirmation = _timestamp(old.get("confirmation_time"))
        entry = _timestamp(old.get("entry_time_causal"))
        graduation = _timestamp(lifecycle.get("graduate_time"))
        migrated_at_entry = graduation <= entry if entry is not None and graduation is not None else None
        if graduation is None:
            flags.add("graduation_event_time_unavailable")
        if migrated_at_entry:
            flags.add("curve_venue_ended_before_assumed_entry")
        if entry is not None and graduation is not None and graduation > 0 and graduation <= entry + 3600:
            flags.add("requires_dex_state_for_1h_outcome")

        buys = [b for b in lifecycle.get("buys", []) if isinstance(b, Mapping)]
        recent = []
        if confirmation is not None:
            recent = [b for b in buys if (t := _timestamp(b.get("timestamp"))) is not None and confirmation - 30 <= t <= confirmation]
        tiny_count = None
        tiny_volume = None
        if quote == USDT and quote_decimals is not None:
            volumes = [v * (Decimal(10) ** (18 - quote_decimals)) for b in recent if (v := _decimal(b.get("bnb_amount"))) is not None and v >= 0]
            tiny = [v for v in volumes if v < 1]
            tiny_count, tiny_volume = len(tiny), float(sum(tiny, Decimal(0)))
            # Unknown amounts stay in the denominator; they are not tiny buys.
            if recent and len(tiny) / len(recent) >= 0.9:
                flags.add("at_least_90pct_buys_below_one_usdt")
        timestamps = [_timestamp(b.get("timestamp")) for b in buys]
        timestamps = [t for t in timestamps if t is not None]
        crossing = _timestamp(old.get("crossing_time"))
        if timestamps and crossing == min(timestamps):
            flags.add("threshold_met_at_first_stored_buy")
        all_trades = buys + [s for s in lifecycle.get("sells", []) if isinstance(s, Mapping)]
        provenance = sum(
            _integer(t.get("block_number")) is not None
            and _integer(t.get("log_index")) is not None
            and bool(t.get("transaction_hash"))
            for t in all_trades
        )
        if provenance < len(all_trades):
            flags.add("incomplete_trade_provenance")
        near_return = _decimal(old.get("h3600_strict_return"))
        near_observation = near_return is not None
        rows.append({
            "source_row_index": index,
            "token": token,
            "symbol": old.get("symbol"),
            "quote_address": quote,
            "quote_symbol": symbols.get(quote),
            "quote_decimals": quote_decimals,
            "base_decimals": base_decimals,
            "quote_metadata_block": quote_snapshot.get("block_number"),
            "quote_metadata_scope": "later_snapshot_for_denomination_audit_only",
            "historical_quote_verified": False,
            "native_quote_supported": None if quote is None else quote == NATIVE_QUOTE,
            "confirmation_time": old.get("confirmation_time"),
            "assumed_entry_time": old.get("entry_time_causal"),
            "old_usd_fdv_proxy": old.get("confirmation_mcap_usd"),
            "fdv_proxy_quote_units": float(fdv_quote) if fdv_quote is not None else None,
            "true_usd_fdv": None,
            "price_basis": "prior_trade_average_not_size_specific_quote",
            "graduation_time": lifecycle.get("graduate_time"),
            "curve_venue_ended_at_entry": migrated_at_entry,
            "old_near_target_observation_present": near_observation,
            "old_near_target_price_proxy_negative": bool(near_return is not None and near_return < 0),
            "actual_execution_status": "unknown",
            "actual_pnl_bnb": None,
            "buys_in_confirmation_window": len(recent),
            "sub_one_usdt_buy_count": tiny_count,
            "sub_one_usdt_total_volume": tiny_volume,
            "trade_count": len(all_trades),
            "trades_with_block_log_tx_identity": provenance,
            "audit_flags": sorted(flags),
        })
    counts = Counter(flag for row in rows for flag in row["audit_flags"])
    quote_counts = Counter(row["quote_address"] or "unknown" for row in rows)
    native = [r for r in rows if r["native_quote_supported"] is True]
    near = [r for r in rows if r["old_near_target_observation_present"]]
    return {
        "schema_version": 1,
        "evidence_status": "prior_profitability_claims_not_validated",
        "safe_for_live_switch": False,
        "model_selection_eligible": False,
        "summary": {
            "candidate_count": len(rows),
            "unique_candidate_tokens": len({r["token"] for r in rows}),
            "native_quote_count": len(native),
            "nonnative_quote_count": sum(r["native_quote_supported"] is False for r in rows),
            "unknown_quote_count": sum(r["quote_address"] is None for r in rows),
            "native_curve_venue_ended_at_entry": sum(r["curve_venue_ended_at_entry"] is True for r in native),
            "near_target_observation_count": len(near),
            "near_target_native_quote_count": sum(r["native_quote_supported"] is True for r in near),
            "actual_execution_unknown_count": len(rows),
            "quote_counts": dict(sorted(quote_counts.items())),
            "audit_flag_counts": dict(sorted(counts.items())),
        },
        "method_notes": [
            "All previous candidates remain in the denominator, regardless of later trade activity.",
            "An unrelated trade near a target timestamp neither proves nor is required for our AMM execution.",
            "Latest token metadata is retained as an audit snapshot, not a causal backtest feature.",
            "FDV proxies use quoted units; stablecoin parity and other FX are not assumed to establish exact historical USD values.",
            "A small buy is an observable amount, not proof of manipulation or an independent human trader.",
        ],
        "rows": sorted(rows, key=lambda row: row["token"]),
    }
