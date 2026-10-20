from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from datetime import timedelta

from app.db import connect
from app.logic import parse_iso_date, taipei_now
from app.prices import ensure_prices
from app.report import snapshot
from app.sync import rebuild_trades, run_sync, status, watch

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class MentionIn(BaseModel):
    videoId: str
    query: str = ""
    symbol: str = ""


class MentionOut(BaseModel):
    videoId: str
    symbol: str


def create_app() -> FastAPI:
    app = FastAPI(title="達人秀結算簿")

    @app.on_event("startup")
    def startup() -> None:
        threading.Thread(target=watch, daemon=True).start()

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/dashboard")
    def dashboard():
        return snapshot()

    @app.get("/api/sync")
    def sync_status():
        return status()

    @app.post("/api/sync")
    def sync_now():
        started = threading.Thread(target=lambda: run_sync(True), daemon=True).start()
        return {"started": started is None or True, "sync": status()}

    @app.get("/api/search")
    def search(q: str = ""):
        query = q.strip()
        if not query:
            return []
        conn = connect()
        try:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT symbol, name, market FROM securities "
                    "WHERE symbol = ? OR name LIKE ? "
                    "ORDER BY CASE WHEN symbol = ? THEN 0 WHEN name = ? THEN 1 ELSE 2 END, name "
                    "LIMIT 8",
                    (query.upper(), f"%{query}%", query.upper(), query),
                )
            ]
        finally:
            conn.close()

    @app.post("/api/mentions")
    def add_mention(body: MentionIn):
        conn = connect()
        try:
            episode = conn.execute(
                "SELECT video_id, broadcast_date FROM episodes WHERE video_id = ?",
                (body.videoId,),
            ).fetchone()
            if episode is None:
                return {"ok": False, "message": "找不到這一集"}
            row, message = _resolve(conn, body.symbol or body.query)
            if row is None:
                return {"ok": False, "message": message}
            conn.execute(
                "DELETE FROM suppressions WHERE video_id = ? AND symbol = ?",
                (body.videoId, row["symbol"]),
            )
            conn.execute(
                "INSERT INTO mentions(video_id, symbol, name, snippet, source) VALUES(?, ?, ?, ?, 'manual') "
                "ON CONFLICT(video_id, symbol) DO UPDATE SET source = 'manual', name = excluded.name",
                (body.videoId, row["symbol"], row["name"], "手動加入"),
            )
            broadcast = episode["broadcast_date"]
            conn.commit()
        finally:
            conn.close()
        start = parse_iso_date(broadcast) or taipei_now().date()
        ensure_prices(
            [(row["symbol"], row["market"])],
            start - timedelta(days=14),
            taipei_now().date(),
            lambda _message: None,
        )
        rebuild_trades()
        return {"ok": True, "symbol": row["symbol"], "name": row["name"]}

    @app.post("/api/mentions/remove")
    def remove_mention(body: MentionOut):
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO suppressions(video_id, symbol) VALUES(?, ?) ON CONFLICT DO NOTHING",
                (body.videoId, body.symbol),
            )
            conn.execute(
                "DELETE FROM mentions WHERE video_id = ? AND symbol = ?",
                (body.videoId, body.symbol),
            )
            conn.commit()
        finally:
            conn.close()
        rebuild_trades()
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _resolve(conn, query: str):
    query = query.strip().lstrip("#＃")
    if not query:
        return None, "請輸入股票代號或名稱"
    exact = conn.execute(
        "SELECT symbol, name, market FROM securities WHERE symbol = ?",
        (query.upper(),),
    ).fetchone()
    if exact:
        return exact, None
    named = conn.execute(
        "SELECT symbol, name, market FROM securities WHERE name = ?",
        (query,),
    ).fetchall()
    if len(named) == 1:
        return named[0], None
    if len(named) > 1:
        return None, "有多檔同名，請改用代號"
    fuzzy = conn.execute(
        "SELECT symbol, name, market FROM securities WHERE name LIKE ? LIMIT 5",
        (f"%{query}%",),
    ).fetchall()
    if len(fuzzy) == 1:
        return fuzzy[0], None
    if len(fuzzy) > 1:
        return None, "對到多檔，請改選完整名稱或代號"
    return None, "找不到這檔股票"


app = create_app()
