"""Run the BAT wrapper against a fake child in a temporary project (no task registration)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if sys.platform != "win32" or not shutil.which("py"):
        print("scheduler wrapper execution: skipped (Windows py launcher required)")
        return 0
    with tempfile.TemporaryDirectory(prefix="realty wrapper ") as temp:
        root = Path(temp)
        scripts = root / "scripts"
        scripts.mkdir()
        wrapper = scripts / "update_realty_scheduled.bat"
        shutil.copyfile(ROOT / "scripts/update_realty_scheduled.bat", wrapper)
        (scripts / "update_realty.py").write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            "Path('observed.json').write_text(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), "
            "'disabled': os.environ.get('TDM_DISABLED')}))\nsys.exit(17)\n", encoding="utf-8")
        args = ["monitoring", "--plan", "--no-notify", "--no-marts"]
        env = {**os.environ, "TDM_DISABLED": "1"}
        # cmd requires the additional outer quotes when the batch path contains spaces.
        command = f'"{os.environ["ComSpec"]}" /d /s /c ""{wrapper}" {" ".join(args)}"'
        result = subprocess.run(command, cwd=root, env=env,
                                capture_output=True, text=True, errors="replace", timeout=30)
        assert result.returncode == 17, (result.returncode, result.stdout, result.stderr)
        observed = json.loads((root / "observed.json").read_text())
        assert observed["args"] == args, observed
        assert Path(observed["cwd"]) == root, observed
        assert observed["disabled"] == "1", observed
        logs = list((root / "data/processed").glob("etl_*.log"))
        assert len(logs) == 1
        assert "Exit code: 17" in logs[0].read_text(errors="replace")
    print("scheduler wrapper runtime checks: ok (fake child, temporary project)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
