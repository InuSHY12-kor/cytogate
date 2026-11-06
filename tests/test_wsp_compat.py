"""Reading older / unusual files: Gating-ML 1.5 workspaces (FlowJo 10.0), FlowJo's
"/"→"_" parameter names, "Freq. of Total" written as freq. of "Total", and FCS 2.0
bit-packed integer data."""

import numpy as np
import pandas as pd
import xml.etree.ElementTree as ET

from cytogate.core import PolygonGate
from cytogate.core.compensation import Compensation
from cytogate.core.gating import GatingTree
from cytogate.core.stats import Stat, StatSpec
from cytogate.core.transforms import Linear, from_xml
from cytogate.io import load_fcs
from cytogate.io.wsp import _normalize_namespaces, parse_gate, parse_statistic
from cytogate.io.wsp_load import _restore_parameter_names

V15 = ('xmlns:gating="http://flowcyt.sourceforge.net/std/Gating-ML/v1.5/gating" '
       'xmlns:transforms="http://flowcyt.sourceforge.net/std/Gating-ML/v1.5/transformations" '
       'xmlns:data-type="http://flowcyt.sourceforge.net/std/Gating-ML/v1.5/datatypes"')


def test_gating_ml_15():
    root = ET.fromstring(f"""<Workspace {V15}><Transformations>
      <transforms:linear transforms:minRange="0" transforms:maxRange="262144" gain="1">
        <data-type:parameter data-type:name="FSC-A"/></transforms:linear></Transformations>
      <Gate><gating:PolygonGate eventsInside="1">
        <gating:dimension><data-type:parameter data-type:name="FSC-A"/></gating:dimension>
        <gating:dimension><data-type:parameter data-type:name="SSC-A"/></gating:dimension>
        <gating:vertex><gating:coordinate data-type:value="1"/><gating:coordinate data-type:value="2"/></gating:vertex>
        <gating:vertex><gating:coordinate data-type:value="3"/><gating:coordinate data-type:value="4"/></gating:vertex>
        <gating:vertex><gating:coordinate data-type:value="5"/><gating:coordinate data-type:value="0"/></gating:vertex>
      </gating:PolygonGate></Gate></Workspace>""")
    _normalize_namespaces(root)
    name, xf = from_xml(root.find("Transformations")[0])
    assert name == "FSC-A" and isinstance(xf, Linear) and xf.top == 262144
    g = parse_gate(root.find("Gate")[0])
    assert isinstance(g, PolygonGate) and g.dims == ("FSC-A", "SSC-A") and g.vertices[1] == (3.0, 4.0)


def test_freq_of_total_and_flowjo10_names():
    el = ET.fromstring('<Statistic name="fj.stat.freqof" ancestor="Total" value="0.5"/>')
    assert parse_statistic(el) == StatSpec(Stat.FREQ_TOTAL)
    el = ET.fromstring('<Statistic name="fj.stat.freqofparent" value="20"/>')
    assert parse_statistic(el) == StatSpec(Stat.FREQ_PARENT)
    el = ET.fromstring('<Statistic name="fj.stat.freqof" ancestor="Live/Singlets" value="20"/>')
    assert parse_statistic(el) == StatSpec(Stat.FREQ_OF, ancestor=("Live", "Singlets"))


def test_slash_names_restored():
    class S:
        pass
    s = S()
    names = ["FITC_Alexa 488-A", "PE-A"]
    s.compensation = Compensation(pd.DataFrame(np.eye(2), index=names, columns=names))
    s.transforms = {"FITC_Alexa 488-A": Linear(0, 1), "Comp-FITC_Alexa 488-A": Linear(0, 2), "PE-A": Linear(0, 3)}
    s.gates = GatingTree()
    s.gates.add((), "g", PolygonGate(("Comp-FITC_Alexa 488-A", "PE-A"), ((0, 0), (1, 0), (1, 1))))
    s.gates.get(("g",)).stats.append(StatSpec(Stat.MEDIAN, "Comp-FITC_Alexa 488-A"))
    _restore_parameter_names(s, ["FSC-A", "FITC/Alexa 488-A", "PE-A"])
    assert list(s.compensation.spillover.index) == ["FITC/Alexa 488-A", "PE-A"]
    assert set(s.transforms) == {"FITC/Alexa 488-A", "Comp-FITC/Alexa 488-A", "PE-A"}
    pop = s.gates.get(("g",))
    assert pop.gate.dims == ("Comp-FITC/Alexa 488-A", "PE-A")
    assert pop.stats[0].parameter == "Comp-FITC/Alexa 488-A"


def _fcs20_packed(path, values, bits=9, rng=512):
    """FCS 2.0, $DATATYPE I, values packed back to back, most significant bit first."""
    n, p = values.shape
    stream = []
    for v in values.ravel():
        stream += [(int(v) >> (bits - 1 - k)) & 1 for k in range(bits)]
    stream += [0] * ((-len(stream)) % 8)
    data = np.packbits(np.array(stream, dtype=np.uint8)).tobytes()
    kw = {"$BYTEORD": "4,3,2,1", "$DATATYPE": "I", "$MODE": "L", "$NEXTDATA": "0", "$PAR": str(p), "$TOT": str(n)}
    for i in range(1, p + 1):
        kw.update({f"$P{i}B": str(bits), f"$P{i}R": str(rng), f"$P{i}N": f"P{i}", f"$P{i}E": "0,0"})
    text = "/" + "/".join(f"{k}/{v}" for k, v in kw.items()) + "/"
    begin_text = 58
    end_text = begin_text + len(text) - 1
    begin_data = end_text + 1
    end_data = begin_data + len(data) - 1
    header = f"FCS2.0    {begin_text:8d}{end_text:8d}{begin_data:8d}{end_data:8d}{0:8d}{0:8d}"
    path.write_bytes(header.encode() + text.encode() + data)


def test_bit_packed_fcs(tmp_path):
    rng = np.random.default_rng(1)
    values = rng.integers(0, 512, size=(300, 3))
    f = tmp_path / "packed.fcs"
    _fcs20_packed(f, values)
    s = load_fcs(f)
    assert s.event_count == 300
    np.testing.assert_array_equal(s.events, values.astype(float))
