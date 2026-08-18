#!/usr/bin/env python3
"""Validate agent-authored result summaries and build the results Telegram message.

Trusts fetch_results.py's won/lost grading as the source of truth — the agent
supplies only the "what happened" narrative per game, never the outcome
itself. Cross-checks that the narrative-spec's matches line up 1:1 with the
results file before building anything, and refuses to run if any fixture is
still pending (no guessing a result that hasn't happened yet).

Usage:
    python tools/prepare_results_message.py --results-file .tmp/results_2026-08-17.json \
        --narrative-spec .tmp/results_narrative_2026-08-17.json \
        --sent-file .tmp/telegram_sent_2026-08-17.json \
        [--output .tmp/results_params_2026-08-17.json]
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


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def escape_markdown(text):
    return MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", text)


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


def main():
    parser = argparse.ArgumentParser(description="Build the Bet of the Day results message.")
    parser.add_argument("--results-file", required=True, help="Output of fetch_results.py.")
    parser.add_argument("--narrative-spec", required=True, help="Agent-authored per-game summaries.")
    parser.add_argument(
        "--sent-file", default=None,
        help="telegram_sent_<date>.json from the original send, so this can reply in-thread.",
    )
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/results_params_<date>.json")
    args = parser.parse_args()

    results, _ = load_json(args.results_file, "Results file")

    if results.get("any_pending"):
        fail(
            "Some fixtures are still pending (not completed) in the results file — "
            "rerun tools/fetch_results.py later once all games have finished. Do not guess."
        )

    results_list = results.get("results")
    if not isinstance(results_list, list) or not results_list:
        fail("Results file has no 'results' list.")

    narrative, _ = load_json(args.narrative_spec, "Narrative spec")
    summaries = narrative.get("summaries")
    if not isinstance(summaries, list):
        fail("Narrative spec missing 'summaries' list.")
    if len(summaries) != len(results_list):
        fail(f"Narrative spec has {len(summaries)} summaries but results has {len(results_list)} legs.")

    narrative_by_match = {}
    for i, s in enumerate(summaries):
        if not isinstance(s, dict) or not s.get("match") or not s.get("summary"):
            fail(f"narrative-spec.summaries[{i}] needs 'match' and 'summary'.")
        narrative_by_match[s["match"]] = s["summary"]

    for r in results_list:
        if r["match"] not in narrative_by_match:
            fail(f"Narrative spec is missing a summary for '{r['match']}'.")

    date_str = results.get("date")
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = f"{parsed_date.day} {ROMANIAN_MONTHS[parsed_date.month - 1]} {parsed_date.year}"
    except (ValueError, TypeError):
        date_display = date_str or "?"

    blocks = []
    for i, r in enumerate(results_list, start=1):
        glyph = "✅" if r["won"] else "❌"
        pick_line = f"{i}⃣ {escape_markdown(str(r['pick']))} @ {r['odds']} — {escape_markdown(r['match'])}"
        result_line = f"{glyph} Scor final: {r['final_score']}"
        summary_line = f"\U0001F4AC {escape_markdown(narrative_by_match[r['match']])}"
        blocks.append(f"{pick_line}\n{result_line}\n{summary_line}")

    all_won = results.get("all_won")
    if all_won:
        closing = (
            "\U0001F389 *Biletul de ieri a fost CÂȘTIGĂTOR!* Felicitări și mulțumim că "
            "ne sunteți alături — pe curând cu un nou bilet! \U0001F340"
        )
    else:
        closing = (
            "\U0001F614 Din păcate biletul de ieri nu a ieșit. Baftă mai multă data "
            "viitoare — și nu uitați, jucați responsabil. \U0001F51E"
        )

    telegram_message = (
        f"\U0001F4CA *Rezultate Biletul Zilei* — {date_display}\n\n"
        + "\n\n".join(blocks)
        + f"\n\n{closing}"
    )

    reply_to_message_ids = {}
    if args.sent_file:
        sent_path = Path(args.sent_file)
        if not sent_path.is_absolute():
            sent_path = REPO_ROOT / sent_path
        if sent_path.exists():
            sent_data = json.loads(sent_path.read_text(encoding="utf-8"))
            reply_to_message_ids = sent_data.get("sent_message_ids", {})
        else:
            warn(f"--sent-file {sent_path} not found — sending as a new message, not a reply.")

    output = {
        "date": date_str,
        "all_won": all_won,
        "telegram_message": telegram_message,
        "reply_to_message_ids": reply_to_message_ids,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / f"results_params_{date_str}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved results message to: {output_path}")
    print(f"All won: {all_won}")
    print("---")
    print(telegram_message)


if __name__ == "__main__":
    main()
