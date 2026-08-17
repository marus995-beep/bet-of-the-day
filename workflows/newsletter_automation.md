# Newsletter Automation

## Objective

Given a topic from the user, research it, structure it into a data-grounded narrative with a few infographics, render it as a single polished self-contained HTML file, publish it as a shareable Claude Artifact, and report both deliverables back.

## Required Inputs

- **Topic** (from the user — required).
- If the request doesn't already imply them, ask **one compact combined question** with sensible defaults stated inline, e.g.:
  > "Quick check before I dig in — general audience, concise/analytical tone, ~4 min read, 2-3 infographics, sources from the past month. Sound right, or want me to adjust any of that?"
  Skip the question entirely if the user's request already gives enough signal (e.g. they specify tone, length, or audience up front). This is a personal tool — don't interrogate.

## Tools Used

- `tools/research_perplexity.py` — one Perplexity Sonar API call per invocation; returns raw cited text, does not synthesize.
- `tools/render_newsletter.py` — renders a content-spec JSON into `newsletters/<slug>-<date>.html` using the templates in `tools/templates/`.
- The built-in `Artifact` tool (not a `tools/` script) — publishes the rendered HTML as a shareable link.

## Steps

1. **Clarify inputs** if needed (see above).
2. **Research.** Call `research_perplexity.py` 1-3 times: one broad overview query plus agent-chosen sub-angles/follow-ups worth covering. Save each result under `.tmp/research/`. Recency defaults to `month` unless the topic calls for something else (e.g. `year` for a slower-moving topic, `week` for fast-breaking news).
3. **Quality gate.** Check each result's `citation_count`, `warning`, and answer length. If a result looks thin, retry **once** with a rephrased or broadened query before escalating to the user — don't loop indefinitely (each retry is a paid API call).
4. **Synthesize and structure** (pure reasoning, no tool). Read all research JSON. Build a numbered source list. Draft a headline, dek, and section flow. Pick 2-3 concrete numbers worth visualizing and map each to the section type that already fits: `stat_row` for standalone figures, `comparison` for 2-3 things measured the same way, `trend` for a series over time, `quote` for a notable direct quote, `list` for a scannable set of points. Copy numbers **verbatim** from the research `answer` text — don't recompute or round them — and spot-check each against its citation before writing it down.
5. **Write the content-spec** to `.tmp/newsletter_spec_<slug>.json`. Shape:
   ```json
   {
     "meta": {
       "topic": "...", "title": "...", "dek": "...",
       "read_time_minutes": 4, "date": "YYYY-MM-DD",
       "sources": [{"index": 1, "title": "...", "url": "https://..."}]
     },
     "sections": [
       {"type": "narrative", "heading": "optional", "body": "paragraph text; use \\n\\n between paragraphs"},
       {"type": "stat_row", "stats": [{"label": "...", "value": "17.1M", "delta": "+23% YoY", "delta_direction": "up", "source_index": 1}]},
       {"type": "comparison", "unit": "%", "items": [{"label": "...", "value": 42, "source_index": 1}]},
       {"type": "trend", "label": "...", "unit": "GWh", "points": [2,4,6], "point_labels": ["2023","2024","2025"], "source_index": 1},
       {"type": "quote", "text": "...", "attribution": "...", "source_index": 1},
       {"type": "list", "icon": "check", "items": ["...", "..."]}
     ]
   }
   ```
   Every stat/comparison-item/trend/quote **must** carry a `source_index` pointing to an entry in `meta.sources` — this is enforced by the render tool. Comparison sections take 2-3 items max; fold smaller items into an "Other" bucket rather than adding a 4th.
6. **Render.** Call `render_newsletter.py --content-spec .tmp/newsletter_spec_<slug>.json`. On a validation error, fix the spec and retry — this is expected iteration, not a failure worth mentioning to the user.
7. **Sanity-check** the output using the tool's stdout (file path, word count, infographic count) — make sure it's non-trivial.
8. **Publish.** Call the `Artifact` tool on the rendered HTML file. Pick a favicon emoji that fits the topic (e.g. 📰 as a stable newsletter identity, or something topic-specific). If publishing fails, still treat the local file as delivered and tell the user publishing can be retried.
9. **Report back**: local file path, Artifact link, a 1-2 sentence summary, source count, and any edge cases hit (e.g. "used month-old sources; nothing newer existed").

## Output

- `newsletters/<slug>-<date>.html` (the deliverable file)
- A published Claude Artifact link
- A short chat summary

## Edge Cases

- **`PERPLEXITY_API_KEY` missing** — the research tool stops immediately with a clear message. Don't fabricate research to route around it; tell the user to add the key.
- **Thin/empty Perplexity results** — retry once with a broadened/rephrased query. If still thin, tell the user the topic may be too niche or too recent, and ask them to broaden it or supply seed sources.
- **Topic too broad** — narrow it proactively via the clarifying question; if the user insists on the broad framing, pick a defensible angle and say so in the report-back.
- **Topic too narrow** — same retry-then-ask pattern as thin results.
- **Persistent API errors / rate limiting** — the tool retries twice internally; if still failing, stop and tell the user rather than shipping a newsletter with unverifiable content.
- **Content-spec validation failure** — this is expected mid-run iteration (the agent's own formatting slip), not a user-facing failure. Fix and retry silently.
- **Fewer usable stats than the target infographic count** — render fewer infographics. Never invent a number to hit a quota. Mention it in the report-back.
- **Artifact publish fails** — still report the local file as delivered; note that publishing can be retried.

## Learnings

- Jinja2 dict access: any content-spec key named `items` must be read in the template via `section["items"]`, not `section.items` — dot-access silently resolves to `dict.items()` (the bound method) instead of the key, which raises a "not iterable" `TypeError` at render time. Any future macro that introduces a new key colliding with a dict method name (`keys`, `values`, `get`, `update`, ...) will hit the same trap.
- The rendered HTML intentionally omits `<!DOCTYPE>`, `<html>`, `<head>`, and `<body>` tags — just a leading `<meta charset>`, a `<title>`, a `<style>` block, and body markup. This is what the `Artifact` tool expects to wrap directly, and browsers render the fragment fine when the file is opened locally too, so one render output serves both delivery targets without a second template or a post-processing step.
