"""Phase 1: reading FCS files.

Ground truth for the 8-color files comes from the FlowJo 10.6.2 workspace
8_color_ICS_simple.wsp, which stores the keywords FlowJo read from each file.
"""

import re
import xml.etree.ElementTree as ET

import flowio
import numpy as np
import pytest

from cytogate.__main__ import main as cli_main
from cytogate.io import FcsReadError, load_fcs, load_folder, read_keywords

EIGHT_COLOR = [
    "101_DEN084Y5_15_E01_008_clean.fcs",
    "101_DEN084Y5_15_E03_009_clean.fcs",
    "101_DEN084Y5_15_E05_010_clean.fcs",
]


def flowjo_keywords(wsp_path):
    """{file name: {keyword: value}} as stored by FlowJo in a .wsp file."""
    root = ET.parse(wsp_path).getroot()
    out = {}
    for sample in root.iter("Sample"):
        uri = sample.find("DataSet").get("uri")
        name = uri.replace("\\", "/").rsplit("/", 1)[-1]
        out[name] = {k.get("name"): k.get("value") for k in sample.iter("Keyword")}
    return out


@pytest.fixture(scope="module")
def fj_keywords(data_dir):
    return flowjo_keywords(data_dir / "8_color_data_set" / "8_color_ICS_simple.wsp")


@pytest.mark.parametrize("name", EIGHT_COLOR)
def test_event_count_matches_flowjo(data_dir, fj_keywords, name):
    s = load_fcs(data_dir / "8_color_data_set" / "fcs_files" / name)
    assert s.event_count == int(fj_keywords[name]["$TOT"])
    assert s.events.shape == (s.event_count, int(fj_keywords[name]["$PAR"]))


@pytest.mark.parametrize("name", EIGHT_COLOR)
def test_keywords_match_flowjo(data_dir, fj_keywords, name):
    """Every keyword in the file appears in FlowJo's record with the same name and value."""
    s = load_fcs(data_dir / "8_color_data_set" / "fcs_files" / name)
    fj = fj_keywords[name]
    assert len(s.keywords) > 50
    for key, value in s.keywords.items():
        assert fj.get(key) == value, key
    # The only extras are FlowJo's own keywords and parameter entries it adds
    # (empty $PnS, compensated channels).
    extras = [k for k in fj if k not in s.keywords]
    assert all(re.fullmatch(r"FJ_.*|\$P\d+[NSR]", k) for k in extras), extras


@pytest.mark.parametrize("name", EIGHT_COLOR)
def test_channels_match_flowjo(data_dir, fj_keywords, name):
    s = load_fcs(data_dir / "8_color_data_set" / "fcs_files" / name)
    fj = fj_keywords[name]
    assert [c.pnn for c in s.channels] == [fj[f"$P{i}N"] for i in range(1, len(s.channels) + 1)]
    assert [c.range for c in s.channels] == [float(fj[f"$P{i}R"]) for i in range(1, len(s.channels) + 1)]


def test_channel_types_and_lookup(data_dir):
    s = load_fcs(data_dir / "8_color_data_set" / "fcs_files" / EIGHT_COLOR[0])
    assert sum(c.is_scatter for c in s.channels) == 6
    assert sum(c.is_fluorescence for c in s.channels) == 8
    assert [c.pnn for c in s.channels if c.is_time] == ["Time"]
    assert s.channel_index("SSC-A") == 3
    assert s.channel_index(4) == 3
    assert np.array_equal(s.channel_events("FSC-A"), s.events[:, 0])
    with pytest.raises(KeyError):
        s.channel_index("nope")


