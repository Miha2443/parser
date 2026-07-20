"""Offline checks for Fedstat 34118 chunk assembly.

The real Fedstat endpoint is flaky and can be unavailable from CI/dev
machines. This test exercises the local fallback path used after download:
small 34118 chunks -> merged xlsx -> dashboard parser.
"""
from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fedstat_checker as fc  # noqa: E402
from app import data_access as da  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _fake_sdmx(payload: dict) -> bytes:
    root = ET.Element("GenericData")
    data_set = ET.SubElement(root, "DataSet")
    years = [x for x in payload["selectedFilterIds"] if fc._is_34118_year_filter(x)]
    periods = [x for x in payload["selectedFilterIds"] if fc._is_34118_period_filter(x)]
    regions = [x for x in payload["selectedFilterIds"] if str(x).startswith("57831_")]
    categories = [x for x in payload["selectedFilterIds"] if str(x).startswith("58389_")]

    # Values only need to be non-empty and stable; correctness here is about
    # preserving coordinates and labels through the chunk merge.
    for year in years:
        year_num = int(year.split("_", 1)[1])
        for period in periods:
            for region in regions:
                for category in categories:
                    series = ET.SubElement(data_set, "Series")
                    key = ET.SubElement(series, "SeriesKey")
                    for field_id, value in (
                        ("3", year),
                        ("33560", period),
                        ("57831", region),
                        ("58389", category),
                    ):
                        ET.SubElement(key, "Value", {"id": field_id, "value": value})
                    obs = ET.SubElement(series, "Obs")
                    ET.SubElement(obs, "ObsValue", {"value": str(year_num)})

    return ET.tostring(root, encoding="utf-8")


def _install_fake_download():
    original = fc.download_excel
    calls: list[tuple[str, int, int]] = []

    def fake_download(
        indicator_id,
        save_dir,
        *,
        remote_date=None,
        driver=None,
        payload_template_override=None,
        save_path_override=None,
        allow_34118_chunks=True,
    ):
        payload = payload_template_override
        _require(payload is not None, "chunk download must pass a payload override")
        years = [x for x in payload["selectedFilterIds"] if fc._is_34118_year_filter(x)]
        periods = [x for x in payload["selectedFilterIds"] if fc._is_34118_period_filter(x)]
        calls.append((indicator_id, len(years), len(periods)))
        return fc._sdmx_34118_to_excel(_fake_sdmx(payload), payload, Path(save_path_override))

    fc.download_excel = fake_download
    return original, calls


class DummyDriver:
    def quit(self) -> None:
        pass


def _check_part(indicator_id: str, *, period_chunk_size: int, year_chunk_size: int) -> None:
    payload = fc._payload_34118_part(indicator_id)
    expected_years = set(range(2015, 2023)) if indicator_id.endswith("часть1") else set(range(2023, 2027))
    original_download, calls = _install_fake_download()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = fc._download_34118_period_chunks(
                indicator_id,
                payload,
                Path(tmp),
                Path(tmp) / f"{indicator_id}.xls",
                remote_date="15.07.2026",
                period_chunk_size=period_chunk_size,
                year_chunk_size=year_chunk_size,
            )
            _require(out is not None and out.exists(), f"{indicator_id} did not produce an output file")
            parsed = da._parse_emiss_34118_periods_file(out)
            _require(not parsed.empty, f"{indicator_id} parsed dataframe is empty")
            _require(set(parsed["year"].astype(int)) == expected_years, f"{indicator_id} years mismatch")
            _require({"РФ", "Москва"}.issubset(set(parsed["region"])), f"{indicator_id} regions missing")
            _require("ИЖС" in parsed.columns and "МКД" in parsed.columns, f"{indicator_id} metrics missing")
            _require((parsed["period_type"] == "month").any(), f"{indicator_id} month rows missing")
            _require((parsed["period_type"] == "quarter").any(), f"{indicator_id} quarter rows missing")
            _require((parsed["period_type"] == "year").any(), f"{indicator_id} annual rows missing")
            _require(calls, f"{indicator_id} did not request any chunks")
            _require(max(year_count for _, year_count, _ in calls) <= year_chunk_size, "year chunk too large")
            _require(max(period_count for _, _, period_count in calls) <= period_chunk_size, "period chunk too large")
    finally:
        fc.download_excel = original_download


def _check_targeted_run_is_strict() -> None:
    original_download_dir = fc.DOWNLOAD_DIR
    original_state_file = fc.STATE_FILE
    original_indicators = fc.INDICATORS
    original_create_driver = fc.create_driver
    original_get_last_update_date = fc.get_last_update_date
    original_download_excel = fc.download_excel

    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fc.DOWNLOAD_DIR = root / "downloads"
            fc.STATE_FILE = root / "fedstat_state.json"
            fc.INDICATORS = {
                "34118_часть1": "part1",
                "34118_часть2": "part2",
            }
            fc.create_driver = lambda download_dir=None: DummyDriver()
            fc.get_last_update_date = lambda _driver, _indicator_id: "15.07.2026"

            def fake_download(indicator_id, save_dir, *, remote_date=None, driver=None):
                if indicator_id == "34118_часть1":
                    save_dir.mkdir(parents=True, exist_ok=True)
                    path = save_dir / "part1.xlsx"
                    path.write_bytes(b"xlsx")
                    return path
                return None

            fc.download_excel = fake_download

            files, ok = fc.run(force=True, only_ids=["34118_часть1", "34118_часть2"])
            _require(len(files) == 1, "strict run setup should download exactly one fake file")
            _require(not ok, "targeted --only run must fail when one requested part is missing")
    finally:
        fc.DOWNLOAD_DIR = original_download_dir
        fc.STATE_FILE = original_state_file
        fc.INDICATORS = original_indicators
        fc.create_driver = original_create_driver
        fc.get_last_update_date = original_get_last_update_date
        fc.download_excel = original_download_excel


def main() -> int:
    _require(fc._parse_only_ids("34118") == ["34118_часть1", "34118_часть2"], "--only=34118 expansion broke")
    _check_part("34118_часть1", period_chunk_size=1, year_chunk_size=99)
    _check_part("34118_часть2", period_chunk_size=1, year_chunk_size=1)
    _check_targeted_run_is_strict()
    print("fedstat 34118 chunk checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
