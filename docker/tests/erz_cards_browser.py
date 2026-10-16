"""TOP-100 -> real Chrome card DOM -> workbook -> marts -> profile API replay.

The target URLs are the exact public TOP-100 emitted by fetch_top in the sibling
test. Card payloads are explicitly controlled Angular fixtures, not copies of
authenticated live data. Only browser HTTP transport and the login session are
replaced; production parsing, DOM readiness, coverage, XLSX/provenance writing,
DataAccess, mart construction and profile responses run without replacements.
"""
from contextlib import ExitStack
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
from threading import Thread
from urllib.parse import urlparse, urlunparse
from unittest.mock import patch

import pandas as pd

import erzrf_checker as ec
from pipeline.erz_cards_contract import NO_HISTORY_EVIDENCE, identity, target_keys, validate_workbook
from pipeline.selenium_utils import create_chrome
from pipeline.source_records import file_sha256


NO_HISTORY_IDS = {"28190308001", "27753932001", "28787766001"}


def card_page(target, index):
    """Controlled measurements exercise missing years, real zero and no history."""
    name = escape(target["name"])
    card_id = re.search(r"-(\d+)$", identity(target["card_url"])).group(1)
    prefix = f"<app-org-table><h3>{name}</h3><div>ID{card_id}</div></app-org-table>"
    if card_id in NO_HISTORY_IDS:
        content = prefix + f"<p>{NO_HISTORY_EVIDENCE}</p><app-org-table-deadline></app-org-table-deadline>"
    else:
        content = prefix + "<app-org-regions-of-presence><a class='accordion__head'>3 (По состоянию на 16.10.2026)</a></app-org-regions-of-presence>"
        content += "<app-org-table-deadline>"
        periods = [("Строится", int(str(target["metric"]).replace(" ", "")))]
        periods += [(f"Сдано в {year}", 0 if index == 1 and year == 2023 else 100000 + index * 100 + year)
                    for year in range(2022, 2027) if not (index == 0 and year == 2022)]
        for label, value in periods:
            formatted = f"{value:,}".replace(",", " ")
            content += (f"<div class='clmn'><p><b>{label}</b></p><p>{formatted} м²</p>"
                        "<p>1 000 м²</p><p>(5,5%)</p><div class='show_btn'>1,25 месяцев</div></div>")
        content += "</app-org-table-deadline>"
    # The actual wait must reject the initial Angular shell and await parsed
    # measured content; an element-name-only readiness test would race this.
    return ("<!doctype html><html><body><app-org-table></app-org-table><script>"
            "setTimeout(()=>{document.body.innerHTML=" + json.dumps(content, ensure_ascii=False)
            + ";},120);</script></body></html>").encode("utf-8")


def write_monitoring_fixture(root, targets):
    """A real workbook supplies only unrelated controlled profile inputs."""
    directory = root / "data/raw/realty/nashdom"
    directory.mkdir(parents=True)
    rows = [{"УИН": f"browser-{index}", "Группа компаний": target["name"], "Общая площадь": 20000,
             "Жилая площадь": 15000, "Строительный адрес": "Тестовый адрес", "Отрасли": "Жилые объекты",
             "Подтип объекта": "МКД", "Группировка": "Жилье", "Назначение": "Жилье"}
            for index, target in enumerate(targets)]
    with pd.ExcelWriter(directory / "monitoring_2_0_20261016.xlsx") as writer:
        pd.DataFrame([{**row, "Год ввода по Мосстату": 2025, "Месяц ввода по Мосстату": "июнь",
                       "Дата ввода по Мосстату": pd.Timestamp("2025-06-01")} for row in rows]).to_excel(
            writer, sheet_name="Реестр РВ", index=False)
        pd.DataFrame([{**row, "Срок выдачи РС": pd.Timestamp("2025-01-01"), "Год выдачи": 2025,
                       "Год ввода по графику": 2026, "Действующее РС": "Да", "Стадия строительства": "Строится",
                       "Дата выдачи": pd.Timestamp("2025-01-01"), "Статус объекта": "Строящийся"} for row in rows]).to_excel(
            writer, sheet_name="Реестр ОКС", index=False)


