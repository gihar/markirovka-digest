"""Interactive script to generate a Telethon StringSession.

Usage:
    uv run python scripts/generate_session.py

Prompts for API ID, API hash, and phone number. After successful
authentication (including 2FA if enabled), prints the StringSession
string to be stored as the TELEGRAM_SESSION environment variable.
"""

import asyncio
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession


async def _generate_session() -> str:
    """Run the interactive authentication flow and return a StringSession string."""
    print("=== Telethon StringSession Generator ===\n")

    api_id_raw = input("Enter your Telegram API ID: ").strip()
    if not api_id_raw.isdigit():
        print("Error: API ID must be a number.", file=sys.stderr)
        sys.exit(1)
    api_id = int(api_id_raw)

    api_hash = input("Enter your Telegram API Hash: ").strip()
    if not api_hash:
        print("Error: API Hash cannot be empty.", file=sys.stderr)
        sys.exit(1)

    phone = input("Enter your phone number (with country code, e.g. +79991234567): ").strip()
    if not phone:
        print("Error: Phone number cannot be empty.", file=sys.stderr)
        sys.exit(1)

    client = TelegramClient(StringSession(), api_id, api_hash)

    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(phone)
        code = input("Enter the code you received: ").strip()
        try:
            await client.sign_in(phone, code)
        except Exception:
            # Might need 2FA password
            password = input("Enter your 2FA password: ").strip()
            await client.sign_in(password=password)

    session_string: str = client.session.save()  # type: ignore[union-attr]
    await client.disconnect()
    return session_string


def main() -> None:
    """Entry point for the session generator script."""
    session_string = asyncio.run(_generate_session())
    print("\n=== Your StringSession (store as TELEGRAM_SESSION env var) ===")
    print(session_string)
    print("\nDone. Keep this value secret — it grants access to your account.")


if __name__ == "__main__":
    main()
