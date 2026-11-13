"""Keyed tokenisation and masking.

Member banks turn identifiers into HMAC-SHA256 tokens with a shared secret, so
the same account or device gets the same token at every bank. Without the key
a token cannot be recomputed from an account number, which is why plain or
salted hashes are not used: account and phone numbers are short enough to
brute-force.

Tokens are pseudonymous, not anonymous: they hide who an account belongs to,
not how it behaves or whom it pays.
"""
import hashlib
import hmac


def _mac(key: bytes, value: str, n: int) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:n]


def account_token(key: bytes, ifsc_prefix: str, acct_no: str) -> str:
    return "tok_" + _mac(key, f"acct|{ifsc_prefix}|{acct_no}", 16)


def device_token(key: bytes, device_id: int) -> str:
    return "dev_" + _mac(key, f"device|{device_id}", 12)


def name_token(key: bytes, name: str) -> str:
    """Token of a normalised name, so two parties can test for a match without revealing it."""
    return _mac(key, "name|" + "".join(ch for ch in name.lower() if ch.isalnum()), 16)


def mask_account(acct_no: str) -> str:
    return "XXXX XXXX " + acct_no[-4:]


def vpa(name: str, acct_no: str, handle: str) -> str:
    """The account's UPI ID (virtual payment address)."""
    user = "".join(ch for ch in name.split()[0].lower() if ch.isalnum())
    return f"{user}{acct_no[-6:]}@{handle}"


def mask_vpa(name: str, acct_no: str, handle: str) -> str:
    user, h = vpa(name, acct_no, handle).split("@")
    return f"{user[:2]}***{user[-2:]}@{h}"
