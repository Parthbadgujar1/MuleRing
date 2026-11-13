"""Bring your own data: train on a bank's real records instead of the synthetic generator.

Point TRAIN_DATA_DIR at a folder holding three CSV files and the models are
trained on them at start-up:

  accounts.csv      account_id, bank, opened_days_ago, is_mule[, name]
  transactions.csv  sender_id, receiver_id, amount, timestamp, channel
  devices.csv       account_id, device_id                       (optional)

  bank             one of the member bank codes (A, B, C)
  is_mule          1 for accounts confirmed as mules by investigation, else 0
  receiver_id      empty for a cash withdrawal
  timestamp        ISO 8601 (2026-10-14T09:31:00) or minutes as a number
  channel          UPI, NEFT, IMPS or ATM

Only the most recent 30 days of transactions are used, to match the features.

    python -m app.dataio export sample_data     write the synthetic training set in this format
    python -m app.dataio check  some_folder     validate a folder and print what it contains
"""
from __future__ import annotations

import csv
import hashlib
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config
from .synth import CH_ATM, CH_NEFT, CH_UPI, CHANNELS, DAY, World

CHANNEL_CODE = {"UPI": CH_UPI, "IMPS": CH_UPI, "NEFT": CH_NEFT, "RTGS": CH_NEFT, "ATM": CH_ATM}
WINDOW_DAYS = config.N_DAYS


class DataError(ValueError):
    """The supplied files do not match the expected format."""


def _rows(path: Path, required: list[str]) -> list[dict]:
    if not path.exists():
        raise DataError(f"{path.name} is missing")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise DataError(f"{path.name} is missing column(s): {', '.join(missing)}")
        return list(reader)


def _minutes(value: str, line: int) -> float:
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() / 60.0
    except ValueError:
        raise DataError(f"transactions.csv line {line}: cannot read timestamp {value!r}") from None


def fingerprint(folder: str | Path) -> str:
    """Short hash of the data files, recorded with the model so a result can be traced to its data."""
    h = hashlib.sha256()
    for name in ("accounts.csv", "transactions.csv", "devices.csv"):
        p = Path(folder) / name
        if p.exists():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def load_world(folder: str | Path) -> World:
    folder = Path(folder)
    accounts = _rows(folder / "accounts.csv", ["account_id", "bank", "opened_days_ago", "is_mule"])
    if len(accounts) < 200:
        raise DataError(f"accounts.csv has {len(accounts)} rows; at least 200 are needed to train")
    ids = [a["account_id"].strip() for a in accounts]
    index = {a: i for i, a in enumerate(ids)}
    if len(index) != len(ids):
        raise DataError("accounts.csv contains duplicate account_id values")
    try:
        bank = np.array([config.BANK_CODES.index(a["bank"].strip().upper()) for a in accounts], dtype=np.int64)
    except ValueError:
        raise DataError(f"accounts.csv: bank must be one of {config.BANK_CODES}") from None
    try:
        age = np.array([max(float(a["opened_days_ago"]), 1.0) for a in accounts])
    except ValueError:
        raise DataError("accounts.csv: opened_days_ago must be a number") from None
    is_mule = np.array([a["is_mule"].strip() in ("1", "true", "True", "TRUE", "yes") for a in accounts])
    if not is_mule.any() or is_mule.all():
        raise DataError("accounts.csv needs both confirmed mules (is_mule=1) and other accounts (is_mule=0)")

    tx = _rows(folder / "transactions.csv", ["sender_id", "receiver_id", "amount", "timestamp", "channel"])
    src, dst, amt, ts, ch = [], [], [], [], []
    unknown = 0
    for line, t in enumerate(tx, start=2):
        s, r = t["sender_id"].strip(), t["receiver_id"].strip()
        code = CHANNEL_CODE.get(t["channel"].strip().upper())
        if code is None:
            raise DataError(f"transactions.csv line {line}: unknown channel {t['channel']!r}")
        if s not in index or (r and r not in index):
            unknown += 1
            continue
        try:
            a = float(t["amount"])
        except ValueError:
            raise DataError(f"transactions.csv line {line}: amount must be a number") from None
        if a <= 0 or (r and s == r):
            continue
        src.append(index[s])
        dst.append(index[r] if r else -1)
        amt.append(a)
        ts.append(_minutes(t["timestamp"], line))
        ch.append(CH_ATM if not r else code)
    if unknown > 0.2 * max(len(tx), 1):
        raise DataError(f"{unknown} of {len(tx)} transactions refer to accounts that are not in accounts.csv")
    if len(src) < 1000:
        raise DataError(f"only {len(src)} usable transactions; at least 1,000 are needed to train")
    ts_arr = np.array(ts)
    end = ts_arr.max() + 1.0
    rel = ts_arr - (end - WINDOW_DAYS * DAY)       # the last 30 days become minutes 0..43,200
    keep = rel >= 0
    order = np.argsort(rel[keep], kind="stable")
    pick = lambda values, dtype: np.array(values, dtype=dtype)[keep][order]

    dev_acct, dev_id = np.arange(len(ids)), np.arange(len(ids))   # by default every account has its own device
    if (folder / "devices.csv").exists():
        rows = _rows(folder / "devices.csv", ["account_id", "device_id"])
        codes: dict[str, int] = {}
        pairs = sorted({(index[r["account_id"].strip()], codes.setdefault(r["device_id"].strip(), len(codes)))
                        for r in rows if r["account_id"].strip() in index})
        if pairs:
            dev_acct, dev_id = (np.array(x, dtype=np.int64) for x in zip(*pairs))

    names = np.array([(a.get("name") or f"Customer {i + 1}").strip() for i, a in enumerate(accounts)])
    return World(
        seed=0, n_days=WINDOW_DAYS, n_banks=len(config.BANKS), bank=bank,
        kind=np.array(["individual"] * len(ids)), is_mule=is_mule, role=np.array([""] * len(ids)),
        ring=np.full(len(ids), -1, dtype=np.int64), age_days=age, name=names, acct_no=np.array(ids),
        phone=np.array([""] * len(ids)),
        src=pick(src, np.int64), dst=pick(dst, np.int64), amt=pick(amt, float), ts=rel[keep][order],
        ch=pick(ch, np.int8), dev_acct=dev_acct, dev_id=dev_id, next_dev=int(dev_id.max()) + 1, next_ring=0,
    )


