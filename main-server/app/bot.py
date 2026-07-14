"""Backward-compatible entry point for the Telegram bot process."""

from app.telegram_ui.app import main


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
