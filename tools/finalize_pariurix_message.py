#!/usr/bin/env python3
"""Combine generate_pariurix_bet.py's leg selection with an agent-authored
rephrasing of each leg's analysis, and build the final Telegram message.

Mirrors prepare_results_message.py's pattern: trusts the deterministic
output (picks, odds, combined odds) as the source of truth, and only accepts
narrative text from the agent — cross-checking every leg has a summary
before building anything. Never lets the agent alter a pick, an odds value,
or the combined odds; only supplies the short rephrased description.

Usage:
    python tools/finalize_pariurix_message.py --selection-file .tmp/pariurix/selection-2026-08-21.json \
        --summaries-file .tmp/pariurix/summaries-2026-08-21.json \
        [--output .tmp/pariurix/bet-2026-08-21.json]
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ROMANIAN_MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]

MARKDOWN_SPECIAL_CHARS = re.compile(r"([_*`\[])")
MAX_SUMMARY_LEN = 200


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
    except (ValueError, TypeError):
        return date_str or "?"


def load_json(path_arg, label):
    path = Path(path_arg)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        fail(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except json.JSONDecodeError as exc:
        fail(f"{label} is not valid JSON: {exc}")


def format_pick_block(index, pick, summary):
    match_line = f"{index}⃣ {escape_markdown(pick['match'])}"
    pick_line = f"{escape_markdown(pick['pick'])} @ {pick['odds']:g}"
    return f"{match_line}\n{pick_line}\n\U0001F4AC {escape_markdown(summary)}"


def main():
    parser = argparse.ArgumentParser(description="Build the final pariurix bet message.")
    parser.add_argument("--selection-file", required=True, help="Output of generate_pariurix_bet.py.")
    parser.add_argument("--summaries-file", required=True,
                         help="Agent-authored rephrased summaries, one per leg.")
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/pariurix/bet-<date>.json")
    args = parser.parse_args()

    selection, _ = load_json(args.selection_file, "Selection file")
    picks = selection.get("picks") or []
    if not picks:
        fail("Selection file has no 'picks'.")

    summaries_data, _ = load_json(args.summaries_file, "Summaries file")
    summaries = summaries_data.get("summaries")
    if not isinstance(summaries, list):
        fail("Summaries file missing 'summaries' list.")

    summary_by_match = {}
    for i, s in enumerate(summaries):
        if not isinstance(s, dict) or not s.get("match") or not s.get("summary"):
            fail(f"summaries[{i}] needs 'match' and 'summary'.")
        text = s["summary"].strip()
        if len(text) > MAX_SUMMARY_LEN:
            fail(
                f"summaries[{i}] for {s['match']!r} is {len(text)} chars, over the "
                f"{MAX_SUMMARY_LEN}-char limit — rephrase it shorter, don't truncate it here."
            )
        summary_by_match[s["match"]] = text

    for pick in picks:
        if pick["match"] not in summary_by_match:
            fail(f"No summary provided for leg {pick['match']!r} — every leg needs a rephrased summary.")

    date_str = selection.get("date")
    date_display_str = date_display(date_str)
    combined = selection.get("combined_odds")

    pick_blocks = [
        format_pick_block(i, p, summary_by_match[p["match"]])
        for i, p in enumerate(picks, start=1)
    ]

    telegram_message = (
        f"\U0001F3AF *Biletul Zilei* — {date_display_str}\n\n"
        + "\n\n".join(pick_blocks)
        + f"\n\n\U0001F4B0 Cotă combinată: *{combined:.2f}x*"
        + "\n\n\U0001F51E Pronosticuri generate de AI, doar pentru divertisment. "
        "Nu reprezintă sfaturi financiare — joacă responsabil."
    )

    result = {
        "date": date_str,
        "source": selection.get("source"),
        "target_odds": selection.get("target_odds"),
        "combined_odds": combined,
        "within_tolerance": selection.get("within_tolerance"),
        "picks": picks,
        "telegram_message": telegram_message,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / "pariurix" / f"bet-{date_str}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved finalized pariurix bet to: {output_path}")
    print("---")
    print(telegram_message)


if __name__ == "__main__":
    main()
