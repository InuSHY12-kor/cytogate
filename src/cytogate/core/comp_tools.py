"""Making compensation matrices from single-stain controls.

Two methods, as offered by FlowJo's Compensation Wizard:

- `spillover_from_controls` (classic): for each single-stain control, compare
  its bright (positive) events with negative events — from an unstained
  control, or the dim events of the same tube:
      S[i, j] = (median_pos_j - median_neg_j) / (median_pos_i - median_neg_i)
  where i is the control's own detector.

- `autospill` (after Roca et al., Nat Commun 2021): no positive/negative
  gates. Each secondary detector is regressed on the primary detector over all
  events of the control with a robust (Huber) fit; the slope is the spillover.
  The fit is then refined on the data compensated with the current estimate
  until the remaining spillover is negligible.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .compensation import Compensation

__all__ = ["Control", "spillover_from_controls", "autospill", "auto_positive_negative"]


@dataclass
class Control:
    """A single-stain control: which detector it stains and its raw events."""

    detector: str
    events: np.ndarray  # (n, len(detectors)) raw values, columns in `detectors` order
    positive: np.ndarray | None = None  # boolean mask, optional (else chosen automatically)
    negative: np.ndarray | None = None


def auto_positive_negative(x: np.ndarray, pos_pct: float = 90.0, neg_pct: float = 30.0, top_cut: float = 99.5):
    """Bright events (above pos_pct, below the saturated top) and dim events (below neg_pct)."""
    lo, hi, top = np.percentile(x, [neg_pct, pos_pct, top_cut])
    return (x >= hi) & (x <= top), x <= lo


def spillover_from_controls(detectors, controls, unstained: np.ndarray | None = None, name="Compensation") -> Compensation:
    detectors = list(detectors)
    n = len(detectors)
    S = np.eye(n)
    neg_medians = np.median(unstained, axis=0) if unstained is not None else None
    for c in controls:
        i = detectors.index(c.detector)
        pos, neg = c.positive, c.negative
        if pos is None or (neg is None and neg_medians is None):
            auto_pos, auto_neg = auto_positive_negative(c.events[:, i])
            pos = auto_pos if pos is None else pos
            neg = auto_neg if neg is None else neg
        med_pos = np.median(c.events[pos], axis=0)
        med_neg = neg_medians if (c.negative is None and neg_medians is not None) else np.median(c.events[neg], axis=0)
        signal = med_pos[i] - med_neg[i]
        if signal <= 0:
            raise ValueError(f"control for {c.detector} has no positive signal")
        S[i] = (med_pos - med_neg) / signal
        S[i, i] = 1.0
    return Compensation(pd.DataFrame(S, index=detectors, columns=detectors), name=name)


def _huber_slope(x: np.ndarray, y: np.ndarray, iters: int = 20, k: float = 1.345) -> tuple[float, float]:
    """Robust line y = a + b x by iteratively reweighted least squares (Huber weights)."""
    w = np.ones_like(x)
    a = b = 0.0
    for _ in range(iters):
        sw = w.sum()
        mx, my = (w * x).sum() / sw, (w * y).sum() / sw
        var = (w * (x - mx) ** 2).sum()
        if var <= 0:
            break
        b = (w * (x - mx) * (y - my)).sum() / var
        a = my - b * mx
        r = y - a - b * x
        s = 1.4826 * np.median(np.abs(r - np.median(r))) or 1e-12
        u = np.abs(r) / (k * s)
        w = np.where(u <= 1, 1.0, 1.0 / np.maximum(u, 1e-12))
    return a, b


def autospill(detectors, controls, max_iter: int = 10, tol: float = 1e-4, max_events: int = 30000,
              seed: int = 0, name="AutoSpill") -> Compensation:
    detectors = list(detectors)
    n = len(detectors)
    rng = np.random.default_rng(seed)
    data = {}
    for c in controls:
        ev = c.events
        if ev.shape[0] > max_events:
            ev = ev[rng.choice(ev.shape[0], max_events, replace=False)]
        data[detectors.index(c.detector)] = ev
    S = np.eye(n)
    for _ in range(max_iter):
        comp_inv = np.linalg.inv(S)
        delta = np.zeros((n, n))
        for i, ev in data.items():
            comp = ev @ comp_inv  # compensated with the current estimate
            x = comp[:, i]
            for j in range(n):
                if j != i:
                    _, b = _huber_slope(x, comp[:, j])
                    delta[i, j] = b
        # residual spillover in compensated space adds to the estimate (first order)
        S = S + delta @ S
        np.fill_diagonal(S, 1.0)
        if np.abs(delta).max() < tol:
            break
    return Compensation(pd.DataFrame(S, index=detectors, columns=detectors), name=name)
