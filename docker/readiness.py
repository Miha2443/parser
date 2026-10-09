"""Probe data-bearing API routes in a running backend, including its release."""
import json
import sys
from urllib.request import urlopen

ROUTES = (
    "/api/v1/catalog", "/api/v1/economics/salary/catalog",
    "/api/v1/economics/ipc/catalog", "/api/v1/economics/accounts/catalog",
    "/api/v1/map/catalog", "/api/v1/linear/catalog",
    "/api/v1/commissioning/annual/catalog", "/api/v1/construction/catalog",
)


def probe(base="http://127.0.0.1:8000", expected=None):
    with urlopen(base + "/api/v1/health", timeout=20) as response:
        health = json.load(response)
    if expected and health.get("release") != expected:
        raise RuntimeError("Backend still serves another release")
    for route in ROUTES:
        with urlopen(base + route, timeout=20) as response:
            payload = json.load(response)
        if route == "/api/v1/catalog" and not payload["controls"]["developers"]:
            raise RuntimeError("Backend has no developers")


if __name__ == "__main__":
    probe(expected=sys.argv[1] if len(sys.argv) > 1 else None)
