"""Fast checks for atomic binary file writes used by downloaders."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.file_utils import stream_response_atomic, validate_excel_file, write_bytes_atomic  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class Response:
    def __init__(self, chunks, *, fail_after: int | None = None):
        self.chunks = chunks
        self.fail_after = fail_after

    def iter_content(self, chunk_size: int):
        for i, chunk in enumerate(self.chunks):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("synthetic stream failure")
            yield chunk


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        bytes_target = root / "bytes.xlsx"
        written = write_bytes_atomic(bytes_target, b"new-content")
        _require(written == len(b"new-content"), "write_bytes_atomic returned bad size")
        _require(bytes_target.read_bytes() == b"new-content", "write_bytes_atomic did not write target")
        _require(not (root / "bytes.xlsx.tmp").exists(), "write_bytes_atomic left temp file")

        bytes_target.write_bytes(b"old-content")
        try:
            write_bytes_atomic(bytes_target, b"")
        except ValueError:
            pass
        else:
            raise AssertionError("empty bytes write should fail")
        _require(bytes_target.read_bytes() == b"old-content", "empty bytes write replaced old target")
        _require(not (root / "bytes.xlsx.tmp").exists(), "empty bytes write left temp file")

        stream_target = root / "stream.xlsx"
        stream_target.write_bytes(b"old-content")
        written = stream_response_atomic(Response([b"new", b"", b"-content"]), stream_target)
        _require(written == len(b"new-content"), "stream_response_atomic returned bad size")
        _require(stream_target.read_bytes() == b"new-content", "stream_response_atomic did not replace target")
        _require(not (root / "stream.xlsx.tmp").exists(), "stream_response_atomic left temp file")

        stream_target.write_bytes(b"old-content")
        try:
            stream_response_atomic(Response([b"partial", b"-content"], fail_after=1), stream_target)
        except RuntimeError:
            pass
        else:
            raise AssertionError("failed stream should raise")
        _require(stream_target.read_bytes() == b"old-content", "failed stream replaced old target")
        _require(not (root / "stream.xlsx.tmp").exists(), "failed stream left temp file")

        try:
            stream_response_atomic(Response([b""]), stream_target)
        except ValueError:
            pass
        else:
            raise AssertionError("empty stream should fail")
        _require(stream_target.read_bytes() == b"old-content", "empty stream replaced old target")
        _require(not (root / "stream.xlsx.tmp").exists(), "empty stream left temp file")

        excel_target = root / "validated.xlsx"
        write_bytes_atomic(excel_target, b"PK\x03\x04xlsx-bytes", validate=validate_excel_file)
        _require(excel_target.read_bytes().startswith(b"PK"), "valid xlsx signature was rejected")

        excel_target.write_bytes(b"PK\x03\x04old-xlsx")
        try:
            write_bytes_atomic(excel_target, b"<html>login</html>", validate=validate_excel_file)
        except ValueError:
            pass
        else:
            raise AssertionError("HTML response should fail Excel validation")
        _require(excel_target.read_bytes() == b"PK\x03\x04old-xlsx", "invalid Excel replaced old target")
        _require(not (root / "validated.xlsx.tmp").exists(), "invalid Excel left temp file")

    print("atomic file write checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
