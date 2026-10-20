from __future__ import annotations

import re
import time

import requests

from app.db import connect, meta_get, meta_set

LISTED_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
OTC_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
ETF_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"

# Exact short names that are also everyday words on this show.
NAME_BLOCK = {
    "開發", "中華", "台灣", "第一", "大成", "電子", "科技", "光電",
    "材料", "國際", "企業", "工業", "建設", "投資", "金融", "控股",
    "食品", "水泥", "鋼鐵", "化學", "塑膠", "汽車", "電機", "醫療",
    "觀光", "紡織", "造紙", "橡膠", "玻璃", "航運", "其他", "證券",
    "銀行", "人壽", "金控", "通訊", "資訊", "生技", "半導體", "實業",
    "股份", "有限", "公司", "集團", "概念", "龍頭", "黑馬", "低檔",
    "被動", "主動", "記憶體", "設備", "封測", "面板", "重電", "機器人",
    "衛星", "載板", "銅箔", "塑化", "航太", "軍工", "綠能", "油電",
    "傳產", "權證", "投信", "外資", "主力", "作帳", "法說", "營收",
    "高股息", "指數", "期貨", "選擇", "個股", "股票", "台股", "美股",
    "升息", "降息", "關稅", "晶圓", "封裝", "測試", "基板", "銅板",
    "冠軍", "數字", "安心", "再生", "動力",
}

TRAILING_REJECT = ("投顧", "投信", "證券", "期貨", "銀證", "操盤")
LEADING_REJECT = {
    "力士": "海",
    "光明": "曝",
}
TRAILING_CHAR = {
    "新晶": "片",
}
CODE_RE = re.compile(r"(?<![A-Za-z0-9])(00\d{2,4}[A-Za-z]?)(?![A-Za-z0-9])")
DATE_RE = re.compile(r"20\d{2}\.\d{2}\.\d{2}")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; EBCMoneyShowLedger/1.0)"
    return session


def _clean_name(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("\u3000", "").replace("－", "-").strip()


def load_universe(force: bool = False) -> int:
    conn = connect()
    try:
        refreshed = meta_get(conn, "universe_at")
        count = conn.execute("SELECT COUNT(*) AS n FROM securities").fetchone()["n"]
        if count and refreshed and not force:
            age = time.time() - float(refreshed)
            if age < 7 * 86400:
                return count
        session = _session()
        listed = session.get(LISTED_URL, timeout=40).json()
        otc = session.get(OTC_URL, timeout=40).json()
        etf = session.get(ETF_URL, timeout=40).json()
        rows: dict[str, tuple[str, str]] = {}
        for item in listed:
            symbol = _clean_name(item.get("公司代號"))
            name = _clean_name(item.get("公司簡稱"))
            if symbol and name:
                rows[symbol] = (name, "TWSE")
        for item in otc:
            symbol = _clean_name(item.get("SecuritiesCompanyCode"))
            name = _clean_name(item.get("CompanyAbbreviation"))
            if symbol and name and symbol not in rows:
                rows[symbol] = (name, "TPEx")
        for item in etf:
            symbol = _clean_name(item.get("基金代號"))
            name = _clean_name(item.get("基金簡稱"))
            if symbol and name and symbol not in rows:
                rows[symbol] = (name, "ETF")
        conn.execute("DELETE FROM securities")
        conn.executemany(
            "INSERT INTO securities(symbol, name, market) VALUES(?, ?, ?)",
            [(symbol, name, market) for symbol, (name, market) in rows.items()],
        )
        meta_set(conn, "universe_at", str(time.time()))
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _aliases(conn) -> list[tuple[str, str, str]]:
    """Return (alias, symbol, display_name) longest alias first."""
    securities = conn.execute("SELECT symbol, name, market FROM securities").fetchall()
    by_symbol = {row["symbol"]: row["name"] for row in securities}
    chosen: dict[str, str] = {}

    def add(alias: str, symbol: str) -> None:
        alias = _clean_name(alias)
        if len(alias) < 2 or alias.isdigit() or alias in NAME_BLOCK:
            return
        if symbol not in by_symbol:
            return
        chosen.setdefault(alias, symbol)

    for row in securities:
        name = row["name"]
        if row["market"] == "ETF" and len(name) < 3:
            continue
        add(name, row["symbol"])
        shortened = name
        for suffix in ("-KY", "KY", "投控", "金控"):
            if shortened.endswith(suffix) and len(shortened) - len(suffix) >= 2:
                shortened = shortened[: -len(suffix)]
                add(shortened, row["symbol"])
        if shortened.endswith("電子") and len(shortened) - 2 >= 2:
            add(shortened[:-2], row["symbol"])
        if shortened.endswith("半導體") and len(shortened) - 3 >= 2:
            add(shortened[:-3] + "半", row["symbol"])

    for alias, symbol in (("台積", "2330"), ("發哥", "2454"), ("台灣50", "0050"), ("元大50", "0050")):
        add(alias, symbol)

    ranked = sorted(chosen.items(), key=lambda item: len(item[0]), reverse=True)
    return [(alias, symbol, by_symbol[symbol]) for alias, symbol in ranked]


def match_text(text: str, aliases: list[tuple[str, str, str]], symbols: set[str]) -> list[dict]:
    if not text:
        return []
    occupied = [False] * len(text)
    found: dict[str, dict] = {}

    def take(symbol: str, name: str, start: int, end: int) -> None:
        if symbol in found:
            return
        snippet_start = max(0, start - 16)
        snippet_end = min(len(text), end + 16)
        snippet = text[snippet_start:snippet_end].replace("\n", " ")
        if snippet_start > 0:
            snippet = "…" + snippet
        if snippet_end < len(text):
            snippet = snippet + "…"
        found[symbol] = {"symbol": symbol, "name": name, "snippet": snippet}

    for alias, symbol, display in aliases:
        start = 0
        while True:
            index = text.find(alias, start)
            if index < 0:
                break
            end = index + len(alias)
            if any(occupied[index:end]):
                start = index + 1
                continue
            tail = text[end : end + 2]
            if any(tail.startswith(suffix) for suffix in TRAILING_REJECT):
                start = index + 1
                continue
            if index and text[index - 1] == LEADING_REJECT.get(alias):
                start = index + 1
                continue
            if text[end : end + 1] == TRAILING_CHAR.get(alias):
                start = index + 1
                continue
            for cursor in range(index, end):
                occupied[cursor] = True
            take(symbol, display, index, end)
            start = end

    code_text = DATE_RE.sub(" ", text)
    for match in CODE_RE.finditer(code_text):
        symbol = match.group(1).upper()
        if symbol not in symbols:
            continue
        start, end = match.span(1)
        if any(occupied[start:end]):
            continue
        name = next((item[2] for item in aliases if item[1] == symbol), symbol)
        take(symbol, name, start, end)
    return list(found.values())


def load_matcher():
    conn = connect()
    try:
        aliases = _aliases(conn)
        symbols = {row["symbol"] for row in conn.execute("SELECT symbol FROM securities")}
        names = {row["symbol"]: row["name"] for row in conn.execute("SELECT symbol, name FROM securities")}
        return aliases, symbols, names
    finally:
        conn.close()
