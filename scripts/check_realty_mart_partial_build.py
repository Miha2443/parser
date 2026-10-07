"""Fast checks for safe partial realty mart builds."""
from __future__ import annotations

import json
import contextlib
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import build_realty_marts as brm  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _quiet_build(*args, **kwargs) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return brm.build(*args, **kwargs)


def main() -> int:
    original_root = brm.ROOT
    original_mart_dir = brm.MART_DIR
    original_manifest = brm.MANIFEST
    original_prepare = brm._prepare_imports
    original_specs = brm._specs
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            brm.ROOT = base
            mart_dir = base / "marts"
            mart_dir.mkdir()
            brm.MART_DIR = mart_dir
            brm.MANIFEST = mart_dir / "manifest.json"

            da = SimpleNamespace(
                load_monitoring_2_0=lambda: pd.DataFrame({"x": [1]}),
                load_erzrf_top=lambda: pd.DataFrame({"y": [2]}),
            )
            brm._prepare_imports = lambda: da
            brm._specs = lambda _da: [
                brm.MartSpec("monitoring_2_0", "load_monitoring_2_0", lambda _da: []),
                brm.MartSpec("erzrf_top", "load_erzrf_top", lambda _da: []),
            ]

            _require(_quiet_build(only={"monitoring_2_0"}) == 0, "scoped bootstrap should succeed")
            manifest = json.loads(brm.MANIFEST.read_text(encoding="utf-8"))
            _require(set(manifest["marts"]) == {"monitoring_2_0"}, "bootstrap must contain only selected mart")
            _require({p.name for p in mart_dir.iterdir()} == {"manifest.json", "monitoring_2_0.pkl"},
                     "bootstrap wrote an unrequested mart")

            brm.MANIFEST.write_text("{bad-json", encoding="utf-8")
            _require(_quiet_build(only={"monitoring_2_0"}) == 2, "partial build with bad manifest should fail")
            _require(brm.MANIFEST.read_text(encoding="utf-8") == "{bad-json", "bad manifest should be preserved")

            erzrf_top = mart_dir / "erzrf_top.pkl"
            pd.to_pickle({"old": pd.DataFrame({"z": [3]})}, erzrf_top)
            brm.MANIFEST.write_text(
                json.dumps({
                    "built_at": "2026-07-06T00:00:00",
                    "marts": {
                        "erzrf_top": {
                            "file": str(erzrf_top.relative_to(base)).replace("\\", "/"),
                        }
                    },
                }),
                encoding="utf-8",
            )
            _require(_quiet_build(only={"monitoring_2_0"}) == 0, "partial build with manifest should succeed")
            manifest = json.loads(brm.MANIFEST.read_text(encoding="utf-8"))
            _require(set(manifest["marts"]) == {"monitoring_2_0", "erzrf_top"}, "partial build should preserve existing mart entries")
            _require("built_at" in manifest["marts"]["erzrf_top"], "legacy manifest built_at should be copied to preserved entry")
    finally:
        brm.ROOT = original_root
        brm.MART_DIR = original_mart_dir
        brm.MANIFEST = original_manifest
        brm._prepare_imports = original_prepare
        brm._specs = original_specs

    print("realty mart partial build checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
