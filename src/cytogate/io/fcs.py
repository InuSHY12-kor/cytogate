"""Reading FCS files (FCS 2.0 / 3.0 / 3.1).

`load_fcs()` returns an `FcsSample`: keywords exactly as written in the file,
a channel table, and the event matrix. Event values are the "raw" scale FlowJo
gates on: FCS data with $PnE log amplification and $PnG gain already applied.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import flowio
import flowkit as fk

__all__ = ["Channel", "FcsSample", "FcsReadError", "load_fcs", "load_folder", "read_keywords"]

FCS_SUFFIXES = {".fcs", ".lmd"}
_SCATTER = re.compile(r"^(FSC|SSC)", re.IGNORECASE)


class FcsReadError(Exception):
    """Raised when a file cannot be read as FCS."""


@dataclass(frozen=True)
class Channel:
    number: int  # 1-based, as in $PnN
    pnn: str  # detector / short name, e.g. "FITC-A"
    pns: str  # stain / long name, e.g. "CD3" ("" if absent)
    range: float  # $PnR
    gain: float  # $PnG (1.0 if absent)
    log_decades: float  # $PnE first value (0 = linear)
    log_offset: float  # $PnE second value

    @property
    def label(self) -> str:
        """Display name in FlowJo style: "PnN :: PnS", or just PnN."""
        return f"{self.pnn} :: {self.pns}" if self.pns else self.pnn

    @property
    def is_scatter(self) -> bool:
        return bool(_SCATTER.match(self.pnn))

    @property
    def is_time(self) -> bool:
        return self.pnn.lower() == "time"

    @property
    def is_fluorescence(self) -> bool:
        return not (self.is_scatter or self.is_time)


def read_keywords(path: str | Path) -> dict[str, str]:
    """Return TEXT-segment keywords with their original names (e.g. "$TOT", "$P1N").

    Only the primary TEXT segment from the header is read (supplemental TEXT
    is ignored), which is enough for display and grouping.
    """
    path = Path(path)
    with path.open("rb") as fh:
        header = fh.read(58)
        if len(header) < 58 or not header.startswith(b"FCS"):
            raise FcsReadError(f"{path.name}: not an FCS file")
        try:
            start, end = int(header[10:18]), int(header[18:26])
        except ValueError as exc:
            raise FcsReadError(f"{path.name}: bad header offsets") from exc
        fh.seek(start)
        raw = fh.read(end - start + 1)

    text = raw.decode("utf-8", errors="replace")
    if not text:
        raise FcsReadError(f"{path.name}: empty TEXT segment")
    delim = text[0]
    # A doubled delimiter is an escaped literal delimiter.
    placeholder = "\x00"
    body = text[1:].replace(delim * 2, placeholder)
    parts = [p.replace(placeholder, delim) for p in body.split(delim)]
    if parts and parts[-1] == "":
        parts.pop()
    keywords: dict[str, str] = {}
    for key, value in zip(parts[0::2], parts[1::2]):
        keywords[key.strip()] = value.strip()
    return keywords


def _float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class FcsSample:
    """One FCS data set: keywords, channels and events."""

    def __init__(self, path: str | Path, fk_sample: fk.Sample, keywords: dict[str, str], warnings_: list[str]):
        self.path = Path(path)
        self._fk = fk_sample
        self.keywords = keywords
        self.warnings = warnings_
        self.channels = self._build_channels()

    # --- identity -------------------------------------------------------
    @property
    def name(self) -> str:
        """Sample name as shown in FlowJo's sample list (the file name)."""
        return self.path.name

    @property
    def version(self) -> str:
        return str(self._fk.version)

    @property
    def event_count(self) -> int:
        return int(self._fk.event_count)

    @property
    def flowkit_sample(self) -> fk.Sample:
        """Underlying FlowKit sample (used by later phases for gating)."""
        return self._fk

    def keyword(self, name: str, default: str | None = None) -> str | None:
        """Case-insensitive keyword lookup; "$" prefix optional."""
        want = name.upper().lstrip("$")
        for key, value in self.keywords.items():
            if key.upper().lstrip("$") == want:
                return value
        return default

    @property
    def cytometer(self) -> str | None:
        return self.keyword("$CYT")

    @property
    def acquisition_date(self) -> str | None:
        return self.keyword("$DATE")

    # --- channels -------------------------------------------------------
    def _build_channels(self) -> list[Channel]:
        out = []
        for row in self._fk.channels.itertuples(index=False):
            pne = row.pne if isinstance(row.pne, tuple) else (0.0, 0.0)
            out.append(
                Channel(
                    number=int(row.channel_number),
                    pnn=str(row.pnn).strip(),
                    pns=str(row.pns or "").strip(),
                    range=_float(row.pnr, 0.0),
                    gain=_float(row.png, 1.0),
                    log_decades=_float(pne[0], 0.0),
                    log_offset=_float(pne[1], 0.0),
                )
            )
        return out

    def channel_table(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "#": c.number,
                    "Name ($PnN)": c.pnn,
                    "Stain ($PnS)": c.pns,
                    "Label": c.label,
                    "Range": c.range,
                    "Gain": c.gain,
                    "Log decades": c.log_decades,
                    "Type": "time" if c.is_time else "scatter" if c.is_scatter else "fluorescence",
                }
                for c in self.channels
            ]
        )

    def channel_index(self, key: str | int) -> int:
        """0-based column index from a PnN, PnS, FlowJo label or 1-based number."""
        if isinstance(key, int):
            return key - 1
        for i, c in enumerate(self.channels):
            if key in (c.pnn, c.label) or (c.pns and key == c.pns):
                return i
        raise KeyError(f"{self.name}: no channel {key!r}")

    # --- events ---------------------------------------------------------
    @property
    def events(self) -> np.ndarray:
        """Event matrix, shape (event_count, channel_count), raw scale."""
        return self._fk.get_events(source="raw")

    def channel_events(self, key: str | int) -> np.ndarray:
        return self.events[:, self.channel_index(key)]

    def to_dataframe(self, labels: str = "pnn") -> pd.DataFrame:
        """Events as a DataFrame; column names by "pnn" or FlowJo-style "label"."""
        cols = [c.pnn if labels == "pnn" else c.label for c in self.channels]
        return pd.DataFrame(self.events, columns=cols)

    def spillover(self) -> pd.DataFrame | None:
        """Spillover matrix from $SPILLOVER / SPILL / $COMP, or None."""
        for key in ("$SPILLOVER", "SPILL", "$SPILL", "SPILLOVER", "$COMP"):
            value = self.keyword(key)
            if value:
                return _parse_spill(value, self.channels)
        return None

    def __repr__(self) -> str:
        return f"<FcsSample {self.name!r} FCS{self.version} {self.event_count:,} events × {len(self.channels)} ch>"


