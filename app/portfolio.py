"""A 50,000 TWD book with at most ten names.

Only common stocks under 300 TWD whose close is above the 5-day average and
whose last five sessions are up get bought. ETFs are skipped. The decision uses the broadcast-day close,
which is already known when the 21:00 show airs. Nothing here reads financial
statements or news, and it is not a forecast.
"""

from __future__ import annotations

from datetime import date
from statistics import fmean

from app.logic import BUDGET, START_DATE, TARGET, format_day, parse_iso_date, shares_for, taipei_now

CAPITAL = BUDGET * 10
SLOTS = 10
PRICE_MAX = 300


def qualify(bars: list[tuple[date, float]], as_of: date) -> dict | None:
    known = [(day, close) for day, close in bars if day <= as_of]
    if len(known) < 6:
        return None
    closes = [close for _, close in known]
    last = closes[-1]
    momentum = last / closes[-6] - 1
    average = fmean(closes[-5:])
    return {
        "asOf": known[-1][0].isoformat(),
        "close": last,
        "momentum5": momentum,
        "aboveAverage": last > average,
        "qualified": last > average and momentum > 0,
    }


def simulate(conn) -> dict:
    today = taipei_now().date()
    start = START_DATE.isoformat()
    rows = conn.execute(
        "SELECT m.video_id, m.symbol, m.name, e.broadcast_date, e.entry_date, s.market "
        "FROM mentions m JOIN episodes e ON e.video_id = m.video_id "
        "LEFT JOIN securities s ON s.symbol = m.symbol "
        "WHERE e.broadcast_date >= ? "
        "ORDER BY e.entry_date, e.broadcast_date, m.symbol",
        (start,),
    ).fetchall()
    symbols = {row["symbol"] for row in rows}
    bars: dict[str, list[tuple[date, float]]] = {symbol: [] for symbol in symbols}
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        for row in conn.execute(
            f"SELECT symbol, date, close FROM prices WHERE symbol IN ({placeholders}) ORDER BY date",
            tuple(symbols),
        ):
            bars[row["symbol"]].append((date.fromisoformat(row["date"]), row["close"]))
    quotes = {(symbol, day): close for symbol, series in bars.items() for day, close in series}

    candidates = []
    for row in rows:
        broadcast = parse_iso_date(row["broadcast_date"])
        entry = parse_iso_date(row["entry_date"])
        signal = qualify(bars.get(row["symbol"], []), broadcast) if broadcast else None
        entry_price = quotes.get((row["symbol"], entry)) if entry else None
        candidates.append(
            {
                "videoId": row["video_id"],
                "symbol": row["symbol"],
                "name": row["name"],
                "broadcast": broadcast,
                "entry": entry,
                "signal": signal,
                "entryPrice": entry_price,
                "market": row["market"],
            }
        )

    by_entry: dict[date, list] = {}
    for item in candidates:
        if item["entry"] is not None:
            by_entry.setdefault(item["entry"], []).append(item)

    positions: dict[str, dict] = {}
    closed: list[dict] = []
    bought: list[dict] = []
    blocked: list[dict] = []
    waiting: list[dict] = []
    rejected = 0
    unfilled = 0
    held_skips = 0
    etf_skips = 0
    expensive = 0

    days = sorted({day for series in bars.values() for day, _ in series if day >= START_DATE})
    for day in days:
        for symbol, pos in list(positions.items()):
            if day <= pos["entry"]:
                continue
            close = quotes.get((symbol, day))
            if close is None:
                continue
            if (close - pos["entryPrice"]) / pos["entryPrice"] + 1e-9 < TARGET:
                continue
            pnl = round(pos["shares"] * (close - pos["entryPrice"]), 2)
            closed.append({**pos, "exit": day, "exitPrice": close, "pnl": pnl, "pnlPct": (close - pos["entryPrice"]) / pos["entryPrice"]})
            del positions[symbol]

        batch = by_entry.get(day, [])
        ready = []
        for item in batch:
            signal = item["signal"] or {}
            if item.get("market") == "ETF":
                etf_skips += 1
                continue
            if _above_price_cap(item):
                expensive += 1
                continue
            if item["symbol"] in positions:
                held_skips += 1
                if signal.get("qualified"):
                    blocked.append(_blocked(item, "已持有"))
                continue
            if not signal.get("qualified"):
                rejected += 1
                continue
            if item["entryPrice"] is None:
                continue
            shares = shares_for(item["entryPrice"])
            if shares == 0:
                unfilled += 1
                continue
            ready.append({**item, "shares": shares, "cost": round(shares * item["entryPrice"], 2)})
        ready.sort(key=lambda item: item["signal"]["momentum5"], reverse=True)
        for item in ready:
            deployed = sum(pos["cost"] for pos in positions.values())
            if len(positions) >= SLOTS:
                blocked.append(_blocked(item, "已滿 10 檔"))
                continue
            if deployed + item["cost"] > CAPITAL + 0.01:
                blocked.append(_blocked(item, "超過 5 萬"))
                continue
            pos = {
                "symbol": item["symbol"],
                "name": item["name"],
                "broadcast": item["broadcast"],
                "entry": item["entry"],
                "entryPrice": item["entryPrice"],
                "shares": item["shares"],
                "cost": item["cost"],
                "momentum5": item["signal"]["momentum5"],
            }
            positions[item["symbol"]] = pos
            bought.append(pos)

    for item in candidates:
        entry = item["entry"]
        if entry is None or entry < today or item["entryPrice"] is not None:
            continue
        if item.get("market") == "ETF" or _above_price_cap(item):
            continue
        if not (item["signal"] or {}).get("qualified"):
            continue
        if item["symbol"] in positions:
            blocked.append(_blocked(item, "已持有"))
            continue
        if len(positions) >= SLOTS or sum(pos["cost"] for pos in positions.values()) + BUDGET > CAPITAL + 0.01:
            blocked.append(_blocked(item, "已滿 10 檔"))
            continue
        waiting.append(_waiting(item))

    open_rows = []
    for pos in positions.values():
        mark_day = None
        mark = None
        for day, close in bars.get(pos["symbol"], []):
            if pos["entry"] <= day <= today and (mark_day is None or day > mark_day):
                mark_day = day
                mark = close
        pnl = round(pos["shares"] * (mark - pos["entryPrice"]), 2) if mark is not None else None
        open_rows.append({**pos, "markPrice": mark, "pnl": pnl, "pnlPct": ((mark - pos["entryPrice"]) / pos["entryPrice"]) if mark is not None else None})
    open_rows.sort(key=lambda item: item["entry"] or date.min, reverse=True)
    closed.sort(key=lambda item: item["exit"], reverse=True)

    realized = round(sum(item["pnl"] for item in closed), 2)
    return {
        "capital": CAPITAL,
        "slots": SLOTS,
        "openCount": len(open_rows),
        "deployed": round(sum(item["cost"] for item in open_rows), 2),
        "realized": realized,
        "unrealized": round(sum(item["pnl"] or 0 for item in open_rows), 2),
        "wins": len(closed),
        "counts": {
            "seen": len(candidates),
            "bought": len(bought),
            "rejected": rejected,
            "etf": etf_skips,
            "expensive": expensive,
            "blocked": len(blocked),
            "unfilled": unfilled,
            "held": held_skips,
            "waiting": len(waiting),
        },
        "open": [_open_json(item) for item in open_rows],
        "closed": [_closed_json(item) for item in closed],
        "waiting": [_wait_json(item) for item in waiting],
        "blocked": [_block_json(item) for item in blocked],
    }


