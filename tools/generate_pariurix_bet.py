#!/usr/bin/env python3
"""Randomly select a combination of pariurix.com tipster picks that targets
a chosen cotă.

Unlike prepare_bet_slip.py (which validates an agent-curated picks-spec),
this tool does the SELECTION itself — randomly sampling combinations of
whatever fetch_pariurix_predictions.py found for the day until one lands
within tolerance of --target-odds, or the closest one found after
--max-attempts tries. Combined odds are still computed deterministically
(product of each leg's cotă) — randomness only decides which legs are in the
bet, never the arithmetic.

Two hard constraints on the candidate pool: no single leg's odds may exceed
MAX_LEG_ODDS (2.99), and there's a floor of MIN_LEGS (3) but deliberately no
ceiling — a 100x target built only from sub-3.00 legs needs however many legs
that actually takes (e.g. ~8-9 legs at ~1.8 average), not a fixed 3-7 range.

Does NOT build the Telegram message. Each leg's full "analysis_raw" (the
tipster's own words, untruncated) is carried through in the output — the
agent should read it and write a short, original rephrasing per leg (not a
truncated copy), then run finalize_pariurix_message.py with that alongside
this tool's output to build the actual message.

Usage:
    python tools/generate_pariurix_bet.py --predictions-file .tmp/pariurix/predictions-2026-08-21.json \
        --target-odds 15 [--seed 42] [--max-attempts 2000] [--output .tmp/pariurix/selection-2026-08-21.json]
"""

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIN_LEGS = 3  # no upper cap — a 100x target with modest per-leg odds needs as many legs as it takes
MAX_LEG_ODDS = 2.99  # never use a single pick priced above this
DEFAULT_TARGET_ODDS = 10.0
TARGET_TOLERANCE_LOW, TARGET_TOLERANCE_HIGH = 0.8, 1.3
MAX_ALLOWED_TARGET_ODDS = 100.0
DEFAULT_MAX_ATTEMPTS = 2000


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


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
    upper = len(predictions)
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


def main():
    parser = argparse.ArgumentParser(description="Randomly select a combined bet from pariurix.com predictions.")
    parser.add_argument("--predictions-file", required=True, help="Output of fetch_pariurix_predictions.py.")
    parser.add_argument("--target-odds", type=float, default=DEFAULT_TARGET_ODDS,
                         help=f"Target combined odds. Default: {DEFAULT_TARGET_ODDS}.")
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible test runs.")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                         help=f"Random combinations to try before giving up. Default: {DEFAULT_MAX_ATTEMPTS}.")
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/pariurix/selection-<date>.json")
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

    all_predictions = data.get("predictions") or []
    date_str = data.get("date")

    predictions = [p for p in all_predictions if p.get("odds") is not None and p["odds"] <= MAX_LEG_ODDS]
    excluded = len(all_predictions) - len(predictions)
    if excluded:
        warn(f"Excluded {excluded} prediction(s) priced above {MAX_LEG_ODDS:.2f}x — never used as a leg.")

    if len(predictions) < MIN_LEGS:
        fail(
            f"Only {len(predictions)} pariurix prediction(s) available for {date_str} with odds "
            f"<= {MAX_LEG_ODDS:.2f}x — need at least {MIN_LEGS} to build a bet. Re-run "
            "fetch_pariurix_predictions.py for a date pariurix has actually published picks for."
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

    result = {
        "date": date_str,
        "source": "pariurix.com",
        "target_odds": args.target_odds,
        "combined_odds": combined,
        "within_tolerance": within_tolerance,
        "picks": picks,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / "pariurix" / f"selection-{date_str}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved pariurix selection to: {output_path}")
    print(f"Combined odds: {combined}x (within tolerance: {within_tolerance})")
    for i, p in enumerate(picks, start=1):
        print(f"  {i}. {p['pick']} @ {p['odds']:.2f} — {p['match']}")
    print("Next: write a short, original rephrasing of each leg's analysis_raw, "
          "then run finalize_pariurix_message.py.")


if __name__ == "__main__":
    main()
