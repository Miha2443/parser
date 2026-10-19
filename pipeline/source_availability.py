"""Evidence-bound provider outages and publication source declarations.

A collector failure is never evidence of an outage. Negative HTTP probes for
browser collectors must be confirmed twice through the collector's Chrome
transport before they can authorize retaining older data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

SOURCES = frozenset({"monitoring", "kvart", "construction", "rasprod", "fedstat", "rosstat", "erz-top", "erz-cards"})
BROWSER_SOURCES = SOURCES - {"monitoring"}
NASHDOM = "https://xn--80az8a.xn--d1aqf.xn--p1ai"
URLS = {
    "monitoring": ("https://docs.google.com/spreadsheets/d/19Z8y6EtoCNbygA9OIjZ7lHaPfeA9fV3Yl6Ned53gNcY/export?format=xlsx",),
    "kvart": (NASHDOM + "/аналитика/квартирография",),
    "rasprod": (NASHDOM + "/аналитика/распроданность-стройготовность",),
    "construction": (NASHDOM + "/аналитика/показатели_жилищного_строительства",
                     NASHDOM + "/аналитика/реализация_строящихся_квартир"),
    "fedstat": tuple("https://www.fedstat.ru/indicator/" + identifier for identifier in ("43246", "57824", "31074", "34118")),
    "rosstat": ("https://77.rosstat.gov.ru/folder/134924", "https://www.rosstat.gov.ru/statistics/accounts"),
    "erz-top": ("https://erzrf.ru/top-zastroyshchikov/rf?topType=0&regionKey=0",),
    "erz-cards": ("https://erzrf.ru/top-zastroyshchikov/rf?topType=0&regionKey=0",),
}
FAMILIES = {"monitoring": "monitoring_2_0", "kvart": "kvartirografia", "rasprod": "rasprodannost",
            "construction": "construction_operational", "erz-top": "erzrf_top", "erz-cards": "erzrf_cards"}
PREFIXES = {"monitoring": ("monitoring_2_0_",), "kvart": ("kvartirografia_",),
            "rasprod": ("rasprodannost_",), "construction": ("construction_operational_", "construction_history.json")}
MAINTENANCE = re.compile(r"ведутся технические работы|сайт временно недоступен|сайт находится на техническом обслуживании|"
                         r"temporarily unavailable|under maintenance|scheduled maintenance|service unavailable", re.I)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0
        self.headings, self.heading_parts, self.heading_depth = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"title", "h1"}:
            self.heading_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"title", "h1"} and self.heading_depth:
            self.heading_depth -= 1
            if not self.heading_depth:
                heading = " ".join(" ".join(self.heading_parts).split())
                if heading and len(heading) <= 300 and len(self.headings) < 4:
                    self.headings.append(heading)
                self.heading_parts.clear()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.heading_depth:
                self.heading_parts.append(data)


def classify_check(check):
    """Recompute from bounded evidence; a caller-supplied reason is not proof."""
    if check.get("network_error") in {"connection_unavailable", "connect_timeout", "read_timeout"}:
        return "provider_unreachable"
    status = check.get("http_status")
    if status in {500, 502, 503, 504}:
        return "provider_unavailable"
    headings = check.get("headings", [])
    if isinstance(headings, list) and len(headings) <= 4 and any(
            isinstance(heading, str) and len(heading) <= 300 and
            (MAINTENANCE.match(heading) or re.fullmatch(r"технические работы[.!]?", heading, re.I))
            for heading in headings):
        return "provider_maintenance"
    text = check.get("text", "")
    if (isinstance(text, str) and len(text) <= 1500 and
            (MAINTENANCE.match(text) or re.search("ведутся технические работы", text, re.I)
             and re.search("сайт временно недоступен", text, re.I))):
        return "provider_maintenance"
    # 401/403, TLS/selector/driver timeouts and a normal login screen do not prove
    # an outage. Two fresh HTTP read/connect timeouts are classified separately.
    # A requests-only WAF response can hide a healthy browser UI.
    return None


def http_check(url, *, get=None):
    import requests
    get = get or requests.get
    result = {"url": url, "checked_at": stamp(), "transport": "http", "http_status": None, "network_error": None, "text": ""}
    try:
        with get(url, timeout=(5, 8), verify=True, stream=True) as response:
            result["http_status"] = response.status_code
            parts = urlsplit(response.url)
            result["final_url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            if "text" in response.headers.get("Content-Type", "").lower():
                body = bytearray()
                for chunk in response.iter_content(4096):
                    body.extend(chunk)
                    if len(body) >= 16384:
                        break
                parser = _Text()
                parser.feed(bytes(body).decode("utf-8", errors="replace"))
                text = " ".join(" ".join(parser.parts).split())
                result["text"] = text if len(text) <= 1500 else ""
                result["headings"] = parser.headings
    except requests.exceptions.ConnectTimeout as exc:
        result["network_error"] = "connect_timeout"
        result["error_class"] = type(exc).__name__
    except requests.exceptions.SSLError as exc:
        result["network_error"] = "tls_error"
        result["error_class"] = type(exc).__name__
    except requests.exceptions.ReadTimeout as exc:
        result["network_error"] = "read_timeout"
        result["error_class"] = type(exc).__name__
    except requests.exceptions.ConnectionError as exc:
        result["network_error"] = "connection_unavailable"
        result["error_class"] = type(exc).__name__
    except requests.exceptions.RequestException as exc:
        result["network_error"] = "unconfirmed_request_error"
        result["error_class"] = type(exc).__name__
    return result


def browser_check(url):
    """Bounded public-page probe using the same Chrome factory as collectors.

    A driver failure, navigation timeout, TLS error or empty document is unknown,
    not an outage. Only explicit Chrome network errors, HTTP status or a visible
    maintenance notice can support a skip. No credentials or exception messages
    (which can contain proxy secrets) are written into evidence.
    """
    from selenium.common.exceptions import WebDriverException
    from pipeline.selenium_utils import create_chrome
    result = {"url": url, "checked_at": stamp(), "transport": "browser",
              "http_status": None, "network_error": None, "text": ""}
    driver = None
    navigation_error = None
    try:
        driver = create_chrome(page_load_strategy="eager")
        driver.set_page_load_timeout(20)
        driver.set_script_timeout(5)
        try:
            driver.get(url)
        except WebDriverException as exc:
            navigation_error = exc
            result["error_class"] = type(exc).__name__
        page = driver.execute_script("""
            const body = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
            const headings = [document.title, ...Array.from(document.querySelectorAll('h1')).map(x => x.innerText)]
                .map(x => (x || '').replace(/\\s+/g, ' ').trim()).filter(x => x && x.length <= 300).slice(0, 4);
            return {href: location.href, status: performance.getEntriesByType('navigation')[0]?.responseStatus || null,
                text: body.length <= 1500 ? body : '', headings};
        """)
        parts = urlsplit(page.get("href", ""))
        result["final_url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        # An interrupted navigation can expose the previous about:blank or a
        # Chrome error page. Neither its content nor its status proves HTTP 503.
        if parts.scheme in {"https", "http"} and navigation_error is None:
            result.update(http_status=page.get("status"), text=page.get("text", ""), headings=page.get("headings", []))
    except Exception as exc:
        result["probe_error"] = type(exc).__name__
    finally:
        if navigation_error is not None:
            match = re.search(r"net::(ERR_[A-Z_]+)", str(navigation_error))
            if match:
                result["browser_error"] = match[1]
                if match[1] in {"ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED",
                                "ERR_CONNECTION_TIMED_OUT", "ERR_TIMED_OUT", "ERR_NAME_NOT_RESOLVED",
                                "ERR_ADDRESS_UNREACHABLE", "ERR_NETWORK_UNREACHABLE", "ERR_INTERNET_DISCONNECTED"}:
                    result["network_error"] = "connection_unavailable"
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
    return result


def outcome(alias, run_id, status, *, phase="collector", reason_code=None, reason=None, evidence=()):
    return {"alias": alias, "run_id": run_id, "status": status, "phase": phase,
            "reason_code": reason_code or ("collected" if status == "success" else "collector_failed"),
            "reason": reason or ("Источник обновлён" if status == "success" else "Сбор источника завершился ошибкой"),
            "checked_at": stamp(), "evidence": list(evidence)}


def check_provider(alias, run_id, *, check=None, report=None):
    # HTTP is a cheap advisory probe. Requests/proxy failures do not establish
    # that Chrome cannot load the source (the 16 Oct server regression).
    injected_check = check
    check = check or http_check
    for url in URLS[alias]:
        first = check(url)
        code = classify_check(first)
        if not code:
            continue
        confirm = check
        if injected_check is None and alias in BROWSER_SOURCES:
            if report:
                report("HTTP-проверка: " + str(first.get("error_class") or first.get("network_error") or first.get("http_status")) +
                       "; перепроверяю через Chrome: " + url)
            confirm = browser_check
            first = confirm(url)
            code = classify_check(first)
            if not code:
                if report:
                    report("Chrome не подтвердил недоступность; HTTP=" + str(first.get("http_status")) +
                           "; ошибка=" + str(first.get("browser_error") or first.get("probe_error") or first.get("error_class")) +
                           "; пропуск источника не разрешён")
                continue
        second = confirm(url)
        if classify_check(second) == code:
            return outcome(alias, run_id, "skipped", phase="preflight", reason_code=code,
                           reason=("Сайт не ответил / недоступен с сервера" if code == "provider_unreachable" else
                                   "Технические работы на сайте" if code == "provider_maintenance" else
                                   "Сайт вернул HTTP " + str(second.get("http_status"))) +
                                  ": подтверждено двумя проверками официальной страницы; канал=" +
                                  second.get("transport", "http") + "; код=" +
                                  str(second.get("browser_error") or second.get("network_error") or second.get("http_status")),
                           evidence=[first, second])
    return None


def validate_outcome(row, alias, run_id, started, finished):
    if not isinstance(row, dict) or row.get("alias") != alias or row.get("run_id") != run_id:
        raise RuntimeError("Source outcome has a different run or alias")
    if row.get("status") not in {"success", "skipped", "failed"}:
        raise RuntimeError("Unknown source outcome")
    try:
        lower, upper = timestamp(started) - 2, timestamp(finished) + 2
        if not lower <= timestamp(row["checked_at"]) <= upper:
            raise ValueError("stale outcome")
        if row["status"] == "skipped":
            checks = row.get("evidence")
            if row.get("phase") not in {"preflight", "postflight"} or not isinstance(checks, list) or len(checks) != 2:
                raise ValueError("missing independent preflight checks")
            if not all(isinstance(check, dict) for check in checks):
                raise ValueError("invalid preflight check")
            if row.get("phase") == "postflight" and not (row.get("collector_reason_code") == "watchdog_timeout"
                    or str(row.get("collector_reason_code", "")).startswith("collector_exit_")):
                raise ValueError("non-provider collector failure cannot be skipped")
            if checks[0].get("url") != checks[1].get("url") or checks[0].get("url") not in URLS[alias]:
                raise ValueError("not an official source entry point")
            if any(not lower <= timestamp(check["checked_at"]) <= upper for check in checks):
                raise ValueError("stale source evidence")
            codes = [classify_check(check) for check in checks]
            if not codes[0] or codes[0] != codes[1] or row.get("reason_code") != codes[0]:
                raise ValueError("reason is not supported by source evidence")
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Invalid source availability evidence: " + str(exc)) from exc
    return row


def read_publication(root):
    path = Path(root) / "data/processed/publication_status.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Invalid publication declaration")
    if value.get("schema_version") != 2:
        return {}  # Legacy releases retain their existing strict gates.
    rows = value.get("sources", {})
    if not isinstance(rows, dict) or set(rows) != SOURCES or not all(isinstance(row, dict) for row in rows.values()):
        raise RuntimeError("Publication source declaration is incomplete")
    for alias, row in rows.items():
        if row.get("state") not in {"fresh", "retained", "unavailable"}:
            raise RuntimeError("Invalid publication source state")
        evidence_alias = row.get("outage_alias", alias)
        if evidence_alias != alias and {evidence_alias, alias} != {"erz-top", "erz-cards"}:
            raise RuntimeError("Unrelated source cannot authorize a declaration")
        validate_outcome(row.get("outcome"), evidence_alias, value.get("run_id"),
                         value.get("run_started_at"), value.get("run_finished_at"))
        if row.get("reason_code") != row["outcome"].get("reason_code"):
            raise RuntimeError("Publication reason contradicts its source outcome")
        if row["state"] == "fresh" and row["outcome"]["status"] != "success":
            raise RuntimeError("Fresh source did not succeed")
        if row["state"] == "unavailable" or row.get("outcome", {}).get("status") == "skipped":
            if row["outcome"]["status"] != "skipped":
                raise RuntimeError("Unavailable source has no confirmed outage")
    return value


def unavailable(root):
    return {alias for alias, row in read_publication(root).get("sources", {}).items() if row["state"] == "unavailable"}


def verify_inventory(root, publication):
    from pipeline.source_records import file_sha256
    root = Path(root)
    for alias, row in publication.get("sources", {}).items():
        actual = source_files(root, alias)
        expected = row.get("inventory", [])
        if {str(file.relative_to(root)) for file in actual} != {item["path"] for item in expected}:
            raise RuntimeError("Publication source family inventory changed: " + alias)
        for item in expected:
            file = root / item["path"]
            if file.stat().st_mtime_ns != item["mtime_ns"] or file_sha256(file) != item["sha256"]:
                raise RuntimeError("Publication source evidence changed: " + alias)


def verify_manifest(root):
    """Read-only health proof for an already validated, pinned release."""
    from pipeline.data_access import DataAccess, DataContext
    from pipeline.build_realty_marts import _specs, _shape_summary
    import pandas as pd
    root = Path(root)
    publication = read_publication(root)
    if publication and publication.get("full_marts_built") is not True:
        raise RuntimeError("Publication did not complete its private mart build")
    verify_inventory(root, publication)
    manifest = json.loads((root / "data/marts/realty/manifest.json").read_text(encoding="utf-8"))
    access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
    specs = {spec.name: spec for spec in _specs(access)}
    if set(manifest.get("marts", {})) != set(specs):
        raise RuntimeError("Incomplete release manifest")
    for name, info in manifest["marts"].items():
        file = root / info["file"]
        if info.get("error") or file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Invalid release mart")
        if _shape_summary(pd.read_pickle(file)) != info.get("summary"):
            raise RuntimeError("Release mart differs from its manifest")
        sources = [{"path": str(path.relative_to(root)).replace("\\", "/"), "size_bytes": path.stat().st_size,
                    "mtime_ns": path.stat().st_mtime_ns} for path in sorted(specs[name].raw_files(access))]
        recorded = [{key: item.get(key) for key in ("path", "size_bytes", "mtime_ns")} for item in info.get("sources", [])]
        if sources != recorded:
            raise RuntimeError("Release manifest source generation changed")
    return publication


def manifest_signature(root):
    """Cheap change detector; successful proof is cached only for this exact set."""
    from pipeline.data_access import DataAccess, DataContext
    from pipeline.build_realty_marts import _specs
    root = Path(root)
    publication = read_publication(root)
    manifest_path = root / "data/marts/realty/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {manifest_path, root / "data/processed/publication_status.json"}
    access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
    for spec in _specs(access):
        files.update(spec.raw_files(access))
    for info in manifest.get("marts", {}).values():
        files.add(root / info["file"])
    for row in publication.get("sources", {}).values():
        files.update(root / item["path"] for item in row.get("inventory", []))
    for identifier in indicator_ids({alias for alias, row in publication.get("sources", {}).items() if row["state"] != "unavailable"}):
        path = root / "data/processed" / (identifier + ".pkl")
        if path.exists():
            files.add(path)
    return tuple((str(file), file.stat().st_size, file.stat().st_mtime_ns, file.stat().st_ctime_ns) for file in sorted(files))


def route_sources(path):
    # Shared pages retain all independent sections that can still be calculated.
    if path == "/api/v1/catalog":
        return {"monitoring"}
    if path in {"/api/v1/profile", "/api/v1/profile/export"}:
        return {"monitoring", "kvart", "rasprod"}
    if path.rstrip("/") == "/api/v1/construction/tree":
        return {"monitoring"}
    if path.rstrip("/") == "/api/v1/construction/permits" or path.startswith("/api/v1/construction/permits/"):
        return {"monitoring"}
    if path.rstrip("/") == "/api/v1/construction/domrf" or path.startswith("/api/v1/construction/domrf/"):
        return {"construction"}
    for prefix, aliases in (("/api/v1/apartments", {"kvart"}), ("/api/v1/sales", {"rasprod"}),
                            ("/api/v1/construction", {"construction"}), ("/api/v1/map", {"monitoring"}),
                            ("/api/v1/commissioning/operational", {"monitoring"}),
                            ("/api/v1/economics/salary", {"fedstat"}), ("/api/v1/economics/ipc", {"fedstat"}),
                            ("/api/v1/economics/accounts", {"rosstat"})):
        if path == prefix or path.startswith(prefix + "/"):
            return aliases
    return set()


def indicator_ids(aliases):
    from pipeline.registry import INDICATORS
    source_ids = {"monitoring": {"monitoring_2_0"}, "construction": {"construction_operational"},
                  "kvart": {"kvartirografia"}, "rasprod": {"rasprodannost"},
                  "erz-top": {"top_rf", "top_msk"}, "erz-cards": {"cards"}}
    return {ind.id for ind in INDICATORS for alias in aliases
            if ind.source == alias and alias in {"fedstat", "rosstat"}
            or alias in source_ids and ind.source in {"nashdom", "erzrf"} and set(ind.source_ids) & source_ids[alias]}


def source_files(root, alias):
    """Family ownership never consumes neighbouring downloads or Nashdom files."""
    from pipeline.data_access import DataAccess, DataContext
    from pipeline.registry import INDICATORS
    from pipeline.download_provenance import receipt_path
    access = DataAccess(DataContext(Path(root), downloads=Path(root) / "downloads", use_marts=False))
    if alias in {"fedstat", "rosstat"}:
        indicators = [ind for ind in INDICATORS if ind.source == alias]
        files = set(access._raw_files([access.DOWNLOADS_DIR], [p for ind in indicators for p in ind.file_patterns]))
        ids = {str(key) for ind in indicators for key in ind.source_ids}
        # 34118 is also consumed directly by the annual source loader.
        if alias == "fedstat":
            ids |= {"34118_часть1", "34118_часть2"}
            files.update(path for path in access.source_files("emiss_34118") if path.parent == access.DOWNLOADS_DIR)
        for receipt in (access.DOWNLOADS_DIR / ".download_receipts").glob("*.json"):
            try:
                if str(json.loads(receipt.read_text(encoding="utf-8")).get("source_id")) in ids:
                    target = access.DOWNLOADS_DIR / receipt.name.removesuffix(".json")
                    if target.is_file():
                        files.add(target)
            except (OSError, ValueError):
                pass
        files |= {receipt_path(file) for file in list(files) if receipt_path(file).is_file()}
    else:
        files = set(access.source_files(FAMILIES[alias]))
        if alias == "construction":
            files.update(access.source_files("construction_history"))
            files = {file for file in files if file.name.startswith(PREFIXES[alias])}
        if alias == "rasprod":
            files |= {file.with_suffix(suffix) for file in list(files)
                      for suffix in (".json", ".coverage.json") if file.with_suffix(suffix).is_file()}
        files |= {file.with_suffix(file.suffix + ".provenance.json") for file in list(files)
                  if file.with_suffix(file.suffix + ".provenance.json").is_file()}
    for file in files:
        if file.is_symlink() or not file.resolve().is_relative_to(Path(root).resolve()):
            raise RuntimeError("Unsafe source family path")
    return sorted(files)
