from __future__ import annotations

import re
import threading
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta

from app.db import connect, meta_get, meta_set
from app.logic import (
    TPE,
    expected_latest_broadcast,
    format_day,
    entry_date,
    take_profit_day,
    previous_broadcast,
    project_calendar,
    shares_for,
    taipei_now,
)
from app.prices import ensure_prices, market_map, price_map, trading_days
from app.universe import load_matcher, load_universe, match_text
from app.youtube_source import (
    SHORTS_PARAMS,
    VIDEOS_PARAMS,
    _session,
    broadcast_date_from_title,
    fetch_player,
    headline,
    is_full_episode,
    is_show_part,
    list_tab,
)

LOOKBACK_DAYS = 70
STATE = {"running": False, "message": "尚未同步", "error": None, "finished_at": None}
STATE_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
BOILERPLATE = ("本節目與分析師", "◆加入", "◆訂閱", "※本節目")


def status() -> dict:
    with STATE_LOCK:
        return dict(STATE)


def _progress(message: str) -> None:
    print(message, flush=True)
    with STATE_LOCK:
        STATE["message"] = message


def should_sync() -> bool:
    conn = connect()
    try:
        ready = meta_get(conn, "ready_through")
        if not ready:
            return True
        return date.fromisoformat(ready) < expected_latest_broadcast()
    finally:
        conn.close()


def _usable(text: str) -> str:
    for marker in BOILERPLATE:
        pos = text.find(marker)
        if pos >= 0:
            text = text[:pos]
    text = re.sub(r"https?://\S+", " ", text)
    return text.replace("#", " ")


def _cached_player(conn, session, video_id: str) -> dict:
    row = conn.execute(
        "SELECT title, description, published_at FROM video_cache WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if row and row["published_at"]:
        return dict(row)
    try:
        info = fetch_player(session, video_id)
    except Exception as exc:
        print(f"略過影片 {video_id}：{exc}", flush=True)
        return {"title": "", "description": "", "published_at": ""}
    conn.execute(
        "INSERT INTO video_cache(video_id, title, description, published_at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(video_id) DO UPDATE SET title = excluded.title, description = excluded.description, "
        "published_at = excluded.published_at",
        (video_id, info["title"], info["description"], info["published_at"]),
    )
    conn.commit()
    time.sleep(0.08)
    return info


def _clip_day(published_at: str) -> date | None:
    if not published_at:
        return None
    stamp = datetime.fromisoformat(published_at)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TPE)
    local = stamp.astimezone(TPE)
    if local.hour >= 21 and local.weekday() < 5:
        return local.date()
    return previous_broadcast(local.date())


