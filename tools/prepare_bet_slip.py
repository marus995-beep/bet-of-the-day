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
MIN_LEGS, MAX_LEGS = 3, 7
DEFAULT_TARGET_ODDS = 10.0
TARGET_TOLERANCE_LOW, TARGET_TOLERANCE_HIGH = 0.8, 1.3  # accept 80%-130% of target
MAX_ALLOWED_TARGET_ODDS = 100.0

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


VALID_BET_TYPES = {"match_winner", "total_goals", "double_chance", "btts", "correct_score"}
VALID_SELECTIONS = {"home", "away", "draw"}
VALID_DOUBLE_CHANCE_SELECTIONS = {"home_or_draw", "away_or_draw", "home_or_away"}
VALID_BTTS_SELECTIONS = {"yes", "no"}


def validate(spec):
    errors = []

    if not spec.get("date"):
        errors.append("missing top-level 'date'")

    picks = spec.get("picks")
    if not isinstance(picks, list):
        errors.append("missing top-level 'picks' list")
        return errors, []

    if not (MIN_LEGS <= len(picks) <= MAX_LEGS):
        errors.append(f"'picks' must contain {MIN_LEGS}-{MAX_LEGS} legs, got {len(picks)}")

    for i, pick in enumerate(picks):
        if not isinstance(pick, dict):
            errors.append(f"picks[{i}] must be an object")
            continue
        for field in ("match", "home_team", "away_team", "fixture_id", "league_id", "market",
                      "bet_type", "selection", "pick", "odds", "rationale"):
            if field not in pick or pick[field] in (None, ""):
                errors.append(f"picks[{i}] missing '{field}'")
        odds = pick.get("odds")
        if odds is not None and (not isinstance(odds, (int, float)) or odds <= 1.0):
            errors.append(f"picks[{i}].odds must be a number > 1.0, got {odds!r}")

        bet_type = pick.get("bet_type")
        selection = pick.get("selection")
        if bet_type is not None and bet_type not in VALID_BET_TYPES:
            errors.append(f"picks[{i}].bet_type must be one of {sorted(VALID_BET_TYPES)}, got {bet_type!r}")
        elif bet_type == "match_winner":
            if selection not in VALID_SELECTIONS:
                errors.append(
                    f"picks[{i}].selection must be one of {sorted(VALID_SELECTIONS)} "
                    f"for bet_type 'match_winner', got {selection!r}"
                )
        elif bet_type == "total_goals":
            if not isinstance(selection, dict) or selection.get("side") not in ("over", "under") \
                    or not isinstance(selection.get("line"), (int, float)):
                errors.append(
                    f"picks[{i}].selection must be {{'side': 'over'|'under', 'line': number}} "
                    f"for bet_type 'total_goals', got {selection!r}"
                )
        elif bet_type == "double_chance":
            if selection not in VALID_DOUBLE_CHANCE_SELECTIONS:
                errors.append(
                    f"picks[{i}].selection must be one of {sorted(VALID_DOUBLE_CHANCE_SELECTIONS)} "
                    f"for bet_type 'double_chance', got {selection!r}"
                )
        elif bet_type == "btts":
            if selection not in VALID_BTTS_SELECTIONS:
                errors.append(
                    f"picks[{i}].selection must be one of {sorted(VALID_BTTS_SELECTIONS)} "
                    f"for bet_type 'btts', got {selection!r}"
                )
        elif bet_type == "correct_score":
            if not isinstance(selection, dict) \
                    or not isinstance(selection.get("home"), int) or selection.get("home") < 0 \
                    or not isinstance(selection.get("away"), int) or selection.get("away") < 0:
                errors.append(
                    f"picks[{i}].selection must be {{'home': int >= 0, 'away': int >= 0}} "
                    f"for bet_type 'correct_score', got {selection!r}"
                )

    return errors, picks


def format_pick_block(index, pick):
    pick_line = f"{index}⃣ {escape_markdown(pick['pick'])} @ {pick['odds']} — {escape_markdown(pick['match'])}"
    comment_line = f"\U0001F4AC {escape_markdown(pick['rationale'])}"
    return f"{pick_line}\n{comment_line}"


def main():
    parser = argparse.ArgumentParser(description="Validate picks and prepare the Telegram message.")
    parser.add_argument("--picks-spec", required=True, help="Path to the picks-spec JSON.")
    parser.add_argument(
        "--target-odds", type=float, default=DEFAULT_TARGET_ODDS,
        help=f"Target combined odds (e.g. from the user's cotă choice). Default: {DEFAULT_TARGET_ODDS}.",
    )
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/bet_params_<date>.json")
    args = parser.parse_args()

    if args.target_odds > MAX_ALLOWED_TARGET_ODDS:
        fail(f"--target-odds {args.target_odds} exceeds the maximum allowed ({MAX_ALLOWED_TARGET_ODDS}x).")
    if args.target_odds <= 1.0:
        fail(f"--target-odds must be > 1.0, got {args.target_odds}.")

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

    target_min = round(args.target_odds * TARGET_TOLERANCE_LOW, 2)
    target_max = round(args.target_odds * TARGET_TOLERANCE_HIGH, 2)
    if not (target_min <= combined_odds <= target_max):
        warn(
            f"Combined odds {combined_odds}x is outside the {target_min}x-{target_max}x "
            f"range for the requested {args.target_odds}x target — shipping anyway, "
            "but consider adjusting a leg."
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
        "target_odds": args.target_odds,
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
