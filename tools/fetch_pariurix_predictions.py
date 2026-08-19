#!/usr/bin/env python3
"""Fetch football tipster predictions (pick, cotă, bookmaker, analysis) from
pariurix.com for a given date.

This is a DIFFERENT kind of source than fetch_football_odds.py: instead of
raw bookmaker market prices, pariurix publishes one tipster's chosen pick per
fixture, with a single cotă attributed to a named bookmaker, plus a written
analysis. That analysis is human editorial opinion, not verified fact — the
same sourcing discipline that applies to WebSearch results applies here even
more strongly: never restate "analysis_raw" as our own established fact
without independent cross-checking. Attribute it ("conform tipsterului X on
pariurix.com") if it's used at all.

No public API — this scrapes two kinds of pages:
  1. The /ponturi listing, which embeds today's-and-upcoming fixtures as
     schema.org SportsEvent JSON-LD (teams, league, kickoff time, a detail
     page URL). This is NOT limited to "today" — observed covering a rolling
     multi-day window a few days out, so a given date (including today)
     may come back with zero picks if pariurix hasn't published for it yet.
  2. Each fixture's own detail page, scraped for: the pick + tipster name
     (from the page's <meta name="description">, format "{match} pont:
     {pick}, adaugat de {tipster}, pe {date}"), the cotă (a specific div),
     the bookmaker name (an image alt attribute), and the analysis text.

Fragile by nature (HTML scraping, not a real API) — if pariurix changes its
page structure, this will need updating. Does NOT get blocked by Cloudflare
(confirmed live, unlike some other tipster sites checked first) but is still
scraping someone else's website — keep this to once a day, not hammered.

Usage:
    python tools/fetch_pariurix_predictions.py [--date 2026-08-21] \
        [--max-fetches 20] [--output .tmp/pariurix/predictions-2026-08-21.json]
"""

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, date as date_cls
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://pariurix.com"
LISTING_URL = f"{BASE_URL}/ponturi"
ROMANIA_TZ = ZoneInfo("Europe/Bucharest")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.5  # politeness between detail-page fetches
MAX_DETAIL_FETCHES = 20  # safety cap per run


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def fetch_page(url, critical=True):
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.exceptions.Timeout:
        message = f"Request to {url} timed out."
        fail(message) if critical else warn(message)
        return None
    except requests.exceptions.ConnectionError as exc:
        message = f"Could not connect to {url}: {exc}"
        fail(message) if critical else warn(message)
        return None
    if response.status_code != 200:
        message = f"{url} returned {response.status_code} (expected 200)."
        if critical:
            fail(message)
        warn(message)
        return None
    return response.text


def parse_listing(listing_html):
    start = listing_html.find("[{")
    end = listing_html.find("</script>", start)
    if start == -1 or end == -1:
        fail("Could not find the JSON-LD event listing on the pariurix.com page "
             "— the site's page structure has likely changed; this scraper needs updating.")
    raw = listing_html[start:end].strip()
    try:
        events = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"pariurix.com's JSON-LD listing was not valid JSON ({exc}) — page structure likely changed.")
    return [e for e in events if e.get("@type") == "SportsEvent"]


