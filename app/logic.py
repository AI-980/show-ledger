"""Paper-trade rules for the weekday 21:00 show.

Buy 5,000 TWD of each mentioned stock at the next session's close.
Sell at the first later close that is at least 10% above the entry. No deadline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
BUDGET = 5000
TARGET = 0.10
START_DATE = date(2026, 10, 1)
WEEKDAY = "一二三四五六日"


def taipei_now() -> datetime:
    return datetime.now(TPE)


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def project_calendar(known: list[date], until: date) -> list[date]:
    """Keep real sessions, then assume future weekdays trade until the calendar prints."""
    days = set(known)
    if not days:
        cursor = until - timedelta(days=10)
    else:
        cursor = max(days) + timedelta(days=1)
    while cursor <= until:
        if cursor.weekday() < 5:
            days.add(cursor)
        cursor += timedelta(days=1)
    return sorted(days)


def next_trading_day(after: date, trading_days: list[date]) -> date | None:
    for day in trading_days:
        if day > after:
            return day
    return None


def next_friday_session(after: date, trading_days: list[date]) -> date | None:
    """Next open Friday after a date. A closed Friday is skipped, not replaced by Thursday."""
    for day in trading_days:
        if day > after and day.weekday() == 4:
            return day
    return None


def entry_date(broadcast: date, trading_days: list[date]) -> date | None:
    """Thursday episodes buy the next open Friday. Every other weekday buys the next session."""
    if broadcast.weekday() == 3:
        return next_friday_session(broadcast, trading_days)
    return next_trading_day(broadcast, trading_days)


def take_profit_day(
    entry: date,
    entry_price: float,
    bars: list[tuple[date, float]],
    today: date,
) -> tuple[date, float] | None:
    """First close after entry that is at least 10% higher. Later bars are ignored."""
    if entry_price <= 0:
        return None
    found: tuple[date, float] | None = None
    for day, close in bars:
        if day <= entry or day > today:
            continue
        if (close - entry_price) / entry_price + 1e-9 < TARGET:
            continue
        if found is None or day < found[0]:
            found = (day, close)
    return found


def expected_latest_broadcast(now: datetime | None = None) -> date:
    """Most recent Monday–Friday episode that should already be on YouTube."""
    now = now or taipei_now()
    day = now.date()
    if day.weekday() >= 5:
        return day - timedelta(days=day.weekday() - 4)
    if now.hour < 22:
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    return day


def previous_broadcast(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def format_day(day: date | None) -> str:
    if day is None:
        return "—"
    return f"{day.month}/{day.day}（{WEEKDAY[day.weekday()]}）"


def shares_for(price: float, budget: int = BUDGET) -> int:
    if price <= 0:
        return 0
    return int(budget // price)


def classify(pnl: float | None) -> str:
    if pnl is None:
        return "unknown"
    rounded = round(pnl, 2)
    if rounded > 0:
        return "win"
    if rounded < 0:
        return "loss"
    return "push"


if __name__ == "__main__":
    days = [date(2026, 10, d) for d in (1, 2, *range(5, 9), *range(12, 17), *range(19, 24))]
    assert next_trading_day(date(2026, 10, 12), days) == date(2026, 10, 13)
    assert entry_date(date(2026, 10, 1), days) == date(2026, 10, 2)
    assert entry_date(date(2026, 10, 8), days) == date(2026, 10, 16)
    assert entry_date(date(2026, 10, 14), days) == date(2026, 10, 15)
    assert entry_date(date(2026, 10, 16), days) == date(2026, 10, 19)
    entry = date(2026, 10, 2)
    bars = [
        (date(2026, 10, 2), 100.0),
        (date(2026, 10, 5), 109.0),
        (date(2026, 10, 6), 112.0),
        (date(2026, 10, 7), 130.0),
    ]
    assert take_profit_day(entry, 100.0, bars, date(2026, 10, 19)) == (date(2026, 10, 6), 112.0)
    assert take_profit_day(entry, 100.0, bars[:2], date(2026, 10, 19)) is None
    assert shares_for(2420) == 2
    assert shares_for(6000) == 0
    assert classify(12) == "win"
    assert classify(-1) == "loss"
    assert classify(0) == "push"
    monday_night = datetime(2026, 10, 19, 23, 11, tzinfo=TPE)
    assert expected_latest_broadcast(monday_night) == date(2026, 10, 19)
    monday_noon = datetime(2026, 10, 19, 12, 0, tzinfo=TPE)
    assert expected_latest_broadcast(monday_noon) == date(2026, 10, 16)
    projected = project_calendar(days, date(2026, 10, 30))
    assert date(2026, 10, 20) in projected
    assert date(2026, 10, 9) not in projected
    print("logic ok")
