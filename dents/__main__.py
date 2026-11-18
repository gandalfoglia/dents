"""CLI: python -m dents --user USERNAME --out ./dist"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .fetch import FetchError, fake_weeks, fetch_weeks
from .render import render, sky_state, zurich_now


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dents", description="Render GitHub contributions as the Dents du Midi.")
    ap.add_argument("--user", required=True, help="GitHub username")
    ap.add_argument("--out", default="./dist", help="output directory (default: ./dist)")
    ap.add_argument("--fake", action="store_true", help="use realistic random data instead of the API")
    ap.add_argument("--seed", type=int, default=None, help="seed for --fake data")
    ap.add_argument("--hour", type=int, choices=range(24), metavar="0-23",
                    help="force the Europe/Zurich hour that picks the sky")
    ap.add_argument("--scale", choices=("sqrt", "log"), default="sqrt", help="height scale (default: sqrt)")
    args = ap.parse_args(argv)

    try:
        weeks = fake_weeks(args.user, args.seed) if args.fake else fetch_weeks(args.user)
    except FetchError as e:
        print(f"dents: {e}", file=sys.stderr)
        return 1

    hour = args.hour if args.hour is not None else zurich_now().hour
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for dark in (False, True):
        path = out / f"dents-{'dark' if dark else 'light'}.svg"
        svg = render(weeks, args.user, hour, dark, args.scale)
        path.write_text(svg, encoding="utf-8")
        print(f"wrote {path} ({len(svg.encode()) / 1024:.1f} KB, {sky_state(hour)} sky, "
              f"{sum(c for _, c in weeks)} contributions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
