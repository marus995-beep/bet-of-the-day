#!/usr/bin/env python3
"""Fetch cited research on a query from the Perplexity Sonar API.

Writes raw, unsummarized results to a JSON file. Does not synthesize or
extract stats — that's left to the agent reading the output.

Usage:
    python tools/research_perplexity.py --query "..." [--recency month] \
        [--model sonar-pro] [--output .tmp/research/foo.json]
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://api.perplexity.ai/chat/completions"
VALID_RECENCY = {"day", "week", "month", "year"}
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2


def slugify(text, max_len=50):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "query"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def call_perplexity(api_key, query, model, recency):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a research assistant. Answer thoroughly and "
                    "factually, citing sources. Include concrete numbers, "
                    "dates, and figures where available."
                ),
            },
            {"role": "user", "content": query},
        ],
        "search_recency_filter": recency,
    }

    attempt = 0
    while True:
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
        except requests.exceptions.Timeout:
            fail("Request to Perplexity API timed out.")
        except requests.exceptions.ConnectionError as exc:
            fail(f"Could not connect to Perplexity API: {exc}")

        if response.status_code == 200:
            return response.json()

        if response.status_code in (401, 403):
            fail(
                f"Perplexity API rejected the request ({response.status_code}): "
                "invalid or unauthorized API key."
            )

        if response.status_code == 429 or response.status_code >= 500:
            attempt += 1
            if attempt > MAX_RETRIES:
                fail(
                    f"Perplexity API still failing after {MAX_RETRIES} retries "
                    f"(status {response.status_code}): {response.text[:300]}"
                )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        fail(
            f"Perplexity API returned status {response.status_code}: "
            f"{response.text[:300]}"
        )


def normalize_citations(raw_response):
    """Perplexity's citation field has shifted across API/model versions.
    Handle both a flat `citations` list of URL strings and a richer
    `search_results` list of {title, url, date} objects."""
    citations = []

    search_results = raw_response.get("search_results")
    if isinstance(search_results, list) and search_results:
        for i, item in enumerate(search_results, start=1):
            if isinstance(item, dict):
                citations.append(
                    {
                        "index": i,
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                    }
                )
        return citations

    flat_citations = raw_response.get("citations")
    if isinstance(flat_citations, list):
        for i, url in enumerate(flat_citations, start=1):
            citations.append({"index": i, "url": url, "title": ""})

    return citations


def main():
    parser = argparse.ArgumentParser(description="Research a query via the Perplexity Sonar API.")
    parser.add_argument("--query", required=True, help="The research question to ask.")
    parser.add_argument(
        "--recency",
        default="month",
        choices=sorted(VALID_RECENCY),
        help="How recent sources should be. Default: month.",
    )
    parser.add_argument("--model", default="sonar-pro", help="Perplexity model. Default: sonar-pro.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path. Default: .tmp/research/<slug>-<timestamp>.json",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        fail(
            "PERPLEXITY_API_KEY is not set in .env. Add it before running research "
            "(https://www.perplexity.ai/settings/api)."
        )

    raw_response = call_perplexity(api_key, args.query, args.model, args.recency)

    try:
        answer = raw_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        answer = ""

    citations = normalize_citations(raw_response)

    warning = None
    if not answer.strip():
        warning = "Perplexity returned an empty answer."
    elif not citations:
        warning = "Perplexity returned an answer with zero citations."

    if warning:
        warn(warning)

    result = {
        "query": args.query,
        "model": args.model,
        "recency_filter": args.recency,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "answer": answer,
        "citations": citations,
        "citation_count": len(citations),
        "warning": warning,
        "raw_response": raw_response,
    }

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = REPO_ROOT / output_path
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = REPO_ROOT / ".tmp" / "research" / f"{slugify(args.query)}-{timestamp}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved research to: {output_path}")
    print(f"Citations: {len(citations)}")
    print(f"Answer length: {len(answer)} chars")
    if warning:
        print(f"Warning: {warning}")


if __name__ == "__main__":
    main()
