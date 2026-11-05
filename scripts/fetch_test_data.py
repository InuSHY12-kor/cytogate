"""Download public example FCS / FlowJo workspace files from the FlowKit repository (BSD-3).

Usage: python scripts/fetch_test_data.py
Files are saved under tests/data/ (git-ignored).
"""

from pathlib import Path
from urllib.request import urlopen

BASE = "https://raw.githubusercontent.com/whitews/FlowKit/master/data/"
FILES = [
    "8_color_data_set/fcs_files/101_DEN084Y5_15_E01_008_clean.fcs",
    "8_color_data_set/fcs_files/101_DEN084Y5_15_E03_009_clean.fcs",
    "8_color_data_set/fcs_files/101_DEN084Y5_15_E05_010_clean.fcs",
    "8_color_data_set/8_color_ICS_simple.wsp",
    "8_color_data_set/8_color_ICS.wsp",
    "8_color_data_set/den_comp.csv",
    "test_comp_example.fcs",
    "flowjo_xforms/tr_biex_l256_w-7.943282_n1.000000_m4.418540_r262144.000029.csv",
]

DEST = Path(__file__).resolve().parent.parent / "tests" / "data"


def main() -> None:
    for rel in FILES:
        out = DEST / rel
        if out.exists():
            print(f"skip  {rel}")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(BASE + rel) as resp:
            out.write_bytes(resp.read())
        print(f"saved {rel} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
