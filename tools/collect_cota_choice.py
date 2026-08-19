#!/usr/bin/env python3
"""Ask the user what combined odds ("cotă") they want for today's bet, via an
inline-keyboard Telegram message, and collect their real response.

Default mode blocks (using Telegram's own long-polling, not a tight loop)
until the user taps a preset button or types a manual value, or until
--max-wait-seconds elapses. Presets are 5x/10x/15x/25x; manual entry accepts
anything in (1, 100] — a manual value over 100 is rejected with an
explanation, and the user is re-prompted.

Two non-blocking modes exist for a split-fire cloud routine design, where one
routine asks in the morning and a separate routine (running hours later)
checks the answer without holding a session open in between:

  --send-only   Clears the Telegram update backlog (so nothing stale gets
                picked up later), sends the question, and exits immediately.
                Telegram's getUpdates offset is consumed server-side at that
                point — a later, unrelated process calling getUpdates will
                only ever see what arrived after this call, with no need to
                pass any state between the two routines.

  --check-only  Sends nothing. Fetches whatever arrived since the last
                --send-only (or previous --check-only) call and reconstructs
                the answer: a preset button tap is immediately valid; a
                "manual entry" tap followed by a later text message in the
                same batch is reconstructed as a manual answer (since nobody
                was listening live to prompt the user in between — this
                mode looks at the whole batch at once instead). Writes
                "chosen_cota": null and "source": "none" if nothing valid
                is found, so the caller can fall back to prepare_bet_slip.py's
                own 10x default rather than guessing here.

Usage:
    python tools/collect_cota_choice.py --chat-id 880474256 --date 2026-08-17 \
        [--max-wait-seconds 600] [--output .tmp/cota_choice_2026-08-17.json]
    python tools/collect_cota_choice.py --chat-id 880474256 --date 2026-08-17 --send-only
    python tools/collect_cota_choice.py --chat-id 880474256 --date 2026-08-17 --check-only
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_ALLOWED_COTA = 100.0
LONG_POLL_TIMEOUT = 25  # seconds, Telegram holds the connection open this long

ROMANIAN_MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]

PRESET_COTAS = [5, 10, 15, 25]


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def api(token, method, critical=True, **params):
    """Call the Telegram Bot API. Non-critical calls (e.g. answerCallbackQuery,
    which is just UI feedback to clear a button's loading spinner) warn and
    return None on failure instead of aborting the whole wait loop — a stale
    callback query id is common and shouldn't kill an otherwise-successful
    flow."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.post(url, json=params, timeout=LONG_POLL_TIMEOUT + 10)
    except requests.exceptions.Timeout:
        message = f"Request to Telegram API ({method}) timed out."
        fail(message) if critical else warn(message)
        return None
    except requests.exceptions.ConnectionError as exc:
        message = f"Could not connect to Telegram API: {exc}"
        fail(message) if critical else warn(message)
        return None
    if response.status_code != 200:
        message = f"Telegram API {method} failed ({response.status_code}): {response.text[:300]}"
        if critical:
            fail(message)
        warn(message)
        return None
    return response.json().get("result")


def date_display(date_str):
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{parsed.day} {ROMANIAN_MONTHS[parsed.month - 1]} {parsed.year}"
    except ValueError:
        return date_str


def send_question(token, chat_id, date_str):
    keyboard = [[{"text": f"Cota {n}x", "callback_data": f"cota:{n}"}] for n in PRESET_COTAS]
    keyboard.append([{"text": "Adaugă cotă manual", "callback_data": "cota:manual"}])
    result = api(
        token, "sendMessage",
        chat_id=chat_id,
        text=f"\U0001F3B2 Ce cotă vrei pentru biletul zilei {date_display(date_str)}?",
        reply_markup={"inline_keyboard": keyboard},
    )
    return result["message_id"]


def clear_backlog_offset(token):
    """Return the offset that skips every update already sitting in the queue,
    so we only react to what happens after this tool starts."""
    updates = api(token, "getUpdates", timeout=0)
    if not updates:
        return 0
    return max(u["update_id"] for u in updates) + 1


def parse_manual_value(text):
    """Validate a manual cotă string. Returns (value, error_message)."""
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None, "not a number"
    if value <= 1.0:
        return None, "<= 1.0"
    if value > MAX_ALLOWED_COTA:
        return None, f"> {MAX_ALLOWED_COTA:g}"
    return value, None


def run_send_only(token, args):
    chat_id = str(args.chat_id)
    clear_backlog_offset(token)  # consumed server-side; nothing stale leaks into the later check
    question_message_id = send_question(token, chat_id, args.date)
    result = {
        "date": args.date, "chat_id": chat_id,
        "question_message_id": question_message_id,
        "sent_at": datetime.utcnow().isoformat() + "Z",
    }
    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / f"cota_question_{args.date}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Sent cotă question to {chat_id} (message_id={question_message_id}). Not waiting for a reply.")
    print(f"Saved to: {output_path}")


