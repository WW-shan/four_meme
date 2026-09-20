"""Artifact writers for live verification runs (JSON evidence + markdown summary)."""

from __future__ import annotations

import json
from pathlib import Path
import time


def write_evidence(out_dir: str | Path, payload: dict) -> Path:
    path = Path(out_dir) / "evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    return path


def _status(entry: dict) -> str:
    if entry.get("kind") == "target":
        if entry.get("ok"):
            return "VERIFIED-LIVE"
        if (entry.get("history_probe") or {}).get("events_exist"):
            return "HISTORICAL"
        if entry.get("code_size_bytes"):
            return "CODE-ONLY"
        return "FAILED"
    if entry.get("ok"):
        return "OK"
    return "FAILED"


def render_markdown(payload: dict) -> str:
    lines = [
        "# 链与发射台实盘核实报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime(payload.get('generated_at', time.time())))}",
        f"- 链数量：{len(payload.get('chains', {}))}",
        "- 判定口径：`VERIFIED-LIVE` = 合约有代码 + 窗口内有真实链上事件（含区块/交易哈希）；"
        "`HISTORICAL` = 有代码且 from-genesis 证明历史上有真实事件，但最近窗口内没有；"
        "`CODE-ONLY` = 有代码但从未观察到事件；`FAILED` = 探测失败。",
        "",
        "## 链级连通性",
        "",
        "| 链 | chainId | 期望 | 最新区块 | 区块时间 | 日志模式 | 可用跨度 | 端点 |",
        "|---|---:|---:|---:|---:|---|---:|---|",
    ]
    for chain, item in payload.get("chains", {}).items():
        health = item.get("health") or {}
        span = health.get("log_span") or {}
        latest_ts = health.get("latest_block_timestamp")
        age = health.get("block_age_seconds")
        lines.append(
            f"| {chain} | {health.get('reported_chain_id')} | {health.get('expected_chain_id')} | "
            f"{health.get('latest_block')} | {latest_ts if latest_ts else '-'} ({age if age is not None else '-'}s 前) | "
            f"{span.get('mode', '-')} | {span.get('accepted_span', '-')} | {health.get('endpoint', '-')} |"
        )
    lines += ["", "## 合约级核实", "",
              "| 链 | 目标 | 地址 | 代码字节 | Sourcify 名称 | 窗口事件数 | 最近事件(区块/距今) | 期望 topic 命中 | 判定 |",
              "|---|---|---|---:|---|---:|---|---|---|"]
    for chain, item in payload.get("chains", {}).items():
        for target in item.get("targets", []):
            sourcify = target.get("sourcify") or {}
            hits = target.get("topic_hits") or {}
            hit_text = ", ".join(f"{sig.split('(')[0]}={count}" for sig, count in hits.items()) or "-"
            latest = target.get("latest_event") or {}
            if latest.get("block"):
                age = latest.get("age_seconds")
                if age is None:
                    age_text = "-"
                elif age < 3600:
                    age_text = f"{age / 60:.0f} 分钟前"
                elif age < 86400 * 2:
                    age_text = f"{age / 3600:.1f} 小时前"
                else:
                    age_text = f"{age / 86400:.1f} 天前"
                latest_text = f"{latest.get('block')} / {age_text}"
            else:
                latest_text = "-"
            lines.append(
                f"| {chain} | {target.get('target_id')} | {target.get('address')} | {target.get('code_size_bytes', '-')} | "
                f"{sourcify.get('name') or '-'} | {(target.get('live_window') or {}).get('logs', '-')} | {latest_text} | {hit_text} | {_status(target)} |"
            )
    blocked = payload.get("blocked_targets", [])
    if blocked:
        lines += ["", "## 未核实（明确阻塞，不猜地址）", "", "| 链 | 平台 | 阻塞原因 | 下一步 |", "|---|---|---|---|"]
        for item in blocked:
            lines.append(f"| {item.get('chain')} | {item.get('platform')} | {item.get('blocked_reason')} | {item.get('next_step')} |")
    discovery = payload.get("discovery", {})
    if discovery:
        lines += ["", "## 链上发现（无地址过滤的真实日志聚合）", ""]
        for chain, item in discovery.items():
            lines.append(f"### {chain}（{item.get('from_block')}–{item.get('to_block')}，{item.get('total_logs')} 条日志，"
                         f"{item.get('distinct_contracts')} 个合约）")
            lines.append("")
            lines.append("| 地址 | 日志数 | 代码字节 | Sourcify 名称 | 最高频 topic0 |")
            lines.append("|---|---:|---:|---|---|")
            for candidate in (item.get("candidates") or [])[:12]:
                top_topic = next(iter((candidate.get("topics") or {}).items()), ("-", 0))
                lines.append(f"| {candidate.get('address')} | {candidate.get('logs')} | {candidate.get('code_size_bytes', '-')} | "
                             f"{(candidate.get('sourcify') or {}).get('name') or '-'} | {top_topic[0]} ({top_topic[1]}) |")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_report(out_dir: str | Path, payload: dict) -> Path:
    path = Path(out_dir) / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(payload), encoding="utf-8")
    return path
