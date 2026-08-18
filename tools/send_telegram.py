#!/usr/bin/env python3
"""Send the Bet of the Day message via the Telegram Bot API.

Unlike WhatsApp, Telegram needs no pre-approved template — this just posts a
single formatted Markdown message to each configured chat. Sends best-effort
to every chat id (one recipient's failure doesn't block the others) and
reports a per-recipient summary.

Usage:
    python tools/send_telegram.py --params-file .tmp/bet_params_2026-08-17.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


class SendError(Exception):
    pass


def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    attempt = 0
    while True:
        try:
            response = requests.post(url, json=payload, timeout=30)
        except requests.exceptions.Timeout:
            raise SendError("request timed out")
        except requests.exceptions.ConnectionError as exc:
            raise SendError(f"connection error: {exc}")

        if response.status_code == 200:
            return response.json()

        if response.status_code == 429 or response.status_code >= 500:
            attempt += 1
            if attempt > MAX_RETRIES:
                raise SendError(f"still failing after {MAX_RETRIES} retries: {response.text[:300]}")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        try:
            detail = response.json().get("description", response.text[:300])
        except ValueError:
            detail = response.text[:300]
        raise SendError(f"rejected ({response.status_code}): {detail}")


def main():
    parser = argparse.ArgumentParser(description="Send the Bet of the Day message via Telegram.")
    parser.add_argument("--params-file", required=True, help="Path to prepare_bet_slip.py's output JSON.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()

    if not token:
        fail("TELEGRAM_BOT_TOKEN is not set in .env.")
    if not chat_ids_raw:
        fail("TELEGRAM_CHAT_IDS is not set in .env (comma-separated chat ids).")
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]

    params_path = Path(args.params_file)
    if not params_path.is_absolute():
        params_path = REPO_ROOT / params_path
    if not params_path.exists():
        fail(f"Params file not found: {params_path}")

    try:
        data = json.loads(params_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Params file is not valid JSON: {exc}")

    message = data.get("telegram_message")
    if not message:
        fail("Params file missing 'telegram_message'.")

    succeeded, failed = [], []
    for chat_id in chat_ids:
        try:
            result = send_message(token, chat_id, message)
            message_id = result.get("result", {}).get("message_id")
            print(f"Sent to {chat_id}: message_id={message_id}")
            succeeded.append(chat_id)
        except SendError as exc:
            print(f"FAILED to send to {chat_id}: {exc}", file=sys.stderr)
            failed.append(chat_id)

    print(f"Delivered to {len(succeeded)}/{len(chat_ids)} recipient(s).")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
