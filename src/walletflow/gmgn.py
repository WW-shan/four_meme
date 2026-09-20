"""GMGN wallet-trade adapter. Parsing is pure; transport is injectable."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.walletflow.pipeline import WalletEvent


def parse_trade_rows(rows: Iterable[Mapping[str, Any]], *, label: str = "kol") -> list[WalletEvent]:
    events: list[WalletEvent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        side = str(row.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            continue
        token = str(row.get("base_address") or row.get("token") or "")
        wallet = str(row.get("maker") or row.get("wallet") or "")
        if not token or not wallet:
            continue
        try:
            amount = float(row.get("amount_usd") or row.get("quote_amount") or 0.0)
            chain_time = float(row.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            continue
        events.append(WalletEvent(wallet=wallet, token=token, side=side, quote_amount=amount,
                                  chain_time=chain_time, label=label))
    return events


class GmgnWalletClient:
    def __init__(self, client=None):
        self.client = client

    def wallet_trades(self, address: str, *, label: str = "kol", limit: int = 100) -> list[WalletEvent]:
        if self.client is None:
            return []
        payload = self.client.wallet_trades(address, limit=limit)
        rows = payload.get("data", {}).get("list", []) if isinstance(payload, Mapping) else []
        return parse_trade_rows(rows, label=label)
