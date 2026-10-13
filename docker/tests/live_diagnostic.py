"""Bounded, anonymous Debian diagnostics; never a complete source run or release."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from docker.coverage import finite, require
from docker.tests import live_public as live


def safe_url(value):
    try:
        parts = urlsplit(str(value))
        if parts.scheme not in {"https", "http"}:
            return parts.scheme + ":" if parts.scheme else "invalid-url"
        return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
    except ValueError:
        return "invalid-url"


def safe_text(value, limit=300):
    text = str(value)
    text = re.sub(r"https?://[^\s\"'<>]+", lambda match: safe_url(match.group()), text)
    text = re.sub(r"(?i)\b(password|passwd|token|authorization|cookie|session|secret|csrf)\b[\"']?\s*[:=]\s*"
                  r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1=[redacted]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r"[A-Za-z0-9_+/=-]{40,}", "[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def targets():
    import fedstat_checker as fedstat
    import nashdom_checker as nashdom
    return [("fedstat43246", "https://www.fedstat.ru/indicator/43246", fedstat.create_driver),
            ("kvart", nashdom._build_kvartirografia_url(), nashdom.create_chrome),
            ("construction", nashdom._build_construction_indicators_url(), nashdom.create_chrome),
            ("rasprod", nashdom._build_rasprodannost_url(), nashdom.create_chrome)]


def page_ready(driver, name, expected_url):
    import fedstat_checker as fedstat
    import nashdom_checker as nashdom
    if safe_url(driver.current_url).rstrip("/") != safe_url(expected_url).rstrip("/"):
        return False
    if name == "fedstat43246":
        return fedstat._date_page_ready(driver, "43246")
    if name == "kvart":
        return nashdom._kvart_lists_ready(driver)
    if name == "construction":
        snapshot = nashdom._read_construction_snapshot(driver)
        return bool(snapshot.get("report_date") and snapshot.get("report_period") and snapshot.get("area_thousand_m2"))
    signature = nashdom._rasprod_content_signature(driver)
    return bool(signature and all(re.search(r"\d", value) for value in signature)
                and nashdom._get_rasprod_current_period(driver))


def network_metadata(entries):
    requests, documents, failures, issuers = {}, [], [], set()
    for entry in entries:
        try:
            event = json.loads(entry["message"])["message"]
            method, params = event["method"], event["params"]
            identifier = params.get("requestId")
            if method == "Network.requestWillBeSent":
                requests[identifier] = safe_url(params["request"]["url"])
            elif method == "Network.responseReceived":
                response = params["response"]
                issuer = response.get("securityDetails", {}).get("issuer")
                if issuer:
                    issuers.add(safe_text(issuer))
                if params.get("type") == "Document":
                    documents.append({"url": safe_url(response["url"]), "status": response["status"]})
            elif method == "Network.loadingFailed":
                failures.append({"url": requests.get(identifier, "unknown"), "type": params.get("type"),
                                 "error": safe_text(params.get("errorText", "")),
                                 "blockedReason": safe_text(params.get("blockedReason", ""))})
        except (KeyError, TypeError, ValueError):
            continue
    return {"document_responses": documents[-20:], "loading_failed": failures[:100], "certificate_issuers": sorted(issuers)}


def browser_probe(name, url, factory, *, deadline):
    from selenium import webdriver
    result = {"name": name, "url": safe_url(url), "ready": False, "navigation_calls": 0}
    driver = None
    started = time.monotonic()
    original = webdriver.Chrome
    def instrumented(*args, **kwargs):
        # Preserve the actual source factory's options; collect only allowlisted
        # diagnostic fields after navigation. Never change certificate policy.
        kwargs["options"].set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
        return original(*args, **kwargs)
    try:
        require(time.monotonic() < deadline, "diagnostic time budget exhausted")
        with patch.object(webdriver, "Chrome", side_effect=instrumented):
            driver = factory(download_dir=Path("/app/downloads"))
        capabilities = driver.capabilities
        result["browser_version"] = safe_text(capabilities.get("browserVersion", ""))
        result["driver_version"] = safe_text(capabilities.get("chrome", {}).get("chromedriverVersion", ""))
        result["page_load_timeout_seconds"] = min(30, max(1, deadline - time.monotonic()))
        result["settle_timeout_seconds"] = 12
        driver.set_page_load_timeout(result["page_load_timeout_seconds"])
        driver.set_script_timeout(10)
        driver.execute_cdp_cmd("Network.enable", {})
        result["navigation_calls"] = 1
        try:
            driver.get(url)  # Exactly one navigation per source, no refresh.
        except Exception as exc:
            result["navigation_error"] = safe_text(type(exc).__name__ + ": " + str(exc))
        settle = min(deadline, time.monotonic() + 12)
        while time.monotonic() < settle:
            try:
                if page_ready(driver, name, url):
                    result["ready"] = True
                    break
            except Exception:
                pass
            time.sleep(.5)
        snapshot = driver.execute_script("""
            return {title:document.title,current_url:location.href,ready_state:document.readyState,
              body_text:(document.body?.innerText||'').slice(0,2000),
              dom_counts:{body:document.body?1:0,tables:document.querySelectorAll('table').length,
                headings:document.querySelectorAll('h2').length,lists:document.querySelectorAll('div.list').length,
                housing_rows:document.querySelectorAll('.HousingTable__Name').length,
                passport_links:[...document.querySelectorAll('a')].filter(x=>/паспорт/i.test(x.textContent||'')).length}};
        """)
        result.update({"title": safe_text(snapshot["title"]), "current_url": safe_url(snapshot["current_url"]),
                       "ready_state": snapshot["ready_state"], "body_text": safe_text(snapshot["body_text"]),
                       "dom_counts": snapshot["dom_counts"]})
    except Exception as exc:
        result["ready"] = False
        result["probe_error"] = safe_text(type(exc).__name__ + ": " + str(exc))
    finally:
        if driver is not None:
            try:
                result.update(network_metadata(driver.get_log("performance")))
                result["js_errors"] = [safe_text(entry["message"]) for entry in driver.get_log("browser")
                                       if entry.get("level") == "SEVERE"][:50]
            except Exception as exc:
                result["log_error"] = safe_text(type(exc).__name__ + ": " + str(exc))
            try:
                driver.quit()
            except Exception:
                pass
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result


def requests_probe(url):
    import requests
    result = {"url": safe_url(url), "verify_tls": True, "request_calls": 1}
    try:
        # No implicit redirected GETs, and no body/cookies/headers in the report.
        with requests.get(url, timeout=(4, 8), stream=True, verify=True, allow_redirects=False) as response:
            result.update({"status": response.status_code, "current_url": safe_url(response.url)})
            socket = getattr(getattr(response.raw, "connection", None), "sock", None)
            if socket:
                cert = socket.getpeercert()
                result["certificate_issuer"] = safe_text(str(cert.get("issuer", "")))
    except Exception as exc:
        result["error"] = safe_text(type(exc).__name__ + ": " + str(exc))
    return result


def rosstat_probe(root, report_dir, started, deadline):
    import rosstat_checker as rosstat
    from pipeline.data_access import DataAccess, DataContext, NA_INDICATORS
    from pipeline.source_records import file_sha256
    from docker.publication import seed
    require(not rosstat.DOWNLOAD_DIR.is_absolute() and rosstat.DOWNLOAD_DIR == Path("downloads"),
            "diagnostic must use the actual relative Rosstat download path")
    seed(root)
    commands = ([sys.executable, "rosstat_checker.py"],
                [sys.executable, "-m", "pipeline.source_history", "migrate", "--root", str(root)],
                [sys.executable, "pipeline/orchestrator.py", "--skip-download", "--only", *NA_INDICATORS])
    with (report_dir / "rosstat-commands.log").open("w", encoding="utf-8") as output:
        for command in commands:
            remaining = deadline - time.monotonic()
            require(remaining > 1, "diagnostic time budget exhausted")
            result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT, timeout=min(120, remaining),
                                    env={**os.environ, "PARSER_REQUIRE_REALTY_MARTS": "0"})
            require(result.returncode == 0, "Rosstat diagnostic stage failed; see rosstat-commands.log")
    access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
    required = {entry["key"] for entries in rosstat.PAGE_SOURCES.values() for entry in entries}
    receipts = live.validate_receipts(access, started, required_ids=required)
    audit = live.validate_etl_audit(access, started, required_ids=NA_INDICATORS)
    processed = {}
    for identifier in NA_INDICATORS:
        frame, path = access.load_indicator(identifier), access.DATA_PROCESSED / (identifier + ".pkl")
        require(not frame.empty and "source_file" in frame and frame.value.map(finite).any(), "Rosstat processed indicator lacks measurements")
        require(path.stat().st_mtime >= int(started), "Rosstat processed file predates the probe")
        require(set(frame.source_file.astype(str)) <= {entry["file"] for entry in receipts.values()}, "Rosstat processed provenance is missing")
        processed[identifier] = {"rows": len(frame), "sha256": file_sha256(path), "files": sorted(set(frame.source_file.astype(str)))}
    return {"success": True, "download_path": str(rosstat.DOWNLOAD_DIR), "collector_verify_tls": rosstat.VERIFY_SSL,
            "downloads": receipts, "processed": processed, "etl": audit}


def main():
    root = Path("/app")
    require(os.environ.get("PARSER_DIAGNOSTIC_ONLY") == "1", "diagnostic mode must be explicitly selected")
    live.guard(root)
    report_dir = root / "logs/live-public"
    report_dir.mkdir(parents=True, exist_ok=True)
    started, deadline = time.time(), time.monotonic() + 360
    summary = {"type": "diagnostic_probe", "scope": "four anonymous pages and Rosstat six economics; not public6/all8 or publication",
               "started_at": datetime.now(timezone.utc).isoformat(), "success": False, "pages": []}
    try:
        summary["versions"] = live.versions()
        for name, url, factory in targets():
            page = browser_probe(name, url, factory, deadline=deadline)
            if time.monotonic() < deadline:
                page["requests"] = requests_probe(url)
            summary["pages"].append(page)
            (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            summary["rosstat"] = rosstat_probe(root, report_dir, started, deadline)
        except Exception as exc:
            summary["rosstat"] = {"success": False, "error": safe_text(type(exc).__name__ + ": " + str(exc))}
        summary["success"] = (len(summary["pages"]) == 4 and all(page["ready"]
                              and not page.get("probe_error") and not page.get("log_error")
                              and 200 <= page.get("requests", {}).get("status", 0) < 300 for page in summary["pages"])
                              and summary["rosstat"]["success"])
    except Exception as exc:
        summary["error"] = safe_text(type(exc).__name__ + ": " + str(exc))
    finally:
        audit = root / "data/processed/etl_audit.jsonl"
        if audit.is_file():
            (report_dir / "etl_audit.jsonl").write_bytes(audit.read_bytes())
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        (report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Diagnostic probe:", "PASS" if summary["success"] else "FAIL", flush=True)
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
