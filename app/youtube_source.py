from __future__ import annotations

import json
import re
import time
from datetime import datetime

import requests

CHANNEL_ID = "UCQvsuaih5lE0n_Ne54nNezg"
BROWSE = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
PLAYER = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
VIDEOS_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"
SHORTS_PARAMS = "EgZzaG9ydHPyBgUKA5oBAA=="
CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20241016.01.00",
        "hl": "zh-TW",
        "gl": "TW",
    }
}
DATE_IN_TITLE = re.compile(r"(20\d{2})\.(\d{2})\.(\d{2})")
HEADLINE_SPLIT = re.compile(r"｜|feat\.|Feat\.")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }
    )
    return session


def _post(session: requests.Session, url: str, payload: dict) -> dict:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                return response.json()
            last_error = RuntimeError(f"HTTP {response.status_code}")
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(0.8 + attempt)
    raise RuntimeError(f"YouTube request failed: {last_error}")


def _video_id_from(node: dict) -> str:
    blob = json.dumps(node, ensure_ascii=False)
    match = re.search(r"/vi/([\w-]{11})/", blob) or re.search(r'"videoId":"([\w-]{11})"', blob)
    return match.group(1) if match else ""


def _title_from_lockup(view_model: dict) -> str:
    meta = view_model.get("metadata", {}).get("lockupMetadataViewModel", {})
    title = meta.get("title") or {}
    if isinstance(title, dict) and title.get("content"):
        return title["content"]
    return ""


def _short_title(view_model: dict) -> str:
    overlay = view_model.get("overlayMetadata") or {}
    primary = overlay.get("primaryText") or {}
    if isinstance(primary, dict) and primary.get("content"):
        return primary["content"]
    text = view_model.get("accessibilityText") or ""
    return text.split(", 觀看次數")[0].strip()


def _items_from_browse(data: dict) -> tuple[list[dict], str | None]:
    items: list[dict] = []
    seen: set[str] = set()

    def add(video_id: str, title: str, kind: str) -> None:
        if not re.fullmatch(r"[\w-]{11}", video_id or ""):
            return
        title = title or ""
        if video_id in seen:
            for item in items:
                if item["video_id"] == video_id and len(title) > len(item["title"]):
                    item["title"] = title
                    item["kind"] = kind
            return
        seen.add(video_id)
        items.append({"video_id": video_id, "title": title, "kind": kind})

    def walk(node) -> None:
        if isinstance(node, dict):
            lockup = node.get("lockupViewModel")
            if isinstance(lockup, dict):
                add(_video_id_from(lockup), _title_from_lockup(lockup), "video")
            short = node.get("shortsLockupViewModel")
            if isinstance(short, dict):
                entity = short.get("entityId") or ""
                found = re.search(r"shorts-shelf-item-(.+)$", entity)
                video_id = found.group(1) if found else ""
                if not re.fullmatch(r"[\w-]{11}", video_id):
                    video_id = _video_id_from(short)
                add(video_id, _short_title(short), "short")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    token = None

    def walk_token(node) -> None:
        nonlocal token
        if token is not None:
            return
        if isinstance(node, dict):
            endpoint = node.get("continuationEndpoint")
            if isinstance(endpoint, dict):
                command = endpoint.get("continuationCommand") or {}
                if command.get("token"):
                    token = command["token"]
                    return
            for value in node.values():
                walk_token(value)
        elif isinstance(node, list):
            for value in node:
                walk_token(value)

    walk_token(data)
    return items, token


def list_tab(session: requests.Session, params: str, pages: int) -> list[dict]:
    payload = {"context": CONTEXT, "browseId": CHANNEL_ID, "params": params}
    collected: list[dict] = []
    seen: set[str] = set()
    token = None
    for _ in range(pages):
        data = _post(session, BROWSE, payload if token is None else {"context": CONTEXT, "continuation": token})
        items, token = _items_from_browse(data)
        fresh = [item for item in items if item["video_id"] not in seen]
        for item in fresh:
            seen.add(item["video_id"])
        collected.extend(fresh)
        if not fresh or not token:
            break
        time.sleep(0.15)
    return collected


def fetch_player(session: requests.Session, video_id: str) -> dict:
    data = _post(session, PLAYER, {"context": CONTEXT, "videoId": video_id})
    details = data.get("videoDetails") or {}
    micro = (data.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    published = micro.get("publishDate") or micro.get("uploadDate") or ""
    return {
        "title": details.get("title") or "",
        "description": details.get("shortDescription") or "",
        "published_at": published,
    }


def broadcast_date_from_title(title: str):
    match = DATE_IN_TITLE.search(title)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def headline(title: str) -> str:
    return HEADLINE_SPLIT.split(title, maxsplit=1)[0]


def is_full_episode(title: str) -> bool:
    return "電視完整版" in title and "理財達人秀" in title and "兆華艾綸說" not in title


def is_show_part(title: str) -> bool:
    return (
        "理財達人秀" in title
        and "兆華艾綸說" not in title
        and "電視完整版" not in title
        and "會員專屬" not in title
        and "part" in title.lower()
    )
