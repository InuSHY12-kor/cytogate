"""Command line: python -m cytogate info <file-or-folder> [--keywords]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .io import FcsReadError, load_fcs, load_folder


def _info(path: Path, show_keywords: bool) -> int:
    if path.is_dir():
        samples, errors = load_folder(path)
    else:
        try:
            samples, errors = [load_fcs(path)], {}
        except FcsReadError as exc:
            samples, errors = [], {path.name: str(exc)}
    for s in samples:
        print(f"\n== {s.name}")
        print(f"FCS {s.version} | {s.event_count:,} events | {len(s.channels)} channels | "
              f"cytometer: {s.cytometer or '-'} | date: {s.acquisition_date or '-'}")
        print(s.channel_table().to_string(index=False))
        spill = s.spillover()
        print(f"spillover matrix: {'%d x %d' % spill.shape if spill is not None else 'none'}")
        for note in s.warnings:
            print(f"note: {note}")
        if show_keywords:
            for k, v in s.keywords.items():
                print(f"  {k} = {v}")
    for name, err in errors.items():
        print(f"\n!! {name}: {err}", file=sys.stderr)
    return 1 if errors and not samples else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cytogate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_info = sub.add_parser("info", help="show channels and keywords of FCS files")
    p_info.add_argument("path", type=Path)
    p_info.add_argument("--keywords", action="store_true", help="list all keywords")
    args = parser.parse_args(argv)
    if args.cmd == "info":
        return _info(args.path, args.keywords)
    return 2


if __name__ == "__main__":
    sys.exit(main())
