#!/usr/bin/env python3
"""Randomly assemble a combined bet from pariurix.com tipster picks that
targets a chosen cotă, and format the Telegram message.

Unlike prepare_bet_slip.py (which validates an agent-curated picks-spec),
this tool does the SELECTION itself — randomly sampling combinations of
whatever fetch_pariurix_predictions.py found for the day until one lands
within tolerance of --target-odds, or the closest one found after
--max-attempts tries. Combined odds are still computed deterministically
(product of each leg's cotă) — randomness only decides which legs are in the
bet, never the arithmetic.

Each leg's rationale is the tipster's own analysis, clearly attributed (not
presented as this project's own verified claim) — see
fetch_pariurix_predictions.py's sourcing_note for why.

Usage:
    python tools/generate_pariurix_bet.py --predictions-file .tmp/pariurix/predictions-2026-08-21.json \
        --target-odds 15 [--seed 42] [--max-attempts 2000] [--output .tmp/pariurix/bet-2026-08-21.json]
"""

import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIN_LEGS, MAX_LEGS = 3, 7
DEFAULT_TARGET_ODDS = 10.0
TARGET_TOLERANCE_LOW, TARGET_TOLERANCE_HIGH = 0.8, 1.3
MAX_ALLOWED_TARGET_ODDS = 100.0
DEFAULT_MAX_ATTEMPTS = 2000

ROMANIAN_MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]

MARKDOWN_SPECIAL_CHARS = re.compile(r"([_*`\[])")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def escape_markdown(text):
    return MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", text)


def date_display(date_str):
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{parsed.day} {ROMANIAN_MONTHS[parsed.month - 1]} {parsed.year}"
    except ValueError:
        return date_str


def combined_odds(picks):
    total = 1.0
    for p in picks:
        total *= p["odds"]
    return total


def find_combination(predictions, target_odds, max_attempts, rng):
    """Random search for a leg combination within tolerance of target_odds.
    Returns (picks, combined, within_tolerance). Keeps the closest-to-target
    combination seen even if nothing lands in tolerance."""
    target_min = target_odds * TARGET_TOLERANCE_LOW
    target_max = target_odds * TARGET_TOLERANCE_HIGH

    best_picks, best_combined, best_distance = None, None, float("inf")
    upper = min(MAX_LEGS, len(predictions))
    lower = min(MIN_LEGS, upper)

    for _ in range(max_attempts):
        k = rng.randint(lower, upper)
        sample = rng.sample(predictions, k)
        combined = combined_odds(sample)

        if target_min <= combined <= target_max:
            return sample, combined, True

        # distance in log-space so a 2x-too-high miss and a 2x-too-low miss count equally
        distance = abs((combined / target_odds) - 1.0) if target_odds else abs(combined)
        if distance < best_distance:
            best_picks, best_combined, best_distance = sample, combined, distance

    return best_picks, best_combined, False


def excerpt(text, max_len=220):
    if not text:
        return None
    text = text.strip()
    return text if len(text) <= max_len else text[:max_len].rsplit(" ", 1)[0] + "…"


def format_pick_block(index, pick):
    pick_line = f"{index}⃣ {escape_markdown(pick['pick'])} @ {pick['odds']:g} — {escape_markdown(pick['match'])}"
    lines = [pick_line]

    summary = excerpt(pick.get("analysis_raw"))
    if summary:
        lines.append(f"\U0001F4AC {escape_markdown(summary)}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Randomly build a combined bet from pariurix.com predictions.")
    parser.add_argument("--predictions-file", required=True, help="Output of fetch_pariurix_predictions.py.")
    parser.add_argument("--target-odds", type=float, default=DEFAULT_TARGET_ODDS,
                         help=f"Target combined odds. Default: {DEFAULT_TARGET_ODDS}.")
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible test runs.")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                         help=f"Random combinations to try before giving up. Default: {DEFAULT_MAX_ATTEMPTS}.")
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/pariurix/bet-<date>.json")
    args = parser.parse_args()

    if args.target_odds > MAX_ALLOWED_TARGET_ODDS:
        fail(f"--target-odds {args.target_odds} exceeds the maximum allowed ({MAX_ALLOWED_TARGET_ODDS}x).")
    if args.target_odds <= 1.0:
        fail(f"--target-odds must be > 1.0, got {args.target_odds}.")

    predictions_path = Path(args.predictions_file)
    if not predictions_path.is_absolute():
        predictions_path = REPO_ROOT / predictions_path
    if not predictions_path.exists():
        fail(f"Predictions file not found: {predictions_path}")

    try:
        data = json.loads(predictions_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Predictions file is not valid JSON: {exc}")

    predictions = data.get("predictions") or []
    date_str = data.get("date")

    if len(predictions) < MIN_LEGS:
        fail(
            f"Only {len(predictions)} pariurix prediction(s) available for {date_str} "
            f"— need at least {MIN_LEGS} to build a bet. Re-run fetch_pariurix_predictions.py "
            "for a date pariurix has actually published picks for."
        )

    rng = random.Random(args.seed)
    picks, combined, within_tolerance = find_combination(predictions, args.target_odds, args.max_attempts, rng)

    if not within_tolerance:
        target_min = round(args.target_odds * TARGET_TOLERANCE_LOW, 2)
        target_max = round(args.target_odds * TARGET_TOLERANCE_HIGH, 2)
        warn(
            f"No random combination landed within {target_min}x-{target_max}x for the "
            f"{args.target_odds}x target after {args.max_attempts} attempts — shipping the "
            f"closest one found ({round(combined, 2)}x)."
        )

    combined = round(combined, 2)
    date_display_str = date_display(date_str) if date_str else "?"
    pick_blocks = [format_pick_block(i, p) for i, p in enumerate(picks, start=1)]

    telegram_message = (
        f"\U0001F3AF *Biletul Zilei* — {date_display_str}\n\n"
        + "\n\n".join(pick_blocks)
        + f"\n\n\U0001F4B0 Cotă combinată: *{combined:.2f}x*"
        + "\n\n\U0001F51E Pronosticuri generate de AI, doar pentru divertisment. "
        "Nu reprezintă sfaturi financiare — joacă responsabil."
    )

    result = {
        "date": date_str,
        "source": "pariurix.com",
        "target_odds": args.target_odds,
        "combined_odds": combined,
        "within_tolerance": within_tolerance,
        "picks": picks,
        "telegram_message": telegram_message,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / "pariurix" / f"bet-{date_str}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved pariurix bet to: {output_path}")
    print(f"Combined odds: {combined}x (within tolerance: {within_tolerance})")
    print("---")
    print(telegram_message)


if __name__ == "__main__":
    main()
