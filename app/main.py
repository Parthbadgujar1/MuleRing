"""MuleRing Shield: FastAPI application.

Run locally:   uvicorn app.main:app --reload
On Railway:    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.gzip import GZipMiddleware

from . import config, presenter
from .pipeline import Engine
from .realtime import Bus, BusFull, LiveStream
from .security import COOKIE, SecurityMiddleware, Sessions, code_ok, is_https
from .store import Store

log = logging.getLogger("mulering")
STATIC = Path(__file__).parent / "static"
TOKEN_RE = re.compile(r"^tok_[0-9a-f]{16}$")
RING_RE = re.compile(r"^RING-[0-9A-F]{6}$")
ACTIONS = {"hold": "held", "confirm": "confirmed", "clear": "cleared", "reopen": None}
ACTION_TEXT = {"hold": "Placed on hold for review", "confirm": "Confirmed as mule account",
               "clear": "Cleared as legitimate", "reopen": "Reopened"}
ACTION_VERB = {"hold": "placed a hold on", "confirm": "confirmed as a mule", "clear": "cleared", "reopen": "reopened"}


class State:
    engine: Engine
    store: Store
    bus: Bus
    stream: LiveStream
    sessions: Sessions


state = State()


def stream_state() -> dict:
    return {"running": state.stream.running, "speed": config.STREAM_SPEED}


def emit(events: list[dict]) -> None:
    """Render engine events for every role and push them to subscribers."""
    for ev in events:
        state.bus.publish(presenter.render_event(state.engine, ev, stream_state()))


def live_step() -> None:
    e = state.engine
    with e.lock:
        events = e.advance(config.STREAM_SPEED * config.STREAM_TICK_SECONDS / 60.0)
        emit(events)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.store = Store(os.path.join(config.DATA_DIR, "mulering.db"))
    state.sessions = Sessions(config.SESSION_SECRET)
    state.engine = Engine()
    state.bus = Bus(config.ROLES, max_clients=config.MAX_LIVE_CLIENTS)
    state.bus.attach(asyncio.get_running_loop())
    # Decisions recorded before a restart still apply to accounts that exist.
    saved = {t: s for t, s in state.store.statuses().items() if t in state.engine.snap.index}
    if saved:
        state.engine.status = saved
        state.engine.rescore()
    state.stream = LiveStream(live_step, lambda: state.bus.count > 0 or state.engine.attack_active,
                              config.STREAM_TICK_SECONDS)
    state.stream.start()
    state.store.log("system", "startup", detail=f"Models trained in {state.engine.train_info['seconds']} s")
    if config.USING_DEFAULT_KEY:
        log.warning("CONSORTIUM_HMAC_KEY is not set: using the built-in demo key")
    yield
    state.stream.stop()


app = FastAPI(title="MuleRing Shield", version="2.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityMiddleware)  # outermost: guards every request, stamps every response


# ---------------------------------------------------------------- access control
def current_role(request: Request) -> str:
    role = state.sessions.read(request.cookies.get(COOKIE))
    if role is None:
        raise HTTPException(401, "Please sign in.")
    return role


def demo_only() -> None:
    if not config.DEMO_CONTROLS:
        raise HTTPException(403, "Demo controls are switched off on this deployment.")


def want_truth(truth: bool) -> bool:
    return truth and config.DEMO_CONTROLS


Role = Depends(current_role)


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["hub", "A", "B", "C"]
    code: str = Field("", max_length=200)


class ActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["hold", "confirm", "clear", "reopen"]
    note: str = Field("", max_length=500)


class StreamBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    running: bool


def session_info(role: str | None) -> dict:
    return {"role": role, "role_name": config.ROLE_NAMES.get(role),
            "roles": [{"code": r, "name": config.ROLE_NAMES[r]} for r in config.ROLES],
            "code_required": bool(config.ACCESS_CODE), "demo_controls": config.DEMO_CONTROLS}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/session")
def get_session(request: Request):
    return session_info(state.sessions.read(request.cookies.get(COOKIE)))


@app.post("/api/session")
def sign_in(body: SignIn, request: Request, response: Response):
    """Start a session. Switching role inside a valid session does not ask for the code again."""
    already = state.sessions.read(request.cookies.get(COOKIE))
    if already is None and not code_ok(body.code):
        state.store.log("system", "signin-failed", detail=f"Wrong access code for {config.ROLE_NAMES[body.role]}")
        raise HTTPException(401, "That access code is not right.")
    response.set_cookie(COOKIE, state.sessions.issue(body.role), max_age=config.SESSION_HOURS * 3600,
                        httponly=True, samesite="strict", secure=is_https(request.scope), path="/")
    if already != body.role:
        state.store.log(config.ROLE_NAMES[body.role], "signin", detail="Signed in", bank=body.role)
    return session_info(body.role)


@app.delete("/api/session")
def sign_out(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return session_info(None)


# ---------------------------------------------------------------- reads
@app.get("/api/overview")
def overview(role: str = Role):
    return presenter.overview(state.engine, role)


@app.get("/api/alerts")
def alerts(truth: bool = False, role: str = Role):
    out = presenter.alerts(state.engine, role, want_truth(truth))
    cases = {c["token"]: c for c in state.store.case_list(None if role == "hub" else role)}
    for a in out:
        c = cases.get(a["token"])
        a["case"] = {"id": c["id"], "state": c["state"], "priority": c["priority"]} if c else None
    return out


@app.get("/api/accounts/{token}")
def account(token: str, truth: bool = False, role: str = Role):
    if not TOKEN_RE.match(token):
        raise HTTPException(404, "Unknown account token")
    out = presenter.detail(state.engine, token, role, want_truth(truth),
                           presenter.audit(state.store.audit(50, token), role))
    if out is None:
        raise HTTPException(404, "Unknown account token")
    if not out["restricted"]:
        case = state.store.case_for(token)
        out["case"] = _full_case(case, role) if case else None
    return out


@app.get("/api/rings")
def rings(role: str = Role):
    return presenter.rings(state.engine, role)


@app.get("/api/rings/{ring_id}")
def ring(ring_id: str, truth: bool = False, role: str = Role):
    out = presenter.ring_detail(state.engine, ring_id, role, want_truth(truth)) if RING_RE.match(ring_id) else None
    if out is None:
        raise HTTPException(404, "Ring not found, or not visible to your role")
    return out


@app.get("/api/privacy")
def privacy(role: str = Role):
    return presenter.privacy(state.engine, role)


@app.get("/api/audit")
def audit(limit: int = Query(200, ge=1, le=500), role: str = Role):
    return {"entries": presenter.audit(state.store.audit(limit), role), "chain": state.store.verify()}


@app.get("/api/activity")
def activity(role: str = Role):
    """Recent events for this role, newest first, plus the current live state."""
    e = state.engine
    return {"events": list(state.bus.recent[role]), "clock": e.when(e.sim_now), "version": e.snap.version,
            "attack": e._attack_view() if e.attack else None, "viewers": state.bus.count, **stream_state()}


@app.get("/api/events")
async def events(request: Request, role: str = Role):
    """Server-sent events. Each subscriber receives only what its role may see."""
    try:
        cid, queue = state.bus.subscribe(role)
    except BusFull:
        raise HTTPException(503, "Too many live connections. Please try again shortly.")

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                if await request.is_disconnected():
                    break
        finally:
            state.bus.unsubscribe(cid)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ---------------------------------------------------------------- writes
@app.post("/api/accounts/{token}/action")
def act(token: str, body: ActionBody, role: str = Role):
    """A bank analyst records a decision on one of the bank's own accounts."""
    e = state.engine
    if not TOKEN_RE.match(token):
        raise HTTPException(404, "Unknown account token")
    with e.lock:
        s = e.snap
        if token not in s.index:
            raise HTTPException(404, "Unknown account token")
        i = s.index[token]
        if not presenter.is_own(s, i, role):
            raise HTTPException(403, "Only the bank that holds an account can act on it. "
                                     "The consortium hub is read-only.")
        before_flag, before_score = s.flagged.copy(), s.score.copy()
        new_status = ACTIONS[body.action]
        if new_status is None:
            e.status.pop(token, None)
        else:
            e.status[token] = new_status
        actor = config.ROLE_NAMES[role]
        note = " ".join(body.note.split())  # collapse whitespace and control characters
        state.store.set_status(token, new_status, actor)
        state.store.log(actor, body.action, token,
                        f"{ACTION_TEXT[body.action]} (risk score {round(float(s.score[i]) * 100)})",
                        bank=role, note=note)
        # Every decision lives in a case file, so one is opened if the account has none.
        case = state.store.case_for(token)
        if case is None and new_status is not None:
            case = state.store.case_create(token, role, "Alert review",
                                           "high" if new_status == "confirmed" else "medium", "", actor)
            state.store.log(actor, "case-create", token,
                            f"Case {case['id']} opened automatically with the decision", bank=role)

        # Confirming or clearing an account changes what its counterparties look like.
        changed = e.rescore()
        s2 = e.snap
        others = np.arange(s2.world.n) != i
        raised = np.flatnonzero(others & s2.flagged & ~before_flag)
        lowered = np.flatnonzero(others & ~s2.flagged & before_flag)
        moved = np.flatnonzero(others & (presenter_band(e, s2.score) != presenter_band(e, before_score)))
        moved = moved[np.argsort(-np.abs(s2.score - before_score)[moved])][:6]
        ripple = [{**presenter.card(e, s2, int(j), role), "before_band": presenter.band(e, before_score[j])}
                  for j in moved]
        if len(raised) or len(lowered):
            state.store.log("Consortium hub", "rescore", token,
                            f"Decision shared as intelligence: {len(raised)} linked account(s) newly flagged, "
                            f"{len(lowered)} no longer flagged")
        emit([{"type": "case", "token": token, "status": new_status or "open", "actor": actor,
               "verb": ACTION_VERB[body.action]}] + [c for c in changed if c.get("token") != token])
    return {"status": new_status or "open", "newly_flagged": int(len(raised)),
            "no_longer_flagged": int(len(lowered)), "ripple": ripple, "case_id": case["id"] if case else None}


