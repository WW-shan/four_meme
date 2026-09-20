import math
from typing import Dict, Iterable, List, Optional

import numpy as np

OPTIONAL_FLOW_FEATURE_NAMES = (
    "sell_volume_10s",
    "sell_volume_30s",
    "sell_volume_60s",
    "total_flow_volume_10s",
    "total_flow_volume_30s",
    "total_flow_volume_60s",
    "sell_pressure_10s",
    "sell_pressure_30s",
    "sell_pressure_60s",
    "signed_imbalance_10s",
    "signed_imbalance_30s",
    "signed_imbalance_60s",
)

REPLAY_CONTEXT_FEATURE_NAMES = (
    "lifecycle_status_chain_lag_seconds",
)


def requires_flow_features(feature_names: Optional[Iterable[str]]) -> bool:
    if not feature_names:
        return False
    optional = set(OPTIONAL_FLOW_FEATURE_NAMES)
    return any(str(name) in optional for name in feature_names)


def resolve_current_price(past_buys: List[Dict], past_sells: List[Dict]) -> float:
    trade_points = []
    for buy in past_buys:
        trade_points.append((int(buy.get("timestamp", 0)), float(buy.get("price", 0.0))))
    for sell in past_sells:
        trade_points.append((int(sell.get("timestamp", 0)), float(sell.get("price", 0.0))))

    if not trade_points:
        return 0.0

    trade_points.sort(key=lambda item: item[0])
    return float(trade_points[-1][1])


