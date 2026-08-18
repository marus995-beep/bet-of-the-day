#!/usr/bin/env python3
"""Validate an agent-authored picks JSON and prepare the Telegram message.

Computes combined odds itself (product of the 4 legs' decimal odds) — this is
the source of truth, not whatever arithmetic the agent did when drafting picks.
Never invents or adjusts odds; only validates, computes, and formats.

Usage:
    python tools/prepare_bet_slip.py --picks-spec .tmp/bet_spec_2026-08-17.json \
        [--output .tmp/bet_params_2026-08-17.json]
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_LEGS = 4
TARGET_MIN, TARGET_MAX = 8.0, 13.0

ROMANIAN_MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]

# Telegram legacy Markdown special chars that need escaping in dynamic text
# (team names, rationale) so a stray "_" or "*" doesn't break formatting.
MARKDOWN_SPECIAL_CHARS = re.compile(r"([_*`\[])")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def escape_markdown(text):
    return MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", text)


def validate(spec):
    errors = []

    if not spec.get("date"):
        errors.append("missing top-level 'date'")

    picks = spec.get("picks")
    if not isinstance(picks, list):
        errors.append("missing top-level 'picks' list")
        return errors, []

    if len(picks) != REQUIRED_LEGS:
        errors.append(f"'picks' must contain exactly {REQUIRED_LEGS} legs, got {len(picks)}")

    for i, pick in enumerate(picks):
        if not isinstance(pick, dict):
            errors.append(f"picks[{i}] must be an object")
            continue
        for field in ("match", "market", "pick", "odds", "rationale"):
            if field not in pick or pick[field] in (None, ""):
                errors.append(f"picks[{i}] missing '{field}'")
        odds = pick.get("odds")
        if odds is not None and (not isinstance(odds, (int, float)) or odds <= 1.0):
            errors.append(f"picks[{i}].odds must be a number > 1.0, got {odds!r}")

    return errors, picks


def format_pick_block(index, pick):
    pick_line = f"{index}⃣ {escape_markdown(pick['pick'])} @ {pick['odds']} — {escape_markdown(pick['match'])}"
    comment_line = f"\U0001F4AC {escape_markdown(pick['rationale'])}"
    return f"{pick_line}\n{comment_line}"


def main():
    parser = argparse.ArgumentParser(description="Validate picks and prepare the Telegram message.")
    parser.add_argument("--picks-spec", required=True, help="Path to the picks-spec JSON.")
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/bet_params_<date>.json")
    args = parser.parse_args()

    spec_path = Path(args.picks_spec)
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    if not spec_path.exists():
        fail(f"Picks spec not found: {spec_path}")

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Picks spec is not valid JSON: {exc}")

    errors, picks = validate(spec)
    if errors:
        fail("Picks spec failed validation:\n  - " + "\n  - ".join(errors))

    combined_odds = 1.0
    for pick in picks:
        combined_odds *= pick["odds"]
    combined_odds = round(combined_odds, 2)

    if not (TARGET_MIN <= combined_odds <= TARGET_MAX):
        warn(
            f"Combined odds {combined_odds}x is outside the {TARGET_MIN}x-{TARGET_MAX}x "
            "target range — shipping anyway, but consider adjusting a leg."
        )

    date_str = spec["date"]
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = f"{parsed_date.day} {ROMANIAN_MONTHS[parsed_date.month - 1]} {parsed_date.year}"
    except ValueError:
        date_display = date_str

    pick_blocks = [format_pick_block(i, p) for i, p in enumerate(picks, start=1)]

    telegram_message = (
        f"\U0001F3AF *Biletul Zilei* — {date_display}\n\n"
        + "\n\n".join(pick_blocks)
        + f"\n\n\U0001F4B0 Cotă combinată: *{combined_odds:.2f}x*"
        + "\n\n\U0001F51E Pronosticuri generate de AI, doar pentru divertisment. "
        "Nu reprezintă sfaturi financiare — joacă responsabil."
    )

    result = {
        "date": date_str,
        "combined_odds": combined_odds,
        "picks": picks,
        "telegram_message": telegram_message,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / f"bet_params_{date_str}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved bet slip to: {output_path}")
    print(f"Combined odds: {combined_odds}x")
    print("---")
    print(telegram_message)


if __name__ == "__main__":
    main()
