"""What a bank shares, and what the consortium hub does with it.

A SharePackage is the only thing that leaves a bank. It contains tokens,
numeric aggregates and the bank's own risk score. It contains no names,
account numbers, phone numbers, UPI IDs, raw device IDs or individual
transactions. The hub works from packages alone and never holds the HMAC key.

Limits worth stating plainly: tokens are pseudonymous, not anonymous. The hub
can see which tokens pay which tokens and in what total amount, so the shared
data still deserves protection as personal data.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np

from . import config
from .features import BEHAVIOUR, network_features
from .tokenise import account_token, device_token


@dataclass
class SharePackage:
    bank: str                  # contributing bank
    account_token: np.ndarray  # one per customer account
    behaviour: np.ndarray      # numeric aggregates, columns = features.BEHAVIOUR
    own_risk: np.ndarray       # the bank's own behavioural risk score, 0..1
    flow_from: np.ndarray      # aggregated payment flows out of the bank's accounts
    flow_to: np.ndarray
    flow_amount: np.ndarray
    flow_count: np.ndarray
    device_account: np.ndarray  # which account tokens were seen on which device tokens
    device_token: np.ndarray


SHARED_FIELDS = [f.name for f in fields(SharePackage)]


def tokens_for(world, key: bytes) -> np.ndarray:
    """Every member bank derives the same token for the same account."""
    return np.array([account_token(key, config.BANKS[b][2], a) for b, a in zip(world.bank, world.acct_no)])


def build_share_package(world, bank_idx: int, tokens, B, risk, es, ed, ea, ec, key: bytes) -> SharePackage:
    mine = np.flatnonzero(world.bank == bank_idx)
    out = world.bank[es] == bank_idx  # each bank reports the flows its own customers send
    dev = world.bank[world.dev_acct] == bank_idx
    dev_tok = {int(d): device_token(key, int(d)) for d in np.unique(world.dev_id[dev])}
    return SharePackage(
        bank=config.BANKS[bank_idx][0], account_token=tokens[mine], behaviour=B[mine], own_risk=risk[mine],
        flow_from=tokens[es[out]], flow_to=tokens[ed[out]], flow_amount=ea[out], flow_count=ec[out],
        device_account=tokens[world.dev_acct[dev]],
        device_token=np.array([dev_tok[int(d)] for d in world.dev_id[dev]]),
    )


@dataclass
class HubGraph:
    """The hub's working copy, built purely from share packages."""
    tokens: np.ndarray
    bank: np.ndarray
    behaviour: np.ndarray
    own_risk: np.ndarray
    es: np.ndarray
    ed: np.ndarray
    ea: np.ndarray
    ec: np.ndarray
    dev_acct: np.ndarray
    dev_id: np.ndarray

    def network(self, risk_override: dict[str, float] | None = None) -> np.ndarray:
        risk = self.own_risk.copy()
        if risk_override:
            index = {t: i for i, t in enumerate(self.tokens)}
            for tok, value in risk_override.items():
                if tok in index:
                    risk[index[tok]] = value
        uniq_senders = self.behaviour[:, BEHAVIOUR.index("uniq_senders")]
        return network_features(len(self.tokens), self.es, self.ed, self.ea, risk, uniq_senders,
                                self.dev_acct, self.dev_id, self.bank, consortium=True)


def assemble(packages: list[SharePackage]) -> HubGraph:
    tokens = np.concatenate([p.account_token for p in packages])
    index = {t: i for i, t in enumerate(tokens)}
    look = lambda arr: np.fromiter((index[t] for t in arr), dtype=np.int64, count=len(arr))
    dev_tokens = np.concatenate([p.device_token for p in packages])
    _, dev_id = np.unique(dev_tokens, return_inverse=True)
    return HubGraph(
        tokens=tokens,
        bank=np.concatenate([np.full(len(p.account_token), config.BANK_CODES.index(p.bank)) for p in packages]),
        behaviour=np.vstack([p.behaviour for p in packages]),
        own_risk=np.concatenate([p.own_risk for p in packages]),
        es=look(np.concatenate([p.flow_from for p in packages])),
        ed=look(np.concatenate([p.flow_to for p in packages])),
        ea=np.concatenate([p.flow_amount for p in packages]),
        ec=np.concatenate([p.flow_count for p in packages]),
        dev_acct=look(np.concatenate([p.device_account for p in packages])),
        dev_id=dev_id.astype(np.int64),
    )