def _above_price_cap(item: dict) -> bool:
    prices = [item["entryPrice"]] if item.get("entryPrice") is not None else []
    close = (item.get("signal") or {}).get("close")
    if close is not None:
        prices.append(close)
    return any(price >= PRICE_MAX for price in prices)


def _blocked(item: dict, reason: str) -> dict:
    return {**item, "reason": reason}


def _waiting(item: dict) -> dict:
    return item


def _open_json(item: dict) -> dict:
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "broadcastLabel": format_day(item["broadcast"]),
        "entryLabel": format_day(item["entry"]),
        "shares": item["shares"],
        "entryPrice": item["entryPrice"],
        "markPrice": item["markPrice"],
        "cost": item["cost"],
        "pnl": item["pnl"],
        "pnlPct": item["pnlPct"],
        "momentum5": item["momentum5"],
    }


def _closed_json(item: dict) -> dict:
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "broadcastLabel": format_day(item["broadcast"]),
        "entryLabel": format_day(item["entry"]),
        "exitLabel": format_day(item["exit"]),
        "shares": item["shares"],
        "entryPrice": item["entryPrice"],
        "exitPrice": item["exitPrice"],
        "pnl": item["pnl"],
        "pnlPct": item["pnlPct"],
    }


def _wait_json(item: dict) -> dict:
    signal = item["signal"] or {}
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "broadcastLabel": format_day(item["broadcast"]),
        "entryLabel": format_day(item["entry"]),
        "momentum5": signal.get("momentum5"),
    }


def _block_json(item: dict) -> dict:
    signal = item["signal"] or {}
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "broadcastLabel": format_day(item["broadcast"]),
        "entryLabel": format_day(item["entry"]),
        "reason": item["reason"],
        "momentum5": signal.get("momentum5"),
    }
