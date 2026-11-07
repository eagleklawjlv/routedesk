"""Rebuild everoute/data/systems.json from a CCP static-data zip.

Usage:
  python scripts/refresh_universe.py /path/to/eve-online-static-data-jsonl.zip
  python scripts/refresh_universe.py --download
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from everoute.universe import build_cache_from_sde


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the bundled solar-system cache.")
    parser.add_argument("zip_path", nargs="?", help="Path to the CCP JSONL static-data zip.")
    parser.add_argument("--download", action="store_true", help="Download the latest public zip from CCP.")
    parser.add_argument(
        "--output",
        default=str(ROOT / "everoute" / "data" / "systems.json"),
        help="Where to write the compact cache.",
    )
    args = parser.parse_args()
    zip_path = Path(args.zip_path) if args.zip_path else Path("/tmp/eve-sde.jsonl.zip")
    if args.download or not args.zip_path:
        from everoute.http import HttpClient

        url = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
        print(f"Downloading {url}")
        blob = HttpClient().get_bytes(url, timeout=300)
        zip_path.write_bytes(blob)
    meta = build_cache_from_sde(zip_path, Path(args.output))
    print(
        f"Wrote {meta['system_count']} systems to {meta['path']} "
        f"(skipped unnamed: {meta['skipped_without_english_name']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
