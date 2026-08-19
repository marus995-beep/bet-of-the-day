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
  1. The /ponturi/fotbal listing, PAGINATED (/pagina-2, /pagina-3, ...),
     which embeds fixtures as schema.org SportsEvent JSON-LD (teams, league,
     kickoff time, a detail page URL, and — usefully — a "description" field
     that turned out to carry the real stat-driven analysis text, e.g.
     recent form, head-to-head goal counts). Pagination is NOT strictly
     chronological by kickoff — it's closer to publish order, so a single
     day's fixtures can be split across the first couple of pages before
     later pages roll into older, unrelated dates (confirmed live: one day's
     17 fixtures were split 7 on page 1 / 10 on page 2, with page 3 pure
     history). collect_events_for_date() pages through and stops once it's
     confident it's past the target date's content — see MIN_PAGES_ALWAYS_SCANNED.
  2. Each fixture's own detail page, scraped only for what the listing
     doesn't have: the pick + tipster name (from the page's
     <meta name="description">, format "{match} pont: {pick}, adaugat de
     {tipster}, pe {date}"), the cotă (a specific div), and the bookmaker
     name (an image alt attribute). Its own "tips-section-analysis" div was
     tried first for the analysis text but turned out to be a thinner,
     more boilerplate blurb than the listing's "description" field — not
     used for that anymore.

Fragile by nature (HTML scraping, not a real API) — if pariurix changes its
page structure, this will need updating. Does NOT get blocked by Cloudflare
(confirmed live, unlike some other tipster sites checked first) but is still
scraping someone else's website — keep this to once a day, not hammered.

By default this is STRICT about the date: only fixtures whose kickoff falls
on exactly --date are returned, nothing else — if pariurix hasn't published
for that date, the result is legitimately zero predictions, not a different
day's games. Pass --allow-fallback-date to opt into substituting the nearest
later published date instead (useful for manual testing, not used in the
daily routine, since "today's bet" must actually be today's games).

Usage:
    python tools/fetch_pariurix_predictions.py [--date 2026-08-21] \
        [--max-fetches 20] [--allow-fallback-date] [--output .tmp/pariurix/predictions-2026-08-21.json]
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
LISTING_URL = f"{BASE_URL}/ponturi/fotbal"  # football only — /ponturi mixes in tennis/other sports
ROMANIA_TZ = ZoneInfo("Europe/Bucharest")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.5  # politeness between detail-page fetches
MAX_DETAIL_FETCHES = 20  # safety cap per run
MAX_LISTING_PAGES = 15  # safety cap on listing pagination (site has hundreds of pages of history)


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


def listing_page_url(page_num):
    return LISTING_URL if page_num == 1 else f"{LISTING_URL}/pagina-{page_num}"


MIN_PAGES_ALWAYS_SCANNED = 2  # today's fixtures were empirically split across pages 1-2


def collect_events_for_date(target_date_str, max_pages):
    """Page through the listing collecting every event on target_date_str.
    Pagination here is NOT purely chronological by kickoff — it's closer to
    publish order, so a single day's fixtures can be split across a couple
    of pages before the pagination rolls into older, unrelated dates
    (confirmed live: today's games were split across pages 1 and 2, with
    page 3 onward pure history). Always scans at least MIN_PAGES_ALWAYS_SCANNED
    pages regardless of early zero-match pages, then stops at the first empty
    page after that. Returns (matching_events, all_dates_seen_across_scanned_pages)."""
    matching = []
    all_dates_seen = set()
    for page_num in range(1, max_pages + 1):
        if page_num > 1:
            time.sleep(REQUEST_DELAY_SECONDS)
        page_html = fetch_page(listing_page_url(page_num), critical=(page_num == 1))
        if page_html is None:
            break
        events = parse_listing(page_html)
        if not events:
            break
        all_dates_seen.update(e["startDate"][:10] for e in events if "startDate" in e)
        page_matches = [e for e in events if e.get("startDate", "")[:10] == target_date_str]
        matching.extend(page_matches)
        if not page_matches and page_num >= MIN_PAGES_ALWAYS_SCANNED:
            break
    return matching, sorted(all_dates_seen)


def extract_detail(detail_html):
    """Pick, tipster, odds, and bookmaker only — the analysis text comes from
    the listing page's JSON-LD "description" field instead (see main()),
    which turned out to carry the real stat-driven analysis; this page's own
    "tips-section-analysis" div is a thinner, more boilerplate blurb by
    comparison."""
    pick, tipster, odds, bookmaker = None, None, None, None

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

    return pick, tipster, odds, bookmaker


def main():
    parser = argparse.ArgumentParser(description="Scrape football tipster picks from pariurix.com for a date.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (Europe/Bucharest). Default: today.")
    parser.add_argument("--max-fetches", type=int, default=MAX_DETAIL_FETCHES,
                         help=f"Cap on detail-page fetches per run. Default: {MAX_DETAIL_FETCHES}.")
    parser.add_argument("--allow-fallback-date", action="store_true",
                         help="Fall back to the nearest published date if --date has no picks. "
                              "Off by default — the caller gets strictly --date's games or nothing.")
    parser.add_argument("--output", default=None, help="Output JSON path. Default: .tmp/pariurix/predictions-<date>.json")
    args = parser.parse_args()

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(ROMANIA_TZ).date()
    )

    requested_date = target_date.isoformat()
    matching, available_dates = collect_events_for_date(requested_date, MAX_LISTING_PAGES)

    if not matching and args.allow_fallback_date:
        # Tips are typically published a day or more ahead of kickoff, so
        # "today" is often empty — fall back to the nearest LATER published
        # date rather than silently returning nothing every morning.
        future_dates = [d for d in available_dates if d > requested_date]
        if future_dates:
            target_date = date_cls.fromisoformat(future_dates[0])
            warn(
                f"No pariurix picks for {requested_date} — falling back to the nearest "
                f"published date, {target_date.isoformat()}."
            )
            matching, more_dates = collect_events_for_date(target_date.isoformat(), MAX_LISTING_PAGES)
            available_dates = sorted(set(available_dates) | set(more_dates))

    if not matching:
        warn(
            f"No pariurix picks found for {requested_date}"
            + (" (fallback was not allowed)." if not args.allow_fallback_date else " even after fallback.")
            + f" Dates currently published on pariurix.com: {available_dates or '(none found)'}."
        )

    seen_urls = set()
    deduped = []
    for event in matching:
        url = event.get("url")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        deduped.append(event)
    if len(deduped) != len(matching):
        warn(f"Removed {len(matching) - len(deduped)} duplicate fixture(s) seen across multiple listing pages.")
    matching = deduped

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

        pick, tipster, odds, bookmaker = extract_detail(detail_html)
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
            "analysis_raw": event.get("description"),
            "source_url": detail_url,
        })

    result = {
        "date": target_date.isoformat(),
        "date_requested": requested_date,
        "used_fallback_date": target_date.isoformat() != requested_date,
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
    if result["used_fallback_date"]:
        print(f"Note: requested {requested_date} had no picks; used {target_date.isoformat()} instead.")
    if not matching:
        print(f"No picks found at all — available dates were: {available_dates}")


if __name__ == "__main__":
    main()
