"""Sign-up and sign-in limits (spec 2026-11-03 batch 3 §9).

Counts live in ``<SYSTEM_ROOT>/rate_limits.sqlite3`` as fixed windows per
bucket. Addresses are stored only as hashes. Nothing is created until the
first counted request; an unreadable database never blocks a sign-in (the
limits are a brake, not a gate).
"""

from __future__ import annotations

import hashlib
import time

from deeptutor.multi_user.system_db import SystemDb

db = SystemDb(
    "rate_limits.sqlite3",
    """\
CREATE TABLE IF NOT EXISTS rate_buckets (
    bucket TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL
);
""",
    "Rate limit",
)

def _setting(name: str, default: int) -> int:
    import os

    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


#: (count, window seconds). Generous by default: on a campus or education network hundreds
#: of students reach the site from one address and may all sign up in the same hour.
#: Each can be changed in the deployment's environment.
REGISTER_PER_ADDRESS = (_setting("DEEPTUTOR_REGISTER_PER_ADDRESS_HOURLY", 300), 3600)
#: Across the whole site, for when the address is unknown or spoofed.
REGISTER_SITE_WIDE = (_setting("DEEPTUTOR_REGISTER_SITE_HOURLY", 2000), 3600)
#: Guessing one account's password: not tied to an address, so not loosened.
LOGIN_FAILURES_PER_USERNAME = (_setting("DEEPTUTOR_LOGIN_FAILURES_PER_ACCOUNT", 10), 900)
LOGIN_FAILURES_PER_ADDRESS = (_setting("DEEPTUTOR_LOGIN_FAILURES_PER_ADDRESS", 300), 900)


class RateLimited(Exception):
    pass


def _now() -> int:
    return int(time.time())


def address_key(address: str) -> str:
    return hashlib.sha256(f"deeptutor-rate:{address}".encode()).hexdigest()[:32]


def _window(limit: tuple[int, int], now: int) -> int:
    return now - (now % limit[1])


def count(bucket: str, limit: tuple[int, int]) -> int:
    """The current window's count, without counting."""
    now = _now()
    try:
        with db.read() as conn:
            if conn is None:
                return 0
            row = conn.execute("SELECT window_start, count FROM rate_buckets WHERE bucket = ?", (bucket,)).fetchone()
    except Exception:
        return 0
    if row is None or row["window_start"] != _window(limit, now):
        return 0
    return int(row["count"])


def hit(bucket: str, limit: tuple[int, int]) -> int:
    """Count one in the bucket's current window and return the new count."""
    now = _now()
    start = _window(limit, now)
    with db.write() as conn:
        conn.execute("DELETE FROM rate_buckets WHERE window_start < ?", (now - 86400,))
        conn.execute(
            "INSERT INTO rate_buckets (bucket, window_start, count) VALUES (?, ?, 1) "
            "ON CONFLICT(bucket) DO UPDATE SET "
            "count = CASE WHEN rate_buckets.window_start = excluded.window_start THEN rate_buckets.count + 1 ELSE 1 END, "
            "window_start = excluded.window_start",
            (bucket, start),
        )
        row = conn.execute("SELECT count FROM rate_buckets WHERE bucket = ?", (bucket,)).fetchone()
    return int(row["count"])


def clear(bucket: str) -> None:
    if not db.path().exists():
        return
    with db.write() as conn:
        conn.execute("DELETE FROM rate_buckets WHERE bucket = ?", (bucket,))


# ── sign-up ────────────────────────────────────────────────


def check_registration(address: str) -> None:
    """Refuse a sign-up once the address (when known) or the site made too many accounts this hour."""
    if (address and count(f"register:{address_key(address)}", REGISTER_PER_ADDRESS) >= REGISTER_PER_ADDRESS[0]) or (
        count("register:site", REGISTER_SITE_WIDE) >= REGISTER_SITE_WIDE[0]
    ):
        raise RateLimited("Too many sign-ups from this network. Try again later.")


def registered(address: str) -> None:
    """Count an account that was made; failed attempts are not counted."""
    try:
        if address:
            hit(f"register:{address_key(address)}", REGISTER_PER_ADDRESS)
        hit("register:site", REGISTER_SITE_WIDE)
    except Exception:
        return


# ── sign-in ────────────────────────────────────────────────


def _login_buckets(username: str, address: str) -> tuple[str, str]:
    return f"login:user:{username.strip().casefold()}", f"login:addr:{address_key(address)}"


def check_login(username: str, address: str) -> None:
    """Refuse while the username or the address has failed too often (no hint which)."""
    user_bucket, address_bucket = _login_buckets(username, address)
    if count(user_bucket, LOGIN_FAILURES_PER_USERNAME) >= LOGIN_FAILURES_PER_USERNAME[0] or (
        address and count(address_bucket, LOGIN_FAILURES_PER_ADDRESS) >= LOGIN_FAILURES_PER_ADDRESS[0]
    ):
        raise RateLimited("Too many attempts. Try again in 15 minutes.")


def login_failed(username: str, address: str) -> None:
    user_bucket, address_bucket = _login_buckets(username, address)
    try:
        hit(user_bucket, LOGIN_FAILURES_PER_USERNAME)
        if address:  # an unknown address is not one shared bucket for everyone
            hit(address_bucket, LOGIN_FAILURES_PER_ADDRESS)
    except Exception:
        return


def login_succeeded(username: str) -> None:
    try:
        clear(_login_buckets(username, "")[0])
    except Exception:
        return


__all__ = [
    "RateLimited", "check_login", "check_registration", "login_failed", "login_succeeded", "registered",
]
