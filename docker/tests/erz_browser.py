"""Captured ERZ markup: all ratings/years, real Chromium downloads and TOP-100.

Only HTTP transport and authentication are replaced in the replay. Production
DOM JavaScript, explicit year navigation, export clicks, Chrome downloads, XLSX checks,
provenance and transactional TOP publication run unchanged in a private folder.
No production data or live authenticated session is used by this test.
"""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
from threading import Thread
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from bs4 import BeautifulSoup
from openpyxl import Workbook

import erzrf_checker as ec
from pipeline.selenium_utils import create_chrome


FIXTURES = Path(__file__).parent / "fixtures/erz_20261016"


def text(cell):
    copy = BeautifulSoup(str(cell), "html.parser")
    for label in copy.select(".developer-td__title"):
        label.decompose()
    return re.sub(r"\s+", " ", copy.get_text()).strip()


def source_rows(html):
    result = []
    for row in BeautifulSoup(html, "html.parser").select("li.developer"):
        link = row.select_one('a[href*="/zastroyschiki/brand/"]')
        rank = row.select_one(".developer-td-1,.developer-speed-td-1")
        metric = row.select_one(".developer-td-4,.developer-speed-td-4,.developer-td-q-4")
        result.append({"rank": int(text(rank)), "name": text(link),
                       "metric": float(text(metric).replace(" ", "").replace(",", "."))})
    return result


def workbook(html, kind, region):
    schemas = json.loads((FIXTURES / "workbook_schemas.json").read_text(encoding="utf-8"))["exports"]
    schema = next(case for case in schemas if case["sorting"] == kind and case["region"] == region)
    headers = schema["headers"]
    normalised = [ec._normalise_label(value) for value in headers]
    name_column = next(i for i, value in enumerate(normalised) if value in {"наименование", "наименование, регион"})
    metric_column = {"obyem_stroitelstva": "Строится, м²", "obyem_vvoda": "Введено, м²",
                     "nakopl_vvod": "Введено, м²", "potreb_kachestva": "Средняя оценка",
                     "skorost": "Скорость строительства, дней/дом"}[kind]
    book = Workbook()
    book.active.append(headers)

    def append(rank, name, metric):
        values = schema["sample_rows"][0].copy()
        values[normalised.index("место")] = rank
        values[name_column] = name
        values[normalised.index(ec._normalise_label(metric_column))] = metric
        book.active.append(values)

    for row in source_rows(html):
        append(row["rank"], row["name"], row["metric"])
    # A valid full-list fixture deliberately differs from the UI counter.
    for rank in range(21, 25):
        append(rank, f"Transport fixture {rank}", 1 if kind == "skorost" else 0)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def paginated(html, developers):
    soup = BeautifulSoup(html, "html.parser")
    template = soup.select_one("li.developer")
    parent = template.parent
    parent.clear()
    for developer in developers:
        row = BeautifulSoup(str(template), "html.parser").select_one("li.developer")
        cells = sorted(row.select('[class*="developer-td-"]'), key=lambda e: int(next(
            c.removeprefix("developer-td-") for c in e.get("class", [])
            if re.fullmatch(r"developer-td-\d+", c))))
        for cell, value in zip(cells, developer["cells"], strict=True):
            cell.clear()
            cell.append(value)
        name = row.select_one(".developer-td-3")
        name.clear()
        link = soup.new_tag("a", href=developer["card_url"])
        link.string = developer["name"]
        name.append(link)
        parent.append(row)
    return str(soup)


