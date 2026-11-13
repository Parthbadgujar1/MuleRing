"""Keyed tokenisation and masking.

Member banks turn identifiers into HMAC-SHA256 tokens with a shared secret, so
the same account or device gets the same token at every bank. Without the key
a token cannot be recomputed from an account number, which is why plain or
salted hashes are not used: account and phone numbers are short enough to
brute-force.
"""
import hashlib
import hmac


def _mac(key: bytes, value: str, n: int) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:n]


def account_token(key: bytes, ifsc_prefix: str, acct_no: str) -> str:
    return "tok_" + _mac(key, f"acct|{ifsc_prefix}|{acct_no}", 16)


def device_token(key: bytes, device_id: int) -> str:
    return "dev_" + _mac(key, f"device|{device_id}", 12)


def mask_account(acct_no: str) -> str:
    return "XXXX XXXX " + acct_no[-4:]


def mask_vpa(name: str, acct_no: str, handle: str) -> str:
    return f"{name.split()[0].lower()[:2]}***{acct_no[-2:]}@{handle}"
