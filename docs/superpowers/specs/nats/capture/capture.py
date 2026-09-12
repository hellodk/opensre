"""Capture nats-server monitoring endpoint responses into fixtures/.

Run after ``run.sh`` from the repo root:

    uv run python docs/superpowers/specs/nats/capture/capture.py

Standard library only. Every response is stored verbatim (status, content type,
body) so tests can replay exact server behaviour, including error shapes.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
N1 = "http://127.0.0.1:18222"
N2 = "http://127.0.0.1:18223"
N3 = "http://127.0.0.1:18224"
SOLO = "http://127.0.0.1:18225"

# name -> (base, path)
CAPTURES: dict[str, tuple[str, str]] = {
    # server status
    "varz": (N1, "/varz"),
    "varz_n2": (N2, "/varz"),
    "varz_solo": (SOLO, "/varz"),
    "healthz": (N1, "/healthz"),
    "healthz_js_enabled_only": (N1, "/healthz?js-enabled-only=true"),
    "healthz_js_server_only": (N1, "/healthz?js-server-only=true"),
    "healthz_solo": (SOLO, "/healthz"),
    "healthz_solo_js_enabled_only": (SOLO, "/healthz?js-enabled-only=true"),
    # connections
    "connz": (N1, "/connz"),
    "connz_sort_pending": (N1, "/connz?sort=pending"),
    "connz_sort_subs_detail": (N1, "/connz?sort=subs&subs=true"),
    "connz_sort_msgs_from_limit": (N1, "/connz?sort=msgs_from&limit=2"),
    "connz_state_closed": (N1, "/connz?state=closed"),
    "connz_state_all": (N1, "/connz?state=all"),
    "connz_bad_sort": (N1, "/connz?sort=bogus"),
    "connz_bad_state": (N1, "/connz?state=bogus"),
    "connz_n2": (N2, "/connz"),
    "connz_solo": (SOLO, "/connz"),
    # subscriptions
    "subsz": (N1, "/subsz"),
    "subsz_detail": (N1, "/subsz?subs=true"),
    "subsz_test_match": (N1, "/subsz?subs=true&test=telemetry.cpu"),
    "subsz_test_nomatch": (N1, "/subsz?subs=true&test=nothing.here"),
    "subsz_n2": (N2, "/subsz"),
    # jetstream
    "jsz": (N1, "/jsz"),
    "jsz_streams": (N1, "/jsz?streams=true"),
    "jsz_streams_consumers": (N1, "/jsz?streams=true&consumers=true"),
    "jsz_streams_consumers_config": (N1, "/jsz?streams=true&consumers=true&config=true"),
    "jsz_stream_filter": (N1, "/jsz?streams=true&consumers=true&stream=ORDERS"),
    "jsz_stream_filter_unknown": (N1, "/jsz?streams=true&consumers=true&stream=NOPE"),
    "jsz_consumer_filter": (N1, "/jsz?streams=true&consumers=true&stream=ORDERS&consumer=billing"),
    "jsz_leader_only": (N1, "/jsz?leader-only=true"),
    "jsz_n2": (N2, "/jsz"),
    "jsz_n3_streams_consumers": (N3, "/jsz?streams=true&consumers=true"),
    "jsz_solo": (SOLO, "/jsz"),
    "jsz_solo_streams": (SOLO, "/jsz?streams=true&consumers=true"),
    # cluster
    "routez": (N1, "/routez"),
    "routez_subs": (N1, "/routez?subs=true"),
    "routez_n3": (N3, "/routez"),
    "routez_solo": (SOLO, "/routez"),
    "gatewayz": (N1, "/gatewayz"),
    "gatewayz_solo": (SOLO, "/gatewayz"),
    "leafz": (N1, "/leafz"),
    "leafz_solo": (SOLO, "/leafz"),
    # accounts (out of scope for tools, kept for reference)
    "accountz": (N1, "/accountz"),
    "accstatz": (N1, "/accstatz"),
    # errors
    "not_found": (N1, "/nonexistent"),
    "root": (N1, "/"),
}


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        ctype = exc.headers.get("Content-Type", "")
        body = exc.read()
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return {"status": status, "content_type": ctype, "text": text, "json": parsed}


def capture_client_port() -> dict:
    """GET /varz against the client port: what a wrong-port misconfiguration returns."""
    with socket.create_connection(("127.0.0.1", 14222), timeout=5) as sock:
        sock.sendall(b"GET /varz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        sock.settimeout(2)
        chunks = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            pass
    return {"status": None, "content_type": "", "text": b"".join(chunks).decode("utf-8", "replace"), "json": None}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    for name, (base, path) in CAPTURES.items():
        result = fetch(base + path)
        if result["json"] is not None:
            out = FIXTURES / f"{name}.json"
            out.write_text(json.dumps(result["json"], indent=2, sort_keys=False) + "\n")
        else:
            out = FIXTURES / f"{name}.txt"
            out.write_text(result["text"])
        index[name] = {
            "url": base + path,
            "status": result["status"],
            "content_type": result["content_type"],
            "file": out.name,
        }
        print(f"{name:34} {result['status']} {out.name}")

    wrong_port = capture_client_port()
    (FIXTURES / "wrong_port_client_4222.txt").write_text(wrong_port["text"])
    index["wrong_port_client_4222"] = {
        "url": "http://127.0.0.1:14222/varz",
        "status": None,
        "content_type": "",
        "file": "wrong_port_client_4222.txt",
    }
    print(f"{'wrong_port_client_4222':34} raw  wrong_port_client_4222.txt")

    (FIXTURES / "index.json").write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    main()
