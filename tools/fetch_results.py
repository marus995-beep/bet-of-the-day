#!/usr/bin/env python3
"""Fetch final scores for a picks-spec's fixtures and grade each leg won/lost.

Deterministic: pulls each fixture's final score from API-Football by
fixture_id (recorded on the pick when it was built) and applies the
bet_type/selection logic already recorded on each pick. Never guesses or
infers a result from a headline or reasoning — only a completed score from
the API counts as a result. A fixture with no completed score yet is marked
"pending", not guessed at.

Usage:
    python tools/fetch_results.py --picks-spec .tmp/bet_spec_2026-08-17.json \
        [--output .tmp/results_2026-08-17.json]
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
API_BASE = "https://v3.football.api-sports.io"
REQUEST_DELAY_SECONDS = 1.1  # stays under the free plan's 10 requests/minute

# API-Football fixture status codes that mean "this match is over and the
# score is final." Anything else (NS, 1H, HT, 2H, ET, P, LIVE, PST, CANC...)
# is treated as not-yet-gradable.
FINISHED_STATUSES = {"FT", "AET", "PEN"}


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def fetch_fixture(api_key, fixture_id):
    url = f"{API_BASE}/fixtures"
    headers = {"x-apisports-key": api_key}
    try:
        response = requests.get(url, headers=headers, params={"id": fixture_id}, timeout=30)
    except requests.exceptions.Timeout:
        fail(f"Request to API-Football timed out for fixture {fixture_id}.")
    except requests.exceptions.ConnectionError as exc:
        fail(f"Could not connect to API-Football: {exc}")

    if response.status_code == 401:
        fail("API-Football rejected the request (401): invalid API_FOOTBALL_KEY.")
    if response.status_code != 200:
        warn(f"API-Football returned {response.status_code} for fixture {fixture_id}: {response.text[:200]}")
        return None

    data = response.json()
    resp = data.get("response") or []
    return resp[0] if resp else None


def extract_score(fixture_data):
    goals = fixture_data.get("goals", {})
    home, away = goals.get("home"), goals.get("away")
    if home is None or away is None:
        return None
    return int(home), int(away)


def grade_pick(pick, home_score, away_score):
    bet_type = pick["bet_type"]
    selection = pick["selection"]

    if home_score > away_score:
        outcome = "home"
    elif away_score > home_score:
        outcome = "away"
    else:
        outcome = "draw"

    if bet_type == "match_winner":
        return outcome == selection
    if bet_type == "total_goals":
        total = home_score + away_score
        if selection["side"] == "over":
            return total > selection["line"]
        return total < selection["line"]
    if bet_type == "double_chance":
        pairs = {
            "home_or_draw": {"home", "draw"},
            "away_or_draw": {"away", "draw"},
            "home_or_away": {"home", "away"},
        }
        return outcome in pairs[selection]
    if bet_type == "btts":
        both_scored = home_score > 0 and away_score > 0
        return both_scored if selection == "yes" else not both_scored
    if bet_type == "correct_score":
        return home_score == selection["home"] and away_score == selection["away"]
    fail(f"Unknown bet_type {bet_type!r} — can't grade.")


def main():
    parser = argparse.ArgumentParser(description="Fetch final scores and grade a picks-spec's legs.")
    parser.add_argument("--picks-spec", required=True, help="Path to the picks-spec JSON to grade.")
    parser.add_argument("--output", default=None, help="Output path. Default: .tmp/results_<date>.json")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        fail("API_FOOTBALL_KEY is not set in .env.")

    spec_path = Path(args.picks_spec)
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    if not spec_path.exists():
        fail(f"Picks spec not found: {spec_path}")

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Picks spec is not valid JSON: {exc}")

    picks = spec.get("picks") or []
    if not picks:
        fail("Picks spec has no 'picks'.")

    results = []
    for i, pick in enumerate(picks):
        fixture_id = pick.get("fixture_id")
        if not fixture_id:
            fail(f"Pick for {pick.get('match')} is missing 'fixture_id' — can't fetch its result.")

        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        fixture_data = fetch_fixture(api_key, fixture_id)

        entry = {
            "match": pick["match"],
            "pick": pick["pick"],
            "odds": pick["odds"],
            "bet_type": pick["bet_type"],
            "selection": pick["selection"],
        }

        if fixture_data is None:
            warn(f"No fixture found for id {fixture_id} ({pick['match']}) — marking pending.")
            entry.update({"status": "pending", "final_score": None, "won": None})
        else:
            status_short = fixture_data.get("fixture", {}).get("status", {}).get("short")
            if status_short not in FINISHED_STATUSES:
                entry.update({"status": "pending", "final_score": None, "won": None})
            else:
                score = extract_score(fixture_data)
                if score is None:
                    warn(f"Fixture {fixture_id} ({pick['match']}) is {status_short} but has no parseable score — marking pending.")
                    entry.update({"status": "pending", "final_score": None, "won": None})
                else:
                    home_score, away_score = score
                    won = grade_pick(pick, home_score, away_score)
                    entry.update({
                        "status": "completed",
                        "final_score": f"{home_score}-{away_score}",
                        "won": won,
                    })

        results.append(entry)

    any_pending = any(r["status"] == "pending" for r in results)
    all_won = None if any_pending else all(r["won"] for r in results)

    output = {
        "date": spec.get("date"),
        "results": results,
        "any_pending": any_pending,
        "all_won": all_won,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / f"results_{spec.get('date')}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved results to: {output_path}")
    for r in results:
        status_label = "PENDING" if r["status"] == "pending" else ("WON" if r["won"] else "LOST")
        print(f"  - {r['match']}: {r.get('final_score') or 'pending'} -> {status_label}")
    print(f"any_pending={any_pending} all_won={all_won}")


if __name__ == "__main__":
    main()
