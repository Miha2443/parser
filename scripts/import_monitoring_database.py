"""Import raw monitoring XLSX into the local shadow database, optionally publish."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.database import MonitoringDatabase
from pipeline.database.import_monitoring import prepare_monitoring


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Explicit raw monitoring XLSX")
    parser.add_argument("--database", type=Path, default=ROOT / "outputs/audit/monitoring.sqlite")
    parser.add_argument("--publish", action="store_true", help="Atomically publish after validation")
    args = parser.parse_args()
    database = MonitoringDatabase(args.database)
    database.initialize()
    expected = database.active_state()
    prepared = prepare_monitoring(args.source, root=ROOT)
    result = database.stage_version(**asdict(prepared))
    if args.publish:
        result["publication"] = asdict(database.publish(result["version_id"], expected=expected))
    result["validation"] = prepared.validation
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
