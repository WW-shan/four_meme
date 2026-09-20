"""Paper fills with explicit slippage and fee assumptions."""

from __future__ import annotations

from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class ShadowFill:
    token: str
    side: str
    quote_amount: float
    token_amount: float
    price: float
    slippage_pct: float
    fee_pct: float
    fee_quote: float
    at: float


@dataclass
class ShadowPosition:
    token: str
    entry_fill: ShadowFill
    peak_price: float
    remaining_fraction: float = 1.0
    realized_quote: float = 0.0
    exits: list[dict] = field(default_factory=list)
    last_price: float | None = None

    def update_price(self, price: float) -> None:
        if price <= 0:
            raise ValueError("price must be positive")
        self.last_price = float(price)
        self.peak_price = max(self.peak_price, float(price))

    def unrealized_pnl_pct(self, price: float | None = None) -> float:
        current = float(price if price is not None else (self.last_price or self.entry_fill.price))
        return current / self.entry_fill.price - 1.0


def _validate(price: float, slippage_pct: float, fee_pct: float) -> None:
    if price <= 0 or not math.isfinite(price):
        raise ValueError("price must be a positive finite number")
    if not 0 <= slippage_pct < 100 or not 0 <= fee_pct < 100:
        raise ValueError("slippage_pct and fee_pct must be between 0 and 100")


def shadow_buy(token: str, quote_amount: float, quote_price: float, *, slippage_pct: float = 2.0,
               fee_pct: float = 1.0, at: float) -> ShadowFill:
    _validate(quote_price, slippage_pct, fee_pct)
    if quote_amount <= 0:
        raise ValueError("quote_amount must be positive")
    effective_price = quote_price * (1 + slippage_pct / 100)
    fee_quote = quote_amount * fee_pct / 100
    token_amount = (quote_amount - fee_quote) / effective_price
    return ShadowFill(token, "buy", quote_amount, token_amount, effective_price, slippage_pct, fee_pct, fee_quote, at)


def shadow_sell(token: str, token_amount: float, quote_price: float, *, slippage_pct: float = 2.0,
                fee_pct: float = 1.0, at: float) -> ShadowFill:
    _validate(quote_price, slippage_pct, fee_pct)
    if token_amount <= 0:
        raise ValueError("token_amount must be positive")
    effective_price = quote_price * (1 - slippage_pct / 100)
    gross = token_amount * effective_price
    fee_quote = gross * fee_pct / 100
    proceeds = gross - fee_quote
    return ShadowFill(token, "sell", proceeds, token_amount, effective_price, slippage_pct, fee_pct, fee_quote, at)
