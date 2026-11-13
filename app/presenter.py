"""Role-dependent serialisation: the single place that decides who sees what.

  hub      the consortium operator: tokens and scores for everyone, no identities,
           no individual transactions, no analyst notes
  A, B, C  a bank analyst: identities and transactions of the bank's own customers;
           only a token, bank and risk band for anyone else

The role always comes from the signed session, never from the request.
"""
from __future__ import annotations

import numpy as np

from . import config
from .consortium import SHARED_FIELDS
from .explain import TEMPLATES, inr, reasons, source_of
from .features import BEHAVIOUR
from .pipeline import CONS_NAMES, Engine, Snapshot
from .rules import RULES
from .synth import CHANNELS
from .tokenise import device_token, mask_account, mask_vpa

ROLES = config.ROLES


def short(token: str) -> str:
    return f"{token[:8]}…{token[-3:]}"


def band(e: Engine, score: float) -> str:
    return "high" if score >= e.thr else "medium" if score >= e.thr * 0.4 else "low"


def is_own(s: Snapshot, i: int, role: str) -> bool:
    return role != "hub" and config.BANK_CODES[s.world.bank[i]] == role


def _memo(s: Snapshot, key: tuple, build):
    """Cache a payload for the lifetime of a snapshot."""
    if key not in s.cache:
        s.cache[key] = build()
    return s.cache[key]


def card(e: Engine, s: Snapshot, i: int, role: str, truth: bool = False) -> dict:
    w, tok = s.world, str(s.tokens[i])
    own = is_own(s, i, role)
    b = config.BANKS[w.bank[i]]
    c = {"token": tok, "label": str(w.name[i]) if own else short(tok), "bank": b[0], "bank_name": b[1],
         "own": own, "band": band(e, s.score[i]), "flagged": bool(s.flagged[i]),
         "status": e.status.get(tok, "open" if s.flagged[i] else "none"),
         "ring": s.ring_of.get(i), "is_new": e.is_new(tok)}
    if own:
        c.update(name=str(w.name[i]), account=mask_account(str(w.acct_no[i])),
                 vpa=mask_vpa(str(w.name[i]), str(w.acct_no[i]), b[3]))
    if own or role == "hub":
        c.update(score=round(float(s.score[i]) * 100), local_score=round(float(s.score_local[i]) * 100),
                 only_consortium=bool(s.flagged[i] and s.score_local[i] < e.thr_local))
    if truth:
        c["truth"] = {"is_mule": bool(w.is_mule[i]), "role": str(w.role[i])}
    return c


def _reasons(s: Snapshot, i: int) -> dict:
    return _memo(s, ("reasons", i), lambda: reasons(CONS_NAMES, s.X[i], s.contrib[i]))


def _scope(s: Snapshot, role: str) -> np.ndarray:
    if role == "hub":
        return np.ones(s.world.n, bool)
    return s.world.bank == config.BANK_CODES.index(role)


# ---------------------------------------------------------------------------
def alerts(e: Engine, role: str, truth: bool = False) -> list[dict]:
    s = e.snap
    idx = np.flatnonzero(s.flagged & _scope(s, role))
    out = []
    for i in idx[np.argsort(-s.score[idx])]:
        c = card(e, s, int(i), role, truth)
        r = _reasons(s, int(i))["raises"]
        c["top_reason"] = r[0]["text"] if r else ""
        out.append(c)
    return out


def _ego_graph(e: Engine, s: Snapshot, i: int, role: str, truth: bool) -> dict:
    keep = {i}
    for mine, other in ((s.ed == i, s.es), (s.es == i, s.ed)):
        j = np.flatnonzero(mine)
        top = j[np.argsort(-s.ea[j])[:14]]
        keep.update(other[top].tolist())
        keep.update(other[j][s.flagged[other[j]]].tolist())
    return _graph(e, s, sorted(keep), role, truth, focus={i}, atm_for={i})