def _parse_spill(value: str, channels: list[Channel]) -> pd.DataFrame:
    tokens = [t.strip() for t in value.split(",")]
    n = int(float(tokens[0]))
    names = tokens[1 : n + 1]
    nums = [float(t) for t in tokens[n + 1 : n + 1 + n * n]]
    if len(nums) != n * n:
        raise FcsReadError(f"spillover matrix has {len(nums)} values, expected {n * n}")
    # Older files name channels by number instead of PnN.
    if all(name.isdigit() for name in names):
        names = [channels[int(name) - 1].pnn for name in names]
    return pd.DataFrame(np.array(nums).reshape(n, n), index=names, columns=names)


def load_fcs(path: str | Path) -> FcsSample:
    """Read one FCS file. Tolerates the common offset mistakes some instruments make."""
    path = Path(path)
    if not path.is_file():
        raise FcsReadError(f"{path}: file not found")
    keywords = read_keywords(path)
    notes: list[str] = []
    attempts = [
        {},
        {"ignore_offset_discrepancy": True},
        {"ignore_offset_discrepancy": True, "use_header_offsets": True},
        {"ignore_offset_error": True},
    ]
    last_exc: Exception | None = None
    for opts in attempts:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                flow_data = flowio.FlowData(str(path), **opts)
                _sanitize_text(flow_data, notes)
                sample = fk.Sample(flow_data)
            notes += [str(w.message) for w in caught]
            if opts:
                notes.append(f"read with relaxed options {sorted(opts)}: file does not fully follow the FCS standard")
            return FcsSample(path, sample, keywords, notes)
        except Exception as exc:  # flowio raises plain exceptions for bad offsets
            last_exc = exc
    raise FcsReadError(f"{path.name}: {last_exc}") from last_exc


def _sanitize_text(flow_data: flowio.FlowData, notes: list[str]) -> None:
    """Drop keyword values that are malformed but not needed to read events."""
    for key in ("timestep",):
        value = flow_data.text.get(key)
        if value is not None:
            try:
                float(value)
            except ValueError:
                del flow_data.text[key]
                notes.append(f"ignored invalid ${key.upper()} value {value!r}")


def load_folder(folder: str | Path, recursive: bool = False) -> tuple[list[FcsSample], dict[str, str]]:
    """Read every FCS file in a folder. Returns (samples, {file name: error})."""
    folder = Path(folder)
    files = folder.rglob("*") if recursive else folder.iterdir()
    samples, errors = [], {}
    for f in sorted(p for p in files if p.is_file() and p.suffix.lower() in FCS_SUFFIXES):
        try:
            samples.append(load_fcs(f))
        except FcsReadError as exc:
            errors[f.name] = str(exc)
    return samples, errors
