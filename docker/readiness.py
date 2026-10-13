"""Probe data-bearing API routes in a running backend, including its release."""
import json
import sys
from pathlib import Path
from urllib.request import urlopen
# Also supports `python docker/readiness.py` from the deployed checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docker.coverage import ROUTES, validate_pages


def probe(base="http://127.0.0.1:8000", expected=None, *, identity_only=False):
    with urlopen(base + "/api/v1/health", timeout=20) as response:
        health = json.load(response)
    if expected and health.get("release") != expected:
        raise RuntimeError("Backend still serves another release")
    if identity_only:
        if not expected or health.get("status") != "ok":
            raise RuntimeError("Lifecycle preflight needs a healthy pinned release")
        return
    def get(route):
        with urlopen(base + route, timeout=20) as response:
            return json.load(response)
    validate_pages(get)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-only", action="store_true",
                        help="Preflight/rollback of an existing release; never accept a new candidate")
    parser.add_argument("expected", nargs="?")
    args = parser.parse_args()
    probe(expected=args.expected, identity_only=args.identity_only)