def check_matrix(driver):
    from urllib.parse import quote
    driver.get("data:text/html;charset=utf-8," + quote('''<app-root></app-root><table>
      <tr><th>Место</th><th>Изменение</th><th>Застройщик</th><th>Строится</th></tr>
      <tr><td>1</td><td>0</td><td><a href="https://erzrf.ru/zastroyschiki/brand/seo">SEO</a></td><td>125</td></tr></table>'''))
    assert ec._scrape_developers_from_table(driver) == []
    captured_empty = (FIXTURES / "empty_rf_construction.html").read_text(encoding="utf-8")
    driver.get("data:text/html;charset=utf-8," + quote(captured_empty))
    assert ec._scrape_developers_from_table(driver) == []
    assert ec._read_top_selection(driver).get("empty_state"), "captured ERZ empty notice was not detected"
    assert not ec._wait_for_top_content(driver, timeout=1)
    # Zero is not a successful population, even when an explicit counter exists.
    driver.execute_script("document.body.insertAdjacentHTML('beforeend', '<p class=\"pagination__hint\">0 - 0 из 0 застройщиков</p>')")
    assert not ec._wait_for_top_content(driver, timeout=0)
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    for case in expected["top"]:
        html = (FIXTURES / (case["case"] + ".html")).read_text(encoding="utf-8")
        driver.get("data:text/html;charset=utf-8," + quote(html))
        assert ec._wait_for_top_content(driver, timeout=3), case["case"]
        rows = ec._scrape_developers_from_table(driver)
        assert len(rows) == 20 and [int(row["place"]) for row in rows] == list(range(1, 21)), case
        assert rows[0]["name"] == case["first_name"], case
        assert ec._visible_metric(rows[0]) == case["first_metric"], case
        assert all(ec._visible_metric(row) is not None for row in rows), case
        assert ec._top_ui_count(ec._read_top_selection(driver))[0] == case["count"], case
        driver.execute_script('''document.querySelectorAll(
          'li.developer:first-child .developer-td-4,li.developer:first-child .developer-speed-td-4,li.developer:first-child .developer-td-q-4'
        ).forEach(e=>e.remove());''')
        assert ec._visible_metric(ec._scrape_developers_from_table(driver)[0]) is None, case
        assert not ec._wait_for_top_content(driver, timeout=0), case
    print("ERZ browser matrix: all 18 real layouts/years and missing-primary-metric rejection passed", flush=True)


