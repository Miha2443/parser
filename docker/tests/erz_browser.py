"""Captured ERZ markup: all ratings/years, real Chromium downloads and TOP-100.

Only HTTP transport and authentication are replaced in the replay. Production
DOM JavaScript, year switching, export clicks, Chrome downloads, XLSX checks,
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
import pandas as pd

import erzrf_checker as ec
from pipeline.selenium_utils import create_chrome
from scripts.check_erzrf_collector_contract import fixture_frame


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


def workbook(html, kind):
    frame = fixture_frame(kind)
    metric_column = {"obyem_stroitelstva": "Строится, м²", "obyem_vvoda": "Введено, м²",
                     "nakopl_vvod": "Введено, м²", "potreb_kachestva": "Средняя оценка",
                     "skorost": "Скорость строительства, дней/дом"}[kind]
    records = []
    for row in source_rows(html):
        record = frame.iloc[0].to_dict()
        record.update({"Место": row["rank"], "Наименование, регион": row["name"], metric_column: row["metric"]})
        records.append(record)
    # A valid full-list fixture deliberately differs from the UI counter.
    for rank in range(21, 25):
        record = frame.iloc[0].to_dict()
        record.update({"Место": rank, "Наименование, регион": f"Transport fixture {rank}",
                       metric_column: 1 if kind == "skorost" else 0})
        records.append(record)
    output = BytesIO()
    pd.DataFrame(records).to_excel(output, index=False)
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
    script = '''<script>
      document.addEventListener('change', async e=>{
        if(!e.target.matches('select[data-fixture-year]'))return;
        const url=new URL(location.href);url.searchParams.set('fixture_year',e.target.value);
        const html=await(await fetch(url)).text();
        document.getElementById('fixture-content').innerHTML=
          new DOMParser().parseFromString(html,'text/html').getElementById('fixture-content').innerHTML;
      });
      document.addEventListener('click', e=>{
        if(!e.target.matches('.fixture-export'))return;
        const url=new URL(location.href);url.pathname='/fixture-export';
        const year=document.querySelector('select[data-fixture-year]');
        if(year)url.searchParams.set('fixture_year',year.value);
        location.href=url;
      });</script>'''
    exports, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                region = "msk" if query.get("regionKey") == ["143443001"] else "rf"
                kind = next(key for key, value in ec.TOP_TYPES.items() if str(value) == query["topType"][0])
                year = query.get("fixture_year", ["2026"])[0] if kind == "obyem_vvoda" else "current"
                case = f"{region}_{kind}_{year}"
                html = pages[case]
                if parsed.path == "/fixture-export":
                    payload = workbook(html, kind)
                    exports.append(case)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    self.send_header("Content-Disposition", 'attachment; filename="fixture.xlsx"')
                else:
                    if kind == "obyem_stroitelstva" and "page" in query:
                        start = (int(query["page"][0]) - 1) * 20
                        html = paginated(html, expected["targets"][region][start:start + 20])
                    soup = BeautifulSoup(html, "html.parser")
                    container = soup.new_tag("div", id="fixture-content")
                    for child in list(soup.body.contents):
                        container.append(child.extract())
                    soup.body.append(container)
                    # Export controls retain the site's exact label and title.
                    html = str(soup).replace("</body>", '''<span title="нажмите для формирования и скачивания файла">
                      <span class="pointer fixture-export">Весь список</span></span></body>''') + script
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
            return driver
        stack.enter_context(patch.multiple(ec, BASE=base, DOWNLOAD_DIR=raw, PER_YEAR_RANGE=list(range(2022, 2027))))
        stack.enter_context(patch.object(ec, "_authenticated_session", side_effect=session))
        stack.enter_context(patch.dict("os.environ", {"SELENIUM_SLEEP_SCALE": "0"}))
        state = {}
        result = ec.fetch_top(state)
        assert not errors, errors
        assert result.complete, (result.error, exports)
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
        assert state["erzrf_top_attempt"]["complete"]
        print("ERZ browser download replay: 18 Chrome XLSX downloads + 2 TOP-100 JSON, verified and published atomically", flush=True)
