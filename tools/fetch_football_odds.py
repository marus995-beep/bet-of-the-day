#!/usr/bin/env python3
"""Fetch today's football fixtures and decimal odds from The Odds API.

Pulls 1X2 (match winner) and over/under 2.5 goals odds for the top European
leagues, filters to fixtures kicking off "today" in Europe/Bucharest, and
consolidates each fixture's best available price per outcome across bookmakers.

Does not pick games or markets — that's the agent's job, reading this output.

Usage:
    python tools/fetch_football_odds.py [--date 2026-08-17] \
        [--leagues soccer_epl,soccer_spain_la_liga] [--output .tmp/odds/foo.json]
"""

import argparse
import json
import os
import sys
from datetime import datetime, date as date_cls
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
API_BASE = "https://api.the-odds-api.com/v4"
ROMANIA_TZ = ZoneInfo("Europe/Bucharest")
MIN_FIXTURES_BEFORE_FALLBACK = 4

DEFAULT_LEAGUES = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
]

FALLBACK_LEAGUES = [
    "soccer_uefa_europa_conference_league",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_brazil_campeonato",
    "soccer_usa_mls",
    "soccer_mexico_ligamx",
    "soccer_argentina_primera_division",
    "soccer_conmebol_copa_libertadores",
]

LEAGUE_NAMES = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
    "soccer_netherlands_eredivisie": "Eredivisie",
    "soccer_portugal_primeira_liga": "Primeira Liga",
    "soccer_brazil_campeonato": "Brasileirão",
    "soccer_usa_mls": "MLS",
    "soccer_mexico_ligamx": "Liga MX",
    "soccer_argentina_primera_division": "Primera División (Arg)",
    "soccer_conmebol_copa_libertadores": "Copa Libertadores",
}


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def fetch_league_odds(api_key, league, markets):
    url = f"{API_BASE}/sports/{league}/odds/"
    params = {
        "apiKey": api_key,
        "regions": "eu",
        "markets": markets,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    try:
        response = requests.get(url, params=params, timeout=30)
    except requests.exceptions.Timeout:
        fail(f"Request to The Odds API timed out for league {league}.")
    except requests.exceptions.ConnectionError as exc:
        fail(f"Could not connect to The Odds API: {exc}")

    if response.status_code == 401:
        fail("The Odds API rejected the request (401): invalid ODDS_API_KEY.")
    if response.status_code == 429:
        fail("The Odds API rate limit / monthly credit quota exceeded (429).")
    if response.status_code == 404:
        warn(f"League key {league!r} not recognized by The Odds API (404) — skipping.")
        return []
    if response.status_code != 200:
        warn(f"The Odds API returned {response.status_code} for {league}: "
             f"{response.text[:200]} — skipping.")
        return []

    return response.json()


def best_h2h(event):
    best = {}
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome["name"]
                price = outcome["price"]
                if name not in best or price > best[name]["price"]:
                    best[name] = {"price": price, "book": bookmaker.get("title", "")}
    if not best:
        return None
    home, away = event.get("home_team"), event.get("away_team")
    result = {}
    for name, val in best.items():
        if name == home:
            result["home"] = val
        elif name == away:
            result["away"] = val
        elif name.lower() == "draw":
            result["draw"] = val
    return result or None


def best_totals(event):
    by_point = {}
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []):
                point = outcome.get("point")
                side = outcome["name"].lower()  # "over" / "under"
                price = outcome["price"]
                slot = by_point.setdefault(point, {})
                if side not in slot or price > slot[side]["price"]:
                    slot[side] = {"price": price, "book": bookmaker.get("title", "")}
    return [
        {"point": point, "over": vals.get("over"), "under": vals.get("under")}
        for point, vals in sorted(by_point.items(), key=lambda kv: (kv[0] is None, kv[0]))
    ]


def to_fixtures(events, league, target_date):
    fixtures = []
    for event in events:
        commence_utc = event.get("commence_time")
        if not commence_utc:
            continue
        dt_utc = datetime.fromisoformat(commence_utc.replace("Z", "+00:00"))
        dt_local = dt_utc.astimezone(ROMANIA_TZ)
        if dt_local.date() != target_date:
            continue
        h2h = best_h2h(event)
        totals = best_totals(event)
        if not h2h and not totals:
            continue
        fixtures.append({
            "id": event.get("id"),
            "league": LEAGUE_NAMES.get(league, league),
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
            "commence_time_utc": commence_utc,
            "commence_time_local": dt_local.strftime("%H:%M"),
            "h2h": h2h,
            "totals": totals,
        })
    return fixtures


def collect(api_key, leagues, markets, target_date):
    all_fixtures = []
    for league in leagues:
        events = fetch_league_odds(api_key, league, markets)
        all_fixtures.extend(to_fixtures(events, league, target_date))
    return all_fixtures


def main():
    parser = argparse.ArgumentParser(description="Fetch today's football odds from The Odds API.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (Europe/Bucharest). Default: today.")
    parser.add_argument("--leagues", default=None, help="Comma-separated Odds API league keys. Default: top European leagues.")
    parser.add_argument("--markets", default="h2h,totals", help="Comma-separated markets. Default: h2h,totals.")
    parser.add_argument("--output", default=None, help="Output JSON path. Default: .tmp/odds/odds-<date>.json")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        fail(
            "ODDS_API_KEY is not set in .env. Add it before running "
            "(free tier: https://the-odds-api.com)."
        )

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(ROMANIA_TZ).date()
    )

    leagues = [l.strip() for l in args.leagues.split(",")] if args.leagues else DEFAULT_LEAGUES
    used_fallback = False

    fixtures = collect(api_key, leagues, args.markets, target_date)

    if len(fixtures) < MIN_FIXTURES_BEFORE_FALLBACK and not args.leagues:
        warn(
            f"Only {len(fixtures)} fixture(s) found today in the primary league set "
            f"(need >= {MIN_FIXTURES_BEFORE_FALLBACK} to pick from) — adding fallback leagues."
        )
        used_fallback = True
        fixtures += collect(api_key, FALLBACK_LEAGUES, args.markets, target_date)
        leagues = leagues + FALLBACK_LEAGUES

    result = {
        "date": target_date.isoformat(),
        "generated_at": datetime.now(ROMANIA_TZ).isoformat(),
        "leagues_queried": leagues,
        "used_fallback_leagues": used_fallback,
        "fixture_count": len(fixtures),
        "fixtures": fixtures,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / "odds" / f"odds-{target_date.isoformat()}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved odds to: {output_path}")
    print(f"Fixtures found for {target_date.isoformat()}: {len(fixtures)}")
    if used_fallback:
        print("Added fallback leagues (primary leagues had too few fixtures today).")
    if not fixtures:
        warn("Zero fixtures found even after fallback — check the date and The Odds API status.")


if __name__ == "__main__":
    main()
