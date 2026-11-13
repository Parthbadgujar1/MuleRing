"""End-to-end checks: privacy boundary, explanations, access rules, security guards and the live stream."""
import json
import os
import sqlite3
import tempfile

import numpy as np
import pytest

DATA_DIR = tempfile.mkdtemp()
os.environ["DATA_DIR"] = DATA_DIR
os.environ.pop("ACCESS_CODE", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import config, presenter, security  # noqa: E402
from app.consortium import SHARED_FIELDS  # noqa: E402
from app.main import app, live_step, state  # noqa: E402
from app.model import GlassBoxGBM  # noqa: E402
from app.tokenise import account_token  # noqa: E402

H = {"X-Requested-With": "MuleRingShield"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_limits():
    security.limiter.reset()


def sign_in(c, role, code=""):
    r = c.post("/api/session", json={"role": role, "code": code}, headers=H)
    assert r.status_code == 200, r.text
    return r


def collect(role):
    """Capture every event published for one role."""
    seen = []
    original = state.bus.publish

    def spy(by_role):
        if by_role.get(role) is not None:
            seen.append(by_role[role])
        original(by_role)

    state.bus.publish = spy
    return seen, lambda: setattr(state.bus, "publish", original)


# ---------------------------------------------------------------- tokens and packages
def test_tokens_are_stable_keyed_and_opaque():
    a = account_token(b"key-1", "BKAA0", "123456789012")
    assert a == account_token(b"key-1", "BKAA0", "123456789012")
    assert a != account_token(b"key-2", "BKAA0", "123456789012")
    assert a != account_token(b"key-1", "BKBB0", "123456789012")
    assert "123456789012" not in a and a.startswith("tok_")


def test_share_package_contains_no_identities(client):
    snap = state.engine.snap
    w = snap.world
    pii = set(w.name.tolist()) | set(w.acct_no.tolist()) | set(w.phone.tolist())
    assert len(snap.packages) == len(config.BANKS)
    for p in snap.packages:
        assert set(vars(p)) == set(SHARED_FIELDS)
        for field in SHARED_FIELDS:
            value = getattr(p, field)
            if isinstance(value, np.ndarray) and value.dtype.kind in "US":
                assert not (set(value.tolist()) & pii), f"{field} leaks an identity"
                assert all(v.startswith(("tok_", "dev_")) for v in value.tolist())


# ---------------------------------------------------------------- model
def test_contributions_add_up_exactly():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, 4))
    X[rng.random(600) < 0.1, 2] = np.nan
    y = (X[:, 0] + 0.5 * np.nan_to_num(X[:, 2]) + rng.normal(scale=0.5, size=600)) > 0.8
    m = GlassBoxGBM(n_rounds=150).fit(X, y, list("abcd"))
    logit = m.intercept + m.contributions(X).sum(axis=1)
    assert np.allclose(1 / (1 + np.exp(-logit)), m.predict(X))
    imp = m.importance()
    assert imp["a"] > imp["b"] and imp["a"] > imp["d"]  # the informative feature dominates
    assert ((m.predict(X) > 0.5) == y).mean() > 0.85


def test_sharing_beats_working_alone(client):
    d = {x["key"]: x for x in state.engine.benchmark["detectors"]}
    assert d["consortium"]["recall"] > d["local"]["recall"] > d["rules"]["recall"]
    assert d["consortium"]["fp"] <= d["local"]["fp"]
    assert d["consortium"]["precision"] > 0.9


# ---------------------------------------------------------------- authentication
def test_everything_needs_a_session(client):
    client.cookies.clear()
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert client.get("/api/session").json()["role"] is None
    for path in ("/api/overview", "/api/alerts", "/api/rings", "/api/privacy", "/api/audit", "/api/activity",
                 "/api/events", "/api/accounts/tok_0000000000000000", "/api/rings/RING-ABCDEF"):
        assert client.get(path).status_code == 401, path
    for path in ("/api/attack", "/api/reset"):
        assert client.post(path, headers=H).status_code == 401, path


def test_session_cookie_is_signed_and_locked_down(client):
    client.cookies.clear()
    r = sign_in(client, "A")
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    value = client.cookies.get(security.COOKIE)
    assert state.sessions.read(value) == "A"
    # Editing the role, or re-signing with another key, is rejected.
    assert state.sessions.read(value.replace("A.", "hub.", 1)) is None
    assert state.sessions.read(security.Sessions(b"other-secret").issue("A")) is None
    assert security.Sessions(config.SESSION_SECRET, ttl=-1).issue("A") and \
        state.sessions.read(security.Sessions(config.SESSION_SECRET, ttl=-1).issue("A")) is None
    client.cookies.clear()
    forged = value.replace("A.", "B.", 1)
    assert client.get("/api/alerts", headers={"Cookie": f"{security.COOKIE}={forged}"}).status_code == 401
    assert client.get("/api/alerts", headers={"Cookie": f"{security.COOKIE}={value}"}).status_code == 200