def _graph(e: Engine, s: Snapshot, nodes: list[int], role: str, truth: bool, focus=frozenset(),
           roles: dict | None = None, atm_for=frozenset(), members=frozenset()) -> dict:
    w = s.world
    mask = np.zeros(w.n, bool)
    mask[nodes] = True
    sel = mask[s.es] & mask[s.ed]
    if members:  # ring view: drop chatter between outsiders
        m = np.zeros(w.n, bool)
        m[list(members)] = True
        sel &= m[s.es] | m[s.ed]
    out_nodes = []
    for i in nodes:
        c = card(e, s, i, role, truth)
        out_nodes.append({"id": c["token"], "label": c["label"], "bank": c["bank"], "band": c["band"],
                          "flagged": c["flagged"], "own": c["own"], "focus": i in focus,
                          "member": i in members, "role": (roles or {}).get(i),
                          "truth": c.get("truth")})
    edges = [{"s": str(s.tokens[a]), "t": str(s.tokens[b]), "amount": round(float(amt)), "count": int(k)}
             for a, b, amt, k in zip(s.es[sel], s.ed[sel], s.ea[sel], s.ec[sel])]
    atm = (w.dst < 0) & np.isin(w.src, list(atm_for))
    if atm.any():
        out_nodes.append({"id": "ATM", "label": "Cash (ATM)", "bank": "", "band": "low", "flagged": False,
                          "own": False, "focus": False, "member": False, "role": None, "atm": True})
        for a in np.unique(w.src[atm]):
            m = atm & (w.src == a)
            edges.append({"s": str(s.tokens[a]), "t": "ATM", "amount": round(float(w.amt[m].sum())),
                          "count": int(m.sum())})
    return {"nodes": out_nodes, "edges": edges}


def _party(s: Snapshot, other: int, role: str) -> str:
    """How a counterparty appears in a bank's own ledger."""
    w = s.world
    if other < 0:
        return "Cash withdrawal (ATM)"
    if is_own(s, other, role):
        return str(w.name[other])
    ob = config.BANKS[w.bank[other]]
    return mask_vpa(str(w.name[other]), str(w.acct_no[other]), ob[3])


def detail(e: Engine, token: str, role: str, truth: bool, history: list[dict]) -> dict | None:
    s = e.snap
    if token not in s.index:
        return None
    i, w = s.index[token], s.world
    c = card(e, s, i, role, truth)
    if role != "hub" and not c["own"]:
        return {**c, "restricted": True, "version": s.version,
                "message": f"This account is held at {c['bank_name']}. Your bank can see its token and "
                           f"risk band, but not its identity, score details or transactions."}
    r = _reasons(s, i)
    by_source = {"own": 0.0, "consortium": 0.0}
    for name, v in zip(CONS_NAMES, s.contrib[i]):
        by_source[source_of(name)] += float(v)
    out = {**c, "restricted": False, "version": s.version, "reasons": r,
           "impact_own": round(by_source["own"], 2), "impact_consortium": round(by_source["consortium"], 2),
           "threshold": round(e.thr * 100), "threshold_local": round(e.thr_local * 100),
           "age_days": round(float(w.age_days[i])),
           "graph": _ego_graph(e, s, i, role, truth), "history": history, "can_act": c["own"]}
    flows = []
    for direction, mine, other in (("in", s.ed == i, s.es), ("out", s.es == i, s.ed)):
        j = np.flatnonzero(mine)
        for k in j[np.argsort(-s.ea[j])[:8]]:
            flows.append({"dir": direction, "party": card(e, s, int(other[k]), role),
                          "amount": round(float(s.ea[k])), "count": int(s.ec[k])})
    out["flows"] = flows
    if c["own"]:  # the bank's own ledger: individual transactions stay inside the bank
        t = np.flatnonzero((w.src == i) | (w.dst == i))
        t = t[np.argsort(-w.ts[t], kind="stable")[:40]]
        tx = []
        for k in t:
            incoming = w.dst[k] == i
            other = int(w.src[k] if incoming else w.dst[k])
            tx.append({"time": e.when(w.ts[k]), "dir": "in" if incoming else "out",
                       "party": _party(s, other, role), "amount": round(float(w.amt[k])),
                       "channel": CHANNELS[w.ch[k]]})
        out["transactions"] = tx
    return out