def presenter_band(e: Engine, score: np.ndarray) -> np.ndarray:
    """Vector form of presenter.band: 2 high, 1 medium, 0 low."""
    return (score >= e.thr).astype(int) + (score >= e.thr * 0.4).astype(int)


# ---------------------------------------------------------------- case files (CRUD)
def _line(text: str, limit: int) -> str:
    """One clean line of text: no control characters, single spaces."""
    return " ".join("".join(ch for ch in text if ch.isprintable() or ch.isspace()).split())[:limit]


def _block(text: str) -> str:
    """Multi-line text with control characters removed and surrounding space trimmed."""
    return "".join(ch for ch in text.replace("\r\n", "\n") if ch.isprintable() or ch == "\n").strip()


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^tok_[0-9a-f]{16}$")
    title: str = Field("", max_length=120)
    priority: Literal["low", "medium", "high"] = "medium"
    assignee: str = Field("", max_length=60)


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    title: str | None = Field(None, max_length=120)
    priority: Literal["low", "medium", "high"] | None = None
    assignee: str | None = Field(None, max_length=60)
    state: Literal["open", "closed"] | None = None


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


def _case_or_404(case_id: int, role: str, write: bool = False) -> dict:
    """Fetch a case the role may see. Another bank's case looks exactly like a missing one."""
    if write and role == "hub":
        raise HTTPException(403, "The consortium hub is read-only. Case files belong to the bank.")
    case = state.store.case_get(case_id)
    if case is None or (role != "hub" and case["bank"] != role):
        raise HTTPException(404, "Case not found")
    return case


