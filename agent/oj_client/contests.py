"""Small, fail-closed adapter for MiniOJ's public contest pages.

There is currently no contest-list JSON API. Only the public Problems table
and the public problem identifier are read; no login, editorial or admin data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from .types import ProtocolValidationError


def contest_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value.strip()):
        raise ValueError("Contest ID must be a positive integer string")
    number = int(value.strip())
    if not 1 <= number <= 2**63 - 1:
        raise ValueError("Invalid contest ID")
    return str(number)


@dataclass(frozen=True)
class ContestProblem:
    label: str
    problem_id: str
    title: str


@dataclass(frozen=True)
class ContestSnapshot:
    contest_id: str
    title: str
    status: str
    problems: list
    source: str = "public_html_v1"


class _Page(HTMLParser):
    """Extract only heading/status, the Problems table and eyebrow identifiers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.title, self.status, self.identifiers = [], "", "", []
        self.heading, self.expect_table, self.saw_table = "", False, False
        self.table_depth, self.rows, self.row, self.links = None, [], None, []

    def handle_starttag(self, tag, attrs):
        if tag in {"meta", "link", "input", "br", "hr", "img", "source", "wbr"}:
            return
        if len(self.stack) >= 128:
            raise ProtocolValidationError("Public page nesting exceeds adapter limits")
        attrs = dict(attrs)
        self.stack.append((tag, attrs, []))
        if tag == "h2":
            self.heading = ""
        if tag == "table" and self.expect_table and not self.saw_table:
            self.table_depth, self.saw_table = len(self.stack), True
        if self.table_depth and tag == "tr":
            self.row, self.links = [], []

    def handle_data(self, data):
        for _, _, texts in self.stack:
            texts.append(data)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            return
        _, attrs, texts = self.stack[-1]
        text = " ".join("".join(texts).split())
        classes = attrs.get("class", "").split()
        if tag == "h1" and not self.title:
            self.title = text[:500]
        if tag == "span" and "tag" in classes and not self.status:
            self.status = text[:50]
        if tag == "p" and "eyebrow" in classes:
            self.identifiers.append(text)
        if tag == "h2":
            self.expect_table = text == "Problems"
        if self.table_depth and self.row is not None:
            if tag == "td":
                self.row.append(text)
            if tag == "a":
                self.links.append((attrs.get("href", ""), text[:500]))
            if tag == "tr":
                self.rows.append((self.row, self.links))
                self.row = None
        if tag == "table" and len(self.stack) == self.table_depth:
            self.table_depth, self.expect_table = None, False
        self.stack.pop()


def public_contest(html, contest_id, base_url):
    parser = _Page()
    parser.feed(html)
    if not parser.title or not parser.saw_table or f"Contest #{contest_id}" not in parser.identifiers:
        raise ProtocolValidationError("Unrecognized public contest page")
    base = urlsplit(base_url)
    prefix = base.path.rstrip("/") + f"/contests/{contest_id}/problems/"
    entries, labels = [], set()
    for cells, links in parser.rows:
        if not links:
            continue
        if len(cells) != 3 or len(links) != 1:
            raise ProtocolValidationError("Unrecognized contest problem row")
        link, title = links[0]
        parsed = urlsplit(link)
        if ((parsed.netloc and (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc))
                or parsed.query or parsed.fragment or not parsed.path.startswith(prefix)):
            raise ProtocolValidationError("Unrecognized contest problem link")
        label = unquote(parsed.path[len(prefix):])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", label) or cells[0] != label or label in labels:
            raise ProtocolValidationError("Invalid or duplicate contest problem label")
        labels.add(label)
        entries.append((label, title))
    if len(entries) > 1000:
        raise ProtocolValidationError("Contest exceeds 1000 problems")
    return parser.title, parser.status or "unknown", entries


def public_problem_identifier(html):
    parser = _Page()
    parser.feed(html)
    identifiers = [item for item in parser.identifiers if re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", item)]
    if len(identifiers) != 1:
        raise ProtocolValidationError("Public contest problem ID is missing or ambiguous")
    return identifiers[0]