def _sync() -> None:
    _progress("更新股票名單")
    load_universe()
    aliases, symbols, _names = load_matcher()
    resolved = {alias: symbol for alias, symbol, _name in aliases}
    for alias in ("富喬", "騰輝", "聯茂", "台郡", "台積", "發哥", "大立光", "日月光", "鴻海", "台光電", "群聯", "技嘉"):
        print(f"  別名 {alias} → {resolved.get(alias, '沒有對到')}", flush=True)

    session = _session()
    _progress("讀取頻道影片")
    videos = list_tab(session, VIDEOS_PARAMS, 12)
    _progress("讀取短影音標題")
    shorts = list_tab(session, SHORTS_PARAMS, 8)
    print(f"影片 {len(videos)} 支，短影音 {len(shorts)} 支", flush=True)

    today = taipei_now().date()
    cutoff = today - timedelta(days=LOOKBACK_DAYS)
    conn = connect()
    try:
        episodes = []
        parts: dict[date, list[str]] = defaultdict(list)
        for item in videos:
            title = item["title"]
            day = broadcast_date_from_title(title)
            if is_full_episode(title) and day and day.weekday() < 5 and cutoff <= day <= today:
                episodes.append({"video_id": item["video_id"], "title": title, "date": day})
            elif is_show_part(title) and day and cutoff <= day <= today:
                parts[day].append(headline(title))

        if not episodes:
            raise RuntimeError("沒有找到平日的電視完整版")
        episode_days = {item["date"] for item in episodes}
        _progress(f"讀取 {len(episodes)} 集完整版說明")
        descriptions: dict[str, str] = {}
        published: dict[str, str] = {}
        for index, episode in enumerate(episodes, start=1):
            _progress(f"讀取完整版說明 {index}/{len(episodes)}")
            info = _cached_player(conn, session, episode["video_id"])
            if info.get("title"):
                episode["title"] = info["title"]
            descriptions[episode["video_id"]] = info.get("description") or ""
            published[episode["video_id"]] = info.get("published_at") or ""

        extras: dict[date, list[str]] = defaultdict(list)
        matched_shorts = []
        for item in shorts:
            title = item["title"]
            if not title or "兆華艾綸說" in title:
                continue
            if not match_text(_usable(headline(title)), aliases, symbols):
                continue
            matched_shorts.append(item)
        _progress(f"對照 {len(matched_shorts)} 支點名短影音的播出日")
        for index, item in enumerate(matched_shorts, start=1):
            if index % 10 == 1:
                _progress(f"對照短影音日期 {index}/{len(matched_shorts)}")
            info = _cached_player(conn, session, item["video_id"])
            day = _clip_day(info.get("published_at") or "")
            if day in episode_days:
                extras[day].append(headline(item["title"]))

        for episode in episodes:
            chunks = [headline(episode["title"]), _usable(descriptions.get(episode["video_id"]) or "")]
            chunks.extend(parts.get(episode["date"], []))
            chunks.extend(extras.get(episode["date"], []))
            corpus = "\n".join(chunk for chunk in chunks if chunk.strip())
            conn.execute(
                "INSERT INTO episodes(video_id, title, broadcast_date, published_at, url, corpus) "
                "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(video_id) DO UPDATE SET "
                "title = excluded.title, broadcast_date = excluded.broadcast_date, "
                "published_at = excluded.published_at, url = excluded.url, corpus = excluded.corpus",
                (
                    episode["video_id"],
                    episode["title"],
                    episode["date"].isoformat(),
                    published.get(episode["video_id"]),
                    f"https://www.youtube.com/watch?v={episode['video_id']}",
                    corpus,
                ),
            )
        conn.commit()

        _progress("辨認提到的股票")
        suppressed = {
            (row["video_id"], row["symbol"])
            for row in conn.execute("SELECT video_id, symbol FROM suppressions")
        }
        manual = {
            (row["video_id"], row["symbol"])
            for row in conn.execute("SELECT video_id, symbol FROM mentions WHERE source = 'manual'")
        }
        conn.execute("DELETE FROM mentions WHERE source = 'auto'")
        found_count = 0
        for row in conn.execute("SELECT video_id, corpus FROM episodes"):
            for hit in match_text(row["corpus"] or "", aliases, symbols):
                key = (row["video_id"], hit["symbol"])
                if key in suppressed or key in manual:
                    continue
                conn.execute(
                    "INSERT INTO mentions(video_id, symbol, name, snippet, source) VALUES(?, ?, ?, ?, 'auto')",
                    (row["video_id"], hit["symbol"], hit["name"], hit["snippet"]),
                )
                found_count += 1
        conn.commit()
        print(f"自動標的 {found_count} 筆", flush=True)

        mention_rows = conn.execute(
            "SELECT DISTINCT symbol FROM mentions"
        ).fetchall()
        mentioned = [row["symbol"] for row in mention_rows]
        markets = market_map(conn, set(mentioned))
        first = conn.execute("SELECT MIN(broadcast_date) AS d FROM episodes").fetchone()["d"]
        start = date.fromisoformat(first) - timedelta(days=14) if first else today - timedelta(days=LOOKBACK_DAYS)
    finally:
        conn.close()

    _progress("下載收盤價")
    ensure_prices([(symbol, markets.get(symbol, "TWSE")) for symbol in mentioned], start, today, _progress)
    _progress("計算 10% 獲利")
    rebuild_trades()
    conn = connect()
    try:
        latest = conn.execute("SELECT MAX(broadcast_date) AS d FROM episodes").fetchone()["d"]
        if latest:
            meta_set(conn, "ready_through", latest)
        meta_set(conn, "synced_at", taipei_now().isoformat(timespec="minutes"))
        conn.commit()
    finally:
        conn.close()


