"""Download public example FCS / FlowJo workspace files.

Usage: python scripts/fetch_test_data.py [--flowjo-vr]
Files are saved under tests/data/ (git-ignored).

- default: FlowKit repository examples (BSD-3)
- --flowjo-vr: FlowJo's public tutorial data (github.com/FlowJo/FlowJo-VR, 8-colour
  PBMC, about 100 MB): a FlowJo 10.0 workspace with FlowJo-computed Median,
  Robust CV and frequencies, used by tests/test_flowjo_validation.py
"""

import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

BASE = "https://raw.githubusercontent.com/whitews/FlowKit/master/data/"
FILES = [
    "8_color_data_set/fcs_files/101_DEN084Y5_15_E01_008_clean.fcs",
    "8_color_data_set/fcs_files/101_DEN084Y5_15_E03_009_clean.fcs",
    "8_color_data_set/fcs_files/101_DEN084Y5_15_E05_010_clean.fcs",
    "8_color_data_set/8_color_ICS_simple.wsp",
    "8_color_data_set/8_color_ICS.wsp",
    "8_color_data_set/den_comp.csv",
    "8_color_data_set/reused_quad_gate_with_child.wsp",
    "8_color_data_set/8_color_ICS_with_ellipse.wsp",
    "8_color_data_set/8_color_ICS_boolean_gate_testing.wsp",
    "simple_diamond_example/simple_diamond_example_quad_gate.wsp",
    "simple_line_example/data_set_simple_line_100.fcs",
    "simple_line_example/single_ellipse_51_events.wsp",
    "simple_diamond_example/test_data_diamond_01.fcs",
    "simple_diamond_example/test_data_diamond_asinh_rect.wsp",
    "simple_diamond_example/test_data_diamond_asinh_rect2.wsp",
    "simple_diamond_example/test_data_diamond_biex_rect.wsp",
    "test_comp_example.fcs",
    "test_data_2d_01.fcs",
    "100715.fcs",
    "noncompliant/data_start_offset_discrepancy_example.fcs",
    "noncompliant/data_stop_offset_discrepancy_example.fcs",
    "flowjo_xforms/tr_biex_l256_w-7.943282_n1.000000_m4.418540_r262144.000029.csv",
]

DEST = Path(__file__).resolve().parent.parent / "tests" / "data"

VR_BASE = "https://raw.githubusercontent.com/FlowJo/FlowJo-VR/master/"
VR_DEST = "flowjo_vr"
VR_FILES = [
    "8 Color PBMC/Lesson_8.wsp",
    "8 Color PBMC/Exp. 2/Panel 2.1/C9.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A1.fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D9.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A7 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C8.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B5 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C1.fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C3.fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D2.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B2 .fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A8 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D3.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A5 .fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B4 .fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A6 .fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A3 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C5.fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C2.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B7 .fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B1 .fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A4 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D1.fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C4.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B6 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D5.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A9 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D7.fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D4.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B9 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C7.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B8 .fcs",
    "8 Color PBMC/Exp. 1/Panel_1.2/B3 .fcs",
    "8 Color PBMC/Exp. 2/Panel 2.2/D6.fcs",
    "8 Color PBMC/Exp. 2/Panel 2.1/C6.fcs",
    "8 Color PBMC/Exp. 1/Panel_1.1/A2 .fcs",
]


def fetch(base: str, files, dest: Path) -> None:
    for rel in files:
        out = dest / rel
        if out.exists():
            print(f"skip  {rel}")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(base + quote(rel)) as resp:
            out.write_bytes(resp.read())
        print(f"saved {rel} ({out.stat().st_size:,} bytes)")


def main() -> None:
    fetch(BASE, FILES, DEST)
    if "--flowjo-vr" in sys.argv[1:]:
        fetch(VR_BASE, VR_FILES, DEST / VR_DEST)


if __name__ == "__main__":
    main()