def export_world(w: World, folder: str | Path, with_names: bool = False) -> Path:
    """Write a world in the import format. Used to produce a template and to test the importer."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "accounts.csv").open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["account_id", "bank", "opened_days_ago", "is_mule"] + (["name"] if with_names else []))
        for i in range(w.n):
            row = [w.acct_no[i], config.BANK_CODES[w.bank[i]], f"{w.age_days[i]:.1f}", int(w.is_mule[i])]
            out.writerow(row + ([w.name[i]] if with_names else []))
    with (folder / "transactions.csv").open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["sender_id", "receiver_id", "amount", "timestamp", "channel"])
        for s, d, a, t, c in zip(w.src.tolist(), w.dst.tolist(), w.amt.tolist(), w.ts.tolist(), w.ch.tolist()):
            out.writerow([w.acct_no[s], w.acct_no[d] if d >= 0 else "", f"{a:.0f}", f"{t:.3f}", CHANNELS[c]])
    with (folder / "devices.csv").open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["account_id", "device_id"])
        for a, d in zip(w.dev_acct.tolist(), w.dev_id.tolist()):
            out.writerow([w.acct_no[a], f"device-{d}"])
    return folder


def _main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "export":
        from .synth import generate_world
        w = generate_world(config.SEED + 1000, int(config.ACCOUNTS_PER_BANK * 5 / 3), 20)
        print(f"wrote {export_world(w, argv[1])}: {w.n} accounts, {len(w.src)} transactions (synthetic)")
        return 0
    if len(argv) == 2 and argv[0] == "check":
        try:
            w = load_world(argv[1])
        except DataError as err:
            print(f"not usable: {err}")
            return 1
        print(f"ok: {w.n} accounts ({int(w.is_mule.sum())} confirmed mules), {len(w.src)} transactions in the "
              f"last {WINDOW_DAYS} days, data fingerprint {fingerprint(argv[1])}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
