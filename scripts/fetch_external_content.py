#!/usr/bin/env python3
"""Fetch Itch.io projects and arXiv publications into Hugo content files."""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import textwrap
from typing import Iterable

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def slugify(value: str, max_length: int = 60) -> str:
    """Return a filesystem-friendly slug."""
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:max_length] if max_length else value


def yaml_escape(value: str) -> str:
    return value.replace("\"", "\\\"")


def format_list(items: Iterable[str]) -> str:
    escaped = [f'"{yaml_escape(item)}"' for item in items if item]
    return f"[{', '.join(escaped)}]"


def fetch_itch_projects(url: str) -> list[dict[str, str]]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    projects: list[dict[str, str]] = []
    for cell in soup.select(".game_cell"):
        title_el = cell.select_one(".title")
        if not title_el:
            continue
        link_el = cell.select_one("a")
        summary_el = cell.select_one(".text")
        title = title_el.get_text(strip=True)
        url_href = link_el["href"] if link_el and link_el.has_attr("href") else ""
        summary = summary_el.get_text(strip=True) if summary_el else ""
        projects.append({
            "title": title,
            "url": url_href,
            "summary": summary,
        })
    return projects


def write_project(entry: dict[str, str], project_dir: pathlib.Path, timestamp: dt.datetime) -> pathlib.Path:
    slug = slugify(entry["title"]) or slugify(entry["url"], max_length=60)
    target = project_dir / slug / "index.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(
        f"""---
# Documentation: https://sourcethemes.com/academic/docs/managing-content/

title: \"{yaml_escape(entry['title'])}\"
summary: \"{yaml_escape(entry['summary'] or 'Game description pending update.')}\"
authors: []
tags: [\"game\"]
categories: []
date: {timestamp.isoformat()}

# Optional external URL for project (replaces project detail page).
external_link: \"{yaml_escape(entry['url'])}\"

# Featured image
# To use, add an image named `featured.jpg/png` to your page's folder.
# Focal points: Smart, Center, TopLeft, Top, TopRight, Left, Right, BottomLeft, Bottom, BottomRight.
image:
  caption: \"\"
  focal_point: \"\"
  preview_only: false

# Custom links (optional).
#   Uncomment and edit lines below to show custom links.
# links:
# - name: Follow
#   url: https://twitter.com
#   icon_pack: fab
#   icon: twitter

url_code: \"\"
url_pdf: \"\"
url_slides: \"\"
url_video: \"\"

# Slides (optional).
#   Associate this project with Markdown slides.
#   Simply enter your slide deck's filename without extension.
#   E.g. `slides = \"example-slides\"` references `content/slides/example-slides.md`.
#   Otherwise, set `slides = \"\"`.
slides: \"\"
---
"""
    ).rstrip() + "\n"
    target.write_text(body, encoding="utf-8")
    return target


def fetch_arxiv_metadata(arxiv_ids: list[str]) -> list[dict[str, str]]:
    if not arxiv_ids:
        return []
    url = "https://export.arxiv.org/api/query?id_list=" + ",".join(arxiv_ids)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    entries: list[dict[str, str]] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        raw_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        arxiv_id = raw_id.rsplit("/", 1)[-1]
        title = entry.findtext("atom:title", default="", namespaces=ATOM_NS).strip()
        summary = entry.findtext("atom:summary", default="", namespaces=ATOM_NS).strip()
        published = entry.findtext("atom:published", default="", namespaces=ATOM_NS)
        if published:
            published_dt = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
        else:
            published_dt = dt.datetime.now(dt.timezone.utc)
        authors = [author.findtext("atom:name", default="", namespaces=ATOM_NS).strip() for author in entry.findall("atom:author", ATOM_NS)]
        pdf_url = ""
        for link in entry.findall("atom:link", ATOM_NS):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
                break
        primary_category = entry.find("arxiv:primary_category", ATOM_NS)
        category = primary_category.get("term") if primary_category is not None else ""
        entries.append({
            "id": arxiv_id,
            "title": title,
            "summary": summary,
            "published": published_dt,
            "authors": authors,
            "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
            "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
            "category": category,
        })
    return entries


def write_publication(entry: dict[str, object], publication_dir: pathlib.Path) -> pathlib.Path:
    slug = slugify(str(entry["id"]))
    target = publication_dir / f"{slug}.md"
    summary = entry["summary"].strip()
    body_lines = [
        "---",
        f"title: \"{yaml_escape(entry['title'])}\"" if entry["title"] else f"title: \"{entry['id']}\"",
        f"authors: {format_list(entry['authors'])}",
        f"date: {entry['published'].isoformat()}",
        "publication_types: [\"3\"]",
        "publication: \"*arXiv e-prints*\"",
        f"url_pdf: \"{yaml_escape(entry['pdf_url'])}\"",
        f"url_source: \"{yaml_escape(entry['abs_url'])}\"",
    ]
    if entry.get("category"):
        body_lines.append(f"tags: [\"{yaml_escape(entry['category'])}\"]")
    summary_text = summary if summary else "Preprint metadata fetched from arXiv."
    body_lines.append(f"summary: \"{yaml_escape(summary_text)}\"")
    body_lines.append("---")
    if summary:
        body_lines.append("")
        body_lines.append(summary)
    target.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--itch-url", default="https://avivajpeyi.itch.io/", help="Itch.io profile URL to scrape.")
    parser.add_argument("--arxiv", nargs="*", default=[], help="ArXiv identifiers to download metadata for.")
    parser.add_argument("--project-dir", type=pathlib.Path, default=pathlib.Path("content/project"), help="Output directory for project pages.")
    parser.add_argument("--publication-dir", type=pathlib.Path, default=pathlib.Path("content/publication"), help="Output directory for publication pages.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch data but do not write files.")
    args = parser.parse_args()

    timestamp = dt.datetime.now(dt.timezone.utc)

    projects = fetch_itch_projects(args.itch_url)
    publications = fetch_arxiv_metadata(args.arxiv)

    if args.dry_run:
        print(f"Fetched {len(projects)} projects and {len(publications)} publications (dry run).")
        return

    for project in projects:
        path = write_project(project, args.project_dir, timestamp)
        print(f"Wrote project: {path}")

    for publication in publications:
        path = write_publication(publication, args.publication_dir)
        print(f"Wrote publication: {path}")


if __name__ == "__main__":
    main()
