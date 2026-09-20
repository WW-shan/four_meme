"""Wallet event aggregation: net inflow, deployer reputation, bundle cohort."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class WalletEvent:
    wallet: str
    token: str
    side: str  # buy | sell
    quote_amount: float
    chain_time: float
    label: str = "unknown"
    received_at: float | None = None

    def __post_init__(self):
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if self.quote_amount < 0:
            raise ValueError("quote_amount must be non-negative")


class WalletFlow:
    def __init__(self):
        self.events: list[WalletEvent] = []

    def add(self, event: WalletEvent) -> None:
        self.events.append(event)

    def extend(self, events: Iterable[WalletEvent]) -> None:
        for event in events:
            self.add(event)

    def net_inflow(self, token: str) -> float:
        return sum((event.quote_amount if event.side == "buy" else -event.quote_amount)
                   for event in self.events if event.token == token)

    def buy_sell_ratio(self, token: str) -> float | None:
        buys = sum(event.quote_amount for event in self.events if event.token == token and event.side == "buy")
        sells = sum(event.quote_amount for event in self.events if event.token == token and event.side == "sell")
        if buys <= 0:
            return None
        return buys / sells if sells > 0 else float("inf")

    def deployer_reputation(self, wallet: str, rug_tokens: Iterable[str] = ()) -> dict:
        rug_set = set(rug_tokens)
        launches = {event.token for event in self.events if event.wallet == wallet and event.label == "deployer"}
        if not launches:
            return {"wallet": wallet, "launches": 0, "rug_rate": None, "bonding_rate": None}
        rugged = len(launches & rug_set)
        return {
            "wallet": wallet,
            "launches": len(launches),
            "rug_rate": rugged / len(launches),
            "bonding_rate": (len(launches) - rugged) / len(launches),
        }

    def bundle_cohort(self, token: str, current_held_pct: Mapping[str, float] | None = None,
                      early_window_seconds: float = 4.0) -> dict:
        cohort = [event for event in self.events if event.token == token and event.side == "buy"]
        if not cohort:
            return {"wallet_count": 0, "total_pct": None, "current_held_pct": None}
        first_time = min(event.chain_time for event in cohort)
        early = [event for event in cohort if event.chain_time - first_time <= early_window_seconds]
        wallets = {event.wallet for event in early}
        current = None
        if current_held_pct is not None:
            current = sum(float(current_held_pct.get(wallet, 0.0)) for wallet in wallets)
        return {"wallet_count": len(wallets), "total_pct": None, "current_held_pct": current}

    def funding_confirmed(self, token: str, minimum_net_inflow: float = 0.0) -> bool:
        return self.net_inflow(token) > minimum_net_inflow
