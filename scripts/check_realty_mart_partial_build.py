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
    original_mart_dir = brm.MART_DIR
    original_manifest = brm.MANIFEST
    original_prepare = brm._prepare_imports
    original_specs = brm._specs
    try:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            base = Path(tmp)
            mart_dir = base / "marts"
            mart_dir.mkdir()
            brm.MART_DIR = mart_dir
            brm.MANIFEST = mart_dir / "manifest.json"

            da = SimpleNamespace(
                load_alpha=lambda: pd.DataFrame({"x": [1]}),
                load_beta=lambda: pd.DataFrame({"y": [2]}),
            )
            brm._prepare_imports = lambda: da
            brm._specs = lambda _da: [
                brm.MartSpec("alpha", "load_alpha", lambda _da: []),
                brm.MartSpec("beta", "load_beta", lambda _da: []),
            ]

            _require(_quiet_build(only={"alpha"}) == 2, "partial build without manifest should fail")
            _require(not brm.MANIFEST.exists(), "failed partial build should not create manifest")

            brm.MANIFEST.write_text("{bad-json", encoding="utf-8")
            _require(_quiet_build(only={"alpha"}) == 2, "partial build with bad manifest should fail")
            _require(brm.MANIFEST.read_text(encoding="utf-8") == "{bad-json", "bad manifest should be preserved")

            beta = mart_dir / "beta.pkl"
            pd.to_pickle({"old": pd.DataFrame({"z": [3]})}, beta)
            brm.MANIFEST.write_text(
                json.dumps({
                    "built_at": "2026-07-06T00:00:00",
                    "marts": {
                        "beta": {
                            "file": str(beta.relative_to(ROOT)).replace("\\", "/"),
                        }
                    },
                }),
                encoding="utf-8",
            )
            _require(_quiet_build(only={"alpha"}) == 0, "partial build with manifest should succeed")
            manifest = json.loads(brm.MANIFEST.read_text(encoding="utf-8"))
            _require(set(manifest["marts"]) == {"alpha", "beta"}, "partial build should preserve existing mart entries")
            _require("built_at" in manifest["marts"]["beta"], "legacy manifest built_at should be copied to preserved entry")
    finally:
        brm.MART_DIR = original_mart_dir
        brm.MANIFEST = original_manifest
        brm._prepare_imports = original_prepare
        brm._specs = original_specs

    print("realty mart partial build checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
