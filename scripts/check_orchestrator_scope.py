"""Check selected/all/invalid processed builds without touching production files."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import orchestrator as orchestrator
import update_realty as ur


def main() -> int:
    with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
        root = Path(temp)
        stack.enter_context(patch.dict(os.environ, {"TDM_DISABLED": "1"}))
        for name, value in {"DATA_PROCESSED": root / "processed", "DOWNLOADS_DIR": root / "downloads",
                            "ETL_LOCK": root / ".etl.lock"}.items():
            stack.enter_context(patch.object(orchestrator, name, value))
        directories = stack.enter_context(patch.object(orchestrator, "ensure_dirs"))
        stack.enter_context(patch.object(orchestrator, "_acquire_lock", return_value=True))
        stack.enter_context(patch.object(orchestrator, "_release_lock"))
        audit = Mock(entries=[])
        audit.finalize.return_value = {"success": 1, "skip": 0, "error": 0}
        stack.enter_context(patch.object(orchestrator, "AuditRun", return_value=audit))
        stack.enter_context(patch.object(orchestrator, "send_summary"))
        stack.enter_context(patch.object(orchestrator, "send_critical"))
        process = stack.enter_context(patch.object(orchestrator, "_process_one"))
        stack.enter_context(redirect_stdout(StringIO()))
        stack.enter_context(redirect_stderr(StringIO()))
        assert orchestrator.run_all(download=False, only={"realty_monitoring_2_0"}) == 0
        assert [call.args[0].id for call in process.call_args_list] == ["realty_monitoring_2_0"]
        process.reset_mock()
        assert orchestrator.run_all(download=False) == 0
        assert [call.args[0].id for call in process.call_args_list] == [ind.id for ind in orchestrator.INDICATORS]
        directories.reset_mock()
        process.reset_mock()
        try:
            orchestrator.run_all(download=False, only={"bogus"})
        except ValueError:
            pass
        else:
            raise AssertionError("unknown scope accepted")
        directories.assert_not_called()
        process.assert_not_called()
        with patch.object(sys, "argv", ["orchestrator.py", "--skip-download", "--only", "bogus"]):
            try:
                orchestrator.main()
            except SystemExit as exc:
                assert exc.code == 2
            else:
                raise AssertionError("invalid CLI scope accepted")
        with patch.object(sys, "argv", ["orchestrator.py", "--skip-download", "--only", "realty_monitoring_2_0"]):
            assert orchestrator.main() == 0
        assert ur.select_processed_indicators(["monitoring"]) == {"realty_monitoring_2_0"}
        assert ur.select_processed_indicators(["monitoring", "erz-top"]) == {
            "realty_monitoring_2_0", "realty_erzrf_top_rf", "realty_erzrf_top_msk"}
        assert ur.select_processed_indicators(["fedstat", "rosstat"]) == {
            ind.id for ind in orchestrator.INDICATORS if ind.source in {"fedstat", "rosstat"}}
    print("orchestrator scope checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