def test_role_cannot_be_chosen_by_the_request(client):
    sign_in(client, "A")
    a = client.get("/api/alerts?view=hub&role=hub").json()
    assert a and all(x["bank"] == "A" and x["own"] for x in a)   # query parameters are ignored
    r = client.post("/api/session", json={"role": "admin"}, headers=H)
    assert r.status_code == 422
    r = client.post("/api/session", json={"role": "A", "is_admin": True}, headers=H)
    assert r.status_code == 422


def test_access_code_gate(client, monkeypatch):
    monkeypatch.setattr(config, "ACCESS_CODE", "test-code-for-pytest")
    client.cookies.clear()
    assert client.get("/api/session").json()["code_required"] is True
    assert client.post("/api/session", json={"role": "A"}, headers=H).status_code == 401
    assert client.post("/api/session", json={"role": "A", "code": "wrong"}, headers=H).status_code == 401
    assert client.get("/api/alerts").status_code == 401
    sign_in(client, "A", "test-code-for-pytest")
    assert client.get("/api/alerts").status_code == 200
    sign_in(client, "B")   # switching role inside a valid session needs no code again
    assert all(x["bank"] == "B" for x in client.get("/api/alerts").json())
    assert client.delete("/api/session", headers=H).json()["role"] is None
    assert client.get("/api/alerts").status_code == 401


