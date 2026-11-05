"""Phase 3: plot rendering (no GUI)."""

import numpy as np
import pytest

from cytogate.plots import (PSEUDOCOLOR_LUT, HistogramY, PlotType, bin2d, density, histogram,
                            probability_levels, render_2d)

rng = np.random.default_rng(0)
N = 50_000
X = np.clip(rng.normal(0.4, 0.08, N), -0.2, 1.2)
Y = np.clip(rng.normal(0.6, 0.05, N), -0.2, 1.2)


def test_bin2d_keeps_every_event_and_piles_offscale_on_edges():
    x = np.array([-0.5, 0.0, 0.5, 0.999, 1.0, 7.0, np.nan])
    y = np.array([0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])
    c = bin2d(x, y, res=10)
    assert c.sum() == 7
    assert c[0, 5] == 3  # -0.5, 0.0 and NaN on the left edge
    assert c[9, 5] == 3  # 0.999, 1.0 and 7.0 on the right edge
    assert bin2d(X, Y).sum() == N


def test_probability_levels_enclose_expected_fractions():
    counts = bin2d(X, Y, 256)
    dens = density(counts)
    levels = probability_levels(dens, counts, 0.05)
    assert 15 <= levels.size <= 19
    assert np.all(np.diff(levels) > 0)
    for k, lev in enumerate(levels, start=1):
        inside = counts[dens > lev].sum() / N
        assert inside == pytest.approx(1 - 0.05 * k, abs=0.02)


@pytest.mark.parametrize("kind", [PlotType.DOT, PlotType.PSEUDOCOLOR, PlotType.CONTOUR, PlotType.DENSITY])
def test_render_kinds(kind):
    r = render_2d(X, Y, kind, res=256)
    assert r.image.shape == (256, 256, 4) and r.image.dtype == np.uint8
    assert r.counts.sum() == N
    empty = r.counts == 0
    if kind != PlotType.DENSITY:  # density fills smoothed area, the rest only draw where events are
        assert np.all(r.image[empty][:, 3] == 0)
    assert (r.image[:, :, 3] > 0).any()


def test_dot_plot_is_black_where_events_are():
    r = render_2d(X, Y, PlotType.DOT, res=128)
    occupied = r.counts > 0
    assert np.all(r.image[occupied] == [0, 0, 0, 255])


def test_pseudocolor_peak_is_red_and_tails_are_blue_side():
    r = render_2d(X, Y, PlotType.PSEUDOCOLOR, res=256)
    peak = np.unravel_index(np.argmax(r.density), r.density.shape)
    assert tuple(r.image[peak]) == tuple(PSEUDOCOLOR_LUT[255])
    occupied = np.argwhere(r.counts > 0)
    lowest = occupied[np.argmin(r.density[tuple(occupied.T)])]
    red, _, blue, _ = r.image[tuple(lowest)]
    assert blue > red


def test_contour_outliers_are_about_the_lowest_band():
    r = render_2d(X, Y, PlotType.CONTOUR, res=256)
    outlier_events = r.counts[(r.image[:, :, 3] > 0)].sum() / N
    assert outlier_events == pytest.approx(0.05, abs=0.02)
    no_outliers = render_2d(X, Y, PlotType.CONTOUR, res=256, show_outliers=False)
    assert not (no_outliers.image[:, :, 3] > 0).any()


def test_histogram():
    edges, h = histogram(X, bins=256)
    assert edges.size == 257 and h.sum() == N
    _, m = histogram(X, bins=256, y_mode=HistogramY.MODE)
    assert m.max() == pytest.approx(100)
    _, s = histogram(X, bins=256, smooth=True)
    assert s.sum() == pytest.approx(N, rel=1e-3)
    assert np.abs(np.diff(s)).max() < np.abs(np.diff(h)).max()


def test_histogram_type_rejected_for_2d():
    with pytest.raises(ValueError):
        render_2d(X, Y, PlotType.HISTOGRAM)


def test_real_sample_counts(data_dir):
    from cytogate.core import ProcessedSample
    from cytogate.io import load_fcs

    ps = ProcessedSample(load_fcs(data_dir / "8_color_data_set/fcs_files/101_DEN084Y5_15_E01_008_clean.fcs"))
    r = render_2d(ps.scaled("FSC-A"), ps.scaled("SSC-A"), PlotType.PSEUDOCOLOR)
    assert r.counts.sum() == ps.event_count == 290172