def extract_features(
    lifecycle: Dict,
    past_buys: List[Dict],
    past_sells: List[Dict],
    sample_time: int,
    *,
    include_flow_features: bool = False,
) -> Dict:
    def _safe_div(num: float, den: float, default: float = 0.0) -> float:
        return float(num / den) if den else float(default)

    def _window_buys(window_seconds: int) -> List[Dict]:
        start = sample_time - int(window_seconds)
        return [b for b in past_buys if int(b.get("timestamp", 0)) > start]

    def _window_sells(window_seconds: int) -> List[Dict]:
        start = sample_time - int(window_seconds)
        return [s for s in past_sells if int(s.get("timestamp", 0)) > start]

    def _segment_buys(left_seconds: int, right_seconds: int) -> List[Dict]:
        left = sample_time - int(left_seconds)
        right = sample_time - int(right_seconds)
        return [b for b in past_buys if left < int(b.get("timestamp", 0)) <= right]

    def _linear_slope(x_vals: List[float], y_vals: List[float]) -> float:
        if len(x_vals) != len(y_vals) or len(x_vals) < 2:
            return 0.0
        x = np.array(x_vals, dtype=float)
        y = np.array(y_vals, dtype=float)
        x_centered = x - float(np.mean(x))
        denom = float(np.sum(x_centered ** 2))
        if denom <= 0.0:
            return 0.0
        return float(np.sum(x_centered * (y - float(np.mean(y)))) / denom)

    def _top_n_holder_share(window_seconds: int, n: int = 10) -> float:
        cutoff = sample_time - int(window_seconds)
        balances: Dict[str, float] = {}
        for buy in past_buys:
            if int(buy.get("timestamp", 0)) > cutoff:
                addr = str(buy.get("account", ""))
                balances[addr] = balances.get(addr, 0.0) + float(buy.get("token_amount", 0.0))
        for sell in past_sells:
            if int(sell.get("timestamp", 0)) > cutoff:
                addr = str(sell.get("account", ""))
                balances[addr] = balances.get(addr, 0.0) - float(sell.get("token_amount", 0.0))

        positive_balances = [balance for balance in balances.values() if balance > 0.0]
        if not positive_balances:
            return 0.0

        positive_balances.sort(reverse=True)
        total_held = float(sum(positive_balances))
        top_n = float(sum(positive_balances[:n]))
        return _safe_div(top_n, total_held, default=0.0)

    time_since_launch = sample_time - lifecycle['create_timestamp']

    total_supply = lifecycle['total_supply'] / 1e18
    launch_fee = lifecycle['launch_fee'] / 1e18
    liquidity_ratio = (launch_fee * 1e18) / lifecycle['total_supply'] if lifecycle['total_supply'] > 0 else 0

    total_buys = len(past_buys)
    total_sells = len(past_sells)
    unique_buyers = len(set(b['account'] for b in past_buys))
    unique_sellers = len(set(s['account'] for s in past_sells))

    total_buy_volume = sum(b['bnb_amount'] for b in past_buys)
    total_sell_volume = sum(s['bnb_amount'] for s in past_sells)

    current_price = resolve_current_price(past_buys, past_sells)
    first_price = past_buys[0]['price'] if past_buys else 0
    price_change_pct = ((current_price - first_price) / first_price * 100) if first_price > 0 else 0

    all_prices = [b['price'] for b in past_buys] + [s['price'] for s in past_sells]
    max_price = max(all_prices) if all_prices else 0
    min_price = min(all_prices) if all_prices else 0

    def calc_window_volume(window_seconds):
        cutoff = sample_time - window_seconds
        return sum(b['bnb_amount'] for b in past_buys if b['timestamp'] >= cutoff)

    volume_10s = calc_window_volume(10)
    volume_30s = calc_window_volume(30)
    volume_1min = calc_window_volume(60)
    volume_2min = calc_window_volume(120)
    volume_5min = calc_window_volume(300)

    buy_pressure = total_buy_volume / (total_buy_volume + total_sell_volume) if (total_buy_volume + total_sell_volume) > 0 else 0.5
    avg_buy_size = total_buy_volume / total_buys if total_buys > 0 else 0
    avg_sell_size = total_sell_volume / total_sells if total_sells > 0 else 0
    trade_frequency = (total_buys + total_sells) / (time_since_launch / 60) if time_since_launch > 0 else 0

    recent_window = 30
    recent_buys = [b for b in past_buys if b['timestamp'] >= sample_time - recent_window]
    recent_avg_price = sum(b['price'] for b in recent_buys) / len(recent_buys) if recent_buys else current_price
    price_momentum = ((recent_avg_price - first_price) / first_price * 100) if first_price > 0 else 0

    buyer_concentration = unique_buyers / total_buys if total_buys > 0 else 0
    seller_concentration = unique_sellers / total_sells if total_sells > 0 else 1
    volume_acceleration = (volume_1min - volume_2min) / volume_2min if volume_2min > 0 else 0

    address_balances = {}
    for buy in past_buys:
        addr = buy['account']
        address_balances[addr] = address_balances.get(addr, 0) + buy['token_amount']
    for sell in past_sells:
        addr = sell['account']
        address_balances[addr] = address_balances.get(addr, 0) - sell['token_amount']

    holder_balances = {addr: balance for addr, balance in address_balances.items() if balance > 0}
    holder_count = len(holder_balances)

    if holder_balances:
        sorted_balances = sorted(holder_balances.values(), reverse=True)
        total_held = sum(sorted_balances)
        top5_balances = sum(sorted_balances[:5]) if len(sorted_balances) >= 5 else sum(sorted_balances)
        holder_concentration_top5 = top5_balances / total_held if total_held > 0 else 0
        max_holder_ratio = sorted_balances[0] / total_held if total_held > 0 else 0
        avg_holding = total_held / holder_count if holder_count > 0 else 0
    else:
        holder_concentration_top5 = 0
        max_holder_ratio = 0
        avg_holding = 0

    creator = lifecycle.get('creator', '')
    creator_is_buyer = creator in [b['account'] for b in past_buys]
    creator_is_seller = creator in [s['account'] for s in past_sells]
    creator_buy_volume = sum(b['bnb_amount'] for b in past_buys if b['account'] == creator)
    creator_sell_volume = sum(s['bnb_amount'] for s in past_sells if s['account'] == creator)
    creator_balance = address_balances.get(creator, 0)
    creator_holding_ratio = creator_balance / total_supply if total_supply > 0 else 0

    creator_buy_share = _safe_div(creator_buy_volume, total_buy_volume, default=0.0)
    creator_sell_share = _safe_div(creator_sell_volume, total_sell_volume, default=0.0)
    buy_volume_per_unique_buyer = _safe_div(total_buy_volume, float(unique_buyers), default=0.0)

    # High volume with little price movement is a useful wash/LPI warning signal.
    volume_price_divergence = math.log1p(max(0.0, total_buy_volume + total_sell_volume)) / (
        1.0 + abs(float(price_change_pct)) / 100.0
    )

    if avg_buy_size > 0:
        whale_threshold = avg_buy_size * 3
        whale_buys = [b for b in past_buys if b['bnb_amount'] > whale_threshold]
        whale_count = len(set(b['account'] for b in whale_buys))
        whale_buy_volume = sum(b['bnb_amount'] for b in whale_buys)
        whale_volume_ratio = whale_buy_volume / total_buy_volume if total_buy_volume > 0 else 0
    else:
        whale_count = 0
        whale_volume_ratio = 0

    buyer_trade_counts = {}
    for buy in past_buys:
        addr = buy['account']
        buyer_trade_counts[addr] = buyer_trade_counts.get(addr, 0) + 1

    repeat_buyers = sum(1 for count in buyer_trade_counts.values() if count > 1)
    repeat_buyer_ratio = repeat_buyers / unique_buyers if unique_buyers > 0 else 0

    buyers_set = set(b['account'] for b in past_buys)
    sellers_set = set(s['account'] for s in past_sells)
    overlap_addresses = buyers_set & sellers_set
    address_overlap_ratio = len(overlap_addresses) / len(buyers_set) if buyers_set else 0
    round_trip_buy_volume = sum(
        float(b.get('bnb_amount', 0.0))
        for b in past_buys
        if b.get('account', '') in sellers_set
    )
    round_trip_buy_volume_ratio = _safe_div(round_trip_buy_volume, total_buy_volume, default=0.0)

    create_time = lifecycle['create_timestamp']
    early_window = 30
    early_buys = [b for b in past_buys if b['timestamp'] - create_time <= early_window]
    early_buy_count = len(early_buys)
    early_buy_volume = sum(b['bnb_amount'] for b in early_buys)
    early_unique_buyers = len(set(b['account'] for b in early_buys))
    early_activity_ratio = early_buy_count / total_buys if total_buys > 0 else 0
    early_volume_ratio = early_buy_volume / total_buy_volume if total_buy_volume > 0 else 0

    burst_detected = False
    max_burst_volume = 0
    burst_window = 10
    if len(past_buys) >= 3:
        for i in range(len(past_buys)):
            window_start = past_buys[i]['timestamp']
            window_buys = [b for b in past_buys if window_start <= b['timestamp'] < window_start + burst_window]
            window_volume = sum(b['bnb_amount'] for b in window_buys)
            if window_volume > max_burst_volume:
                max_burst_volume = window_volume
            if window_volume > total_buy_volume * 0.3:
                burst_detected = True
    burst_intensity = max_burst_volume / total_buy_volume if total_buy_volume > 0 else 0

    if len(past_buys) >= 3:
        buy_intervals = []
        for i in range(1, len(past_buys)):
            interval = past_buys[i]['timestamp'] - past_buys[i-1]['timestamp']
            buy_intervals.append(interval)
        avg_interval = sum(buy_intervals) / len(buy_intervals)
        interval_variance = sum((x - avg_interval) ** 2 for x in buy_intervals) / len(buy_intervals)
        interval_std = interval_variance ** 0.5
        interval_regularity = interval_std / avg_interval if avg_interval > 0 else 0
    else:
        interval_regularity = 0

    if all_prices:
        avg_price = sum(all_prices) / len(all_prices)
        price_variance = sum((p - avg_price) ** 2 for p in all_prices) / len(all_prices)
        price_volatility = (price_variance ** 0.5) / avg_price if avg_price > 0 else 0
    else:
        price_volatility = 0

    if avg_buy_size > 0:
        small_buy_threshold = avg_buy_size * 0.5
        small_buys = [b for b in past_buys if b['bnb_amount'] < small_buy_threshold]
        small_buy_ratio = len(small_buys) / len(past_buys)
        large_buy_threshold = avg_buy_size * 2
        large_buys = [b for b in past_buys if b['bnb_amount'] > large_buy_threshold]
        large_buy_ratio = len(large_buys) / len(past_buys)
    else:
        small_buy_ratio = 0
        large_buy_ratio = 0

    # ===== 新增: 买盘质量斜率特征 (10/30/60秒) =====
    buys_10 = _window_buys(10)
    buys_30 = _window_buys(30)
    buys_60 = _window_buys(60)

    vol_10 = float(sum(b.get("bnb_amount", 0.0) for b in buys_10))
    vol_30 = float(sum(b.get("bnb_amount", 0.0) for b in buys_30))
    vol_60 = float(sum(b.get("bnb_amount", 0.0) for b in buys_60))
    cnt_10 = float(len(buys_10))
    cnt_30 = float(len(buys_30))
    cnt_60 = float(len(buys_60))
    ub_10 = float(len(set(b.get("account", "") for b in buys_10)))
    ub_30 = float(len(set(b.get("account", "") for b in buys_30)))
    ub_60 = float(len(set(b.get("account", "") for b in buys_60)))

    window_centers = [5.0, 15.0, 30.0]
    buy_volume_slope_10_60 = _linear_slope(window_centers, [vol_10, vol_30, vol_60])
    buy_count_slope_10_60 = _linear_slope(window_centers, [cnt_10, cnt_30, cnt_60])
    unique_buyers_slope_10_60 = _linear_slope(window_centers, [ub_10, ub_30, ub_60])

    buy_volume_slope_10_30 = _safe_div(vol_30 - vol_10, 20.0)
    buy_volume_slope_30_60 = _safe_div(vol_60 - vol_30, 30.0)
    buy_volume_acceleration_10_60 = buy_volume_slope_30_60 - buy_volume_slope_10_30

    buy_count_slope_10_30 = _safe_div(cnt_30 - cnt_10, 20.0)
    buy_count_slope_30_60 = _safe_div(cnt_60 - cnt_30, 30.0)
    buy_count_acceleration_10_60 = buy_count_slope_30_60 - buy_count_slope_10_30

    unique_buyers_slope_10_30 = _safe_div(ub_30 - ub_10, 20.0)
    unique_buyers_slope_30_60 = _safe_div(ub_60 - ub_30, 30.0)
    unique_buyers_acceleration_10_60 = unique_buyers_slope_30_60 - unique_buyers_slope_10_30

    volume_per_trade_10s = _safe_div(vol_10, cnt_10)
    volume_per_trade_30s = _safe_div(vol_30, cnt_30)
    volume_per_trade_60s = _safe_div(vol_60, cnt_60)

    # ===== 新增: 地址行为质量特征 =====
    buys_0_10 = _segment_buys(10, 0)
    buys_10_60 = _segment_buys(60, 10)
    buyers_0_10 = set(b.get("account", "") for b in buys_0_10)
    buyers_10_60 = set(b.get("account", "") for b in buys_10_60)
    buyers_30 = set(b.get("account", "") for b in buys_30)
    buyers_60 = set(b.get("account", "") for b in buys_60)
    sellers_60 = set(s.get("account", "") for s in _window_sells(60))

    new_buyers_0_10 = buyers_0_10 - buyers_10_60
    recent_new_buyer_ratio_10s = _safe_div(float(len(new_buyers_0_10)), float(len(buyers_0_10)), default=0.0)

    recent_repeat_buyer_ratio_30s = 0.0
    if buys_30:
        counts_30: Dict[str, int] = {}
        for buy in buys_30:
            addr = str(buy.get("account", ""))
            counts_30[addr] = counts_30.get(addr, 0) + 1
        repeat_30 = sum(1 for c in counts_30.values() if c > 1)
        recent_repeat_buyer_ratio_30s = _safe_div(float(repeat_30), float(len(counts_30)), default=0.0)

    buy_sell_overlap_ratio_60s = _safe_div(float(len(buyers_60 & sellers_60)), float(len(buyers_60)), default=0.0)
    recent_seller_reentry_ratio_30s = _safe_div(float(len(buyers_30 & sellers_60)), float(len(buyers_30)), default=0.0)

    if buyers_0_10 or buyers_10_60:
        inter = len(buyers_0_10 & buyers_10_60)
        union = len(buyers_0_10 | buyers_10_60)
        buyer_set_churn_10s_vs_prev50s = 1.0 - _safe_div(float(inter), float(union), default=0.0)
    else:
        buyer_set_churn_10s_vs_prev50s = 0.0

    # ===== 新增: 行为动力学特征 =====
    top10_holder_share_10s = float(_top_n_holder_share(window_seconds=10, n=10))
    top10_holder_share_30s = float(_top_n_holder_share(window_seconds=30, n=10))
    concentration_decay_10_30 = float(_safe_div(top10_holder_share_10s - top10_holder_share_30s, 20.0, default=0.0))

    eps = 1e-9
    retail_entry_rate_ratio_30s = float(
        _safe_div(unique_buyers_slope_10_30, max(buy_volume_slope_10_30, eps), default=0.0)
    )

    sells_10 = _window_sells(10)
    sells_30 = _window_sells(30)
    sells_60 = _window_sells(60)
    sell_volume_10s = float(sum(float(s.get("bnb_amount", 0.0)) for s in sells_10))
    sell_volume_30s = float(sum(float(s.get("bnb_amount", 0.0)) for s in sells_30))
    sell_volume_60s = float(sum(float(s.get("bnb_amount", 0.0)) for s in sells_60))
    total_flow_volume_10s = float(vol_10 + sell_volume_10s)
    total_flow_volume_30s = float(vol_30 + sell_volume_30s)
    total_flow_volume_60s = float(vol_60 + sell_volume_60s)
    sell_pressure_10s = float(_safe_div(sell_volume_10s, total_flow_volume_10s, default=0.0))
    sell_pressure_30s = float(_safe_div(sell_volume_30s, total_flow_volume_30s, default=0.0))
    sell_pressure_60s = float(_safe_div(sell_volume_60s, total_flow_volume_60s, default=0.0))
    sell_pressure_change_10_60 = sell_pressure_10s - sell_pressure_60s
    signed_imbalance_10s = float(_safe_div(vol_10 - sell_volume_10s, total_flow_volume_10s, default=0.0))
    signed_imbalance_30s = float(_safe_div(vol_30 - sell_volume_30s, total_flow_volume_30s, default=0.0))
    signed_imbalance_60s = float(_safe_div(vol_60 - sell_volume_60s, total_flow_volume_60s, default=0.0))
    recent_sell_pressure_10s = sell_volume_10s
    liquidity_proxy_10s = float(launch_fee + vol_10)
    lp_resistance_ratio_10s = float(
        _safe_div(liquidity_proxy_10s, max(recent_sell_pressure_10s, eps), default=0.0)
    )

    features = {
        'total_supply': total_supply,
        'launch_fee': launch_fee,
        'liquidity_ratio': liquidity_ratio,
        'name_length': len(lifecycle['name']),
        'symbol_length': len(lifecycle['symbol']),
        'time_since_launch': time_since_launch,
        'total_buys': total_buys,
        'total_sells': total_sells,
        'unique_buyers': unique_buyers,
        'unique_sellers': unique_sellers,
        'total_buy_volume': total_buy_volume,
        'total_sell_volume': total_sell_volume,
        'volume_10s': volume_10s,
        'volume_30s': volume_30s,
        'volume_1min': volume_1min,
        'volume_2min': volume_2min,
        'volume_5min': volume_5min,
        'current_price': current_price,
        'first_price': first_price,
        'price_change_pct': price_change_pct,
        'max_price': max_price,
        'min_price': min_price,
        'price_momentum': price_momentum,
        'buy_pressure': buy_pressure,
        'avg_buy_size': avg_buy_size,
        'avg_sell_size': avg_sell_size,
        'trade_frequency': trade_frequency,
        'buyer_concentration': buyer_concentration,
        'seller_concentration': seller_concentration,
        'volume_acceleration': volume_acceleration,
        'holder_count': holder_count,
        'holder_concentration_top5': holder_concentration_top5,
        'max_holder_ratio': max_holder_ratio,
        'avg_holding': avg_holding,
        'creator_is_buyer': 1 if creator_is_buyer else 0,
        'creator_is_seller': 1 if creator_is_seller else 0,
        'creator_buy_volume': creator_buy_volume,
        'creator_buy_share': creator_buy_share,
        'creator_sell_volume': creator_sell_volume,
        'creator_sell_share': creator_sell_share,
        'creator_holding_ratio': creator_holding_ratio,
        'whale_count': whale_count,
        'whale_volume_ratio': whale_volume_ratio,
        'repeat_buyer_ratio': repeat_buyer_ratio,
        'address_overlap_ratio': address_overlap_ratio,
        'round_trip_buy_volume_ratio': round_trip_buy_volume_ratio,
        'buy_volume_per_unique_buyer': buy_volume_per_unique_buyer,
        'volume_price_divergence': volume_price_divergence,
        'early_buy_count': early_buy_count,
        'early_buy_volume': early_buy_volume,
        'early_unique_buyers': early_unique_buyers,
        'early_activity_ratio': early_activity_ratio,
        'early_volume_ratio': early_volume_ratio,
        'burst_detected': 1 if burst_detected else 0,
        'burst_intensity': burst_intensity,
        'max_burst_volume': max_burst_volume,
        'interval_regularity': interval_regularity,
        'price_volatility': price_volatility,
        'small_buy_ratio': small_buy_ratio,
        'large_buy_ratio': large_buy_ratio,

        # 买盘质量斜率特征
        'buy_volume_slope_10_30': buy_volume_slope_10_30,
        'buy_volume_slope_30_60': buy_volume_slope_30_60,
        'buy_volume_slope_10_60': buy_volume_slope_10_60,
        'buy_volume_acceleration_10_60': buy_volume_acceleration_10_60,
        'buy_count_slope_10_30': buy_count_slope_10_30,
        'buy_count_slope_30_60': buy_count_slope_30_60,
        'buy_count_slope_10_60': buy_count_slope_10_60,
        'buy_count_acceleration_10_60': buy_count_acceleration_10_60,
        'unique_buyers_slope_10_30': unique_buyers_slope_10_30,
        'unique_buyers_slope_30_60': unique_buyers_slope_30_60,
        'unique_buyers_slope_10_60': unique_buyers_slope_10_60,
        'unique_buyers_acceleration_10_60': unique_buyers_acceleration_10_60,
        'volume_per_trade_10s': volume_per_trade_10s,
        'volume_per_trade_30s': volume_per_trade_30s,
        'volume_per_trade_60s': volume_per_trade_60s,

        # 地址行为质量特征
        'recent_new_buyer_ratio_10s': recent_new_buyer_ratio_10s,
        'recent_repeat_buyer_ratio_30s': recent_repeat_buyer_ratio_30s,
        'buy_sell_overlap_ratio_60s': buy_sell_overlap_ratio_60s,
        'recent_seller_reentry_ratio_30s': recent_seller_reentry_ratio_30s,
        'buyer_set_churn_10s_vs_prev50s': buyer_set_churn_10s_vs_prev50s,

        # 行为动力学特征
        'top10_holder_share_10s': top10_holder_share_10s,
        'top10_holder_share_30s': top10_holder_share_30s,
        'concentration_decay_10_30': concentration_decay_10_30,
        'retail_entry_rate_ratio_30s': retail_entry_rate_ratio_30s,
        'lp_resistance_ratio_10s': lp_resistance_ratio_10s,
        'sell_pressure_change_10_60': sell_pressure_change_10_60,
    }

    if include_flow_features:
        features.update({
            'sell_volume_10s': sell_volume_10s,
            'sell_volume_30s': sell_volume_30s,
            'sell_volume_60s': sell_volume_60s,
            'total_flow_volume_10s': total_flow_volume_10s,
            'total_flow_volume_30s': total_flow_volume_30s,
            'total_flow_volume_60s': total_flow_volume_60s,
            'sell_pressure_10s': sell_pressure_10s,
            'sell_pressure_30s': sell_pressure_30s,
            'sell_pressure_60s': sell_pressure_60s,
            'signed_imbalance_10s': signed_imbalance_10s,
            'signed_imbalance_30s': signed_imbalance_30s,
            'signed_imbalance_60s': signed_imbalance_60s,
        })

    return features