def _ring_summary(e: Engine, s: Snapshot, r: dict, role: str) -> dict:
    mine = [i for i in r["members_idx"] if is_own(s, i, role)]
    return {"id": r["id"], "size": len(r["members_idx"]),
            "banks": [config.BANKS[b][1] for b in r["banks"]],
            "funnelled": round(r["funnelled"]), "senders": r["senders"],
            "cash_withdrawn": round(r["cash_withdrawn"]), "sent_onward": round(r["sent_onward"]),
            "first": e.when(r["first"]), "last": e.when(r["last"]),
            "mean_score": round(r["mean_score"] * 100), "own_members": len(mine),
            "is_new": any(e.is_new(str(s.tokens[i])) for i in r["members_idx"])}


def rings(e: Engine, role: str) -> list[dict]:
    s = e.snap
    scope = _scope(s, role)
    return [_ring_summary(e, s, r, role) for r in s.rings if scope[r["members_idx"]].any()]


def ring_detail(e: Engine, ring_id: str, role: str, truth: bool) -> dict | None:
    s = e.snap
    r = next((x for x in s.rings if x["id"] == ring_id), None)
    if r is None or not _scope(s, role)[r["members_idx"]].any():
        return None
    w = s.world
    members = r["members_idx"]
    senders = r["sender_idx"]
    nodes = list(dict.fromkeys(list(members) + senders[:45] + r["receiver_idx"][:6]))
    summary = _ring_summary(e, s, r, role)
    own_senders = [int(i) for i in senders if is_own(s, int(i), role)]
    summary.update(
        version=s.version,
        members=[{**card(e, s, i, role, truth), "role": r["roles"][i]} for i in members],
        graph=_graph(e, s, nodes, role, truth, roles=r["roles"], atm_for=set(members), members=set(members)),
        senders_shown=min(len(senders), 45), senders_total=int(len(senders)),
        own_senders=len(own_senders),
        own_sender_names=[str(w.name[i]) for i in own_senders[:12]],
    )
    return summary


# ---------------------------------------------------------------------------
def kpis(e: Engine, role: str) -> dict:
    """The headline numbers. Cheap, because they are pushed on every tick."""
    s = e.snap

    def build():
        w = s.world
        scope = _scope(s, role)
        flagged = s.flagged & scope
        p2a = w.dst >= 0
        tx_scope = scope[w.src] | (p2a & scope[np.where(p2a, w.dst, 0)])
        my_rings = [r for r in s.rings if scope[r["members_idx"]].any()]
        return {"accounts": int(scope.sum()), "transactions": int(tx_scope.sum()), "alerts": int(flagged.sum()),
                "rings": len(my_rings), "funnelled": round(sum(r["funnelled"] for r in my_rings)),
                "only_consortium": int((flagged & (s.score_local < e.thr_local)).sum()),
                "score_seconds": s.timing["seconds"], "scored_accounts": s.timing["accounts"]}

    k = dict(_memo(s, ("kpis", role), build))
    scope = _scope(s, role)
    k["held"] = sum(1 for t, st in e.status.items() if st == "held" and t in s.index and scope[s.index[t]])
    k["new_alerts"] = sum(1 for t in list(e.flag_time) if e.is_new(t) and t in s.index
                          and scope[s.index[t]] and s.flagged[s.index[t]])
    return k


def overview(e: Engine, role: str) -> dict:
    s, w = e.snap, e.snap.world
    banks = []
    for bi, b in enumerate(config.BANKS):
        m = w.bank == bi
        banks.append({"code": b[0], "name": b[1], "accounts": int(m.sum()), "alerts": int((s.flagged & m).sum()),
                      "only_consortium": int((s.flagged & m & (s.score_local < e.thr_local)).sum()),
                      "rings": sum(1 for r in s.rings if bi in r["banks"])})
    top = sorted(e.m_cons.importance().items(), key=lambda kv: -kv[1])[:10]
    return {
        "role": role, "role_name": config.ROLE_NAMES[role], "kpis": kpis(e, role),
        "banks": banks if role == "hub" else [], "benchmark": e.benchmark, "rules": RULES,
        "model": {**e.train_info, "features": len(CONS_NAMES),
                  "top_features": [{"feature": k, "label": TEMPLATES[k][0], "source": source_of(k),
                                    "weight": round(v, 2)} for k, v in top]},
        "window_days": w.n_days, "version": s.version,
    }


