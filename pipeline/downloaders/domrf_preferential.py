"""Acquire DOM.RF's public regional XLSX and atomically refresh its source.

Only links advertised by the reporting page are followed. An ordinary browser
is a fallback for JavaScript-rendered menus or HTTP refusal, never a CAPTCHA
solver. Raw regional workbooks live only in a private temporary directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urljoin, urlsplit
from zipfile import ZipFile, BadZipFile

import requests


MAX_WORKBOOK_BYTES = 100 * 1024 * 1024
REGIONAL_LABEL = "\u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0440\u044f\u0434\u044b (\u0440\u0435\u0433\u0438\u043e\u043d\u044b)"


class AcquisitionError(RuntimeError):
    pass


def official_url(url, base):
    url = urljoin(base, url)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    if (parsed.scheme != "https" or host not in {"xn--d1aqf.xn--p1ai", "www.xn--d1aqf.xn--p1ai"}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise AcquisitionError("Reporting page advertised a non-official download URL")
    return url


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = {**dict(attrs), "text": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None


def discover_workbook(html, page_url):
    parser = _Links()
    parser.feed(html)
    matches = set()
    for link in parser.links:
        label = " ".join((link.get("text", "") + " " + (link.get("download") or "")).casefold().split())
        href = link.get("href") or ""
        if REGIONAL_LABEL in label and unquote(urlsplit(href).path).casefold().endswith(".xlsx"):
            matches.add(official_url(href, page_url))
    if len(matches) != 1:
        raise AcquisitionError("Regional statistical XLSX link is missing or ambiguous")
    return matches.pop()


def validate_workbook(path):
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_WORKBOOK_BYTES:
        raise AcquisitionError("Regional workbook is empty or exceeds the download size limit")
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                raise AcquisitionError("Download is not an XLSX workbook")
            if sum(item.file_size for item in archive.infolist()) > 1024 * 1024 * 1024:
                raise AcquisitionError("Regional workbook expands beyond the safety limit")
            if archive.testzip() is not None:
                raise AcquisitionError("Regional workbook is incomplete")
    except BadZipFile as exc:
        raise AcquisitionError("Download is not a complete XLSX workbook") from exc


def http_workbook(directory, *, session=None):
    from pipeline.preferential_mortgage import SOURCE_URL
    own_session = session is None
    session = session or requests.Session()
    try:
        with session.get(SOURCE_URL, timeout=(15, 45), stream=True) as response:
            response.raise_for_status()
            page_url = official_url(response.url, SOURCE_URL)
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) > 5 * 1024 * 1024:
                    raise AcquisitionError("Reporting page exceeds the size limit")
            url = discover_workbook(body.decode("utf-8"), page_url)
        target = Path(directory) / "regional-statistics.xlsx"
        with session.get(url, timeout=(15, 90), stream=True) as response:
            response.raise_for_status()
            official_url(response.url, url)
            size = 0
            with target.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_WORKBOOK_BYTES:
                        raise AcquisitionError("Regional workbook exceeds the download size limit")
                    stream.write(chunk)
        validate_workbook(target)
        return {"path": target, "downloadUrl": url, "transport": "http"}
    finally:
        if own_session:
            session.close()


def browser_workbook(directory, *, browser_factory=None):
    from pipeline.preferential_mortgage import SOURCE_URL
    from pipeline.selenium_utils import create_chrome, wait_for_download
    from selenium.webdriver import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    browser_factory = browser_factory or create_chrome
    driver = browser_factory(download_dir=Path(directory), headless=True)
    try:
        driver.set_page_load_timeout(60)
        driver.get(SOURCE_URL)

        def advertised(browser):
            # Do not interact with access-verification controls or transfer cookies.
            body = browser.find_element(By.TAG_NAME, "body").text.casefold()
            if any(text in body for text in ("confirm you are not a robot", "verify you are human",
                                             "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435, \u0447\u0442\u043e \u0432\u044b \u043d\u0435 \u0440\u043e\u0431\u043e\u0442")):
                raise AcquisitionError("Public reporting page requires access verification; no bypass attempted")
            try:
                return discover_workbook(browser.page_source, browser.current_url)
            except AcquisitionError:
                return False

        url = WebDriverWait(driver, 45).until(advertised)
        for close in driver.find_elements(By.CSS_SELECTOR, ".starRate__close"):
            if close.is_displayed():
                close.click()
        menu = driver.find_element(By.CSS_SELECTOR, ".report-btn-with-dropdown-links")
        ActionChains(driver).move_to_element(menu).perform()
        links = driver.find_elements(By.CSS_SELECTOR, ".report-btn-with-dropdown-links a[href]")
        link = next((item for item in links if item.get_attribute("href") == url), None)
        if link is None:
            raise AcquisitionError("Regional XLSX is not present in the public download menu")
        before = set(Path(directory).iterdir())
        if link.is_displayed() and link.is_enabled():
            link.click()
        else:
            # Responsive/overlay menus can keep a public link hidden in headless
            # Chrome. Normal navigation follows that same advertised href.
            driver.get(url)
        target = wait_for_download(Path(directory), before_snapshot=before, timeout=120)
        if target is None:
            raise AcquisitionError("Public browser workbook download did not complete")
        validate_workbook(target)
        return {"path": target, "downloadUrl": url, "transport": "browser"}
    finally:
        driver.quit()


def acquire_workbook(directory, *, session=None, browser_factory=None):
    try:
        return http_workbook(directory, session=session)
    except (requests.RequestException, AcquisitionError, UnicodeError) as exc:
        print("DOM.RF HTTP acquisition unavailable: " + type(exc).__name__ + "; trying public browser menu", flush=True)
    return browser_workbook(directory, browser_factory=browser_factory)


def freshness(document):
    """Source publication date outranks collection time, never filesystem mtime."""
    source_date = document.get("sourceDate")
    source_date = datetime.fromisoformat(source_date.replace("Z", "+00:00")) if source_date else None
    collected = datetime.fromisoformat(document["collectedAt"].replace("Z", "+00:00"))
    return tuple(float("-inf") if value is None else
                 value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
                 for value in (source_date, collected))


@contextmanager
def collection_lock(root):
    state = Path(root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    with (state / ".preferential-mortgage.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _read_source(path):
    from pipeline.preferential_mortgage import validate_source
    if not path.is_file() or path.is_symlink():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        validate_source(document)
        freshness(document)
        return document
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _atomic_source(destination, document, directory):
    from pipeline.preferential_mortgage import validate_source
    validate_source(document)
    staged = Path(directory) / "source.json"
    with staged.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    validate_source(json.loads(staged.read_text(encoding="utf-8")))
    os.replace(staged, destination)


def bootstrap_seed(root, destination, directory):
    """Use the freshest valid bundled seed only when raw is missing/invalid.

    The image copy is outside /app/data because the host data volume can hide
    the import shipped in the image, including during the dedicated update.
    """
    existing = _read_source(destination)
    if existing is not None:
        return existing, False
    selected = None
    for folder in (Path(root) / "data/imports", Path("/opt/parser-seed/data/imports")):
        for path in folder.glob("preferential-mortgage-*.json"):
            document = _read_source(path)
            if document is not None and (selected is None or freshness(document) > freshness(selected)):
                selected = document
    if selected is not None:
        _atomic_source(destination, selected, directory)
        print("Preferential mortgage: installed validated bundled seed dated " + str(selected.get("sourceDate")), flush=True)
    return selected, selected is not None


def refresh(root, *, workbook=None, acquire=None):
    from pipeline.preferential_mortgage import SOURCE_FILE, parse_workbook, validate_source
    root = Path(root)
    destination = root / SOURCE_FILE
    if destination.is_symlink() or not destination.resolve().is_relative_to(root.resolve()):
        raise AcquisitionError("Unsafe preferential mortgage source destination")
    with collection_lock(root):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".source-staging-preferential-", dir=destination.parent) as folder:
            existing, seeded = bootstrap_seed(root, destination, folder)
            try:
                if workbook is None:
                    acquired = (acquire or acquire_workbook)(Path(folder))
                else:
                    acquired = {"path": Path(workbook), "transport": "local_workbook"}
                validate_workbook(Path(acquired["path"]))
                document = parse_workbook(Path(acquired["path"]))
                if acquired.get("downloadUrl"):
                    document["downloadUrl"] = acquired["downloadUrl"]
                validate_source(document)
                new_freshness = freshness(document)
            except Exception as exc:
                if existing is None:
                    raise
                return {"status": "retained", "reason": "seed_retained_after_refresh_failure" if seeded else "refresh_failed",
                        "sourceDate": existing["sourceDate"], "liveUpdated": False,
                        "errorClass": type(exc).__name__, "error": str(exc)[:500]}
            if existing is not None and freshness(existing) > new_freshness:
                return {"status": "retained", "reason": "older_source_rejected", "sourceDate": existing["sourceDate"]}
            result = {"status": "success", "sourceDate": document["sourceDate"],
                      "records": len(document.get("records", document.get("flows", []))),
                      "transport": acquired["transport"]}
            _atomic_source(destination, document, folder)
            return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("PARSER_ROOT", Path(__file__).resolve().parents[2])))
    parser.add_argument("--workbook", "--import-workbook", type=Path, help="Normalize an operator-downloaded official workbook; never downgrade a newer source")
    args = parser.parse_args(argv)
    try:
        result = refresh(args.root, workbook=args.workbook)
    except Exception as exc:
        print("Preferential mortgage refresh failed; previous source unchanged: " + type(exc).__name__ + ": " + str(exc), flush=True)
        return 1
    print("Preferential mortgage refresh: " + json.dumps(result), flush=True)
    if result["status"] != "success":
        print("WARNING: no live refresh; a validated retained/seed source is available for publication; exit=2", flush=True)
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
