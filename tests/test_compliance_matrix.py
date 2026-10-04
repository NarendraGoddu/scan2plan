"""The compliance summary must agree with the tables it summarises.

Regression test. The summary originally claimed 30 requirements with 18 Met while
the tables held 35 with 24 Met -- both numbers invented by hand and both wrong. A
compliance document whose own totals do not add up is the last place a reviewer
should find an error, so the counts are derived from the tables and asserted.
"""

from __future__ import annotations

import collections
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATRIX = os.path.join(ROOT, "docs", "compliance_matrix.md")
REPORT = os.path.join(ROOT, "docs", "final_report.md")

ROW = re.compile(r"^\|\s*(\d+\.\d+)\s*\|(.+)$")


def _rows() -> list[tuple[str, str]]:
    with open(MATRIX, encoding="utf-8") as fh:
        rows = []
        for line in fh:
            m = ROW.match(line)
            if not m:
                continue
            cells = [c.strip() for c in m.group(2).split("|")]
            rows.append((m.group(1), re.sub(r"\*", "", cells[1])))
        return rows


def _counts() -> collections.Counter:
    c = collections.Counter()
    for _rid, status in _rows():
        if status.startswith("Met"):
            c["Met"] += 1
        elif status.startswith("Partial"):
            c["Partial"] += 1
        elif status.startswith("Prototype"):
            c["Prototype"] += 1
        elif "Not" in status:
            c["Not met / Not done"] += 1
        else:
            c["UNCLASSIFIED"] += 1
    return c


def test_every_requirement_has_a_recognisable_status():
    """A status nobody can classify silently inflates the Met count."""
    counts = _counts()
    assert counts["UNCLASSIFIED"] == 0, (
        "requirements with an unparseable status: "
        f"{[r for r in _rows() if 'Not' not in r[1] and not r[1].startswith(('Met','Partial','Prototype'))]}"
    )


def test_summary_table_matches_the_tables():
    counts = _counts()
    total = sum(v for k, v in counts.items() if k != "UNCLASSIFIED")

    with open(MATRIX, encoding="utf-8") as fh:
        text = fh.read()

    def stated(label: str) -> int:
        m = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(\d+)\s*\|", text)
        assert m, f"summary table has no row for {label!r}"
        return int(m.group(1))

    assert stated("Met") == counts["Met"]
    assert stated("Partial") == counts["Partial"]
    assert stated("Prototype") == counts["Prototype"]
    assert stated("Not met / Not done") == counts["Not met / Not done"]

    m = re.search(r"\*\*Total requirements\*\*\s*\|\s*\*\*(\d+)\*\*", text)
    assert m, "summary table has no bolded total row"
    assert int(m.group(1)) == total


def test_final_report_quotes_the_same_numbers():
    """The report repeats the counts, so a change in one must change the other."""
    counts = _counts()
    with open(REPORT, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(
        r"(\d+)\s+requirements:\s*\*\*(\d+) Met,\s*(\d+) Partial,\s*"
        r"(\d+) Prototype,\s*(\d+) Not met",
        text,
    )
    assert m, "final_report.md does not state the compliance counts"
    total, met, partial, proto, notmet = (int(g) for g in m.groups())
    assert total == sum(v for k, v in counts.items() if k != "UNCLASSIFIED")
    assert met == counts["Met"]
    assert partial == counts["Partial"]
    assert proto == counts["Prototype"]
    assert notmet == counts["Not met / Not done"]


def test_no_requirement_is_claimed_met_without_evidence():
    """Every Met row must cite a path or a command, not just assert success."""
    with open(MATRIX, encoding="utf-8") as fh:
        text = fh.read()
    offenders = []
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        status = re.sub(r"\*", "", cells[1])
        evidence = cells[2] if len(cells) > 2 else ""
        if status.startswith("Met") and not re.search(r"[./`]|python", evidence):
            offenders.append(m.group(1))
    assert not offenders, f"Met rows with no file path or command cited: {offenders}"