def _case_event(case: dict, role: str, verb: str) -> None:
    emit([{"type": "case", "token": case["token"], "status": state.engine.status.get(case["token"], "open"),
           "actor": config.ROLE_NAMES[role], "verb": verb}])


def _full_case(case: dict, role: str) -> dict:
    return presenter.case_view(state.engine, case, role, state.store.note_list(case["id"]))


@app.get("/api/cases")
def list_cases(role: str = Role):
    bank = None if role == "hub" else role
    return [presenter.case_view(state.engine, c, role) for c in state.store.case_list(bank)]


@app.post("/api/cases", status_code=201)
def create_case(body: CaseCreate, response: Response, role: str = Role):
    e = state.engine
    if role == "hub":
        raise HTTPException(403, "The consortium hub is read-only. Case files belong to the bank.")
    s = e.snap
    i = s.index.get(body.token)
    if i is None:
        raise HTTPException(404, "Unknown account token")
    if not presenter.is_own(s, i, role):
        raise HTTPException(403, "You can only open a case on your own bank's account.")
    actor = config.ROLE_NAMES[role]
    case = state.store.case_create(body.token, role, _line(body.title, 120) or "Alert review", body.priority,
                                   _line(body.assignee, 60), actor)
    if case is None:
        raise HTTPException(409, "This account already has a case.")
    state.store.log(actor, "case-create", body.token, f"Case {case['id']} opened, priority {case['priority']}", bank=role)
    _case_event(case, role, "opened a case on")
    response.headers["Location"] = f"/api/cases/{case['id']}"
    return _full_case(case, role)


