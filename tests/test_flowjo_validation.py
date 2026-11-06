"""Phase 8: CytoGate against numbers FlowJo saved, on FlowJo's public tutorial data.

Needs `python scripts/fetch_test_data.py --flowjo-vr` (about 100 MB); skipped otherwise.

FlowJo 10.0 left some values in this workspace un-recalculated: the same number
for different files (e.g. 19,666 T cells for A1, C8 and C9). Those are not
comparisons, so only values that are unique across samples are checked.
"""

import collections
import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VR = ROOT / "tests" / "data" / "flowjo_vr" / "8 Color PBMC"


def _validator():
    spec = importlib.util.spec_from_file_location("validate_flowjo", ROOT / "scripts" / "validate_flowjo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rows():
    if not (VR / "Lesson_8.wsp").is_file():
        pytest.skip("FlowJo tutorial data missing: run `python scripts/fetch_test_data.py --flowjo-vr`")
    opened, rows = _validator().validate(VR / "Lesson_8.wsp", [VR])
    assert opened.missing == [] and len(opened.workspace.samples) == 35
    return rows


def fresh(rows, stat):
    def key(r):
        return r["population"], r["statistic"], r["parameter"]

    seen = collections.defaultdict(collections.Counter)
    done = set()
    for r in rows:
        if (r["sample"],) + key(r) in done:  # population count and its "Count" statistic node
            continue
        done.add((r["sample"],) + key(r))
        seen[key(r)][float(r["flowjo"])] += 1
    out, done = [], set()
    for r in rows:
        if (r["statistic"] != stat or r["population"] == "Ungated" or not isinstance(r["rel_diff"], float)
                or math.isnan(r["rel_diff"]) or (r["sample"],) + key(r) in done):
            continue
        done.add((r["sample"],) + key(r))
        if seen[key(r)][float(r["flowjo"])] == 1:
            out.append(r)
    assert out
    return sorted(r["rel_diff"] for r in out)


def test_counts(rows):
    d = fresh(rows, "Count")
    # measured: 98 values, median 0.013 %, 90th percentile 0.20 %, worst 0.96 %
    assert len(d) >= 90
    assert d[len(d) // 2] < 0.0005
    assert d[int(0.9 * (len(d) - 1))] < 0.0025
    assert d[-1] < 0.01


@pytest.mark.parametrize("stat", ["Median", "Robust CV"])
def test_parameter_statistics(rows, stat):
    # measured: Median 41 values, median 0.06 %, worst 2.3 %; Robust CV 32 values, median 0.13 %, worst 2.6 %
    d = fresh(rows, stat)
    assert d[len(d) // 2] < 0.002
    assert d[-1] < 0.03


def test_frequency_of_total(rows):
    d = fresh(rows, "fj.stat.freqof")  # measured: 8 values, worst 0.05 %
    assert d[-1] < 0.002