def test_events_equal_raw_file_values(data_dir):
    """Linear, gain-1 file: events equal the stored values; Time is scaled into seconds
    with FlowJo's gain ($ETIM - $BTIM) / stored time range (checked against the
    gain FlowJo records, in test_gating.py)."""
    path = data_dir / "8_color_data_set" / "fcs_files" / EIGHT_COLOR[0]
    s = load_fcs(path)
    fd = flowio.FlowData(str(path))
    stored = np.reshape(np.asarray(fd.events, dtype=float), (-1, fd.channel_count))
    t = s.channel_index("Time")
    gain = 69.0 / (stored[:, t].max() - stored[:, t].min())  # $BTIM 13:37:39, $ETIM 13:38:48
    assert s.time_gain == pytest.approx(gain)
    stored[:, t] *= gain
    np.testing.assert_allclose(s.events, stored, rtol=1e-6)


def test_spillover_matrix(data_dir):
    s = load_fcs(data_dir / "8_color_data_set" / "fcs_files" / EIGHT_COLOR[0])
    spill = s.spillover()
    assert spill.shape == (8, 8)
    assert list(spill.index) == [c.pnn for c in s.channels if c.is_fluorescence]
    np.testing.assert_allclose(np.diag(spill.values), 1.0)
    assert spill.loc["CD8 PerCP-Cy55 FLR-A", "CD4 PE-Cy7 FLR-A"] == pytest.approx(0.12701208842983025)


@pytest.mark.parametrize(
    "name, version, events, channels",
    [
        ("100715.fcs", "2.0", 65016, 16),
        ("test_comp_example.fcs", "3.0", 10703, 18),
        ("test_data_2d_01.fcs", "3.1", 10000, 2),
    ],
)
def test_fcs_versions(data_dir, name, version, events, channels):
    s = load_fcs(data_dir / name)
    assert (s.version, s.event_count, len(s.channels)) == (version, events, channels)
    assert s.event_count == int(s.keyword("$TOT"))


def test_stain_labels_flowjo_style(data_dir):
    s = load_fcs(data_dir / "100715.fcs")
    assert s.channels[3].label == "B515-A :: KI67"
    assert s.channels[0].label == "FSC-A"
    assert s.channel_index("KI67") == s.channel_index("B515-A :: KI67") == 3
    assert list(s.to_dataframe("label").columns)[3] == "B515-A :: KI67"


@pytest.mark.parametrize("which", ["start", "stop"])
def test_noncompliant_files_load_with_notes(data_dir, which):
    s = load_fcs(data_dir / "noncompliant" / f"data_{which}_offset_discrepancy_example.fcs")
    assert s.event_count == 2
    assert any("relaxed" in n for n in s.warnings)
    assert any("$TIMESTEP" in n for n in s.warnings)


def test_load_folder_reports_bad_files(data_dir, tmp_path):
    good = data_dir / "test_data_2d_01.fcs"
    (tmp_path / good.name).write_bytes(good.read_bytes())
    (tmp_path / "broken.fcs").write_bytes(b"not an fcs file")
    (tmp_path / "notes.txt").write_text("ignored")
    samples, errors = load_folder(tmp_path)
    assert [s.name for s in samples] == ["test_data_2d_01.fcs"]
    assert list(errors) == ["broken.fcs"]


def test_missing_file():
    with pytest.raises(FcsReadError):
        load_fcs("does/not/exist.fcs")


def test_read_keywords_escaped_delimiter(tmp_path):
    text = b"/$TOT/5/$PAR/1/NOTE/a//b/"
    header = b"FCS3.1    " + b"%8d" % 58 + b"%8d" % (58 + len(text) - 1) + b"%8d" % 0 * 4
    f = tmp_path / "t.fcs"
    f.write_bytes(header + text)
    assert read_keywords(f) == {"$TOT": "5", "$PAR": "1", "NOTE": "a/b"}


def test_cli_info(data_dir, capsys):
    assert cli_main(["info", str(data_dir / "100715.fcs")]) == 0
    out = capsys.readouterr().out
    assert "FCS 2.0 | 65,016 events | 16 channels" in out
    assert "B515-A :: KI67" in out
