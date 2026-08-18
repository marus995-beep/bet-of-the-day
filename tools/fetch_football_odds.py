#!/usr/bin/env python3
"""Fetch today's football fixtures, bookmaker odds (many markets), and
API-Football's own predictions, following the fixture-priority policy.

Uses API-Football (api-football.com) rather than The Odds API — it covers
Romania Liga I (which The Odds API doesn't carry at all) and offers far more
markets per fixture (double chance, both teams to score, correct score,
handicaps, etc., not just 1X2/totals), plus a per-fixture algorithmic
prediction (win/draw/away %, goal expectancy, an advice string).

Fixture listing is one cheap call per day (all leagues at once via `?date=`).
Odds and predictions are one call EACH per fixture, so this tool caps how
many fixtures it enriches (MAX_FIXTURES_TO_ENRICH) to stay within the free
plan's 100 requests/day and 10 requests/minute limits — it works through the
priority league order first, so the cap always falls on the least important
candidates, never on Romania/UCL/UEL/UECL/top-5 games.

Does not pick games or markets — that's the agent's job, reading this output.

Usage:
    python tools/fetch_football_odds.py [--date 2026-08-18] [--output .tmp/odds/foo.json]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
API_BASE = "https://v3.football.api-sports.io"
ROMANIA_TZ = ZoneInfo("Europe/Bucharest")
MIN_FIXTURES_BEFORE_FALLBACK = 3
MAX_FIXTURES_TO_ENRICH = 12  # caps odds+predictions calls (2 each) per run
REQUEST_DELAY_SECONDS = 1.1  # stays under the free plan's 10 requests/minute

# Priority order (see workflows/bet_of_the_day.md "Fixture Priority Policy"):
#   1. Romania Liga I (league id 283) — included first if a fixture exists.
#   2-4. UCL / UEL / UECL, any stage (round is carried in each fixture's
#        "league" field, e.g. "Play-offs", "Group Stage", so no separate
#        qualification-vs-main league id is needed the way The Odds API had).
#   5. Top club leagues by value/popularity: Premier League, La Liga, Serie
#      A, Bundesliga, Ligue 1 (in that order).
PRIORITY_CLUB_LEAGUES = [
    283,  # Liga I (România)
    2,    # UEFA Champions League
    3,    # UEFA Europa League
    848,  # UEFA Europa Conference League
    39,   # Premier League
    140,  # La Liga
    135,  # Serie A
    78,   # Bundesliga
    61,   # Ligue 1
]

# Broader club leagues — tried if the priority set alone is thin, but this
# still isn't enough evidence of an international break on its own.
BROADER_CLUB_LEAGUES = [
    88,   # Eredivisie
    94,   # Primeira Liga
    71,   # Brasileirão Série A
    253,  # MLS
    262,  # Liga MX
    128,  # Liga Profesional Argentina
]

# International fixtures — used ONLY when both club tiers above come back
# thin, which is the signal for "this is an international break." Per policy,
# these REPLACE the club pool rather than blend with it once triggered. Any
# level of any international competition counts (finals or qualifiers, any
# confederation), not just UEFA competitions.
INTERNATIONAL_LEAGUES = [
    1,    # FIFA World Cup
    32,   # World Cup Qualifying (Europe)
    34,   # World Cup Qualifying (South America)
    4,    # UEFA Euro
    960,  # Euro Qualifying
    5,    # UEFA Nations League
    9,    # Copa América
    6,    # Africa Cup of Nations
    22,   # CONCACAF Gold Cup
]
# NOTE: international FRIENDLIES are not in this list. API-Football does
# carry friendlies (as a "Friendlies" league), but a fixture there could be
# a national-team friendly OR a club pre-season friendly with no reliable way
# to tell them apart from the league id alone — deliberately left out to
# avoid mis-classifying a club friendly as an international-break fixture.
# See workflow Learnings if a friendlies-only break needs to be handled.

LEAGUE_NAMES = {
    283: "Liga I (România)",
    2: "UEFA Champions League",
    3: "UEFA Europa League",
    848: "UEFA Europa Conference League",
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    78: "Bundesliga",
    61: "Ligue 1",
    88: "Eredivisie",
    94: "Primeira Liga",
    71: "Brasileirão Série A",
    253: "MLS",
    262: "Liga MX",
    128: "Liga Profesional Argentina",
    1: "FIFA World Cup",
    32: "World Cup Qualifying (Europe)",
    34: "World Cup Qualifying (South America)",
    4: "UEFA Euro",
    960: "Euro Qualifying",
    5: "UEFA Nations League",
    9: "Copa América",
    6: "Africa Cup of Nations",
    22: "CONCACAF Gold Cup",
}

_request_count = 0


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def api_get(endpoint, api_key, params):
    global _request_count
    url = f"{API_BASE}/{endpoint}"
    headers = {"x-apisports-key": api_key}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.exceptions.Timeout:
        fail(f"Request to API-Football timed out for {endpoint}.")
    except requests.exceptions.ConnectionError as exc:
        fail(f"Could not connect to API-Football: {exc}")
    _request_count += 1

    if response.status_code == 401:
        fail("API-Football rejected the request (401): invalid API_FOOTBALL_KEY.")
    if response.status_code != 200:
        warn(f"API-Football returned {response.status_code} for {endpoint}: {response.text[:200]}")
        return {}

    data = response.json()
    errors = data.get("errors")
    if errors:
        warn(f"API-Football reported errors for {endpoint} {params}: {errors}")
    return data


def fetch_fixtures_for_date(api_key, target_date):
    data = api_get("fixtures", api_key, {
        "date": target_date.isoformat(),
        "timezone": "Europe/Bucharest",
    })
    return data.get("response", [])


def best_market(bookmakers, bet_name):
    best = {}
    for bookmaker in bookmakers:
        for bet in bookmaker.get("bets", []):
            if bet.get("name") != bet_name:
                continue
            for value in bet.get("values", []):
                name = value.get("value")
                try:
                    price = float(value.get("odd"))
                except (TypeError, ValueError):
                    continue
                if name not in best or price > best[name]["price"]:
                    best[name] = {"price": price, "book": bookmaker.get("name", "")}
    return best


def extract_odds(bookmakers):
    mw = best_market(bookmakers, "Match Winner")
    h2h = None
    if mw:
        h2h = {}
        if "Home" in mw:
            h2h["home"] = mw["Home"]
        if "Draw" in mw:
            h2h["draw"] = mw["Draw"]
        if "Away" in mw:
            h2h["away"] = mw["Away"]

    totals_raw = best_market(bookmakers, "Goals Over/Under")
    totals_by_point = {}
    for value, info in totals_raw.items():
        parts = value.split(" ", 1)
        if len(parts) != 2:
            continue
        side, line_str = parts[0].lower(), parts[1]
        try:
            line = float(line_str)
        except ValueError:
            continue
        totals_by_point.setdefault(line, {})[side] = info
    totals = [
        {"point": point, "over": vals.get("over"), "under": vals.get("under")}
        for point, vals in sorted(totals_by_point.items())
    ]

    btts_raw = best_market(bookmakers, "Both Teams Score")
    btts = None
    if btts_raw:
        btts = {}
        if "Yes" in btts_raw:
            btts["yes"] = btts_raw["Yes"]
        if "No" in btts_raw:
            btts["no"] = btts_raw["No"]

    dc_raw = best_market(bookmakers, "Double Chance")
    double_chance = None
    if dc_raw:
        double_chance = {}
        if "Home/Draw" in dc_raw:
            double_chance["home_or_draw"] = dc_raw["Home/Draw"]
        if "Draw/Away" in dc_raw:
            double_chance["away_or_draw"] = dc_raw["Draw/Away"]
        if "Home/Away" in dc_raw:
            double_chance["home_or_away"] = dc_raw["Home/Away"]

    cs_raw = best_market(bookmakers, "Exact Score")
    correct_score = None
    if cs_raw:
        scored = []
        for value, info in cs_raw.items():
            if ":" not in value:
                continue
            home_str, away_str = value.split(":", 1)
            if not (home_str.isdigit() and away_str.isdigit()):
                continue
            scored.append({
                "home": int(home_str), "away": int(away_str),
                "price": info["price"], "book": info["book"],
            })
        scored.sort(key=lambda x: x["price"])  # lowest price first = most likely
        correct_score = scored[:10]

    return {
        "h2h": h2h,
        "totals": totals,
        "btts": btts,
        "double_chance": double_chance,
        "correct_score": correct_score,
    }


def extract_predictions(predictions_response):
    if not predictions_response:
        return None
    pred = predictions_response[0].get("predictions", {})
    winner = pred.get("winner") or {}
    return {
        "winner_name": winner.get("name"),
        "winner_comment": winner.get("comment"),
        "win_or_draw": pred.get("win_or_draw"),
        "under_over": pred.get("under_over"),
        "advice": pred.get("advice"),
        "goals_expectation": pred.get("goals"),
        "percent": pred.get("percent"),
    }


def enrich_fixture(api_key, fx):
    fixture_id = fx["fixture"]["id"]

    time.sleep(REQUEST_DELAY_SECONDS)
    odds_data = api_get("odds", api_key, {"fixture": fixture_id})
    odds_resp = odds_data.get("response") or []
    bookmakers = odds_resp[0]["bookmakers"] if odds_resp else []

    time.sleep(REQUEST_DELAY_SECONDS)
    pred_data = api_get("predictions", api_key, {"fixture": fixture_id})
    prediction = extract_predictions(pred_data.get("response"))

    commence_utc = fx["fixture"]["date"]
    dt_utc = datetime.fromisoformat(commence_utc.replace("Z", "+00:00"))
    dt_local = dt_utc.astimezone(ROMANIA_TZ)

    league = fx["league"]
    league_label = LEAGUE_NAMES.get(league["id"], league.get("name"))
    if league.get("round"):
        league_label = f"{league_label} — {league['round']}"

    return {
        "fixture_id": fixture_id,
        "league": league_label,
        "league_id": league["id"],
        "home_team": fx["teams"]["home"]["name"],
        "away_team": fx["teams"]["away"]["name"],
        "commence_time_utc": commence_utc,
        "commence_time_local": dt_local.strftime("%H:%M"),
        "odds": extract_odds(bookmakers),
        "prediction": prediction,
    }


def collect_tier(api_key, all_fixtures_today, league_ids, budget_remaining):
    candidates = [fx for fx in all_fixtures_today if fx["league"]["id"] in league_ids]
    # enrich in priority order: earlier entries in league_ids first
    candidates.sort(key=lambda fx: league_ids.index(fx["league"]["id"]))

    enriched = []
    for fx in candidates:
        if budget_remaining <= 0:
            warn(
                f"Hit MAX_FIXTURES_TO_ENRICH ({MAX_FIXTURES_TO_ENRICH}) — "
                f"skipping {len(candidates) - len(enriched)} more candidate(s) in this tier."
            )
            break
        enriched.append(enrich_fixture(api_key, fx))
        budget_remaining -= 1
    return enriched, budget_remaining


def main():
    parser = argparse.ArgumentParser(description="Fetch today's football odds and predictions from API-Football.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (Europe/Bucharest). Default: today.")
    parser.add_argument("--output", default=None, help="Output JSON path. Default: .tmp/odds/odds-<date>.json")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        fail(
            "API_FOOTBALL_KEY is not set in .env. Sign up free (no card required) at "
            "https://www.api-football.com and add the key before running."
        )

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(ROMANIA_TZ).date()
    )

    all_fixtures_today = fetch_fixtures_for_date(api_key, target_date)
    if not all_fixtures_today:
        warn(
            "API-Football returned zero fixtures for this date. Note: the free plan only allows "
            "fixture lookups within a rolling ~3-day window around today — a date outside that "
            "window will always come back empty regardless of real-world fixtures."
        )

    budget_remaining = MAX_FIXTURES_TO_ENRICH
    international_break_mode = False

    fixtures, budget_remaining = collect_tier(api_key, all_fixtures_today, PRIORITY_CLUB_LEAGUES, budget_remaining)
    leagues_used = list(PRIORITY_CLUB_LEAGUES) if fixtures else []

    if len(fixtures) < MIN_FIXTURES_BEFORE_FALLBACK:
        warn(
            f"Only {len(fixtures)} fixture(s) found in the priority league set "
            f"(need >= {MIN_FIXTURES_BEFORE_FALLBACK}) — adding broader club leagues."
        )
        broader_fixtures, budget_remaining = collect_tier(api_key, all_fixtures_today, BROADER_CLUB_LEAGUES, budget_remaining)
        fixtures += broader_fixtures
        leagues_used += BROADER_CLUB_LEAGUES

    if len(fixtures) < MIN_FIXTURES_BEFORE_FALLBACK:
        warn(
            f"Still only {len(fixtures)} fixture(s) even with broader club leagues — this looks "
            "like an international break. Switching to international fixtures ONLY (replacing "
            "the thin club pool, not blending with it)."
        )
        international_break_mode = True
        budget_remaining = MAX_FIXTURES_TO_ENRICH  # fresh budget for the replacement pool
        fixtures, budget_remaining = collect_tier(api_key, all_fixtures_today, INTERNATIONAL_LEAGUES, budget_remaining)
        leagues_used = list(INTERNATIONAL_LEAGUES)

        if not fixtures:
            warn(
                "Zero fixtures even across all international tournaments — this window likely has "
                "ONLY friendlies. This tool deliberately doesn't auto-include the 'Friendlies' "
                "league id (can't distinguish national-team friendlies from club pre-season "
                "friendlies by league id alone) — see workflow Learnings for how to handle this "
                "manually if it comes up."
            )

    result = {
        "date": target_date.isoformat(),
        "generated_at": datetime.now(ROMANIA_TZ).isoformat(),
        "leagues_queried": sorted(set(leagues_used)),
        "international_break_mode": international_break_mode,
        "fixture_count": len(fixtures),
        "api_requests_used_this_run": _request_count,
        "fixtures": fixtures,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / "odds" / f"odds-{target_date.isoformat()}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved odds to: {output_path}")
    print(f"Fixtures found for {target_date.isoformat()}: {len(fixtures)}")
    print(f"API-Football requests used this run: {_request_count}")
    if international_break_mode:
        print("International break mode: fixtures are international-only (club leagues were too thin).")
    if not fixtures:
        warn("Zero fixtures found even after all fallbacks — check the date and API-Football status.")


if __name__ == "__main__":
    main()