# ---------------------------------------------------------------- request guards
def test_writes_need_the_anti_forgery_header_and_same_origin(client):
    sign_in(client, "A")
    token = client.get("/api/alerts").json()[0]["token"]
    body = {"action": "hold"}
    assert client.post(f"/api/accounts/{token}/action", json=body).status_code == 403
    assert client.post(f"/api/accounts/{token}/action", json=body,
                       headers={**H, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/reset", headers={"Origin": "http://testserver"}).status_code == 403
    assert client.get(f"/api/accounts/{token}").json()["status"] == "open"   # nothing was changed


def test_input_is_validated(client):
    sign_in(client, "A")
    token = client.get("/api/alerts").json()[0]["token"]
    url = f"/api/accounts/{token}/action"
    assert client.post(url, json={"action": "freeze"}, headers=H).status_code == 422
    assert client.post(url, json={"action": "hold", "view": "B"}, headers=H).status_code == 422
    assert client.post(url, json={"action": "hold", "note": "x" * 501}, headers=H).status_code == 422
    assert client.post(url, json={"action": "hold", "note": "x" * 9000}, headers=H).status_code == 413
    for bad in ("tok_zzzz", "tok_0000000000000000", "../../etc/passwd", "tok_' OR 1=1 --"):
        assert client.get(f"/api/accounts/{bad}").status_code == 404
    assert client.get("/api/rings/RING-zzzzzz").status_code == 404
    assert client.get("/api/audit?limit=100000").status_code == 422
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_security_headers(client):
    for path in ("/", "/api/health", "/static/app.js"):
        h = client.get(path).headers
        assert "script-src 'self'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert "max-age" in client.get("/static/vendor/cytoscape.min.js").headers["cache-control"]
    secure = client.get("/api/health", headers={"X-Forwarded-Proto": "https"}).headers
    assert "max-age" in secure["strict-transport-security"]


def test_rate_limits(client, monkeypatch):
    monkeypatch.setitem(security.LIMITS, "signin", (5, 300.0))
    monkeypatch.setattr(config, "ACCESS_CODE", "test-code-for-pytest")
    client.cookies.clear()
    codes = [client.post("/api/session", json={"role": "A", "code": "guess"}, headers=H).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    r = client.post("/api/session", json={"role": "A", "code": "test-code-for-pytest"}, headers=H)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1   # even the right code must wait


def test_cookie_is_secure_over_https_and_failed_sign_ins_are_audited(client, monkeypatch):
    client.cookies.clear()
    r = client.post("/api/session", json={"role": "A"}, headers={**H, "X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()
    monkeypatch.setattr(config, "ACCESS_CODE", "test-code-for-pytest")
    client.cookies.clear()
    assert client.post("/api/session", json={"role": "B", "code": "nope"}, headers=H).status_code == 401
    assert state.store.audit(1)[0]["action"] == "signin-failed"


def test_live_connections_are_capped():
    from app.realtime import Bus, BusFull
    bus = Bus(config.ROLES, max_clients=2)
    a, _ = bus.subscribe("A")
    bus.subscribe("hub")
    with pytest.raises(BusFull):
        bus.subscribe("B")
    bus.unsubscribe(a)
    assert bus.subscribe("B") and bus.count == 2


# ---------------------------------------------------------------- who sees what
def test_hub_never_receives_names_or_transactions(client):
    sign_in(client, "hub")
    alerts = client.get("/api/alerts").json()
    assert alerts and all("name" not in a and "account" not in a for a in alerts)
    detail = client.get(f"/api/accounts/{alerts[0]['token']}").json()
    assert "transactions" not in detail and "name" not in detail and detail["can_act"] is False
    assert client.get("/api/privacy").json()["raw"] is None
    names = set(state.engine.snap.world.name.tolist())
    blob = client.get("/api/rings").text + client.get(f"/api/rings/{client.get('/api/rings').json()[0]['id']}").text
    assert not any(n in blob for n in names)


def test_bank_sees_only_its_own_customers(client):
    sign_in(client, "A")
    a = client.get("/api/alerts").json()
    assert a and all(x["bank"] == "A" and x["own"] and x["name"] for x in a)
    token = a[0]["token"]
    own = client.get(f"/api/accounts/{token}").json()
    assert own["restricted"] is False and own["transactions"] and own["reasons"]["raises"]
    times = [t["time"] for t in own["transactions"]]
    assert times == sorted(times, reverse=True)
    sign_in(client, "B")
    other = client.get(f"/api/accounts/{token}").json()
    assert other["restricted"] is True
    assert not ({"name", "score", "transactions", "reasons", "account", "vpa"} & set(other))


def test_only_the_owning_bank_can_act_and_it_is_audited(client):
    sign_in(client, "A")
    token = client.get("/api/alerts").json()[0]["token"]
    url = f"/api/accounts/{token}/action"
    for role in ("hub", "B"):
        sign_in(client, role)
        assert client.post(url, json={"action": "hold"}, headers=H).status_code == 403
    sign_in(client, "A")
    r = client.post(url, json={"action": "hold", "note": "private   note\n<script>"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "held"
    assert client.get(f"/api/accounts/{token}").json()["status"] == "held"
    entries = client.get("/api/audit").json()["entries"]
    assert entries[0]["action"] == "case-create"             # the decision opened a case file
    entry = next(x for x in entries if x["action"] == "hold")
    assert entry["token"] == token and entry["actor"] == "Bank A analyst"
    assert entry["note"] == "private note <script>"          # stored as text, whitespace collapsed

    # The hub sees that a decision was made, never the note; another bank sees neither.
    sign_in(client, "hub")
    hub_entry = next(x for x in client.get("/api/audit").json()["entries"]
                     if x["token"] == token and x["action"] == "hold")
    assert hub_entry["note"] is None and hub_entry["note_hidden"] is True
    sign_in(client, "B")
    b_entries = client.get("/api/audit").json()["entries"]
    assert all(x["token"] != token for x in b_entries)
    assert not any(x["actor"] == "Bank A analyst" and x["action"] in ("signin", "hold", "confirm", "clear", "reopen")
                   for x in b_entries)

    # Clearing removes it from the queue; reopening brings it back.
    sign_in(client, "A")
    client.post(url, json={"action": "clear"}, headers=H)
    assert token not in [x["token"] for x in client.get("/api/alerts").json()]
    client.post(url, json={"action": "reopen"}, headers=H)
    assert token in [x["token"] for x in client.get("/api/alerts").json()]


def test_confirming_a_mule_alerts_the_linked_bank(client):
    sign_in(client, "hub")
    client.post("/api/reset", headers=H)
    sign_in(client, "C")
    rohan = next(a for a in client.get("/api/alerts").json() if a["only_consortium"])
    seen_a, restore = collect("A")
    try:
        out = client.post(f"/api/accounts/{rohan['token']}/action", json={"action": "confirm"}, headers=H).json()
    finally:
        restore()
    assert out["status"] == "confirmed" and out["newly_flagged"] >= 1
    # The linked account is at another bank: C sees only a token, and A receives the alert by name.
    linked = [c for c in out["ripple"] if c["bank"] == "A"]
    assert linked and all(not c["own"] and "name" not in c and "score" not in c for c in linked)
    new_for_a = [p for p in seen_a if p["type"] == "alert" and p["kind"] == "new"]
    assert new_for_a and not new_for_a[0]["text"].startswith("tok_")


# ---------------------------------------------------------------- audit integrity
def test_audit_chain_detects_tampering(client):
    sign_in(client, "A")
    chain = client.get("/api/audit").json()["chain"]
    assert chain["ok"] and chain["entries"] >= 3
    db = sqlite3.connect(os.path.join(DATA_DIR, "mulering.db"))
    target = db.execute("SELECT id, detail FROM audit_log ORDER BY id LIMIT 1 OFFSET 1").fetchone()
    try:
        db.execute("UPDATE audit_log SET detail = 'quietly rewritten' WHERE id = ?", (target[0],))
        db.commit()
        broken = client.get("/api/audit").json()["chain"]
        assert broken["ok"] is False and broken["broken_at"] == target[0]
    finally:
        db.execute("UPDATE audit_log SET detail = ? WHERE id = ?", (target[1], target[0]))
        db.commit()
        db.close()
    assert client.get("/api/audit").json()["chain"]["ok"] is True


# ---------------------------------------------------------------- live stream
def test_quiet_stream_raises_no_alerts(client):
    sign_in(client, "hub")
    client.post("/api/reset", headers=H)
    before = client.get("/api/overview").json()["kpis"]
    seen, restore = collect("hub")
    try:
        for _ in range(15):
            live_step()
    finally:
        restore()
    ticks = [p for p in seen if p["type"] == "tick"]
    assert len(ticks) == 15 and ticks[-1]["version"] > ticks[0]["version"] - 1
    assert sum(t["new_tx"] for t in ticks) > 0                       # ordinary activity keeps flowing
    assert not [p for p in seen if p["type"] in ("alert", "ring")]   # and it does not trip the detector
    assert all("ledger" not in t for t in ticks)                     # the hub never sees single transactions
    assert client.get("/api/overview").json()["kpis"]["alerts"] == before["alerts"]


def test_live_attack_is_detected_as_it_unfolds(client):
    sign_in(client, "hub")
    client.post("/api/reset", headers=H)
    before = client.get("/api/overview").json()["kpis"]["alerts"]
    hub, restore_hub = collect("hub")
    try:
        a = client.post("/api/attack", headers=H).json()
        assert a["members"] >= 5 and a["executed"] == 0 and len(a["banks"]) >= 2
        assert client.post("/api/attack", headers=H).status_code == 409   # one at a time
        for _ in range(150):
            live_step()
            if state.engine.attack["done"]:
                break
    finally:
        restore_hub()
    done = state.engine.attack
    assert done["done"] and done["executed"] == done["total"]
    assert done["flagged"] >= 0.6 * len(done["members"])
    assert done["first_alert_min"] is not None and done["paid_before_alert"] < done["planned"]
    alerts = [p for p in hub if p["type"] == "alert" and p["kind"] == "new"]
    assert alerts and all(p["text"].startswith("tok_") for p in alerts)   # tokens only for the hub
    ring_events = [p for p in hub if p["type"] == "ring"]
    assert ring_events and len({p["ring"] for p in ring_events}) == 1     # one ring, one stable id while it grows
    ring = client.get(f"/api/rings/{ring_events[0]['ring']}").json()
    assert ring["is_new"] and len(ring["banks"]) >= 2 and ring["graph"]["nodes"]
    assert client.get("/api/overview").json()["kpis"]["alerts"] > before
    act = client.get("/api/activity").json()
    assert act["attack"]["done"] and any(e["type"] == "ring" for e in act["events"])


def test_events_are_filtered_by_role(client):
    """A bank is told about its own accounts by name, and nothing about other banks' alerts."""
    e = state.engine
    s = e.snap
    token = str(s.tokens[np.flatnonzero(s.flagged & (s.world.bank == 0))[0]])
    out = presenter.render_event(e, {"type": "alert", "kind": "new", "token": token}, {"running": True, "speed": 60})
    assert out["B"] is None and out["C"] is None
    assert out["hub"]["text"].startswith("tok_") and "Bank A" in out["hub"]["text"]
    name = str(s.world.name[s.index[token]])
    assert out["A"]["text"].startswith(name) and name not in out["hub"]["text"]
    tick = presenter.render_event(e, {"type": "tick", "new_tx": 0, "new_legit": 0, "fresh": slice(0, 0)},
                                  {"running": True, "speed": 60})
    assert "ledger" in tick["A"] and "ledger" not in tick["hub"]
    assert tick["A"]["kpis"]["accounts"] < tick["hub"]["kpis"]["accounts"]


def test_demo_controls_can_be_switched_off(client, monkeypatch):
    sign_in(client, "A")
    monkeypatch.setattr(config, "DEMO_CONTROLS", False)
    for path in ("/api/attack", "/api/reset"):
        assert client.post(path, headers=H).status_code == 403
    assert client.post("/api/stream", json={"running": False}, headers=H).status_code == 403
    assert all("truth" not in a for a in client.get("/api/alerts?truth=1").json())   # no answer key either
    assert client.get("/api/session").json()["demo_controls"] is False


# ---------------------------------------------------------------- case files: create, read, update, delete
def _fresh_bank_a(client):
    sign_in(client, "hub")
    client.post("/api/reset", headers=H)
    sign_in(client, "A")
    return client.get("/api/alerts").json()[0]["token"]


def test_case_crud_round_trip(client):
    token = _fresh_bank_a(client)
    assert client.get("/api/cases").json() == []

    # Create
    new = {"token": token, "title": "  Check   payee\u0000 ", "priority": "high", "assignee": "Meera"}
    r = client.post("/api/cases", json=new, headers=H)
    case = r.json()
    url = f"/api/cases/{case['id']}"
    assert r.status_code == 201 and r.headers["location"] == url
    assert case["title"] == "Check payee" and case["priority"] == "high" and case["assignee"] == "Meera"
    assert case["state"] == "open" and case["version"] == 1 and case["notes"] == [] and case["created_by"] == "Bank A analyst"
    assert client.post("/api/cases", json=new, headers=H).status_code == 409        # one case per account

    # Read
    assert [c["id"] for c in client.get("/api/cases").json()] == [case["id"]]
    assert client.get(url).json()["assignee"] == "Meera"
    assert client.get(f"/api/accounts/{token}").json()["case"]["id"] == case["id"]
    assert next(a for a in client.get("/api/alerts").json() if a["token"] == token)["case"]["state"] == "open"

    # Update
    r = client.patch(url, json={"version": 1, "assignee": "Arjun", "priority": "low"}, headers=H)
    assert r.status_code == 200 and r.json()["assignee"] == "Arjun" and r.json()["priority"] == "low" and r.json()["version"] == 2
    assert client.patch(url, json={"version": 1, "title": "stale write"}, headers=H).status_code == 409   # lost-update guard
    assert client.get(url).json()["title"] == "Check payee"
    same = client.patch(url, json={"version": 2, "assignee": "Arjun"}, headers=H)
    assert same.status_code == 200 and same.json()["version"] == 2                  # nothing changed, nothing bumped
    closed = client.patch(url, json={"version": 2, "state": "closed"}, headers=H).json()
    assert closed["state"] == "closed" and closed["version"] == 3

    # Notes: create, read, update, delete
    r = client.post(f"{url}/notes", json={"body": "  first line\r\nsecond line  "}, headers=H)
    assert r.status_code == 201 and r.json()["note_count"] == 1
    n1 = r.json()["notes"][0]
    assert n1["body"] == "first line\nsecond line" and n1["author"] == "Bank A analyst" and n1["edited"] is False
    n2 = client.post(f"{url}/notes", json={"body": "to be removed"}, headers=H).json()["notes"][1]
    edited = client.patch(f"{url}/notes/{n1['id']}", json={"body": "corrected"}, headers=H).json()
    assert edited["notes"][0]["body"] == "corrected" and edited["notes"][1]["body"] == "to be removed"
    r = client.delete(f"{url}/notes/{n2['id']}", headers=H)
    assert r.status_code == 204 and r.content == b""
    assert [n["id"] for n in client.get(url).json()["notes"]] == [n1["id"]]
    assert client.patch(f"{url}/notes/{n2['id']}", json={"body": "x"}, headers=H).status_code == 404
    assert client.delete(f"{url}/notes/{n2['id']}", headers=H).status_code == 404

    # Delete
    r = client.delete(url, headers=H)
    assert r.status_code == 204 and r.content == b""
    assert client.get(url).status_code == 404 and client.delete(url, headers=H).status_code == 404
    assert client.get("/api/cases").json() == [] and client.get(f"/api/accounts/{token}").json()["case"] is None
    again = client.post("/api/cases", json={"token": token}, headers=H).json()
    assert again["id"] != case["id"] and again["notes"] == [] and again["title"] == "Alert review"   # notes went with the case

    actions = {x["action"] for x in client.get("/api/audit").json()["entries"]}
    assert {"case-create", "case-update", "case-delete", "note-add", "note-edit", "note-delete"} <= actions
    assert client.get("/api/audit").json()["chain"]["ok"] is True


def test_case_files_are_private_to_the_bank(client):
    token = _fresh_bank_a(client)
    case = client.post("/api/cases", json={"token": token, "title": "Confidential summary", "assignee": "Meera"}, headers=H).json()
    url = f"/api/cases/{case['id']}"
    client.post(f"{url}/notes", json={"body": "secret note text"}, headers=H)
    seen_b, restore = collect("B")
    seen_hub, restore_hub = collect("hub")
    try:
        client.post(f"{url}/notes", json={"body": "another secret"}, headers=H)
    finally:
        restore()
        restore_hub()
    assert not seen_b                                                    # another bank hears nothing
    assert seen_hub and "secret" not in str(seen_hub) and seen_hub[0]["text"].startswith("Bank A analyst added a note on tok_")

    # The hub learns that a case exists, and nothing inside it. It cannot change anything.
    sign_in(client, "hub")
    listed = client.get("/api/cases").json()
    blob = client.get("/api/cases").text + client.get(url).text + client.get(f"/api/accounts/{token}").text + client.get("/api/audit").text
    assert len(listed) == 1 and listed[0]["redacted"] and listed[0]["note_count"] == 2
    assert not ({"title", "assignee", "notes", "created_by", "version"} & set(listed[0]))
    assert "secret" not in blob and "Confidential" not in blob and "Meera" not in blob
    for method, path, body in (("post", "/api/cases", {"token": token}), ("patch", url, {"version": 1, "state": "closed"}),
                               ("delete", url, None), ("post", f"{url}/notes", {"body": "x"})):
        kwargs = {"headers": H, **({"json": body} if body else {})}
        assert getattr(client, method)(path, **kwargs).status_code == 403, (method, path)

    # Another bank cannot even tell that the case exists.
    sign_in(client, "B")
    assert client.get("/api/cases").json() == []
    assert client.get(url).status_code == 404
    assert client.patch(url, json={"version": 1, "title": "hijack"}, headers=H).status_code == 404
    assert client.delete(url, headers=H).status_code == 404
    assert client.post(f"{url}/notes", json={"body": "x"}, headers=H).status_code == 404
    assert client.post("/api/cases", json={"token": token}, headers=H).status_code == 403
    sign_in(client, "A")
    assert client.get(url).json()["title"] == "Confidential summary" and len(client.get(url).json()["notes"]) == 2


def test_case_input_is_validated(client):
    token = _fresh_bank_a(client)
    post = lambda body, **kw: client.post("/api/cases", json=body, headers=H, **kw).status_code
    assert post({"token": "tok_nope"}) == 422 and post({"token": "tok_0000000000000000"}) == 404
    assert post({"token": token, "priority": "urgent"}) == 422
    assert post({"token": token, "title": "x" * 121}) == 422
    assert post({"token": token, "bank": "B"}) == 422                                   # unknown fields are refused
    assert client.post("/api/cases", json={"token": token}).status_code == 403          # no anti-forgery header
    assert client.get("/api/cases").json() == []                                        # none of that created anything
    case = client.post("/api/cases", json={"token": token}, headers=H).json()
    url = f"/api/cases/{case['id']}"
    assert client.patch(url, json={"title": "no version"}, headers=H).status_code == 422
    assert client.patch(url, json={"version": 1, "state": "archived"}, headers=H).status_code == 422
    for body in ("", "   \n\t ", "x" * 2001):
        assert client.post(f"{url}/notes", json={"body": body}, headers=H).status_code == 422
    assert client.get(url).json()["note_count"] == 0
    assert client.get("/api/cases/not-a-number").status_code == 422
    assert client.get("/api/cases/999999").status_code == 404
    note = client.post(f"{url}/notes", json={"body": "<script>alert(1)</script>"}, headers=H).json()["notes"][0]
    assert note["body"] == "<script>alert(1)</script>"                                  # stored as plain text; the page escapes it


def test_decision_opens_a_case_and_deleting_it_resets_the_decision(client):
    token = _fresh_bank_a(client)
    out = client.post(f"/api/accounts/{token}/action", json={"action": "hold"}, headers=H).json()
    assert out["status"] == "held" and out["case_id"]
    case = client.get(f"/api/cases/{out['case_id']}").json()
    assert case["token"] == token and case["decision"] == "held" and case["title"] == "Alert review"
    again = client.post(f"/api/accounts/{token}/action", json={"action": "confirm"}, headers=H).json()
    assert again["case_id"] == out["case_id"] and len(client.get("/api/cases").json()) == 1
    assert client.delete(f"/api/cases/{out['case_id']}", headers=H).status_code == 204
    account = client.get(f"/api/accounts/{token}").json()
    assert account["status"] == "open" and account["case"] is None
    assert token in [a["token"] for a in client.get("/api/alerts").json()]
    sign_in(client, "hub")
    client.post("/api/reset", headers=H)
    assert client.get("/api/cases").json() == []


# ---------------------------------------------------------------- ingestion API
def _event(sample, **changes):
    from uuid import uuid4
    return {**sample, "event_id": "evt-" + uuid4().hex[:20], **changes}


def test_ingested_payment_to_a_flagged_account_is_scored_and_reported(client):
    _fresh_bank_a(client)
    samples = {s["key"]: s["event"] for s in client.get("/api/ingest/samples").json()}
    assert {"normal", "risky", "stale"} <= set(samples)
    before = client.get("/api/payments").json()["count"]
    seen_a, restore = collect("A")
    seen_b, restore_b = collect("B")
    seen_hub, restore_hub = collect("hub")
    try:
        r = client.post("/api/ingest/transactions", json={"events": [samples["normal"], samples["risky"]]}, headers=H)
        assert r.status_code == 202 and r.json()["accepted"] == 2 and r.json()["queued"] == 2
        live_step()
    finally:
        restore(); restore_b(); restore_hub()
    tick = next(p for p in seen_a if p["type"] == "tick")
    assert tick["received"] == 2
    pays = [p for p in seen_a if p["type"] == "payment"]
    assert len(pays) == 1 and pays[0]["payment"]["amount"] == 24000         # the everyday payment raised nothing
    view = pays[0]["payment"]
    assert view["payee"]["flagged"] and not view["payee"]["own"] and view["payee"]["label"].startswith("tok_")
    assert view["risk"] >= view["risk_alone"] and view["reasons"][0]["feature"] == "payee_score"
    assert view["reasons"][0]["source"] == "consortium" and "Hold the payment" in view["advice"]
    # Only the payer's bank is told. The hub never sees single payments.
    assert not [p for p in seen_b + seen_hub if p["type"] == "payment"]
    listed = client.get("/api/payments").json()
    assert listed["visible"] and listed["count"] == before + 1 and listed["rows"][0]["id"] == view["id"]
    sign_in(client, "hub")
    hub = client.get("/api/payments").json()
    assert hub["visible"] is False and hub["rows"] == [] and hub["count"] >= 1


def test_ingestion_is_idempotent_ordered_and_guarded(client):
    _fresh_bank_a(client)
    samples = {s["key"]: s["event"] for s in client.get("/api/ingest/samples").json()}
    post = lambda events, **kw: client.post("/api/ingest/transactions", json={"events": events}, headers=H, **kw)
    first = _event(samples["normal"])
    assert post([first]).json()["accepted"] == 1
    again = post([first]).json()
    assert again["accepted"] == 0 and again["duplicates"] == 1 and again["queued"] == 1     # sent twice, counted once

    stale = post([samples["stale"]]).json()["results"][0]
    assert stale["result"] == "rejected" and "replay" in stale["reason"].lower()
    future = post([_event(samples["normal"], timestamp="2099-01-01T00:00:00+00:00")]).json()["results"][0]
    assert future["result"] == "rejected"
    foreign = post([_event(samples["normal"], sender_bank="B")]).json()["results"][0]
    assert foreign["result"] == "rejected" and "own customers" in foreign["reason"]
    unknown = post([_event(samples["normal"], sender_account="999999999999")]).json()["results"][0]
    assert unknown["result"] == "rejected"
    for status in ("pending", "failed"):
        ack = post([_event(samples["normal"], status=status)]).json()
        assert ack["acknowledged"] == 1 and ack["accepted"] == 0

    # Bad shapes never reach the queue.
    assert post([_event(samples["normal"], amount=-5)]).status_code == 422
    assert post([_event(samples["normal"], event_id="x")]).status_code == 422
    assert post([_event(samples["normal"], receiver_account=None)]).status_code == 422
    assert post([_event(samples["normal"], surprise=1)]).status_code == 422
    assert post([]).status_code == 422
    assert client.post("/api/ingest/transactions", json={"events": [first]}).status_code == 403   # no anti-forgery header

    status = client.get("/api/ingest/status").json()
    assert status["totals"]["accepted"] == 1 and status["totals"]["duplicate"] == 1 and status["totals"]["rejected"] == 4
    assert len(status["dead_letters"]) == 4 and status["queued"] == 1
    live_step()
    assert client.get("/api/ingest/status").json()["queued"] == 0
    w = state.engine.snap.world
    assert w.amt[-1] == first["amount"] and str(w.acct_no[w.src[-1]]) == first["sender_account"]   # applied exactly once
    sign_in(client, "hub")
    assert client.post("/api/ingest/transactions", json={"events": [_event(samples["normal"])]}, headers=H).status_code == 403
    hub_status = client.get("/api/ingest/status").json()
    assert "dead_letters" not in hub_status and hub_status["totals"]["accepted"] == 1


def test_a_reversed_payment_is_removed_again(client):
    _fresh_bank_a(client)
    sample = next(s["event"] for s in client.get("/api/ingest/samples").json() if s["key"] == "normal")
    post = lambda events: client.post("/api/ingest/transactions", json={"events": events}, headers=H).json()
    odd = 7654321                      # an amount nothing else in the data has
    pay = _event(sample, amount=odd)
    post([pay])
    live_step()
    assert int((state.engine.snap.world.amt == odd).sum()) == 1
    undo = _event(sample, amount=odd, status="reversed", reverses=pay["event_id"])
    assert post([undo])["accepted"] == 1
    live_step()
    assert int((state.engine.snap.world.amt == odd).sum()) == 0
    missing = post([_event(sample, status="reversed", reverses="evt-does-not-exist-000")])["results"][0]
    assert missing["result"] == "rejected"


def test_ingestion_key_for_bank_adapters(client, monkeypatch):
    _fresh_bank_a(client)
    sample = next(s["event"] for s in client.get("/api/ingest/samples").json() if s["key"] == "normal")
    client.cookies.clear()                                   # a machine client has no session
    monkeypatch.setattr(config, "INGEST_KEYS", {"A": "adapter-key-for-pytest"})
    url, body = "/api/ingest/transactions", {"events": [_event(sample)]}
    assert client.post(url, json=body, headers=H).status_code == 401
    assert client.post(url, json=body, headers={"X-Ingest-Key": "wrong"}).status_code == 401
    ok = client.post(url, json=body, headers={"X-Ingest-Key": "adapter-key-for-pytest"})
    assert ok.status_code == 202 and ok.json()["accepted"] == 1
    as_b = client.post(url, json={"events": [_event(sample, sender_bank="B")]}, headers={"X-Ingest-Key": "adapter-key-for-pytest"})
    assert as_b.json()["results"][0]["result"] == "rejected"   # the key is for Bank A only
    assert client.get("/api/alerts", headers={"X-Ingest-Key": "adapter-key-for-pytest"}).status_code == 401


# ---------------------------------------------------------------- QR check
def test_qr_check_flags_scam_codes_without_revealing_the_payee(client):
    _fresh_bank_a(client)
    samples = client.get("/api/qr/samples").json()
    check = lambda payload: client.post("/api/qr/check", json={"payload": payload}, headers=H).json()
    shop = check(samples[0]["payload"])
    assert shop["level"] == "low" and shop["payee"]["flagged"] is False
    assert any("matches the name" in f["text"] for f in shop["findings"])
    lookalike = check(samples[1]["payload"])
    assert lookalike["level"] == "high" and "look-alike" in lookalike["findings"][0]["text"]
    scam = check(samples[2]["payload"])
    texts = " ".join(f["text"] for f in scam["findings"])
    assert scam["level"] == "high" and scam["payee"]["flagged"]
    assert "flagged as a likely mule" in texts and "does not match the name" in texts and "refund" in texts
    assert set(scam["payee"]) == {"bank_name", "band", "flagged", "ring"}        # a band, never an identity
    names = set(state.engine.snap.world.name.tolist())
    assert not any(n in json.dumps(scam) for n in names)
    assert check("https://example.com/pay")["level"] == "high"
    assert check("upi://pay?pa=not-a-vpa")["level"] == "high"
    assert client.post("/api/qr/check", json={"payload": "x" * 601}, headers=H).status_code == 422


# ---------------------------------------------------------------- model record and evaluation
def test_model_page_reports_honest_evaluation(client):
    from app.evaluation import evaluate
    sign_in(client, "A")
    e = state.engine
    if e.evaluation is None or "error" in e.evaluation:
        if getattr(e, "_tests", None) is None:
            e._benchmark(e.snap)
        e.evaluation = evaluate(e)
    m = client.get("/api/model").json()
    card = m["card"]
    assert card["model_version"].startswith("mrs-") and card["data_source"] == "synthetic" and card["key_version"] == "v1"
    by = {d["key"]: d for d in m["detectors"]}
    assert set(by) == {"rules", "behaviour", "local", "consortium"}             # the ablation, step by step
    assert by["consortium"]["recall"] > by["local"]["recall"] > by["behaviour"]["recall"] > by["rules"]["recall"]
    assert by["consortium"]["fp_per_1000"] < by["local"]["fp_per_1000"]
    for d in by.values():
        assert d["tp"] + d["fn"] == m["benchmark"]["mules"] and d["tn"] + d["fp"] == m["benchmark"]["accounts"] - m["benchmark"]["mules"]
    ev = m["evaluation"]
    curve = ev["threshold"]["points"]
    assert curve[0]["recall"] >= curve[-1]["recall"] and curve[0]["precision"] <= curve[-1]["precision"]
    names = [r["name"] for r in ev["robustness"]]
    assert "A fraud pattern never seen in training" in names and "Messy data" in names
    unseen = next(r for r in ev["robustness"] if r["name"].startswith("A fraud pattern"))
    clean = ev["robustness"][0]
    assert unseen["consortium"]["recall"] < clean["consortium"]["recall"]        # the weakness is reported, not hidden
    assert ev["temporal"]["consortium"]["recall"] > 0.6
    pay = {d["key"]: d for d in ev["payments"]["detectors"]}
    assert pay["consortium"]["money_caught"] > pay["local"]["money_caught"]
    assert len(m["drift"]) == 8 and all(d["status"] in ("stable", "watch", "drifted") for d in m["drift"])
    assert m["metrics"]["accounts"] > 0 and m["real_world"] is None or "results" in m["real_world"]


def test_training_on_supplied_files(tmp_path):
    """The same engine trains from CSV files in the documented format instead of the generator."""
    from app.dataio import DataError, export_world, load_world
    from app.pipeline import Engine
    from app.synth import generate_world
    source = generate_world(4242, 500, 12)
    folder = export_world(source, tmp_path / "bank_data")
    loaded = load_world(folder)
    assert loaded.n == source.n and int(loaded.is_mule.sum()) == int(source.is_mule.sum())
    assert len(loaded.src) == len(source.src) and np.allclose(np.sort(loaded.amt), np.sort(source.amt))
    e = Engine(n_per_bank=300, train_dir=str(folder))
    info = e.train_info
    assert info["data_source"] == "files" and info["data_id"].startswith("files-")
    assert "holdout" in info["threshold_from"] and info["train_accounts"] < source.n
    b = e.benchmark
    assert b["synthetic"] is False and "held out" in b["source"]
    cons = next(d for d in b["detectors"] if d["key"] == "consortium")
    assert cons["recall"] > 0.5 and cons["precision"] > 0.5
    (folder / "accounts.csv").write_text("account_id,bank\n1,A\n", encoding="utf-8")
    with pytest.raises(DataError):
        load_world(folder)


def test_readiness_metrics_exports_and_backup(client, tmp_path):
    client.cookies.clear()
    ready = client.get("/api/ready")
    assert ready.status_code == 200 and all(ready.json()["checks"].values())
    assert client.get("/api/metrics").status_code == 401
    sign_in(client, "A")
    met = client.get("/api/metrics").json()
    assert met["ticks"] > 0 and met["tick_ms_avg"] > 0 and "ingest" in met
    r = client.get("/api/audit/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.text.splitlines()[0].startswith("entry,time_utc")
    assert "Consortium hub operator,signin" not in r.text                   # same visibility rules as on screen
    token = client.get("/api/alerts").json()[0]["token"]
    client.post(f"/api/accounts/{token}/action", json={"action": "confirm"}, headers=H)
    labels = client.get("/api/feedback/export").text.splitlines()
    assert labels[0].startswith("account_token,decision") and any(line.startswith(f"{token},confirmed,1,") for line in labels)
    sign_in(client, "hub")
    assert client.get("/api/feedback/export").status_code == 403
    copy = tmp_path / "copy.db"
    assert state.store.backup(str(copy)) > 0
    from app.store import Store
    assert Store(str(copy)).verify()["ok"] is True
    client.post("/api/reset", headers=H)


def test_reset_restores_the_starting_state(client):
    sign_in(client, "hub")
    assert client.post("/api/reset", headers=H).json()["status"] == "ok"
    o = client.get("/api/overview").json()
    assert o["kpis"]["alerts"] > 0 and o["kpis"]["held"] == 0 and o["kpis"]["new_alerts"] == 0
    assert client.get("/api/activity").json()["attack"] is None
    assert client.get("/api/audit").json()["chain"]["ok"] is True
