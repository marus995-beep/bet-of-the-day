#!/usr/bin/env python3
"""Send the Bet of the Day WhatsApp template message via the Meta Cloud API.

Reads the six positional template parameters (date, 4 pick lines, combined odds)
from prepare_bet_slip.py's output and posts a template message. Proactive
(non-session) WhatsApp messages must use a pre-approved template — see
workflows/bet_of_the_day.md for the exact template text to submit in Meta
Business Manager.

Usage:
    python tools/send_whatsapp.py --params-file .tmp/bet_params_2026-08-17.json
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
GRAPH_API_VERSION = "v21.0"
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def send_template(token, phone_number_id, to, template_name, language, params):
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(p)} for p in params],
                }
            ],
        },
    }

    attempt = 0
    while True:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
        except requests.exceptions.Timeout:
            fail("Request to the WhatsApp Graph API timed out.")
        except requests.exceptions.ConnectionError as exc:
            fail(f"Could not connect to the WhatsApp Graph API: {exc}")

        if response.status_code == 200:
            return response.json()

        if response.status_code == 429 or response.status_code >= 500:
            attempt += 1
            if attempt > MAX_RETRIES:
                fail(f"WhatsApp Graph API still failing after {MAX_RETRIES} retries: {response.text[:400]}")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        # 4xx (other than 429): don't retry, surface Meta's error detail directly.
        try:
            detail = response.json().get("error", {})
            fail(
                f"WhatsApp Graph API rejected the request ({response.status_code}): "
                f"{detail.get('message', response.text[:300])} "
                f"(type={detail.get('type')}, code={detail.get('code')})"
            )
        except (ValueError, AttributeError):
            fail(f"WhatsApp Graph API returned {response.status_code}: {response.text[:300]}")


def main():
    parser = argparse.ArgumentParser(description="Send the Bet of the Day WhatsApp template message.")
    parser.add_argument("--params-file", required=True, help="Path to prepare_bet_slip.py's output JSON.")
    parser.add_argument("--language", default="ro", help="Template language code. Default: ro (Romanian).")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    token = os.environ.get("WHATSAPP_TOKEN", "").strip()
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    to = os.environ.get("WHATSAPP_RECIPIENT", "").strip()
    template_name = os.environ.get("WHATSAPP_TEMPLATE_NAME", "bet_of_the_day").strip()

    missing = [
        name
        for name, val in [
            ("WHATSAPP_TOKEN", token),
            ("WHATSAPP_PHONE_NUMBER_ID", phone_number_id),
            ("WHATSAPP_RECIPIENT", to),
        ]
        if not val
    ]
    if missing:
        fail(f"Missing required .env vars: {', '.join(missing)}")

    params_path = Path(args.params_file)
    if not params_path.is_absolute():
        params_path = REPO_ROOT / params_path
    if not params_path.exists():
        fail(f"Params file not found: {params_path}")

    try:
        data = json.loads(params_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Params file is not valid JSON: {exc}")

    params = data.get("whatsapp_params")
    if not isinstance(params, list) or not params:
        fail("Params file missing non-empty 'whatsapp_params' list.")

    result = send_template(token, phone_number_id, to, template_name, args.language, params)

    message_id = None
    messages = result.get("messages")
    if isinstance(messages, list) and messages:
        message_id = messages[0].get("id")

    print(f"Sent WhatsApp template '{template_name}' to {to}.")
    print(f"Message id: {message_id}")


if __name__ == "__main__":
    main()