def privacy(e: Engine, role: str) -> dict:
    s, w = e.snap, e.snap.world
    scope = _scope(s, role)
    cand = np.flatnonzero(s.flagged & scope)
    i = int(cand[np.argmax(s.score[cand])]) if len(cand) else int(np.flatnonzero(scope)[0])
    b = config.BANKS[w.bank[i]]
    devs = w.dev_id[w.dev_acct == i]
    out_edges = np.flatnonzero(s.es == i)[:4]
    raw = None
    if role != "hub":
        raw = [
            ["Customer name", str(w.name[i])],
            ["Account number", mask_account(str(w.acct_no[i]))],
            ["IFSC", b[2] + "001234"],
            ["UPI ID", mask_vpa(str(w.name[i]), str(w.acct_no[i]), b[3])],
            ["Mobile number", str(w.phone[i])],
            ["Device ID", ", ".join(f"IMEI-{int(d):06d}" for d in devs) or "none on record"],
            ["Transactions", f"{int(((w.src == i) | (w.dst == i)).sum())} individual records with "
                             f"counterparty, amount, time and channel"],
        ]
    shared = [
        ["account_token", str(s.tokens[i])],
        ["bank", b[1]],
        ["own_risk", f"{s.risk0[i]:.2f}"],
        ["behaviour", ", ".join(f"{k}={s.B[i, j]:.4g}" for j, k in enumerate(BEHAVIOUR))],
        ["device_token", ", ".join(device_token(e.key, int(d)) for d in devs) or "none"],
        ["flows", "; ".join(f"{short(str(s.tokens[s.ed[k]]))} {inr(s.ea[k])} in {int(s.ec[k])} payment(s)"
                            for k in out_edges) or "none"],
    ]
    packages = [{"bank": p.bank, "accounts": int(len(p.account_token)), "flows": int(len(p.flow_from)),
                 "device_links": int(len(p.device_token))} for p in s.packages]
    return {
        "example_token": str(s.tokens[i]), "raw": raw, "shared": shared, "packages": packages,
        "shared_fields": SHARED_FIELDS, "default_key": config.USING_DEFAULT_KEY,
        "access_code": bool(config.ACCESS_CODE), "session_hours": config.SESSION_HOURS,
        "never_shared": ["Customer name", "Account number", "UPI ID", "Mobile number", "Address and KYC documents",
                         "Raw device ID", "Individual transactions"],
    }


def case_view(e: Engine, case: dict, role: str, notes: list[dict] | None = None) -> dict | None:
    """A case file as one role may see it.

    The owning bank gets everything. The hub learns that a case exists, its
    state and priority, but not its title, assignee or notes. Other banks get
    nothing.
    """
    if role != "hub" and case["bank"] != role:
        return None
    s = e.snap
    i = s.index.get(case["token"])
    bank = config.BANKS[config.BANK_CODES.index(case["bank"])]
    out = {"id": case["id"], "token": case["token"], "bank": bank[0], "bank_name": bank[1],
           "priority": case["priority"], "state": case["state"], "note_count": case["note_count"],
           "created_at": case["created_at"], "updated_at": case["updated_at"],
           "decision": e.status.get(case["token"], "open"),
           "account": card(e, s, i, role) if i is not None else None}
    if role == "hub":
        out["redacted"] = True
        return out
    out.update(redacted=False, title=case["title"], assignee=case["assignee"], created_by=case["created_by"],
               version=case["version"])
    if notes is not None:
        out["notes"] = [{"id": n["id"], "author": n["author"], "body": n["body"], "created_at": n["created_at"],
                         "updated_at": n["updated_at"], "edited": n["updated_at"] != n["created_at"]} for n in notes]
    return out


