"""Write the phone site: the same pages, with the ledger baked in."""

import json
import shutil
from pathlib import Path

from app.report import snapshot

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"


def main() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "static", SITE / "static")
    shutil.copy(ROOT / "static" / "index.html", SITE / "index.html")
    (SITE / "dashboard.json").write_text(
        json.dumps(snapshot(), ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
