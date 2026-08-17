#!/usr/bin/env python3
"""Validate an agent-authored picks JSON and prepare WhatsApp template parameters.

Computes combined odds itself (product of the 4 legs' decimal odds) — this is
the source of truth, not whatever arithmetic the agent did when drafting picks.
Never invents or adjusts odds; only validates, computes, and formats.

Usage:
    python tools/prepare_bet_slip.py --picks-spec .tmp/bet_spec_2026-08-17.json \
        [--output .tmp/bet_params_2026-08-17.json]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_LEGS = 4
TARGET_MIN, TARGET_MAX = 8.0, 13.0
MAX_LINE_LEN = 220  # WhatsApp template body params: keep each line short/legible

ROMANIAN_MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


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


def clip(text, label):
    if len(text) > MAX_LINE_LEN:
        warn(f"{label} exceeds {MAX_LINE_LEN} chars, truncating: {text[:60]}...")
        text = text[: MAX_LINE_LEN - 1].rstrip() + "…"
    return text


def format_pick_line(pick):
    return clip(f"{pick['pick']} @ {pick['odds']} — {pick['match']}", "Pick line")


def format_comment_line(pick):
    return clip(pick["rationale"], "Comment line")


def main():
    parser = argparse.ArgumentParser(description="Validate picks and prepare WhatsApp template params.")
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

    pick_lines = [format_pick_line(p) for p in picks]
    comment_lines = [format_comment_line(p) for p in picks]

    # Interleaved so each game's line is immediately followed by its comment,
    # matching the template body's alternating {{pick}} / {{comment}} slots.
    whatsapp_params = [date_display]
    for pick_line, comment_line in zip(pick_lines, comment_lines):
        whatsapp_params += [pick_line, comment_line]
    whatsapp_params += [f"{combined_odds:.2f}"]

    result = {
        "date": date_str,
        "combined_odds": combined_odds,
        "picks": picks,
        "whatsapp_params": whatsapp_params,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / f"bet_params_{date_str}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved bet slip to: {output_path}")
    print(f"Combined odds: {combined_odds}x")
    for pick_line, comment_line in zip(pick_lines, comment_lines):
        print(f"  - {pick_line}")
        print(f"      → {comment_line}")


if __name__ == "__main__":
    main()
