from __future__ import annotations

import time
from datetime import date, datetime

import requests

from app.db import connect
from app.logic import TPE

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    return session


def _suffixes(market: str) -> list[str]:
    if market == "TPEx":
        return [".TWO", ".TW"]
    return [".TW", ".TWO"]


def fetch_closes(
    session: requests.Session,
    symbol: str,
    market: str,
    start: date,
    end: date,
) -> list[tuple[str, float]]:
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=TPE).timestamp())
    period2 = int(datetime(end.year, end.month, end.day, tzinfo=TPE).timestamp()) + 86400
    for suffix in _suffixes(market):
        for attempt in range(3):
            try:
                response = session.get(
                    CHART.format(symbol=f"{symbol}{suffix}"),
                    params={"interval": "1d", "period1": period1, "period2": period2},
                    timeout=25,
                )
            except requests.RequestException:
                time.sleep(0.6 + attempt)
                continue
            if response.status_code == 429:
                time.sleep(1.5 + attempt)
                continue
            if response.status_code == 404:
                break
            if response.status_code != 200:
                time.sleep(0.6)
                continue
            result = (response.json().get("chart") or {}).get("result")
            if not result:
                break
            stamps = result[0].get("timestamp") or []
            closes = (result[0].get("indicators") or {}).get("quote", [{}])[0].get("close") or []
            rows = []
            for stamp, close in zip(stamps, closes):
                if close is None:
                    continue
                day = datetime.fromtimestamp(stamp, TPE).date().isoformat()
                rows.append((day, round(float(close), 2)))
            if rows:
                return rows
        time.sleep(0.05)
    return []


def ensure_prices(symbols: list[tuple[str, str]], start: date, end: date, progress) -> None:
    """symbols is a list of (symbol, market). Always refreshes 0050."""
    conn = connect()
    session = _session()
    try:
        wanted = [("0050", "ETF")]
        seen = {"0050"}
        for symbol, market in symbols:
            if symbol not in seen:
                seen.add(symbol)
                wanted.append((symbol, market))

        target = end.isoformat()
        pending = []
        for symbol, market in wanted:
            row = conn.execute("SELECT MAX(date) AS d FROM prices WHERE symbol = ?", (symbol,)).fetchone()
            latest = row["d"]
            if latest and latest >= target:
                continue
            pending.append((symbol, market))

        for index, (symbol, market) in enumerate(pending, start=1):
            progress(f"下載股價 {symbol}（{index}/{len(pending)}）")
            rows = fetch_closes(session, symbol, market, start, end)
            if rows:
                conn.executemany(
                    "INSERT INTO prices(symbol, date, close) VALUES(?, ?, ?) "
                    "ON CONFLICT(symbol, date) DO UPDATE SET close = excluded.close",
                    [(symbol, day, close) for day, close in rows],
                )
                conn.commit()
            time.sleep(0.12)
    finally:
        conn.close()


def trading_days(conn) -> list[date]:
    rows = conn.execute("SELECT date FROM prices WHERE symbol = '0050' ORDER BY date").fetchall()
    days = [date.fromisoformat(row["date"]) for row in rows]
    if days:
        return days
    rows = conn.execute("SELECT DISTINCT date FROM prices ORDER BY date").fetchall()
    return [date.fromisoformat(row["date"]) for row in rows]


def price_map(conn, symbols: set[str]) -> dict[tuple[str, str], float]:
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, date, close FROM prices WHERE symbol IN ({placeholders})",
        tuple(symbols),
    ).fetchall()
    return {(row["symbol"], row["date"]): row["close"] for row in rows}


def market_map(conn, symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, market FROM securities WHERE symbol IN ({placeholders})",
        tuple(symbols),
    ).fetchall()
    found = {row["symbol"]: row["market"] for row in rows}
    for symbol in symbols:
        found.setdefault(symbol, "TWSE")
    return found
