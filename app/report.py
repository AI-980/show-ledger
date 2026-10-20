from __future__ import annotations

from statistics import fmean

from app.db import connect, meta_get
from app.logic import START_DATE, classify, format_day, parse_iso_date, taipei_now
from app.portfolio import simulate
from app.sync import status


def _trade_json(row) -> dict:
    return {
        "videoId": row["video_id"],
        "symbol": row["symbol"],
        "name": row["name"],
        "status": row["status"] if row["status"] != "settled" else classify(row["pnl"]),
        "state": row["status"],
        "shares": row["shares"],
        "cost": row["cost"],
        "entryPrice": row["entry_price"],
        "exitPrice": row["exit_price"],
        "markPrice": row["mark_price"],
        "pnl": row["pnl"],
        "pnlPct": row["pnl_pct"],
        "snippet": row["snippet"],
        "broadcastDate": row["broadcast_date"],
        "entryDate": row["entry_date"],
        "settleDate": row["settle_date"],
    }


def _sum_pnl(rows: list[dict]) -> float:
    return round(sum(row["pnl"] or 0 for row in rows), 2)


def _in_book(broadcast_date: str | None) -> bool:
    """Count from the episode aired on the start date, not from the later buy."""
    return (broadcast_date or "") >= START_DATE.isoformat()


