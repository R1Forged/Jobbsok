from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Telegram bot token and find TELEGRAM_CHAT_ID.")
    parser.add_argument("--write-env", action="store_true", help="Write TELEGRAM_CHAT_ID to .env when exactly one chat is found.")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing. Put your new BotFather token in .env first.")
        return 1

    bot = request_json(token, "getMe")
    if not bot.get("ok"):
        explain_telegram_error("getMe", bot)
        return 1

    result = bot.get("result", {})
    print(f"Bot OK: @{result.get('username', '<unknown>')} ({result.get('first_name', 'unnamed')})")
    print("Send a message to the bot in Telegram now if you have not already done that.")

    updates = request_json(token, "getUpdates")
    if not updates.get("ok"):
        explain_telegram_error("getUpdates", updates)
        return 1

    chats = collect_chats(updates.get("result", []))
    if not chats:
        print("No chats found yet. Send /start to the bot, wait a few seconds, then run this again.")
        return 2

    print("\nFound chat ids:")
    for chat_id, label in chats.items():
        print(f"  TELEGRAM_CHAT_ID={chat_id}  # {label}")

    if args.write_env:
        if len(chats) != 1:
            print("\nNot writing .env because multiple chats were found. Copy the correct numeric id manually.")
            return 3
        chat_id = next(iter(chats))
        write_env_value(ENV_PATH, "TELEGRAM_CHAT_ID", chat_id)
        print(f"\nWrote TELEGRAM_CHAT_ID={chat_id} to {ENV_PATH}")

    return 0


def request_json(token: str, method: str) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.get(url, timeout=20)
    except requests.RequestException as exc:
        return {"ok": False, "description": f"Request failed: {exc}"}
    try:
        return response.json()
    except ValueError:
        return {"ok": False, "error_code": response.status_code, "description": response.text[:300]}


def explain_telegram_error(method: str, payload: dict[str, Any]) -> None:
    code = payload.get("error_code", "unknown")
    description = payload.get("description", "unknown error")
    print(f"Telegram {method} failed: {code} {description}")
    if code in {401, 404}:
        print("Most likely cause: the bot token is invalid, mistyped, or revoked. Create/revoke a token in @BotFather and update .env.")


def collect_chats(updates: list[dict[str, Any]]) -> dict[str, str]:
    chats: dict[str, str] = {}
    for update in updates:
        message = update.get("message") or update.get("edited_message") or update.get("channel_post")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            continue
        chat_id = str(chat["id"])
        label_parts = [
            chat.get("type"),
            chat.get("title"),
            chat.get("username") and f"@{chat.get('username')}",
            " ".join(part for part in [chat.get("first_name"), chat.get("last_name")] if part),
        ]
        chats[chat_id] = ", ".join(str(part) for part in label_parts if part)
    return chats


def write_env_value(path: Path, key: str, value: str) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""

    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", flags=re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
