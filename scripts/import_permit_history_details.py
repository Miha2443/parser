"""Reproducibly package the explicitly supplied export-detail workbook."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.permit_history_details import DEFAULT_PATH, extract_history, write_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    payload = extract_history(args.source)
    write_history(payload, args.output)
    print(f"Packaged {len(payload['records'])} export-detail rows: {args.output}")
    print(f"Source SHA256: {payload['source']['sha256']}")


if __name__ == "__main__":
    main()
