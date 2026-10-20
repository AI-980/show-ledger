"""Entry point for the cloud schedule. New episodes and daily closes."""

from app.sync import refresh_book, run_sync


def main() -> None:
    run_sync(False)
    refresh_book()


if __name__ == "__main__":
    main()
