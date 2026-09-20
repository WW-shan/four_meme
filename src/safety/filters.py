"""Independent safety filters. Unknown critical fields never pass."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any, Callable, Mapping

from config.scanner_config import FilterThresholds

CRITICAL_FILTERS = frozenset({
    "liquidity_min", "dev_holding", "taxes", "recent_trades", "permissions",
    "holders", "lp_lock", "honeypot_sim", "bundle_cohort",
})
LEARNING_CRITICAL = frozenset({"honeypot_sim"})

FILTER_WEIGHTS = {
    "honeypot_sim": 15, "liquidity_min": 10, "permissions": 10, "holders": 10,
    "dev_holding": 8, "taxes": 8, "lp_lock": 8, "bundle_cohort": 8,
    "dev_history": 8, "recent_trades": 5, "mcap_band": 5,
    "early_snipers": 3, "social_signal": 2,
}


@dataclass(frozen=True)
class FilterResult:
    filter_id: str
    status: str  # pass | fail | skip | error
    reason: str
    metadata: dict = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _result(filter_id: str, status: str, reason: str, metadata: dict | None = None,
            started: float | None = None) -> FilterResult:
    duration = (time.perf_counter() - started) * 1000 if started else 0.0
    return FilterResult(filter_id, status, reason, metadata or {}, duration)


def _unknown(filter_id: str, field_name: str, started: float) -> FilterResult:
    return _result(filter_id, "error", f"{field_name} is unknown", {"unknown": [field_name]}, started)


def liquidity_min(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    value = _number(snapshot.get("liquidity_usd"))
    if value is None:
        return _unknown("liquidity_min", "liquidity_usd", started)
    if value < thresholds.liquidity_min_usd:
        return _result("liquidity_min", "fail",
                       f"liquidity ${value:,.0f} < ${thresholds.liquidity_min_usd:,.0f}", {"liquidity_usd": value}, started)
    return _result("liquidity_min", "pass", f"liquidity ${value:,.0f}", {"liquidity_usd": value}, started)


def dev_holding(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    value = _number(snapshot.get("dev_holding_pct"))
    if value is None:
        return _unknown("dev_holding", "dev_holding_pct", started)
    if value > thresholds.dev_holding_max_pct:
        return _result("dev_holding", "fail",
                       f"dev holds {value:.2f}% > {thresholds.dev_holding_max_pct}%", {"dev_holding_pct": value}, started)
    return _result("dev_holding", "pass", f"dev holds {value:.2f}%", {"dev_holding_pct": value}, started)


def taxes(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    buy = _number(snapshot.get("buy_tax_pct"))
    sell = _number(snapshot.get("sell_tax_pct"))
    if buy is None or sell is None:
        return _unknown("taxes", "buy_tax_pct/sell_tax_pct", started)
    spread = abs(buy - sell)
    if buy > thresholds.buy_tax_max_pct or sell > thresholds.sell_tax_max_pct:
        return _result("taxes", "fail", f"tax buy={buy}% sell={sell}% exceeds max", {"buy": buy, "sell": sell}, started)
    if spread > thresholds.tax_spread_max_pp:
        return _result("taxes", "fail", f"tax spread {spread:.2f}pp > {thresholds.tax_spread_max_pp}pp",
                       {"spread_pp": spread}, started)
    return _result("taxes", "pass", f"tax buy={buy}% sell={sell}%", {"buy": buy, "sell": sell}, started)


def recent_trades(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    value = _number(snapshot.get("trades_recent"))
    if value is None:
        return _unknown("recent_trades", "trades_recent", started)
    if value <= 0:
        return _result("recent_trades", "fail", "no recent trades", {"trades_recent": value}, started)
    return _result("recent_trades", "pass", f"{int(value)} recent trades", {"trades_recent": value}, started)


def mcap_band(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    value = _number(snapshot.get("mcap_usd"))
    if value is None:
        return _unknown("mcap_band", "mcap_usd", started)
    if value < thresholds.mcap_min_usd or value > thresholds.mcap_max_usd:
        return _result("mcap_band", "fail",
                       f"mcap ${value:,.0f} outside ${thresholds.mcap_min_usd:,.0f}-${thresholds.mcap_max_usd:,.0f}",
                       {"mcap_usd": value}, started)
    preferred = thresholds.mcap_preferred_min_usd <= value <= thresholds.mcap_preferred_max_usd
    return _result("mcap_band", "pass", f"mcap ${value:,.0f}" + (" (preferred)" if preferred else ""),
                   {"mcap_usd": value, "preferred": preferred}, started)


def permissions(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    fields = ("mint_authority", "freeze_authority", "owner_renounced", "blacklist", "pausable")
    missing = [name for name in fields if name not in snapshot]
    # owner/blacklist/pausable must be explicitly verified; mint/freeze may be
    # explicitly None, which means the authority is renounced.
    missing.extend(name for name in ("owner_renounced", "blacklist", "pausable") if snapshot.get(name) is None)
    if missing:
        return _unknown("permissions", ",".join(sorted(set(missing))), started)
    dangers = []
    if snapshot.get("mint_authority") not in (None, "", False, "renounced"):
        dangers.append("mint_authority")
    if snapshot.get("freeze_authority") not in (None, "", False, "renounced"):
        dangers.append("freeze_authority")
    if snapshot.get("owner_renounced") is False:
        dangers.append("owner_not_renounced")
    if snapshot.get("blacklist") is True:
        dangers.append("blacklist")
    if snapshot.get("pausable") is True:
        dangers.append("pausable")
    if dangers:
        return _result("permissions", "fail", "dangerous permissions: " + ",".join(dangers), {"danger": dangers}, started)
    return _result("permissions", "pass", "no dangerous permissions", {}, started)


def holders(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    top1 = _number(snapshot.get("top1_pct"))
    top10 = _number(snapshot.get("top10_pct"))
    non_lp = _number(snapshot.get("non_lp_max_pct"))
    if top1 is None or top10 is None or non_lp is None:
        return _unknown("holders", "top1_pct/top10_pct/non_lp_max_pct", started)
    breaches = []
    if top1 > thresholds.top1_max_pct:
        breaches.append(f"top1 {top1:.2f}% > {thresholds.top1_max_pct}%")
    if top10 > thresholds.top10_max_pct:
        breaches.append(f"top10 {top10:.2f}% > {thresholds.top10_max_pct}%")
    if non_lp > thresholds.non_lp_max_pct:
        breaches.append(f"non-LP {non_lp:.2f}% > {thresholds.non_lp_max_pct}%")
    if breaches:
        return _result("holders", "fail", "; ".join(breaches), {"top1": top1, "top10": top10, "non_lp": non_lp}, started)
    return _result("holders", "pass", f"top1={top1:.2f}% top10={top10:.2f}%", {"top1": top1, "top10": top10}, started)


def lp_lock(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    burned = snapshot.get("lp_burned")
    locked = _number(snapshot.get("lp_locked_pct"))
    if burned is None and locked is None:
        return _unknown("lp_lock", "lp_burned/lp_locked_pct", started)
    if burned is True or (locked is not None and locked >= 50.0):
        return _result("lp_lock", "pass", f"lp_burned={burned} lp_locked={locked}", {"lp_burned": burned, "lp_locked_pct": locked}, started)
    return _result("lp_lock", "fail", f"lp not locked/burned (locked={locked})", {"lp_locked_pct": locked}, started)


def honeypot_sim(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    value = snapshot.get("honeypot_sim")
    if value is None:
        return _unknown("honeypot_sim", "honeypot_sim", started)
    if value is True:
        return _result("honeypot_sim", "pass", "sell simulation passed", {}, started)
    return _result("honeypot_sim", "fail", "sell simulation failed", {}, started)


def bundle_cohort(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    current = _number(snapshot.get("bundle_current_held_pct"))
    if current is None:
        return _unknown("bundle_cohort", "bundle_current_held_pct", started)
    wallets = _number(snapshot.get("bundle_wallet_count"))
    total = _number(snapshot.get("bundle_total_pct"))
    if current > thresholds.bundle_current_held_max_pct or (wallets is not None and wallets >= 4 and total is not None and total > 50):
        return _result("bundle_cohort", "fail",
                       f"bundle current held {current:.2f}% (wallets={wallets}, total={total})",
                       {"current": current, "wallets": wallets, "total": total}, started)
    return _result("bundle_cohort", "pass", f"bundle current held {current:.2f}%",
                   {"current": current, "wallets": wallets, "total": total}, started)


def dev_history(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    value = _number(snapshot.get("deployer_rug_rate"))
    if value is None:
        return _unknown("dev_history", "deployer_rug_rate", started)
    if value > thresholds.dev_rug_rate_max:
        return _result("dev_history", "fail", f"deployer rug rate {value:.2f} > {thresholds.dev_rug_rate_max}",
                       {"deployer_rug_rate": value}, started)
    return _result("dev_history", "pass", f"deployer rug rate {value:.2f}", {"deployer_rug_rate": value}, started)


def early_snipers(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    value = _number(snapshot.get("early_sniper_count"))
    if value is None:
        return _unknown("early_snipers", "early_sniper_count", started)
    if value > thresholds.early_sniper_max:
        return _result("early_snipers", "fail", f"{int(value)} early snipers > {thresholds.early_sniper_max}",
                       {"early_sniper_count": value}, started)
    return _result("early_snipers", "pass", f"{int(value)} early snipers", {"early_sniper_count": value}, started)


def social_signal(snapshot: Mapping[str, Any], thresholds: FilterThresholds) -> FilterResult:
    started = time.perf_counter()
    present = any(snapshot.get(key) is not None for key in ("x_mentions_1h", "kol_mentions_1h", "social_url"))
    if present:
        return _result("social_signal", "pass", "social signal present", {}, started)
    return _result("social_signal", "skip", "no social signal coverage", {}, started)


FILTERS: tuple[Callable[[Mapping[str, Any], FilterThresholds], FilterResult], ...] = (
    honeypot_sim, liquidity_min, permissions, holders, dev_holding, taxes, lp_lock,
    bundle_cohort, dev_history, recent_trades, mcap_band, early_snipers, social_signal,
)


def evaluate_filters(snapshot: Mapping[str, Any], thresholds: FilterThresholds,
                     mode: str = "safe") -> list[FilterResult]:
    return [fn(snapshot, thresholds) for fn in FILTERS]