def check_download_replay():
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    pages = {case["case"]: (FIXTURES / (case["case"] + ".html")).read_text(encoding="utf-8")
             for case in expected["top"]}
    # Reproduce Select2/Angular ignoring or reverting synthetic change events.
    # Route date is the server input; no fixture-only event mutates the result.
    script = '''<script>
      document.addEventListener('change', e=>{
        if(!e.target.matches('select[data-fixture-year]'))return;
        e.target.value='2026';fetch('/fixture-unexpected-year-change');
      });
      document.addEventListener('click', e=>{
        if(!e.target.matches('.fixture-export'))return;
        const url=new URL(location.href);url.pathname='/fixture-export';
        location.href=url;
      });</script>'''
    exports, errors, navigations, unexpected_changes = [], [], [], []
    # A fully rendered ERZ page can explicitly report no matching developers.
    # Exercise the user's alternate-indicator/back workaround in the complete
    # production flow, including historical years, all-developers and TOP-100.
    empty_budgets = {
        ("rf_obyem_stroitelstva_current", None): 5,
        ("msk_obyem_vvoda_2022", None): 2,
        ("rf_obyem_vvoda_2024", None): 1,
        ("rf_obyem_stroitelstva_current", "3"): 2,
        ("rf_potreb_kachestva_current", None): 1,
    }
    empty_served = {key: 0 for key in empty_budgets}
    route_visits = []
    empty_markup = (FIXTURES / "empty_rf_construction.html").read_text(encoding="utf-8")

    def empty_table(html):
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.select("li.developer"):
            row.decompose()
        for counter in soup.select(".pagination__hint"):
            counter.decompose()
        notice = BeautifulSoup(empty_markup, "html.parser").select_one("sws-loading")
        soup.body.append(notice)
        return str(soup)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/fixture-unexpected-year-change":
                    unexpected_changes.append(self.path)
                    self.send_response(204)
                    self.end_headers()
                    return
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                region = "msk" if query.get("regionKey") == ["143443001"] else "rf"
                kind = next(key for key, value in ec.TOP_TYPES.items() if str(value) == query["topType"][0])
                year = query.get("date", ["2026"])[0] if kind == "obyem_vvoda" else "current"
                case = f"{region}_{kind}_{year}"
                html = pages[case]
                if parsed.path == "/fixture-export":
                    payload = workbook(html, kind, region)
                    exports.append(case)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    self.send_header("Content-Disposition", 'attachment; filename="fixture.xlsx"')
                else:
                    navigations.append(case)
                    route_visits.append((case, query.get("page", [None])[0], query))
                    if kind == "obyem_stroitelstva" and "page" in query:
                        start = (int(query["page"][0]) - 1) * 20
                        html = paginated(html, expected["targets"][region][start:start + 20])
                    soup = BeautifulSoup(html, "html.parser")
                    container = soup.new_tag("div", id="fixture-content")
                    for child in list(soup.body.contents):
                        container.append(child.extract())
                    soup.body.append(container)
                    # Angular may initially retain the default year's complete,
                    # measured table even though location already contains date.
                    # Reloading 2026 is also valid without any table-digest change.
                    settled_content = str(container)
                    key = (case, query.get("page", [None])[0])
                    is_empty = (query.get("fixturePermanentEmpty") == ["1"]
                                or empty_served.get(key, 0) < empty_budgets.get(key, 0))
                    if is_empty:
                        if key in empty_served and query.get("fixturePermanentEmpty") != ["1"]:
                            empty_served[key] += 1
                        container.clear()
                        for child in list(BeautifulSoup(empty_table(html), "html.parser").body.contents):
                            container.append(child.extract())
                        delayed = ""
                    elif kind == "obyem_vvoda" and year != "2026":
                        stale = BeautifulSoup(pages[f"{region}_obyem_vvoda_2026"], "html.parser")
                        container.clear()
                        for child in list(stale.body.contents):
                            container.append(child.extract())
                        delayed = "<script>setTimeout(()=>{document.getElementById('fixture-content').outerHTML=" + json.dumps(settled_content) + ";},650);</script>"
                    else:
                        delayed = ""
                    # Export controls retain the site's exact label and title.
                    html = str(soup).replace("</body>", '''<span title="нажмите для формирования и скачивания файла">
                      <span class="pointer fixture-export">Весь список</span></span></body>''') + script + delayed
                    payload = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                errors.append(repr(exc))
                self.send_error(500)

    with tempfile.TemporaryDirectory(prefix="erz-browser-replay-") as tmp, ExitStack() as stack:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        raw = Path(tmp) / "raw/erzrf"
        base = f"http://127.0.0.1:{server.server_port}"
        def session(directory):
            driver = create_chrome(download_dir=directory, page_load_strategy="eager")
            # An already selected current year need not change the measured
            # table digest after a genuine navigation/reload acknowledgement.
            request = ec.TopExport("rf", "obyem_vvoda", 2026)
            url = ec._build_top_url(request.region, request.sorting_key, year=request.year)
            driver.get(url)
            assert ec._wait_for_top_content(driver, timeout=5, request=request, top_types=ec.TOP_TYPES)
            before = ec._top_table_digest(driver)
            driver.get(url)
            assert ec._wait_for_top_content(driver, timeout=5, request=request, top_types=ec.TOP_TYPES)
            assert before == ec._top_table_digest(driver)
            return driver
        stack.enter_context(patch.multiple(ec, BASE=base, DOWNLOAD_DIR=raw, PER_YEAR_RANGE=list(range(2022, 2027))))
        stack.enter_context(patch.object(ec, "_authenticated_session", side_effect=session))
        stack.enter_context(patch.dict("os.environ", {"SELENIUM_SLEEP_SCALE": "0"}))
        state = {}
        result = ec.fetch_top(state)
        assert not errors, errors
        assert not unexpected_changes, unexpected_changes
        assert result.complete, (result.error, exports)
        assert empty_served == empty_budgets, empty_served
        for case, page, query in route_visits:
            if "obyem_vvoda" in case:
                assert query.get("date") == [case.rsplit("_", 1)[1]], (case, query)
            if case.startswith("rf_") and any(key in case for key in ec.ALL_DEVELOPERS_SORTINGS):
                assert query.get("isAllDevelopersShown") == ["true"], (case, query)
        page3 = [item for item in route_visits if item[:2] == ("rf_obyem_stroitelstva_current", "3")]
        assert len(page3) == 3, page3
        annual_cases = {f"{region}_obyem_vvoda_{year}" for region in ("rf", "msk") for year in range(2022, 2027)}
        assert annual_cases <= set(navigations), navigations
        assert len(exports) == 18 and set(exports) == set(pages), exports
        assert len(result.files) == 20
        for region in ("rf", "msk"):
            payload = json.loads(next(raw.glob(f"top_developers_{region}_*.json")).read_text(encoding="utf-8"))
            assert [int(row["place"]) for row in payload["developers"]] == list(range(1, 101))
            assert [row["card_url"] for row in payload["developers"]] == [row["card_url"] for row in expected["targets"][region]]
        for path in result.files:
            if path.suffix == ".xlsx":
                proof = json.loads(path.with_suffix(".xlsx.provenance.json").read_text(encoding="utf-8"))
                assert proof["request_correlation"]["matched_rows"] == 20, proof
                if proof["sorting"] == "obyem_vvoda":
                    requested_year = proof["requested_year"]
                    assert parse_qs(urlparse(proof["observed_url"]).query)["date"] == [str(requested_year)], proof
                    assert proof["selected_year"] == requested_year, proof
                    assert proof["year_selection_evidence"]["year"] == requested_year, proof
                    assert proof["year_selection_evidence"]["selection_mode"] == "url_date", proof
                if proof["region"] == "msk" and proof["sorting"] in {"obyem_stroitelstva", "obyem_vvoda"}:
                    assert len(proof["workbook"]["project_columns"]) == 19, proof
                    assert proof["workbook"]["empty_separator_columns"] == [33], proof
        from pipeline.data_access import DataAccess, DataContext
        access = DataAccess(DataContext(Path(tmp), use_marts=False))
        access.ERZRF_PATHS = [raw]
        payload = access.load_erzrf_top()
        for kind in ec.TOP_TYPES:
            for region in ("rf", "msk"):
                assert len(payload[kind][region]) == 24, (kind, region)
        assert state["erzrf_top_attempt"]["complete"]
        # A provider that stays empty gets exactly five recoveries. This must
        # never click an export or mutate any already published source file.
        before_files = {path: path.read_bytes() for path in raw.rglob("*") if path.is_file()}
        before_exports = exports.copy()
        recovery_driver = create_chrome(download_dir=Path(tmp) / "persistent-empty-downloads", page_load_strategy="eager")
        try:
            request = ec.TopExport("rf", "obyem_stroitelstva")
            target = ec._build_top_url(request.region, request.sorting_key) + "&fixturePermanentEmpty=1"
            recovery_driver.get(target)
            before_visits = len(route_visits)
            assert not ec._ensure_top_content(recovery_driver, request=request, target_url=target,
                                              timeout=1, recovery_attempts=5)
            visits = route_visits[before_visits:]
            target_visits = [item for item in visits if item[2].get("fixturePermanentEmpty") == ["1"]]
            assert len(target_visits) == 5, visits
            assert len(visits) == 10, visits
            assert recovery_driver.current_url == target
            assert exports == before_exports
            assert {path: path.read_bytes() for path in raw.rglob("*") if path.is_file()} == before_files
            print("ERZ persistent-empty browser: exactly five alternate/back recoveries, no export or source changes", flush=True)
        finally:
            recovery_driver.quit()
        from docker.tests.erz_cards_browser import check_cards_replay
        check_cards_replay(raw, Path(tmp))
        print("ERZ browser download replay: 18 Chrome XLSX downloads + 2 TOP-100 JSON; explicit empty tables recovered by alternate-indicator/back cycles (including fifth recovery), historical date, TOP-100 page 3 and all-developers restored; all ten tables loaded by DataAccess", flush=True)


