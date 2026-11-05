"""Compensation (spillover) matrices, FlowJo style.

A spillover matrix S has one row per fluorochrome/detector: S[i, j] is the
fraction of signal from detector i's dye that appears in detector j.
Compensated values are raw @ inv(S).

As in FlowJo, compensated parameters are added next to the originals with
a prefix: "TNFa FITC FLR-A" -> "Comp-TNFa FITC FLR-A".
"""

from __future__ import annotations

import csv
import io
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .transforms import NS, _fmt

__all__ = ["Compensation", "ACQUISITION_DEFINED"]

ACQUISITION_DEFINED = "Acquisition-defined"  # FlowJo's name for the matrix in the FCS file
_T = "{%s}" % NS["transforms"]
_D = "{%s}" % NS["data-type"]


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


@dataclass
class Compensation:
    spillover: pd.DataFrame  # square, index == columns == detector names (PnN)
    name: str = ACQUISITION_DEFINED
    prefix: str = "Comp-"
    suffix: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self):
        s = self.spillover
        if s.shape[0] != s.shape[1] or list(s.index) != list(s.columns):
            raise ValueError("spillover matrix must be square with matching row/column names")
        self.spillover = s.astype(float)

    # --- construction ---------------------------------------------------
    @classmethod
    def from_sample(cls, sample) -> "Compensation | None":
        """The matrix stored in the FCS file ($SPILLOVER / SPILL), or None."""
        spill = sample.spillover()
        return None if spill is None else cls(spill, name=ACQUISITION_DEFINED)

    @classmethod
    def from_csv(cls, path: str | Path, name: str | None = None) -> "Compensation":
        """Read a matrix saved as CSV.

        Accepts the plain layout (first line = detector names, optionally
        starting with "#") and the layout with row names in the first column.
        """
        path = Path(path)
        return cls.from_csv_text(path.read_text(), name=name or path.stem)

    @classmethod
    def from_csv_text(cls, text: str, name: str = "Imported") -> "Compensation":
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
        header, body = rows[0], rows[1:]
        header[0] = header[0].lstrip("#").strip()
        if header[0] == "":  # corner cell above a row-name column
            header = header[1:]
        if body and not _is_number(body[0][0]):  # rows start with their name
            body = [r[1:] for r in body]
        values = np.array([[float(c) for c in r[: len(header)]] for r in body])
        if values.shape != (len(header), len(header)):
            raise ValueError(f"matrix is {values.shape}, expected {len(header)} x {len(header)}")
        return cls(pd.DataFrame(values, index=header, columns=header), name=name)

    @classmethod
    def from_xml(cls, el: ET.Element) -> "Compensation":
        """Parse FlowJo's <transforms:spilloverMatrix> element (.wsp or .mtx)."""
        rows = el.findall(_T + "spillover")
        names = [r.get(_D + "parameter") for r in rows]
        values = np.zeros((len(names), len(names)))
        for i, r in enumerate(rows):
            coeffs = {c.get(_D + "parameter"): float(c.get(_T + "value")) for c in r.findall(_T + "coefficient")}
            values[i] = [coeffs[n] for n in names]
        return cls(
            pd.DataFrame(values, index=names, columns=names),
            name=el.get("name", "Imported"),
            prefix=el.get("prefix", "Comp-"),
            suffix=el.get("suffix", ""),
            id=el.get(_T + "id") or str(uuid.uuid4()),
        )

    # --- output ---------------------------------------------------------
    def to_csv(self, path: str | Path | None = None) -> str:
        """CSV with a header line of detector names (FlowJo-compatible)."""
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(self.detectors)
        for row in self.spillover.values:
            w.writerow([f"{v:.6f}" for v in row])
        if path is not None:
            Path(path).write_text(buf.getvalue())
        return buf.getvalue()

    def to_xml(self, version: str = "CytoGate") -> ET.Element:
        """FlowJo <transforms:spilloverMatrix> element."""
        el = ET.Element(_T + "spilloverMatrix", {
            "spectral": "0", "prefix": self.prefix, "name": self.name, "editable": "1",
            "color": "#c0c0c0", "version": version, "status": "FINALIZED",
            _T + "id": self.id, "suffix": self.suffix,
        })
        params = ET.SubElement(el, _D + "parameters")
        for d in self.detectors:
            ET.SubElement(params, _D + "parameter", {_D + "name": d, "userProvidedCompInfix": self.comp_name(d)})
        for d, row in zip(self.detectors, self.spillover.values):
            sp = ET.SubElement(el, _T + "spillover", {_D + "parameter": d, "userProvidedCompInfix": self.comp_name(d)})
            for d2, v in zip(self.detectors, row):
                ET.SubElement(sp, _T + "coefficient", {_D + "parameter": d2, _T + "value": _fmt(v)})
        return el

    # --- use ------------------------------------------------------------
    @property
    def detectors(self) -> list[str]:
        return list(self.spillover.index)

    def comp_name(self, detector: str) -> str:
        return f"{self.prefix}{detector}{self.suffix}"

    def compensate(self, events: np.ndarray, channel_names: list[str]) -> np.ndarray:
        """Compensated values of the matrix's detectors, shape (n_events, n_detectors)."""
        missing = [d for d in self.detectors if d not in channel_names]
        if missing:
            raise KeyError(f"channels not in sample: {missing}")
        idx = [channel_names.index(d) for d in self.detectors]
        # Solve x @ S = raw instead of forming inv(S) explicitly.
        return np.linalg.solve(self.spillover.values.T, events[:, idx].T).T