def run_check_only(token, args):
    chat_id = str(args.chat_id)
    updates = api(token, "getUpdates", timeout=0, allowed_updates=["message", "callback_query"]) or []

    chosen = None
    source = None
    awaiting_manual = False

    for update in updates:
        cq = update.get("callback_query")
        if cq and str(cq.get("message", {}).get("chat", {}).get("id")) == chat_id:
            data = cq.get("data", "")
            if data == "cota:manual":
                awaiting_manual = True
                continue
            if data.startswith("cota:"):
                chosen = float(data.split(":", 1)[1])
                source = "button"
                awaiting_manual = False
                continue

        msg = update.get("message")
        if awaiting_manual and msg and str(msg.get("chat", {}).get("id")) == chat_id and "text" in msg:
            value, err = parse_manual_value(msg["text"])
            if value is not None:
                chosen = value
                source = "manual"
            awaiting_manual = False  # only the first reply after the tap counts

    if updates:
        clear_backlog_offset(token)  # mark this batch consumed so tomorrow starts clean

    if chosen is None:
        result = {"date": args.date, "chat_id": chat_id, "chosen_cota": None, "source": "none"}
        print("No valid cotă response found in this window — caller should use the default target.")
    else:
        result = {"date": args.date, "chat_id": chat_id, "chosen_cota": chosen, "source": source}
        print(f"Chosen cotă: {chosen}x (source: {source})")

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / f"cota_choice_{args.date}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Ask for and collect the user's cotă choice via Telegram.")
    parser.add_argument("--chat-id", required=True, help="Telegram chat id to ask.")
    parser.add_argument("--date", required=True, help="Date (YYYY-MM-DD) this cotă is for.")
    parser.add_argument("--max-wait-seconds", type=int, default=600, help="Give up after this long. Default: 600.")
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/cota_choice_<date>.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--send-only", action="store_true", help="Send the question and exit immediately (no waiting).")
    mode.add_argument("--check-only", action="store_true", help="Check for a response since the last send; send nothing.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        fail("TELEGRAM_BOT_TOKEN is not set in .env.")

    if args.send_only:
        run_send_only(token, args)
        return
    if args.check_only:
        run_check_only(token, args)
        return

    chat_id = str(args.chat_id)
    offset = clear_backlog_offset(token)
    question_message_id = send_question(token, chat_id, args.date)
    print(f"Sent cotă question to {chat_id} (message_id={question_message_id}). Waiting for a reply...")

    awaiting_manual = False
    deadline = time.time() + args.max_wait_seconds

    while time.time() < deadline:
        remaining = deadline - time.time()
        poll_timeout = max(1, min(LONG_POLL_TIMEOUT, int(remaining)))
        updates = api(token, "getUpdates", offset=offset, timeout=poll_timeout,
                      allowed_updates=["message", "callback_query"])

        for update in updates:
            offset = update["update_id"] + 1

            cq = update.get("callback_query")
            if cq and str(cq.get("message", {}).get("chat", {}).get("id")) == chat_id:
                data = cq.get("data", "")
                if data == "cota:manual":
                    api(token, "answerCallbackQuery", critical=False,
                        callback_query_id=cq["id"], text="Introdu cota manual mai jos.")
                    api(token, "sendMessage", chat_id=chat_id,
                        text=f"Introdu cota dorită (număr, maxim {MAX_ALLOWED_COTA:g}x):")
                    awaiting_manual = True
                    continue
                if data.startswith("cota:"):
                    chosen = float(data.split(":", 1)[1])
                    api(token, "answerCallbackQuery", critical=False,
                        callback_query_id=cq["id"], text=f"Ai ales cota {chosen:g}x!")
                    result = {"date": args.date, "chat_id": chat_id, "chosen_cota": chosen, "source": "button"}
                    write_output(args, result)
                    return

            msg = update.get("message")
            if awaiting_manual and msg and str(msg.get("chat", {}).get("id")) == chat_id and "text" in msg:
                text = msg["text"].strip().replace(",", ".")
                try:
                    value = float(text)
                except ValueError:
                    api(token, "sendMessage", chat_id=chat_id, text="Te rog introdu un număr valid (ex: 37.5).")
                    continue
                if value <= 1.0:
                    api(token, "sendMessage", chat_id=chat_id, text="Cota trebuie să fie mai mare decât 1. Încearcă din nou:")
                    continue
                if value > MAX_ALLOWED_COTA:
                    api(token, "sendMessage", chat_id=chat_id,
                        text=f"Cota maximă permisă este {MAX_ALLOWED_COTA:g}x. Te rog introdu o valoare mai mică:")
                    continue
                api(token, "sendMessage", chat_id=chat_id, text=f"Ai ales cota {value:g}x, se generează biletul...")
                result = {"date": args.date, "chat_id": chat_id, "chosen_cota": value, "source": "manual"}
                write_output(args, result)
                return

    fail(f"No cotă choice received within {args.max_wait_seconds}s.")


def write_output(args, result):
    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / f"cota_choice_{args.date}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Chosen cotă: {result['chosen_cota']}x (source: {result['source']})")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()
