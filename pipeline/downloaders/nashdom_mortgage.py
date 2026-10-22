"""Collect the mortgage page through its published region and date controls.

No guessed API endpoints, transferred browser cookies, or access-verification
automation. Observations are literal single-month measurements, not YTD.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from pipeline.mortgage_history import (
    METRICS, SOURCE_URL, HistoryValidationError, load_checkpoint, load_published,
    plan_years, publish_history, record_failure, save_checkpoint, validate_bundle,
)
from pipeline.downloaders.nashdom_mortgage_api import ENDPOINTS, normalize_month
from pipeline.downloaders.nashdom_mortgage_network import MortgageResponseCapture, ResponseCaptureError
from pipeline.downloaders.nashdom_mortgage_access import VISIBILITY_SCRIPT, read_access_state


REGION_VALUES = {"rf": "1", "msk": "1762"}
RAW_PATH = Path("data/raw/realty/nashdom")
COLLECTOR_VERSION = "mortgage-browser-response-month-v2"
LEGACY_COLLECTOR_VERSION = "mortgage-dom-month-v1"


class AccessVerificationRequired(RuntimeError):
    pass


class PageNotReady(RuntimeError):
    pass


class CollectionIncomplete(RuntimeError):
    def __init__(self, message, *, published=False):
        super().__init__(message)
        self.published = published


@contextmanager
def collection_lock(raw_dir):
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (raw_dir / ".mortgage_history.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another mortgage collector is active") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def require_scope(metadata):
    if metadata.get("filters") != {"currency": "ALL", "market": "total", "housing": "all"}:
        raise RuntimeError("Mortgage page has unsupported currency/market/housing filters")
    selection = metadata.get("selection", {})
    if selection.get("includeAutonomous") != "yes" or selection.get("creditOrganizations"):
        raise RuntimeError("Mortgage page does not cover all credit organizations and autonomous regions")


def year_bundle(region, year, observations):
    """Only confirmed single-month measurements enter the monthly history."""
    rows, months, evidence = [], [], []
    for month, monthly in observations:
        period = f"{year:04d}-{month:02d}"
        if (monthly.get("region") != region or monthly.get("period") != period
                or monthly.get("basis") != "month"):
            raise ValueError("Mortgage observation identity/basis mismatch")
        if monthly.get("sourcePrecision") != "raw response":
            raise ValueError("Mortgage year requires exact response observations, not rounded cards")
        exact = normalize_month(region, period, monthly.get("rawMetrics", {}).get("exchanges"))
        if exact["metrics"] != monthly["metrics"]:
            raise ValueError("Mortgage observation differs from its exact response evidence")
        for segment in ("all", "primary", "secondary"):
            values = monthly["metrics"][segment]
            rows.append({"region": region, "period": period, "segment": segment,
                         "basis": "month", **{key: values[key] for key in METRICS},
                         "sourceEvidence": monthly.get("rawMetrics", {})})
        months.append(month)
        evidence.append({"period": period, "monthly": monthly.get("selectionEvidence")})
    return validate_bundle({"region": region, "year": year, "sourceUrl": SOURCE_URL,
                            "sourceDate": None, "collectedAt": utc_now(), "records": rows,
                            "availableMonths": months, "evidence": evidence,
                            "collector": COLLECTOR_VERSION,
                            "method": "ordinary page controls; matching monthly general/details responses",
                            "precision": "raw response; no rendered-card rounding"})


class BrowserClient:
    """Ordinary clicks; response completion and stable cards guard stale data."""
    def __init__(self, raw_dir, *, timeout=60):
        self.raw_dir, self.timeout = Path(raw_dir), timeout
        self.driver = None
        self.responses = None
        self._interaction = 0
        self._last_access_state = {}
        self._navigation_timed_out = False

    def __enter__(self):
        from selenium.common.exceptions import TimeoutException
        from pipeline.selenium_utils import create_chrome
        from pipeline.downloaders.nashdom_operational_browser import TRACK_REQUESTS
        self._interaction = 0
        self._last_access_state = {}
        self._navigation_timed_out = False
        stage = "navigation"
        try:
            self.driver = create_chrome(headless=os.environ.get("HEADLESS", "1") != "0",
                                        performance_logs=True)
            self.driver.set_page_load_timeout(self.timeout)
            self.responses = MortgageResponseCapture(self.driver)
            self.responses.enable()
            try:
                self.driver.get(SOURCE_URL)
            except TimeoutException:
                # Analytics/assets can outlive page load; let the page finish normally.
                self._navigation_timed_out = True
            stage = "document"
            self._wait_for_page()
            self.driver.execute_script(TRACK_REQUESTS)
            stage = "observation"
            self._wait()
            return self
        except BaseException as exc:
            if isinstance(exc, Exception) and stage != "observation":
                self._diagnostic(stage, exc)
            self.__exit__()
            raise

    def __exit__(self, *args):
        driver, self.driver = self.driver, None
        self.responses = None
        if driver is not None:
            driver.quit()

    def _access_check(self):
        state = read_access_state(self.driver)
        self._last_access_state = state
        if state["captchaVisible"]:
            raise AccessVerificationRequired("DOM.RF shows a visible CAPTCHA; manual verification required; previous data retained")
        if state["denied"]:
            raise AccessVerificationRequired("DOM.RF shows an access-denied page; previous data retained")
        return state

    def _wait_for_page(self):
        from selenium.common.exceptions import JavascriptException, TimeoutException
        from selenium.webdriver.support.ui import WebDriverWait

        def ready(driver):
            try:
                state = self._access_check()
            except JavascriptException as exc:
                if any(marker in str(exc).lower() for marker in (
                        "execution context was destroyed", "cannot find context", "document unloaded")):
                    return False
                raise
            self.responses.poll()
            return state if state["ready"] else False

        try:
            return WebDriverWait(self.driver, self.timeout, poll_frequency=0.3).until(ready)
        except TimeoutException as exc:
            raise PageNotReady("Mortgage page did not become ready; this does not establish a CAPTCHA; previous data retained") from exc

    def _diagnostic(self, stage, error):
        """Private bounded page evidence, never copied into published snapshots."""
        if self.driver is None:
            return
        directory = self.raw_dir / ".mortgage_history" / "diagnostics"
        name = "page_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex
        metadata = {"at": utc_now(), "stage": stage, "sourceUrl": SOURCE_URL,
                    "errorClass": type(error).__name__,
                    "state": {key: value for key, value in self._last_access_state.items() if key != "title"},
                    "navigationTimedOut": self._navigation_timed_out}
        screenshot_path = directory / (name + ".png")
        path = directory / (name + ".json")
        metadata_complete = False
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                screenshot = self.driver.get_screenshot_as_png()
                if isinstance(screenshot, bytes) and 0 < len(screenshot) <= 5 * 1024 * 1024:
                    with screenshot_path.open("xb") as stream:
                        os.chmod(stream.name, 0o600)
                        stream.write(screenshot)
                    metadata["screenshot"] = name + ".png"
            except Exception as exc:
                metadata["screenshotErrorClass"] = type(exc).__name__
            with path.open("x", encoding="utf-8") as stream:
                os.chmod(stream.name, 0o600)
                json.dump(metadata, stream, ensure_ascii=False, allow_nan=False, indent=2)
            metadata_complete = True
            older = sorted((item for item in directory.glob("page_*.json") if item != path),
                           key=lambda item: item.stat().st_mtime_ns, reverse=True)
            for old in older[4:]:
                old.unlink(missing_ok=True)
                old.with_suffix(".png").unlink(missing_ok=True)
            for orphan in directory.glob("page_*.png"):
                if not orphan.with_suffix(".json").exists():
                    orphan.unlink(missing_ok=True)
            print(f"Mortgage browser diagnostic: {path}", file=sys.stderr, flush=True)
        except OSError as exc:
            if not metadata_complete:
                for incomplete in (path, screenshot_path):
                    try:
                        incomplete.unlink(missing_ok=True)
                    except OSError:
                        pass
            print(f"Mortgage browser diagnostic unavailable: {type(exc).__name__}", file=sys.stderr, flush=True)

    def _html(self):
        # Serialize actual checked properties; React need not update attributes.
        return self.driver.execute_script(VISIBILITY_SCRIPT + """
          const clone=document.documentElement.cloneNode(true);
          const live=document.querySelectorAll('input');
          clone.querySelectorAll('input').forEach((e,i)=>{
            if(live[i].checked) e.setAttribute('checked',''); else e.removeAttribute('checked');
          });
          const markers='js-challenge-loader,#captcha_root,#id_captcha_loaded,#id_captcha_frame_div,'
            + '#captcha_root *,#id_captcha_loaded *,#id_captcha_frame_div *,'
            + 'iframe[src*="captcha" i],input[name*="captcha" i]';
          const copied=clone.querySelectorAll(markers);
          document.querySelectorAll(markers).forEach((element,index)=>
            copied[index].setAttribute('data-mortgage-visible',String(visible(element))));
          return clone.outerHTML;
        """)

    def _network(self):
        return self.driver.execute_script("""
          const t=window.__operationalHistoryRequests;
          return t ? {pending:t.pending,idle:performance.now()-t.changed,finished:t.finished}:null;
        """)

    def _mark(self):
        self.responses.poll()
        not_before = self.driver.execute_script("""
          const t=window.__operationalHistoryRequests;
          if(!t || t.pending) throw Error('Mortgage page is not idle');
          t.finished=[];t.changed=performance.now();
          return (performance.timeOrigin+performance.now())/1000;
        """)
        self._interaction = self.responses.begin_interaction(not_before=not_before)

    def _wait(self, *, region=None, period=None, basis=None, year=None, requested=False):
        from selenium.webdriver.support.ui import WebDriverWait
        from pipeline.downloaders.nashdom_mortgage_dom import discover_page, parse_page
        previous, stable = None, 0
        generation = self._interaction
        if requested and generation == 0:
            raise ResponseCaptureError("Mortgage UI interaction was not marked before selection")

        def ready(driver):
            nonlocal previous, stable
            state = self._access_check()
            self.responses.poll()
            if not state["ready"]:
                stable = 0
                return False
            network = self._network()
            if (not network or network["pending"] or network["idle"] < 500
                    or requested and not network["finished"]):
                stable = 0
                return False
            details_errors = [item for item in network["finished"] if item.get("status") != 200]
            if any(type(item.get("status")) is not int or item["status"] != 500
                   or item.get("url") != ENDPOINTS["details"] for item in details_errors):
                raise RuntimeError("Mortgage page data request failed")
            html = self._html()
            try:
                meta = discover_page(html)
                if (region and meta["region"] != region or period and meta["period"] != period
                        or basis and meta["basis"] != basis
                        or year and int(meta["period"][:4]) != year):
                    return False
                observation = parse_page(html, expected_region=meta["region"],
                                         expected_period=meta["period"], require_month=basis == "month")
            except ValueError:
                return False
            require_scope(meta)
            if details_errors and meta["basis"] != "month":
                raise RuntimeError("Mortgage details failure is not a verified monthly observation")
            if meta["basis"] == "month":
                exact = self.responses.month(meta["region"], meta["period"], generation=generation)
                if exact is None:
                    stable = 0
                    return False
                if details_errors and not any(item.get("status") == 500
                        and item.get("url") == ENDPOINTS["details"]
                        for item in exact["rawMetrics"]["exchanges"]):
                    raise RuntimeError("Mortgage details failure has no matching exact response")
                exact["rawMetrics"]["renderedPage"] = observation.get("rawMetrics", {})
                observation = exact
            signature = hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest()
            stable = stable + 1 if signature == previous else 0
            previous = signature
            if stable < 2:
                return False
            observation["selectionEvidence"] = {"renderStable": True, "pendingRequests": 0,
                "completedRequests": network["finished"], "region": meta["region"],
                "period": meta["period"], "basis": meta["basis"],
                "interactionGeneration": generation,
                "responseCapture": meta["basis"] == "month"}
            return meta, observation

        try:
            return WebDriverWait(self.driver, self.timeout, poll_frequency=0.3).until(
                ready, message="Mortgage page did not establish stable scope-matched exact monthly responses")
        except Exception as exc:
            self._diagnostic("observation", exc)
            raise

    def _choose(self, selector):
        from selenium.webdriver.common.by import By
        element = self.driver.find_element(By.CSS_SELECTOR, selector)
        if element.is_selected():
            return False
        container = element.find_element(By.XPATH,
            "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' filter-list ')][1]")
        self._mark()
        container.find_element(By.CSS_SELECTOR, ".filter-list__btn button").click()
        self.driver.find_element(By.CSS_SELECTOR, selector).find_element(By.XPATH, "..").click()
        return True

    def region(self, region):
        selected = self._choose(f"#base-header .main-filters input[value='{REGION_VALUES[region]}']")
        return self._wait(region=region, requested=selected)[0]

    def select_year(self, region, year):
        selected = self._choose(f"#base-header .range-select input[value='{year}']")
        return self._wait(region=region, year=year, requested=selected)[0]

    def select_month(self, region, year, month):
        period = f"{year:04d}-{month:02d}"
        selected = self._choose(f"#base-header .range-select input[value='{period}-01']")
        return self._wait(region=region, period=period, requested=selected)[0]

    def basis(self, region, period, basis):
        from selenium.webdriver.common.by import By
        element = self.driver.find_element(By.CSS_SELECTOR, f".filters-panel input[value='{basis}']")
        selected = not element.is_selected()
        if selected:
            self._mark()
            panel = self.driver.find_element(By.CSS_SELECTOR, ".filters-panel__content")
            if not panel.is_displayed():
                self.driver.find_element(By.CSS_SELECTOR, ".filters-panel__btn").click()
            self.driver.find_element(By.CSS_SELECTOR, f".filters-panel input[value='{basis}']").find_element(By.XPATH, "..").click()
            self.driver.find_element(By.CSS_SELECTOR, ".filters-panel__actions .btn-success").click()
        return self._wait(region=region, period=period, basis=basis, requested=selected)[1]

    def discover(self):
        return {region: self.region(region)["years"] for region in REGION_VALUES}

    def collect_year(self, region, year):
        self.region(region)
        metadata = self.select_year(region, year)
        observations = []
        for month in sorted(metadata["months"]):
            self.select_month(region, year, month)
            period = f"{year:04d}-{month:02d}"
            monthly = self.basis(region, period, "month")
            observations.append((month, monthly))
            print(f"Mortgage month verified: {region}/{period}", flush=True)
            time.sleep(0.3)
        return year_bundle(region, year, observations)


def details_failure_scopes(document):
    """Only a validated explicit source error warrants repairing old parameters."""
    failures = set()
    for row in document.get("records", []) if document else []:
        if row.get("segment") != "all":
            continue
        evidence = row.get("sourceEvidence")
        exchanges = evidence.get("exchanges") if isinstance(evidence, dict) else None
        if not isinstance(exchanges, list) or not any(isinstance(item, dict)
                and item.get("url") == ENDPOINTS["details"]
                and type(item.get("status")) is int and item["status"] == 500 for item in exchanges):
            continue
        try:
            normalize_month(row["region"], row["period"], exchanges)
        except ValueError:
            continue
        failures.add((row["region"], int(row["period"][:4])))
    return failures


def collection_plan(available, previous, *, force=False):
    """Migrate rounded-card years and repair explicit temporary source failures."""
    planned = set(plan_years(available, previous, force=force))
    sources = previous.get("yearSources", {}) if previous else {}
    for region, years in available.items():
        for year in years:
            source = sources.get(region, {}).get(str(year), {})
            if source.get("collector") == LEGACY_COLLECTOR_VERSION:
                planned.add((region, year))
    planned.update((region, year) for region, year in details_failure_scopes(previous)
                   if year in available[region])
    return sorted(planned)


def collect(root, *, client_factory=BrowserClient, force=False):
    raw_dir = Path(root) / RAW_PATH
    with collection_lock(raw_dir):
        previous = load_published(raw_dir)
        successful, failures, published = [], [], False
        with client_factory(raw_dir) as client:
            available = client.discover()
            if set(available) != set(REGION_VALUES) or any(not available[key] for key in REGION_VALUES):
                raise ValueError("Mortgage source did not publish years for both regions")
            planned = collection_plan(available, previous, force=force)
            requested = {region: [year for key, year in planned if key == region] for region in REGION_VALUES}
            # Collect both latest regions first, so an interrupted bootstrap can
            # already publish useful real observations and resume older years.
            for region, year in sorted(planned, key=lambda item: (-item[1], item[0])):
                try:
                    try:
                        cached = load_checkpoint(raw_dir, region, year)
                    except HistoryValidationError:
                        cached = None
                    already_published = previous and any(row["region"] == region and
                        int(row["period"][:4]) == year for row in previous["records"])
                    source = previous.get("yearSources", {}).get(region, {}).get(str(year), {}) if previous else {}
                    legacy = source.get("collector") == LEGACY_COLLECTOR_VERSION
                    if (not force and (not already_published or legacy) and cached is not None
                            and cached.get("collector") == COLLECTOR_VERSION
                            and (region, year) not in details_failure_scopes(cached)
                            and year < max(available[region])):
                        bundle = cached
                        print(f"Mortgage checkpoint resumed: {region}/{year}", flush=True)
                    else:
                        bundle = client.collect_year(region, year)
                        save_checkpoint(raw_dir, bundle)
                    successful.append((region, year))
                except Exception as exc:
                    record_failure(raw_dir, region, year, type(exc).__name__ + ": " + str(exc))
                    failures.append((region, year))
                    print(f"Mortgage year failed: {region}/{year}: {type(exc).__name__}: {exc}", flush=True)
                    if isinstance(exc, AccessVerificationRequired):
                        break
                    continue
                try:
                    previous = publish_history(raw_dir, available, prior=previous,
                                               requested_years=requested, refreshed_years=successful)
                    published = True
                except HistoryValidationError:
                    if previous is not None or {key for key, _ in successful} == set(REGION_VALUES):
                        raise
            # No successful scrape must not be reported as a successful refresh.
            if not successful or failures:
                raise CollectionIncomplete(
                    f"Mortgage history incomplete: {len(successful)} verified years; "
                    f"{len(planned) - len(successful)} planned years remain unrefreshed",
                    published=published)
            if load_published(raw_dir) is None:
                raise RuntimeError("Mortgage history has no publishable observations for both regions")
            missing_parameters = details_failure_scopes(previous)
            if missing_parameters:
                raise CollectionIncomplete(
                    "Mortgage calendar collected; parameters remain unavailable after explicit "
                    f"details source errors in {len(missing_parameters)} region-years",
                    published=published)
            return previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("PARSER_ROOT", Path(__file__).resolve().parents[2])))
    parser.add_argument("--force", action="store_true", help="Refresh every source-observed year")
    args = parser.parse_args(argv)
    try:
        result = collect(args.root, force=args.force)
    except CollectionIncomplete as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2 if exc.published else 1
    except Exception as exc:
        print(f"Mortgage collection stopped: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Mortgage collection complete: {len(result['records'])} observations", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