@app.get("/api/cases/{case_id}")
def read_case(case_id: int, role: str = Role):
    return _full_case(_case_or_404(case_id, role), role)


@app.patch("/api/cases/{case_id}")
def update_case(case_id: int, body: CaseUpdate, role: str = Role):
    case = _case_or_404(case_id, role, write=True)
    changes = {}
    if body.title is not None:
        changes["title"] = _line(body.title, 120) or "Alert review"
    if body.assignee is not None:
        changes["assignee"] = _line(body.assignee, 60)
    if body.priority is not None:
        changes["priority"] = body.priority
    if body.state is not None:
        changes["state"] = body.state
    changes = {k: v for k, v in changes.items() if case[k] != v}
    if not changes:
        return _full_case(case, role)   # nothing to do: the update is idempotent
    updated = state.store.case_update(case_id, changes, body.version)
    if updated is None:
        raise HTTPException(409, "Someone else changed this case. Reload it and try again.")
    what = ", ".join("state" if k == "state" else k for k in changes)
    detail = f"Case {case_id} updated: {what}"
    if "state" in changes:
        detail = f"Case {case_id} {'closed' if changes['state'] == 'closed' else 'reopened'}" + \
                 (f"; also updated {', '.join(k for k in changes if k != 'state')}" if len(changes) > 1 else "")
    state.store.log(config.ROLE_NAMES[role], "case-update", case["token"], detail, bank=role)
    _case_event(updated, role, "closed the case on" if changes.get("state") == "closed" else "updated the case on")
    return _full_case(updated, role)


@app.delete("/api/cases/{case_id}", status_code=204)
def delete_case(case_id: int, role: str = Role):
    """Delete a case, its notes and the decision recorded on the account."""
    e = state.engine
    case = _case_or_404(case_id, role, write=True)
    with e.lock:
        state.store.case_delete(case_id)
        events = []
        if case["token"] in e.status:   # the decision goes with the case
            e.status.pop(case["token"])
            state.store.set_status(case["token"], None, config.ROLE_NAMES[role])
            events = e.rescore()
        state.store.log(config.ROLE_NAMES[role], "case-delete", case["token"],
                        f"Case {case_id} deleted with {case['note_count']} note(s); decision reset to open", bank=role)
        emit([{"type": "case", "token": case["token"], "status": "open", "actor": config.ROLE_NAMES[role],
               "verb": "deleted the case on"}] + events)
    return Response(status_code=204)