def audit(entries: list[dict], role: str) -> list[dict]:
    """A bank sees its own entries and system events. The hub sees every event but no notes."""
    out = []
    for a in entries:
        if role != "hub" and a["bank"] not in ("", role):
            continue
        row = {k: a[k] for k in ("id", "ts", "actor", "action", "token", "detail")}
        row["hash"] = a["hash"][:12]
        if a["note"]:
            row["note"] = a["note"] if role != "hub" else None
            row["note_hidden"] = role == "hub"
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Live events
# ---------------------------------------------------------------------------
def render_event(e: Engine, ev: dict, stream: dict) -> dict[str, dict | None]:
    """Turn one engine event into what each role may be told about it."""
    s = e.snap
    kind = ev["type"]
    clock = e.when(e.sim_now)

    def who(i: int, role: str) -> str:
        w = s.world
        return str(w.name[i]) if is_own(s, i, role) else f"{short(str(s.tokens[i]))} at {config.BANKS[w.bank[i]][1]}"

    out: dict[str, dict | None] = {}
    for role in ROLES:
        p = None
        if kind == "tick":
            p = {"type": "tick", "version": s.version, "clock": clock, "kpis": kpis(e, role),
                 "new_tx": ev["new_tx"], **stream}
            if role != "hub":  # a bank watches its own ledger; the hub never sees single transactions
                p["ledger"] = _ledger(e, s, ev["fresh"], role)
        elif kind == "alert":
            i = s.index.get(ev["token"])
            if i is not None and (role == "hub" or is_own(s, i, role)):
                score = round(float(s.score[i]) * 100)
                if ev["kind"] == "new":
                    r = _reasons(s, i)["raises"]
                    p = {"type": "alert", "kind": "new", "level": "high", "token": ev["token"],
                         "text": f"{who(i, role)} flagged at risk {score}",
                         "detail": r[0]["text"] if r else "",
                         "shared": bool(s.score_local[i] < e.thr_local)}
                else:
                    p = {"type": "alert", "kind": "resolved", "level": "info", "token": ev["token"],
                         "text": f"{who(i, role)} is no longer flagged (risk now {score})"}
        elif kind == "ring":
            r = next((x for x in s.rings if x["id"] == ev["ring"]), None)
            if r is not None:
                mine = sum(1 for i in r["members_idx"] if is_own(s, i, role))
                if role == "hub" or mine:
                    size, nb = len(r["members_idx"]), len(r["banks"])
                    tail = "" if role == "hub" else f", {mine} at your bank"
                    text = (f"New ring {r['id']}: {size} linked accounts across {nb} banks{tail}"
                            if ev["kind"] == "new" else f"Ring {r['id']} grew to {size} accounts{tail}")
                    p = {"type": "ring", "kind": ev["kind"], "level": "high" if ev["kind"] == "new" else "info",
                         "ring": r["id"], "text": text}
        elif kind == "case":
            i = s.index.get(ev["token"])
            if i is not None and (role == "hub" or is_own(s, i, role)):
                p = {"type": "case", "level": "info", "token": ev["token"], "status": ev["status"],
                     "text": f"{ev['actor']} {ev['verb']} {who(i, role)}"}
        elif kind == "attack":
            p = {k: v for k, v in ev.items()}
        elif kind == "system":
            p = {"type": "system", "level": "info", "what": ev["what"], "text": ev["text"]}
        if p is not None:
            p["clock"] = clock
        out[role] = p
    return out


def _ledger(e: Engine, s: Snapshot, fresh: slice, role: str, limit: int = 6) -> list[dict]:
    w = s.world
    idx = np.arange(fresh.start, fresh.stop)
    if len(idx) == 0:
        return []
    mine = _scope(s, role)
    p2a = w.dst[idx] >= 0
    keep = idx[mine[w.src[idx]] | (p2a & mine[np.where(p2a, w.dst[idx], 0)])][-limit:]
    return [{"time": e.when(w.ts[k]), "from": _party(s, int(w.src[k]), role), "to": _party(s, int(w.dst[k]), role),
             "amount": round(float(w.amt[k])), "channel": CHANNELS[w.ch[k]]} for k in keep]
