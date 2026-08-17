#!/usr/bin/env python3
"""Render a newsletter content-spec JSON into a single self-contained HTML file.

All visual design (palette, typography, mark specs) lives in the Jinja2
templates under tools/templates/ — this script validates the content-spec,
precomputes any derived rendering data (bar widths, sparkline coordinates,
delta glyphs), and renders. It never invents prose or numbers.

Usage:
    python tools/render_newsletter.py --content-spec .tmp/newsletter_spec_foo.json \
        [--output-dir newsletters/] [--slug foo]
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "tools" / "templates"

ALLOWED_SECTION_TYPES = {"narrative", "stat_row", "comparison", "trend", "quote", "list"}
ICON_GLYPHS = {"check": "✓", "arrow": "→", "star": "★", "dot": "•"}
COMPARISON_COLOR_VARS = ["--accent", "--accent-2", "--accent-3"]


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def slugify(text, max_len=50):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "newsletter"


def validate_spec(spec):
    errors = []

    if "meta" not in spec or not isinstance(spec["meta"], dict):
        errors.append("missing top-level 'meta' object")
    if "sections" not in spec or not isinstance(spec["sections"], list):
        errors.append("missing top-level 'sections' list")
    if errors:
        return errors

    meta = spec["meta"]
    if not meta.get("title"):
        errors.append("meta.title is required")
    sources = meta.get("sources")
    if not isinstance(sources, list):
        errors.append("meta.sources must be a list")
        sources = []
    valid_indices = set()
    for i, src in enumerate(sources):
        if not isinstance(src, dict) or "index" not in src or "url" not in src:
            errors.append(f"meta.sources[{i}] must have 'index' and 'url'")
            continue
        valid_indices.add(src["index"])

    def check_source_index(path, value):
        if value is None:
            errors.append(f"{path} missing 'source_index'")
        elif value not in valid_indices:
            errors.append(f"{path} source_index {value!r} not found in meta.sources")

    sections = spec["sections"]
    if not sections:
        errors.append("sections list is empty")

    for si, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"section[{si}] must be an object")
            continue
        s_type = section.get("type")
        if s_type not in ALLOWED_SECTION_TYPES:
            errors.append(
                f"section[{si}].type {s_type!r} is not one of {sorted(ALLOWED_SECTION_TYPES)}"
            )
            continue

        if s_type == "narrative":
            if not section.get("body"):
                errors.append(f"section[{si}] (narrative) missing 'body'")

        elif s_type == "stat_row":
            stats = section.get("stats")
            if not isinstance(stats, list) or not stats:
                errors.append(f"section[{si}] (stat_row) missing non-empty 'stats' list")
            else:
                for j, stat in enumerate(stats):
                    if not stat.get("label") or "value" not in stat:
                        errors.append(f"section[{si}].stats[{j}] needs 'label' and 'value'")
                    check_source_index(f"section[{si}].stats[{j}]", stat.get("source_index"))
                    if stat.get("delta") and stat.get("delta_direction") not in ("up", "down"):
                        errors.append(
                            f"section[{si}].stats[{j}] has 'delta' but 'delta_direction' "
                            "must be 'up' or 'down'"
                        )

        elif s_type == "comparison":
            items = section.get("items")
            if not isinstance(items, list) or not (2 <= len(items) <= 3):
                errors.append(
                    f"section[{si}] (comparison) needs 2-3 'items' "
                    "(fold smaller items into an 'Other' bucket rather than adding more)"
                )
                items = items or []
            for j, item in enumerate(items):
                if not item.get("label") or not isinstance(item.get("value"), (int, float)):
                    errors.append(f"section[{si}].items[{j}] needs 'label' and numeric 'value'")
                check_source_index(f"section[{si}].items[{j}]", item.get("source_index"))

        elif s_type == "trend":
            points = section.get("points")
            if not isinstance(points, list) or len(points) < 2:
                errors.append(f"section[{si}] (trend) needs a 'points' list of length >= 2")
                points = []
            labels = section.get("point_labels")
            if labels is not None and len(labels) != len(points):
                errors.append(f"section[{si}] point_labels length must match points length")
            if not section.get("label"):
                errors.append(f"section[{si}] (trend) missing 'label'")
            check_source_index(f"section[{si}]", section.get("source_index"))

        elif s_type == "quote":
            if not section.get("text"):
                errors.append(f"section[{si}] (quote) missing 'text'")
            check_source_index(f"section[{si}]", section.get("source_index"))

        elif s_type == "list":
            items = section.get("items")
            if not isinstance(items, list) or not items:
                errors.append(f"section[{si}] (list) missing non-empty 'items' list")

    return errors


def build_trend_svg(points, width=240, height=56, pad_x=6, pad_y=10):
    n = len(points)
    min_v, max_v = min(points), max(points)
    span = (max_v - min_v) or 1
    coords = []
    for i, p in enumerate(points):
        x = pad_x + ((i / (n - 1)) if n > 1 else 0) * (width - 2 * pad_x)
        y = height - pad_y - ((p - min_v) / span) * (height - 2 * pad_y)
        coords.append((round(x, 1), round(y, 1)))
    polyline = " ".join(f"{x},{y}" for x, y in coords)
    dots = [{"x": x, "y": y, "is_last": i == n - 1} for i, (x, y) in enumerate(coords)]
    return {"width": width, "height": height, "polyline": polyline, "dots": dots}


def prepare_sections(sections):
    """Attach precomputed rendering data (bar widths, SVG coordinates, glyphs)
    so the templates stay near-logic-free."""
    prepared = []
    for section in sections:
        section = dict(section)
        s_type = section["type"]

        if s_type == "stat_row":
            for stat in section["stats"]:
                direction = stat.get("delta_direction")
                stat["delta_glyph"] = "▲" if direction == "up" else ("▼" if direction == "down" else "")
                stat["delta_class"] = direction or ""

        elif s_type == "comparison":
            max_val = max(item["value"] for item in section["items"])
            for i, item in enumerate(section["items"]):
                item["pct"] = round((item["value"] / max_val) * 100, 1) if max_val else 0
                item["color_var"] = COMPARISON_COLOR_VARS[i % len(COMPARISON_COLOR_VARS)]
                unit = section.get("unit", "")
                item["display_value"] = f"{item['value']}{unit}"

        elif s_type == "trend":
            section["svg"] = build_trend_svg(section["points"])
            section["point_labels"] = section.get("point_labels") or [""] * len(section["points"])

        elif s_type == "list":
            section["icon_glyph"] = ICON_GLYPHS.get(section.get("icon"), ICON_GLYPHS["check"])

        prepared.append(section)
    return prepared


def count_words(spec):
    total = 0
    for section in spec["sections"]:
        if section["type"] == "narrative":
            total += len(section.get("body", "").split())
        elif section["type"] == "quote":
            total += len(section.get("text", "").split())
    return total


def count_infographics(spec):
    return sum(1 for s in spec["sections"] if s["type"] in ("stat_row", "comparison", "trend"))


def main():
    parser = argparse.ArgumentParser(description="Render a newsletter content-spec into HTML.")
    parser.add_argument("--content-spec", required=True, help="Path to the content-spec JSON file.")
    parser.add_argument("--output-dir", default="newsletters", help="Directory for the rendered HTML.")
    parser.add_argument("--slug", default=None, help="Filename slug. Default: derived from meta.title.")
    args = parser.parse_args()

    spec_path = Path(args.content_spec)
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    if not spec_path.exists():
        fail(f"Content spec not found: {spec_path}")

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"Content spec is not valid JSON: {exc}")

    errors = validate_spec(spec)
    if errors:
        fail("Content spec failed validation:\n  - " + "\n  - ".join(errors))

    meta = spec["meta"]
    slug = args.slug or slugify(meta.get("topic") or meta["title"])
    render_date = meta.get("date") or date.today().isoformat()

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("newsletter.html.j2")

    html = template.render(
        meta=meta,
        sources=meta.get("sources", []),
        sections=prepare_sections(spec["sections"]),
        render_date=render_date,
    )

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slug}-{render_date}.html"
    output_path.write_text(html, encoding="utf-8")

    print(f"Rendered newsletter to: {output_path}")
    print(f"Word count: {count_words(spec)}")
    print(f"Infographic count: {count_infographics(spec)}")


if __name__ == "__main__":
    main()