def strip_html(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_detail(detail_html, home_team=None, away_team=None):
    pick, tipster, odds, bookmaker, analysis = None, None, None, None, None

    m = re.search(r'<meta name="description" content="(.*?)"', detail_html, re.S)
    if m:
        desc = html.unescape(m.group(1))
        m2 = re.search(r"pont:\s*(.*?),\s*adaugat de\s*(.*?),", desc)
        if m2:
            pick, tipster = m2.group(1).strip(), m2.group(2).strip()

    m3 = re.search(
        r'<div class="text-lg text-secondary font-bold bg-light-primary px-2 py-1 rounded">([\d.,]+)</div>',
        detail_html,
    )
    if m3:
        try:
            odds = float(m3.group(1).replace(",", "."))
        except ValueError:
            odds = None

    m4 = re.search(r'alt="([^"]+)"\s+src="[^"]*\/media\/agentii\/[^"]*"', detail_html)
    if m4:
        bookmaker = m4.group(1)

    m5 = re.search(
        r'<div class="tips-section-analysis">(.*?)</div>\s*</div>', detail_html, re.S
    )
    if m5:
        analysis = strip_html(m5.group(1))
        # the section's own <h2> heading ("Analiză și informații pentru X v Y")
        # is redundant once the match is already shown elsewhere — drop it,
        # using the known team names for an exact (not heuristic) match.
        if home_team and away_team:
            heading = f"Analiză și informații pentru {home_team} v {away_team}"
            if analysis.startswith(heading):
                analysis = analysis[len(heading):].strip()

    return pick, tipster, odds, bookmaker, analysis


def main():
    parser = argparse.ArgumentParser(description="Scrape football tipster picks from pariurix.com for a date.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (Europe/Bucharest). Default: today.")
    parser.add_argument("--max-fetches", type=int, default=MAX_DETAIL_FETCHES,
                         help=f"Cap on detail-page fetches per run. Default: {MAX_DETAIL_FETCHES}.")
    parser.add_argument("--output", default=None, help="Output JSON path. Default: .tmp/pariurix/predictions-<date>.json")
    args = parser.parse_args()

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(ROMANIA_TZ).date()
    )

    listing_html = fetch_page(LISTING_URL)
    events = parse_listing(listing_html)

    matching = [e for e in events if e.get("startDate", "")[:10] == target_date.isoformat()]
    available_dates = sorted(set(e["startDate"][:10] for e in events if "startDate" in e))

    if not matching:
        warn(
            f"No pariurix picks found for {target_date.isoformat()}. Dates currently "
            f"published on pariurix.com: {available_dates or '(none found)'}. Tips seem to "
            "be published a day or more ahead of kickoff — this is expected, not a bug; "
            "the caller should decide whether to use a different date or skip this source today."
        )

    predictions = []
    for i, event in enumerate(matching):
        if i >= args.max_fetches:
            warn(f"Hit --max-fetches ({args.max_fetches}) — skipping {len(matching) - i} more fixture(s).")
            break

        detail_url = event.get("url")
        if not detail_url:
            warn(f"Fixture {event.get('name')!r} has no detail page URL — skipping.")
            continue

        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        detail_html = fetch_page(detail_url, critical=False)
        if detail_html is None:
            warn(f"Could not fetch detail page for {event.get('name')!r} — skipping.")
            continue

        competitors = event.get("competitor", [])
        home_team = competitors[0]["name"] if len(competitors) > 0 else None
        away_team = competitors[1]["name"] if len(competitors) > 1 else None

        pick, tipster, odds, bookmaker, analysis = extract_detail(detail_html, home_team, away_team)
        if pick is None or odds is None:
            warn(f"Could not extract pick/odds for {event.get('name')!r} from its detail page — skipping "
                 "(page structure may not match what this scraper expects).")
            continue

        predictions.append({
            "match": event.get("name"),
            "home_team": home_team,
            "away_team": away_team,
            "league": event.get("location", {}).get("name"),
            "commence_time_local": event.get("startDate"),
            "pick": pick,
            "tipster": tipster,
            "odds": odds,
            "bookmaker": bookmaker,
            "analysis_raw": analysis,
            "source_url": detail_url,
        })

    result = {
        "date": target_date.isoformat(),
        "generated_at": datetime.now(ROMANIA_TZ).isoformat(),
        "source": "pariurix.com",
        "sourcing_note": (
            "Picks, odds, and analysis are one tipster's opinion, scraped from a "
            "betting-tips site — NOT independently verified. 'analysis_raw' is "
            "attributed human commentary, not established fact; cross-check any "
            "specific claim (form, injuries, streaks) before using it in a rationale, "
            "same discipline as WebSearch-sourced context. There is no deterministic "
            "results source for fixtures that aren't also covered by API-Football "
            "(notably Romania Liga I/Superliga) — grading such a leg the next day is "
            "currently unsolved."
        ),
        "available_dates_on_site": available_dates,
        "prediction_count": len(predictions),
        "predictions": predictions,
    }

    output_path = Path(args.output) if args.output else REPO_ROOT / ".tmp" / "pariurix" / f"predictions-{target_date.isoformat()}.json"
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved {len(predictions)} pariurix prediction(s) to: {output_path}")
    if not matching:
        print(f"No picks for {target_date.isoformat()} — available dates were: {available_dates}")


if __name__ == "__main__":
    main()