def _trends(conn, trades: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for trade in trades:
        item = grouped.setdefault(
            trade["symbol"],
            {"name": trade["name"], "wins": 0, "losses": 0, "pnl": 0.0},
        )
        if trade["state"] != "settled":
            continue
        item["pnl"] += trade["pnl"] or 0
        if trade["status"] == "win":
            item["wins"] += 1
        elif trade["status"] == "loss":
            item["losses"] += 1

    start = START_DATE.isoformat()
    board = []
    for symbol, info in grouped.items():
        prices = conn.execute(
            "SELECT date, close FROM prices WHERE symbol = ? AND date >= ? ORDER BY date",
            (symbol, start),
        ).fetchall()
        if len(prices) < 2:
            continue
        closes = [row["close"] for row in prices]
        first, latest = closes[0], closes[-1]
        trend = latest / first - 1
        momentum = latest / closes[-6] - 1 if len(closes) >= 6 else None
        average = fmean(closes[-5:])
        above = latest > average
        strong = trend > 0 and momentum is not None and momentum > 0 and above
        board.append(
            {
                "symbol": symbol,
                "name": info["name"],
                "fromPrice": first,
                "latestPrice": latest,
                "returnSinceStart": trend,
                "momentum5": momentum,
                "aboveAverage": above,
                "strong": strong,
                "wins": info["wins"],
                "losses": info["losses"],
                "pnl": round(info["pnl"], 2),
                "closes": [round(price, 2) for price in closes],
            }
        )
    board.sort(key=lambda item: (item["strong"], item["returnSinceStart"]), reverse=True)
    return board


def snapshot() -> dict:
    conn = connect()
    try:
        episode_rows = [
            row
            for row in conn.execute(
                "SELECT video_id, title, broadcast_date, url, entry_date, settle_date "
                "FROM episodes ORDER BY broadcast_date DESC, video_id"
            )
            if _in_book(row["broadcast_date"])
        ]
        trade_rows = [
            row
            for row in conn.execute("SELECT * FROM trades ORDER BY broadcast_date DESC, symbol")
            if _in_book(row["broadcast_date"])
        ]
        trades_by_video: dict[str, list] = {}
        flat = []
        for row in trade_rows:
            item = _trade_json(row)
            item["broadcastLabel"] = format_day(parse_iso_date(row["broadcast_date"]))
            item["entryLabel"] = format_day(parse_iso_date(row["entry_date"]))
            item["settleLabel"] = format_day(parse_iso_date(row["settle_date"]))
            trades_by_video.setdefault(row["video_id"], []).append(item)
            flat.append(item)

        episodes_by_id = {
            row["video_id"]: {
                "videoId": row["video_id"],
                "title": row["title"],
                "url": row["url"],
                "broadcastDate": row["broadcast_date"],
                "broadcastLabel": format_day(parse_iso_date(row["broadcast_date"])),
                "entryDate": row["entry_date"],
                "entryLabel": format_day(parse_iso_date(row["entry_date"])),
            }
            for row in episode_rows
        }
        buckets: dict[str, list] = {}
        for trade in flat:
            key = trade["settleDate"] if trade["state"] == "settled" else "holding"
            buckets.setdefault(key, []).append(trade)
        seen_videos = {trade_row["video_id"] for trade_row in trade_rows}
        if any(row["video_id"] not in seen_videos for row in episode_rows):
            buckets.setdefault("holding", [])

        weeks: dict[str, dict] = {}
        for key, trades in buckets.items():
            holding = key == "holding"
            week = weeks.setdefault(
                key,
                {
                    "id": key,
                    "settleDate": None if holding else key,
                    "label": "還沒到 10%" if holding else format_day(parse_iso_date(key)),
                    "holding": holding,
                    "episodes": [],
                },
            )
            by_video: dict[str, list] = {}
            for trade in trades:
                by_video.setdefault(trade["videoId"], []).append(trade)
            for video_id, episode_trades in by_video.items():
                source = episodes_by_id.get(video_id)
                if source is None:
                    continue
                week["episodes"].append({**source, "trades": episode_trades})
        for row in episode_rows:
            if row["video_id"] in seen_videos:
                continue
            weeks["holding"]["episodes"].append({**episodes_by_id[row["video_id"]], "trades": []})

        week_list = []
        for week in weeks.values():
            trades = [trade for episode in week["episodes"] for trade in episode["trades"]]
            settled = [trade for trade in trades if trade["state"] == "settled"]
            wins = [trade for trade in settled if trade["status"] == "win"]
            week_list.append(
                {
                    **week,
                    "wins": len(wins),
                    "losses": 0,
                    "pushes": 0,
                    "open": sum(1 for trade in trades if trade["state"] == "open"),
                    "pending": sum(1 for trade in trades if trade["state"] == "pending"),
                    "unfilled": sum(1 for trade in trades if trade["state"] == "unfilled"),
                    "pnl": _sum_pnl(settled),
                    "unrealized": _sum_pnl([trade for trade in trades if trade["state"] == "open"]),
                    "settled": len(settled),
                }
            )
        week_list.sort(key=lambda week: (week["holding"], week["settleDate"] or ""), reverse=True)

        settled = [trade for trade in flat if trade["state"] == "settled"]
        wins = [trade for trade in settled if trade["status"] == "win"]
        open_trades = [trade for trade in flat if trade["state"] == "open"]
        hit_denom = len(wins) + len(open_trades)
        cost = sum(trade["cost"] or 0 for trade in settled)
        pnl = _sum_pnl(settled)
        latest = next((week for week in week_list if week["settled"]), None)
        return {
            "asOf": taipei_now().date().isoformat(),
            "startDate": START_DATE.isoformat(),
            "syncedAt": meta_get(conn, "synced_at"),
            "summary": {
                "winRate": (len(wins) / hit_denom) if hit_denom else None,
                "wins": len(wins),
                "losses": 0,
                "pushes": 0,
                "pnl": pnl,
                "cost": round(cost, 2),
                "returnPct": (pnl / cost) if cost else None,
                "unrealized": _sum_pnl(open_trades),
                "open": len(open_trades),
                "pending": sum(1 for trade in flat if trade["state"] == "pending"),
                "unfilled": sum(1 for trade in flat if trade["state"] == "unfilled"),
                "noPrice": sum(1 for trade in flat if trade["state"] == "no_price"),
                "episodes": len(episode_rows),
                "trades": len(flat),
            },
            "latestSettled": latest,
            "portfolio": simulate(conn),
            "trends": _trends(conn, flat),
            "weeks": week_list,
            "sync": status(),
        }
    finally:
        conn.close()
