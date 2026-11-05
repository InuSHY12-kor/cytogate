"""Phase 2: compensation and processed samples.

Ground truth: FlowJo 10.6.2's 8_color_ICS_simple.wsp, which stores the
"Acquisition-defined" spillover matrix, the parameter list ($P1N..$P23N,
including the Comp- parameters FlowJo created) and per-parameter transforms.
"""

import xml.etree.ElementTree as ET

import flowkit as fk
import numpy as np
import pytest

from cytogate.core import Biex, Compensation, Linear, Logicle, ProcessedSample, from_xml
from cytogate.core.transforms import NS
from cytogate.io import load_fcs

FCS = "8_color_data_set/fcs_files/101_DEN084Y5_15_E01_008_clean.fcs"
WSP = "8_color_data_set/8_color_ICS_simple.wsp"


@pytest.fixture(scope="module")
def sample(data_dir):
    return load_fcs(data_dir / FCS)


@pytest.fixture(scope="module")
def processed(sample):
    return ProcessedSample(sample)


@pytest.fixture(scope="module")
def wsp_sample_el(data_dir):
    root = ET.parse(data_dir / WSP).getroot()
    for s in root.iter("Sample"):
        if s.find("DataSet").get("uri").endswith(FCS.rsplit("/", 1)[-1]):
            return s
    raise AssertionError("sample not in workspace")


def test_matrix_matches_flowjo_acquisition_defined(sample, wsp_sample_el):
    fj = Compensation.from_xml(wsp_sample_el.find(".//{%s}spilloverMatrix" % NS["transforms"]))
    ours = Compensation.from_sample(sample)
    assert fj.name == ours.name == "Acquisition-defined"
    assert fj.prefix == ours.prefix == "Comp-"
    assert fj.detectors == ours.detectors
    # FlowJo stores the coefficients as single-precision floats (~8 digits).
    np.testing.assert_allclose(ours.spillover.values, fj.spillover.values, rtol=1e-7, atol=1e-10)


def test_csv_matrix(data_dir, sample):
    csv = Compensation.from_csv(data_dir / "8_color_data_set" / "den_comp.csv")
    ours = Compensation.from_sample(sample)
    assert csv.detectors == ours.detectors
    np.testing.assert_allclose(csv.spillover.values, ours.spillover.values, atol=5e-7)


def test_csv_round_trip_and_row_name_layout(sample):
    comp = Compensation.from_sample(sample)
    again = Compensation.from_csv_text(comp.to_csv())
    np.testing.assert_allclose(again.spillover.values, comp.spillover.values, atol=5e-7)
    with_rows = ",A,B\nA,1,0.1\nB,0.2,1\n"
    m = Compensation.from_csv_text(with_rows)
    assert m.detectors == ["A", "B"]
    assert m.spillover.loc["B", "A"] == 0.2
    with pytest.raises(ValueError):
        Compensation.from_csv_text("A,B\n1,0\n")


def test_xml_round_trip(sample):
    comp = Compensation.from_sample(sample)
    el = comp.to_xml()
    text = ET.tostring(el).decode()
    assert "transforms:spilloverMatrix" in text and 'userProvidedCompInfix="Comp-TNFa FITC FLR-A"' in text
    again = Compensation.from_xml(ET.fromstring(text))
    assert (again.name, again.prefix, again.id) == (comp.name, comp.prefix, comp.id)
    np.testing.assert_array_equal(again.spillover.values, comp.spillover.values)


def test_compensated_values(sample, processed):
    comp = processed.compensation
    names = [c.pnn for c in sample.channels]
    idx = [names.index(d) for d in comp.detectors]
    compensated = np.column_stack([processed.values(comp.comp_name(d)) for d in comp.detectors])
    # Undoing compensation gives back the raw detector values.
    np.testing.assert_allclose(compensated @ comp.spillover.values, sample.events[:, idx], rtol=1e-9, atol=1e-6)
    # Same result as FlowKit's implementation.
    fks = sample.flowkit_sample
    matrix = fk.Matrix(comp.spillover.values, comp.detectors)
    expected = matrix.apply(fks)[:, idx]
    np.testing.assert_allclose(compensated, expected, rtol=1e-9, atol=1e-6)


def test_parameters_match_flowjo(processed, wsp_sample_el):
    keywords = {k.get("name"): k.get("value") for k in wsp_sample_el.iter("Keyword")}
    n = int(keywords["$PAR"])
    fj_names = [keywords[f"$P{i}N"] for i in range(1, 24)]
    assert n == 15
    assert [p.name for p in processed.parameters] == fj_names
    assert processed.data.shape == (processed.event_count, 23)
    # Every parameter FlowJo assigned a transform to exists here.
    fj_xform_params = [from_xml(el)[0] for el in wsp_sample_el.find("Transformations")]
    assert sorted(fj_xform_params) == sorted(fj_names)


def test_flowjo_transforms_apply(processed, wsp_sample_el):
    p = ProcessedSample(processed.sample, transforms=dict(from_xml(el) for el in wsp_sample_el.find("Transformations")))
    assert p.transforms["Comp-CD3 APC-H7 FLR-A"] == Logicle(262144, 1, 4.418539922, 0)
    assert p.transforms["FSC-A"] == Linear(0, 262144, 1)
    y = p.scaled("Comp-CD3 APC-H7 FLR-A")
    np.testing.assert_allclose(y, Logicle(262144, 1, 4.418539922, 0).apply(p.values("Comp-CD3 APC-H7 FLR-A")))


def test_default_transforms(processed):
    assert processed.transforms["FSC-A"] == Linear(0, 262144)
    t = processed.values("Time")
    assert processed.transforms["Time"] == Linear(t.min(), t.max())
    assert processed.transforms["Comp-CD4 PE-Cy7 FLR-A"] == Biex(max_range=262144)
    assert processed.transforms["CD4 PE-Cy7 FLR-A"] == Biex(max_range=262144)
    assert 0 <= processed.scaled("FSC-A").min() and processed.scaled("Time").max() == pytest.approx(1)


def test_lookup_by_label_and_stain(data_dir):
    p = ProcessedSample(load_fcs(data_dir / "100715.fcs"))
    assert p.compensation is not None
    assert p.parameter("CD3").name == "Comp-R780-A"  # stain -> compensated parameter
    assert p.parameter("Comp-R780-A :: CD3").name == "Comp-R780-A"
    assert p.parameter("R780-A").name == "R780-A"
    p.set_transform("CD3", Linear(0, 1000))
    assert p.transforms["Comp-R780-A"] == Linear(0, 1000)
    with pytest.raises(KeyError):
        p.parameter("CD999")


def test_no_compensation(sample):
    p = ProcessedSample(sample, compensation=None)
    assert len(p.parameters) == 15 and not any(x.compensated for x in p.parameters)


def test_missing_detector_rejected(sample):
    import pandas as pd
    comp = Compensation(pd.DataFrame([[1.0]], index=["nope"], columns=["nope"]))
    with pytest.raises(KeyError):
        ProcessedSample(sample, compensation=comp)
