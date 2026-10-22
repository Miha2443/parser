"""Read only the mortgage responses produced by ordinary page interactions.

Chrome performance logs establish each POST's exact scope. Response bodies are
read from that same completed request, without sending requests or copying any
cookies, headers, or credentials into the history.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import copy
import json
import math
import time
from urllib.parse import urlsplit

from pipeline.downloaders.nashdom_mortgage_api import (
    ENDPOINTS, REGION_IDS, match_request, normalize_month,
)


class ResponseCaptureError(RuntimeError):
    """The page did not establish a usable exact monthly response pair."""


class MortgageResponseCapture:
    def __init__(self, driver, *, max_requests=128, max_scopes=48,
                 max_body_bytes=2 * 1024 * 1024, max_age=45,
                 body_attempts=12, clock=time.monotonic):
        self.driver = driver
        self.max_requests, self.max_scopes = max_requests, max_scopes
        self.max_body_bytes, self.max_age = max_body_bytes, max_age
        self.body_attempts, self.clock = body_attempts, clock
        if min(max_requests, max_scopes, max_body_bytes, max_age, body_attempts) <= 0:
            raise ValueError("Mortgage capture limits must be positive")
        self._requests = OrderedDict()
        self._scopes = OrderedDict()
        self.generation = 0
        self._generation_error = None
        self._not_before = None

    def begin_interaction(self, *, not_before=None):
        """Invalidate prior selections before a UI action, without any requests.

        The caller drains old log events before marking the browser's wall clock.
        That boundary also excludes delayed pre-action request events.
        """
        if not_before is not None and (type(not_before) not in (int, float)
                or not math.isfinite(not_before) or not_before <= 0):
            raise ValueError("Mortgage interaction needs a finite browser timestamp")
        self.generation += 1
        self._generation_error = None
        self._not_before = not_before
        self._requests.clear()
        self._scopes.clear()
        return self.generation

    def enable(self):
        """Must run before navigation, including the initial page's requests."""
        self.driver.execute_cdp_cmd("Network.enable", {
            "maxTotalBufferSize": self.max_body_bytes * 8,
            "maxResourceBufferSize": self.max_body_bytes,
            "maxPostDataSize": 4096,
        })

    def _scope(self, request):
        url, method, payload = (request.get(key) for key in ("url", "method", "postData"))
        if url not in ENDPOINTS.values() or method != "POST" or not isinstance(payload, str):
            return None
        if len(payload) > 4096:
            return None
        try:
            data = json.loads(payload)
        except (ValueError, RecursionError):
            return None
        if not isinstance(data, dict):
            return None
        region = next((name for name, value in REGION_IDS.items()
                       if type(data.get("regionId")) is int and data["regionId"] == value), None)
        reporting = data.get("reportingPeriod")
        period = reporting[:-3] if isinstance(reporting, str) else None
        endpoint = match_request(url, method, payload, region, period)
        return (region, period, endpoint) if endpoint else None

    def _failed(self, state, message):
        state["error"] = message
        self._requests.pop(state["requestId"], None)

    def _invalidate(self, message):
        self._generation_error = message
        self._requests.clear()
        self._scopes.clear()

    @staticmethod
    def _mortgage_url(url):
        if not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url)
            for endpoint in ENDPOINTS.values():
                known = urlsplit(endpoint)
                if ((parsed.scheme, parsed.netloc) == (known.scheme, known.netloc)
                        and parsed.path.startswith(known.path.rsplit("/", 1)[0] + "/")):
                    return True
        except ValueError:
            pass
        return False

    def _request(self, params):
        request_id, request = params.get("requestId"), params.get("request")
        if not isinstance(request, dict):
            return
        relevant = self._mortgage_url(request.get("url"))
        if relevant and self._not_before is not None:
            wall_time = params.get("wallTime")
            if type(wall_time) not in (int, float) or not math.isfinite(wall_time):
                self._invalidate("Mortgage request has no interaction timestamp")
                return
            if wall_time < self._not_before:
                return
        if not isinstance(request_id, str):
            if relevant:
                self._invalidate("Mortgage request has no capture identity")
            return
        previous = self._requests.get(request_id)
        if previous is not None:
            self._failed(previous, "Mortgage response was redirected or superseded")
        if self._generation_error:
            return
        scope = self._scope(request)
        if scope is None:
            if relevant:
                self._invalidate("Unexpected mortgage request filters, basis, or endpoint")
            return
        region, period, endpoint = scope
        key = region, period
        values = self._scopes.setdefault(key, {})
        self._scopes.move_to_end(key)
        old = values.get(endpoint)
        if old:
            self._requests.pop(old["requestId"], None)
        state = {"requestId": request_id, "generation": self.generation,
                 "started": self.clock(), "attempts": 0,
                 "finished": False, "exchange": {"url": request["url"],
                 "method": request["method"], "request": request["postData"]}}
        values[endpoint] = state
        self._requests[request_id] = state
        while len(self._scopes) > self.max_scopes:
            _, evicted = self._scopes.popitem(last=False)
            for item in evicted.values():
                self._requests.pop(item["requestId"], None)
        while len(self._requests) > self.max_requests:
            _, evicted = self._requests.popitem(last=False)
            evicted["error"] = "Mortgage capture request limit exceeded"

    def _response(self, params):
        request_id = params.get("requestId")
        state = self._requests.get(request_id) if isinstance(request_id, str) else None
        if state is None:
            return
        response = params.get("response")
        if not isinstance(response, dict) or response.get("url") != state["exchange"]["url"]:
            self._failed(state, "Mortgage response URL does not match its request")
            return
        status, mime = response.get("status"), response.get("mimeType")
        # The normalizer, not the transport, validates this specific error body.
        details_error = state["exchange"]["url"] == ENDPOINTS["details"] and status == 500
        if type(status) is not int or (status != 200 and not details_error):
            self._failed(state, "Mortgage source response has an unsupported HTTP status")
            return
        if not isinstance(mime, str):
            self._failed(state, "Mortgage response has no JSON content type")
            return
        mime = mime.split(";", 1)[0].strip().lower()
        if mime != "application/json" and not (mime.startswith("application/") and mime.endswith("+json")):
            self._failed(state, "Mortgage source returned non-JSON content")
            return
        state["exchange"].update(status=status, mimeType=mime)

    def _read_body(self, state):
        state["attempts"] += 1
        try:
            response = self.driver.execute_cdp_cmd("Network.getResponseBody", {
                "requestId": state["requestId"],
            })
        except Exception:
            # Chrome may emit loadingFinished just before its body is readable.
            if state["attempts"] >= self.body_attempts:
                self._failed(state, "Completed mortgage response body is unavailable")
            return
        if not isinstance(response, dict) or not isinstance(response.get("body"), str):
            self._failed(state, "Mortgage response body is missing")
            return
        body = response["body"]
        encoded = response.get("base64Encoded", False)
        if type(encoded) is not bool or len(body) > self.max_body_bytes * (4 / 3) + 4:
            self._failed(state, "Mortgage response body exceeds capture limits")
            return
        try:
            if encoded:
                body = base64.b64decode(body, validate=True).decode("utf-8")
            if len(body.encode("utf-8")) > self.max_body_bytes:
                raise ValueError("response too large")
        except (ValueError, UnicodeError):
            self._failed(state, "Mortgage response body is not bounded UTF-8 JSON")
            return
        state["exchange"]["body"] = body
        self._requests.pop(state["requestId"], None)

    def poll(self):
        """Drain new log events, retaining only sanitized mortgage exchanges."""
        for entry in self.driver.get_log("performance"):
            try:
                message = json.loads(entry["message"])["message"]
                method, params = message["method"], message["params"]
            except (KeyError, TypeError, ValueError, RecursionError):
                continue
            if not isinstance(params, dict):
                continue
            if method == "Network.requestWillBeSent":
                self._request(params)
            elif method == "Network.responseReceived":
                self._response(params)
            elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                request_id = params.get("requestId")
                state = self._requests.get(request_id) if isinstance(request_id, str) else None
                if state is not None:
                    if method == "Network.loadingFailed":
                        self._failed(state, "Mortgage page data request did not complete")
                    else:
                        state["finished"] = True
        for state in list(self._requests.values()):
            if self.clock() - state["started"] > self.max_age:
                self._failed(state, "Mortgage response capture expired")
            elif state["finished"] and "status" in state["exchange"]:
                self._read_body(state)

    def month(self, region, period, *, generation=None):
        """Return exact normalized values, None while pending, or a clear error."""
        if generation is not None and generation != self.generation:
            return None
        if self._generation_error:
            raise ResponseCaptureError(self._generation_error)
        pair = self._scopes.get((region, period), {})
        if any(state["generation"] != self.generation for state in pair.values()):
            return None
        if any(state.get("error") for state in pair.values()):
            raise ResponseCaptureError(next(state["error"] for state in pair.values() if state.get("error")))
        if set(pair) != set(ENDPOINTS) or any("body" not in state["exchange"] for state in pair.values()):
            return None
        return normalize_month(region, period,
                               [copy.deepcopy(pair[name]["exchange"]) for name in ENDPOINTS])