def check_provider_replay():
    """Exercise run_source's preflight with real Chrome, not only fetch_top.

    Reproduce 8.txt's requests connection error while Chrome can load the page;
    the real child process must start. True maintenance/503 must still skip.
    """
    import os
    from pipeline import source_availability as availability
    from scripts import update_realty as update

    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            requests_seen.append(path)
            bodies = {"/healthy": "<h1>ТОП застройщиков РФ</h1>", "/maintenance": "<h1>Технические работы</h1>",
                      "/unavailable": "<h1>Service unavailable</h1>", "/forbidden": "<h1>Forbidden</h1>", "/blank": ""}
            payload = ("<!doctype html><html><body>" + bodies[path] + "</body></html>").encode("utf-8")
            self.send_response(503 if path == "/unavailable" else 403 if path == "/forbidden" else 200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with tempfile.TemporaryDirectory(prefix="provider-browser-replay-") as tmp, ExitStack() as stack:
        root = Path(tmp)
        marker = root / "child-started"
        script = root / "child.py"
        script.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n", encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        stack.enter_context(patch.multiple(update, ROOT=root, REALTY_ROOT=root / "data/raw/realty"))
        stack.enter_context(patch.dict(update.SOURCE_MAP, {"erz-top": (str(script), [])}))
        stack.enter_context(patch.object(availability, "http_check", side_effect=lambda url: {
            "url": url, "checked_at": availability.stamp(), "transport": "http",
            "network_error": "connection_unavailable"}))
        for path, expected in (("/healthy", "success"), ("/unavailable", "skipped"),
                               ("/maintenance", "skipped"), ("/forbidden", "success"), ("/blank", "success")):
            url = f"http://127.0.0.1:{server.server_port}{path}"
            marker.unlink(missing_ok=True)
            started = availability.stamp()
            with patch.dict(availability.URLS, {"erz-top": (url,)}):
                result = update.run_source("erz-top", {**os.environ, "REALTY_RUN_ID": "browser-replay"}, do_archive=False)
                assert result["status"] == expected, result
                availability.validate_outcome(result, "erz-top", "browser-replay", started, availability.stamp())
            assert marker.exists() == (expected == "success"), result
            if expected == "skipped":
                assert {item["transport"] for item in result["evidence"]} == {"browser"}, result
                assert requests_seen.count(path) == 2, requests_seen
        print("Provider preflight replay: HTTP failure + real Chrome + real collector child; 503/maintenance confirmed twice; 403/blank defer to collector", flush=True)