@app.post("/api/cases/{case_id}/notes", status_code=201)
def add_note(case_id: int, body: NoteBody, role: str = Role):
    case = _case_or_404(case_id, role, write=True)
    text = _block(body.body)
    if not text:
        raise HTTPException(422, "A note cannot be empty.")
    note = state.store.note_add(case_id, config.ROLE_NAMES[role], text)
    state.store.case_touch(case_id)
    state.store.log(config.ROLE_NAMES[role], "note-add", case["token"],
                    f"Note {note['id']} added to case {case_id} ({len(text)} characters)", bank=role)
    _case_event(case, role, "added a note on")
    return _full_case(state.store.case_get(case_id), role)


@app.patch("/api/cases/{case_id}/notes/{note_id}")
def edit_note(case_id: int, note_id: int, body: NoteBody, role: str = Role):
    case = _case_or_404(case_id, role, write=True)
    text = _block(body.body)
    if not text:
        raise HTTPException(422, "A note cannot be empty.")
    if state.store.note_update(case_id, note_id, text) is None:
        raise HTTPException(404, "Note not found")
    state.store.case_touch(case_id)
    state.store.log(config.ROLE_NAMES[role], "note-edit", case["token"],
                    f"Note {note_id} on case {case_id} edited", bank=role)
    _case_event(case, role, "edited a note on")
    return _full_case(state.store.case_get(case_id), role)


@app.delete("/api/cases/{case_id}/notes/{note_id}", status_code=204)
def delete_note(case_id: int, note_id: int, role: str = Role):
    case = _case_or_404(case_id, role, write=True)
    if not state.store.note_delete(case_id, note_id):
        raise HTTPException(404, "Note not found")
    state.store.case_touch(case_id)
    state.store.log(config.ROLE_NAMES[role], "note-delete", case["token"],
                    f"Note {note_id} on case {case_id} deleted", bank=role)
    _case_event(case, role, "deleted a note on")
    return Response(status_code=204)


@app.post("/api/attack", dependencies=[Depends(demo_only)])
def attack(role: str = Role):
    """Start a new mule ring. Its payments arrive over the next minute or two of real time."""
    e = state.engine
    with e.lock:
        try:
            events = e.launch_attack()
        except RuntimeError as err:
            raise HTTPException(409, str(err))
        a = e._attack_view()
        state.stream.running = True
        state.store.log(config.ROLE_NAMES[role], "attack", detail=(
            f"Simulated attack {a['n']} started: {a['members']} mule accounts across {len(a['banks'])} banks, "
            f"{a['total']} payments planned"))
        emit([{"type": "system", "what": "attack",
               "text": f"Simulated attack started: {a['members']} mule accounts across {len(a['banks'])} banks"}]
             + events)
    return a


@app.post("/api/stream", dependencies=[Depends(demo_only)])
def stream(body: StreamBody, role: str = Role):
    state.stream.running = body.running
    word = "resumed" if body.running else "paused"
    state.store.log(config.ROLE_NAMES[role], "stream", detail=f"Live stream {word}")
    emit([{"type": "system", "what": "stream", "text": f"Live stream {word}"}])
    return stream_state()


@app.post("/api/reset", dependencies=[Depends(demo_only)])
def reset(role: str = Role):
    e = state.engine
    with e.lock:
        e.reset()
        state.store.clear_cases()
        state.bus.clear()
        state.stream.running = True
        state.store.log(config.ROLE_NAMES[role], "reset",
                        detail="Live data restored to its starting state; case decisions cleared")
        emit([{"type": "system", "what": "reset", "text": "Demo data restored to its starting state"}])
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