def rebuild_trades() -> None:
    conn = connect()
    try:
        known = trading_days(conn)
        today = taipei_now().date()
        calendar = project_calendar(known, today + timedelta(days=21))
        quotes = price_map(conn, {row["symbol"] for row in conn.execute("SELECT DISTINCT symbol FROM mentions")})
        conn.execute("DELETE FROM trades")
        episodes = conn.execute("SELECT * FROM episodes").fetchall()
        for episode in episodes:
            broadcast = date.fromisoformat(episode["broadcast_date"])
            entry = entry_date(broadcast, calendar)
            conn.execute(
                "UPDATE episodes SET entry_date = ?, settle_date = NULL WHERE video_id = ?",
                (entry.isoformat() if entry else None, episode["video_id"]),
            )
            mentions = conn.execute(
                "SELECT symbol, name, snippet FROM mentions WHERE video_id = ?",
                (episode["video_id"],),
            ).fetchall()
            for mention in mentions:
                trade = _trade(
                    entry,
                    mention["symbol"],
                    quotes,
                    today,
                )
                conn.execute(
                    "INSERT INTO trades(video_id, symbol, name, broadcast_date, entry_date, entry_price, "
                    "shares, cost, settle_date, exit_price, mark_price, pnl, pnl_pct, status, snippet) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        episode["video_id"],
                        mention["symbol"],
                        mention["name"],
                        episode["broadcast_date"],
                        entry.isoformat() if entry else None,
                        trade["entry_price"],
                        trade["shares"],
                        trade["cost"],
                        trade["settle"].isoformat() if trade["settle"] else None,
                        trade["exit_price"],
                        trade["mark_price"],
                        trade["pnl"],
                        trade["pnl_pct"],
                        trade["status"],
                        mention["snippet"],
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _latest(quotes: dict, symbol: str, start: date, end: date) -> float | None:
    best_day = None
    best = None
    for (item_symbol, day), close in quotes.items():
        if item_symbol != symbol:
            continue
        current = date.fromisoformat(day)
        if start <= current <= end and (best_day is None or current > best_day):
            best_day = current
            best = close
    return best


def _bars(quotes: dict, symbol: str) -> list[tuple[date, float]]:
    bars = []
    for (item_symbol, day), close in quotes.items():
        if item_symbol == symbol:
            bars.append((date.fromisoformat(day), close))
    return bars


def _trade(entry: date | None, symbol: str, quotes: dict, today: date) -> dict:
    blank = {
        "entry_price": None,
        "shares": None,
        "cost": None,
        "exit_price": None,
        "mark_price": None,
        "pnl": None,
        "pnl_pct": None,
        "status": "pending",
        "settle": None,
    }
    if entry is None or entry > today:
        return blank
    entry_price = quotes.get((symbol, entry.isoformat()))
    if entry_price is None:
        if entry < today:
            blank["status"] = "no_price"
        return blank
    shares = shares_for(entry_price)
    cost = round(shares * entry_price, 2)
    if shares == 0:
        return {
            **blank,
            "entry_price": entry_price,
            "shares": 0,
            "cost": 0,
            "status": "unfilled",
        }
    hit = take_profit_day(entry, entry_price, _bars(quotes, symbol), today)
    if hit:
        settle, exit_price = hit
        pnl = round(shares * (exit_price - entry_price), 2)
        return {
            "entry_price": entry_price,
            "shares": shares,
            "cost": cost,
            "exit_price": exit_price,
            "mark_price": exit_price,
            "pnl": pnl,
            "pnl_pct": (exit_price - entry_price) / entry_price,
            "status": "settled",
            "settle": settle,
        }
    mark = _latest(quotes, symbol, entry, today)
    pnl = round(shares * (mark - entry_price), 2) if mark is not None else None
    return {
        "entry_price": entry_price,
        "shares": shares,
        "cost": cost,
        "exit_price": None,
        "mark_price": mark,
        "pnl": pnl,
        "pnl_pct": ((mark - entry_price) / entry_price) if mark is not None else None,
        "status": "open",
        "settle": None,
    }


def run_sync(force: bool = False) -> bool:
    if not RUN_LOCK.acquire(blocking=False):
        return False
    try:
        with STATE_LOCK:
            STATE["running"] = True
            STATE["error"] = None
        if not force and not should_sync():
            _progress("已經是最新")
            with STATE_LOCK:
                STATE["finished_at"] = taipei_now().isoformat(timespec="minutes")
            return True
        _sync()
        with STATE_LOCK:
            STATE["finished_at"] = taipei_now().isoformat(timespec="minutes")
        _progress("同步完成")
        return True
    except Exception as exc:
        traceback.print_exc()
        with STATE_LOCK:
            STATE["error"] = str(exc)
        _progress("同步失敗")
        return False
    finally:
        with STATE_LOCK:
            STATE["running"] = False
        RUN_LOCK.release()


def refresh_book() -> None:
    """Download the latest closes and rebuild the ledger, even when no new episode is due."""
    conn = connect()
    try:
        mentioned = [row["symbol"] for row in conn.execute("SELECT DISTINCT symbol FROM mentions")]
        markets = market_map(conn, set(mentioned))
        first = conn.execute("SELECT MIN(broadcast_date) AS d FROM episodes").fetchone()["d"]
        today = taipei_now().date()
        start = date.fromisoformat(first) - timedelta(days=14) if first else today - timedelta(days=LOOKBACK_DAYS)
    finally:
        conn.close()
    _progress("下載收盤價")
    ensure_prices([(symbol, markets.get(symbol, "TWSE")) for symbol in mentioned], start, today, _progress)
    _progress("計算 10% 獲利")
    rebuild_trades()
    conn = connect()
    try:
        meta_set(conn, "synced_at", taipei_now().isoformat(timespec="minutes"))
        conn.commit()
    finally:
        conn.close()


def watch() -> None:
    while True:
        try:
            run_sync(False)
        except Exception:
            traceback.print_exc()
        time.sleep(1200)


if __name__ == "__main__":
    ok = run_sync(True)
    raise SystemExit(0 if ok else 1)