def check_cards_replay(raw: Path, workroot: Path):
    target_file = next(raw.glob("top_developers_rf_*.json"))
    payload = json.loads(target_file.read_text(encoding="utf-8"))
    targets = payload["developers"]
    assert len(targets) == 100
    expected = target_keys(targets)
    by_path = {urlparse(row["card_url"]).path: (index, row) for index, row in enumerate(targets)}
    assert len(by_path) == 100
    assert NO_HISTORY_IDS <= {path.rsplit("-", 1)[-1] for path in by_path}
    # The TOP replay serves its source route from loopback. Restore only that
    # route's official transport origin at the TOP/cards handoff; all 100 exact
    # developer URLs, names, places and IDs produced by fetch_top stay unchanged.
    selected = urlparse(payload["selection"]["url"])
    if selected.hostname == "127.0.0.1":
        payload["selection"]["url"] = urlunparse(selected._replace(scheme="https", netloc="erzrf.ru"))
        target_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    visited, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            try:
                index, row = by_path[parsed.path]
                assert parsed.query == urlparse(row["card_url"]).query
                visited.append(parsed.path)
                body = card_page(row, index)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                errors.append(repr(exc))
                self.send_error(500)

    with ExitStack() as stack:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        origin = f"127.0.0.1:{server.server_port}"

        class BrowserTransport:
            """Route official URLs to loopback while preserving path/query identity."""
            def __init__(self, directory):
                self.driver = create_chrome(download_dir=directory, page_load_strategy="eager")

            def get(self, url):
                identity(url)  # Keep production origin/ID validation before mapping.
                parsed = urlparse(url)
                self.driver.get(urlunparse(parsed._replace(scheme="http", netloc=origin)))

            @property
            def current_url(self):
                parsed = urlparse(self.driver.current_url)
                assert parsed.netloc == origin
                return urlunparse(parsed._replace(scheme="https", netloc="erzrf.ru"))

            def __getattr__(self, name):
                return getattr(self.driver, name)

        stack.enter_context(patch.multiple(ec, DOWNLOAD_DIR=raw, CARDS_DIR=raw / "cards"))
        stack.enter_context(patch.object(ec, "_authenticated_session", side_effect=BrowserTransport))
        stack.enter_context(patch.dict("os.environ", {"ERZ_CARDS_LIMIT": "0", "SELENIUM_SLEEP_SCALE": "0"}))
        state = {}
        result = ec.fetch_cards(state)
        assert result.complete, (result.error, result.failed, state)
        assert not errors, errors
        assert visited == [urlparse(row["card_url"]).path for row in targets]
        assert result.confirmed == expected
        assert state["erzrf_cards_attempt"]["complete"]
        workbook = result.files[0]
        rows, confirmed = validate_workbook(workbook, expected)
        assert len(rows) == 100 and confirmed == expected
        proof = json.loads(workbook.with_suffix(".xlsx.provenance.json").read_text(encoding="utf-8"))
        assert proof["targets_sha256"] == file_sha256(target_file)
        assert proof["sha256"] == file_sha256(workbook)
        assert len(proof["unavailable"]) == 3

    # Feed the exact production outputs into a private canonical release tree.
    root = workroot / "cards-publication"
    canonical_raw = root / "data/raw/realty/erzrf"
    shutil.copytree(raw, canonical_raw)
    write_monitoring_fixture(root, targets)
    from pipeline.data_access import DataAccess, DataContext
    from docker.coverage import validate_erz_sources
    access = DataAccess(DataContext(root, use_marts=False))
    validate_erz_sources(access)
    canonical = access.load_erzrf_cards()
    assert len(canonical) == 100
    assert pd.isna(canonical.iloc[0]["Сдано_2022_м²_num"])
    assert canonical.iloc[1]["Сдано_2023_м²_num"] == 0
    assert canonical.iloc[0]["Сдано_2025_м²_num"] == 102025
    no_history = canonical[canonical.history_status.eq("not_published")]
    assert len(no_history) == 3
    assert no_history.filter(regex="_num$").isna().all().all()

    from pipeline import build_realty_marts as build
    from pipeline import paths
    mart_dir = root / "data/marts/realty"
    with patch.multiple(build, ROOT=root, MART_DIR=mart_dir, MANIFEST=mart_dir / "manifest.json"), \
            patch.object(paths, "DOWNLOADS_DIR", root / "downloads"):
        assert build.build(strict=True, only={"erzrf_top", "erzrf_cards", "monitoring_2_0"}) == 0
    consumed = DataAccess(DataContext(root, use_marts=True))
    validate_erz_sources(consumed)
    pd.testing.assert_frame_equal(consumed.load_erzrf_cards(), canonical)

    # Exercise the real API and shared calculations using those built marts.
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.profile_service import ProfileService
    from pipeline.dev_name_utils import normalize_developer_name
    service = ProfileService(DataContext(root, use_marts=True), check_interval=0)
    with TestClient(create_app(service=service)) as client:
        for target in [targets[0], targets[1], *[target for target in targets
                    if urlparse(target["card_url"]).path.rsplit("-", 1)[-1] in NO_HISTORY_IDS]]:
            response = client.get("/api/v1/profile", params={"developer": normalize_developer_name(target["name"]), "region": "msk"})
            assert response.status_code == 200, response.text
            profile = response.json()["profiles"][0]
            card = profile["delays"]["cardRow"]
            assert card["source_id"] == identity(target["card_url"])
            if target is targets[0]:
                assert card["Сдано_2022_м²_num"] is None
                assert card["Сдано_2025_м²_num"] == 102025
            elif target is targets[1]:
                assert card["Сдано_2023_м²_num"] == 0
            else:
                assert card["history_status"] == "not_published"
                assert card["Сдано_2025_м²_num"] is None
    print("ERZ full card replay: actual TOP-100 -> 100 Chrome card navigations with delayed Angular DOM -> verified XLSX/provenance -> private real marts -> coverage and profile API; 97 measured, 3 explicit identified no-history cards, missing years stay null and published zero stays zero", flush=True)
