"""Measure route latency and check the live event stream against a running server.

    python docs/latency_check.py http://localhost:8000

Uses only the standard library plus httpx (a dev dependency).
"""
import json
import statistics
import sys
import time

import httpx

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"
H = {"X-Requested-With": "MuleRingShield"}


def timed(client, method, path, n=25, **kw):
    ms = []
    for _ in range(n):
        t = time.perf_counter()
        r = client.request(method, path, **kw)
        ms.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
    ms.sort()
    return statistics.median(ms), ms[int(len(ms) * 0.95) - 1], len(r.content)


with httpx.Client(base_url=BASE, timeout=20) as c:
    rows = []
    for role in ("hub", "A"):
        assert c.post("/api/session", json={"role": role}, headers=H).status_code == 200
        alerts = c.get("/api/alerts").json()
        ring = c.get("/api/rings").json()[0]["id"]
        paths = ["/api/session", "/api/overview", "/api/alerts", f"/api/accounts/{alerts[0]['token']}",
                 "/api/rings", f"/api/rings/{ring}", "/api/privacy", "/api/audit", "/api/activity"]
        for p in paths:
            p50, p95, size = timed(c, "GET", p, headers={"Accept-Encoding": "gzip"})
            rows.append((role, "GET " + p.replace(alerts[0]["token"], "{token}").replace(ring, "{ring}"), p50, p95, size))
        if role == "A":
            url = f"/api/accounts/{alerts[-1]['token']}/action"
            for action in ("hold", "reopen"):
                p50, p95, size = timed(c, "POST", url, n=6, json={"action": action}, headers=H)
                rows.append((role, f"POST /api/accounts/{{token}}/action ({action})", p50, p95, size))
    for path in ("/", "/static/app.js", "/static/vendor/cytoscape.min.js"):
        r = c.get(path, headers={"Accept-Encoding": "gzip"})
        rows.append(("-", "GET " + path, r.elapsed.total_seconds() * 1000, 0, int(r.headers.get("content-length", len(r.content)))))

    print(f"{'role':4} {'route':52} {'p50 ms':>8} {'p95 ms':>8} {'bytes':>8}")
    for role, route, p50, p95, size in rows:
        print(f"{role:4} {route:52} {p50:8.1f} {p95:8.1f} {size:8d}")

    # Live stream: events must arrive as they happen, not in one buffered lump.
    arrivals, kinds = [], []
    t0 = time.perf_counter()
    with c.stream("GET", "/api/events", headers={"Accept-Encoding": "gzip"}) as s:
        print("\nevent stream:", s.status_code, s.headers.get("content-type"), "encoding:", s.headers.get("content-encoding"))
        for line in s.iter_lines():
            if line.startswith("data: "):
                arrivals.append(time.perf_counter() - t0)
                kinds.append(json.loads(line[6:])["type"])
            if time.perf_counter() - t0 > 9:
                break
    gaps = [round(b - a, 1) for a, b in zip(arrivals, arrivals[1:])]
    print(f"{len(arrivals)} events in 9 s, first after {arrivals[0]:.1f} s, gaps {gaps}, types {sorted(set(kinds))}")
