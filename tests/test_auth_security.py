"""Unit coverage for password hashing, token comparison and login throttling."""

import pytest

from security.auth import (
    LoginThrottle,
    csrf_matches,
    csrf_token_for,
    hash_password,
    hash_session_token,
    needs_rehash,
    new_session_token,
    normalize_username,
    validate_password,
    verify_password,
)


def test_password_hashes_are_argon2id_salted_and_verify():
    first = hash_password("Correct-Horse-Battery-Staple-9")
    second = hash_password("Correct-Horse-Battery-Staple-9")
    assert first != second
    assert first.startswith("$argon2id$")
    assert verify_password("Correct-Horse-Battery-Staple-9", first)
    assert not verify_password("wrong password", first)
    assert not verify_password("Correct-Horse-Battery-Staple-9", "not-a-hash")
    assert not needs_rehash(first)
    # Hashes from the old scrypt build are upgraded on the next sign-in.
    assert needs_rehash("scrypt$32768$8$1$abc$def")


def test_username_and_password_policy():
    assert normalize_username("  Owner_One ") == "owner_one"
    for bad in ("ab", "has space", "../etc", "A" * 40, "name!"):
        with pytest.raises(ValueError):
            normalize_username(bad)

    validate_password("Correct-Horse-Battery-Staple-9", "owner")
    for bad in ("short", "x" * 129, "            ", "owner-is-my-password"):
        with pytest.raises(ValueError):
            validate_password(bad, "owner")
    with pytest.raises(ValueError):
        validate_password("password1234")


def test_session_tokens_and_csrf_are_unpredictable_and_bound():
    token = new_session_token()
    other = new_session_token()
    assert token != other
    assert len(hash_session_token(token)) == 64
    assert hash_session_token(token) != token
    csrf = csrf_token_for(token)
    assert csrf_matches(token, csrf)
    assert not csrf_matches(token, csrf_token_for(other))
    assert not csrf_matches(token, "")


def test_login_throttle_locks_then_resets():
    throttle = LoginThrottle(limit=2, window=60, base_lock=2, max_lock=2)
    assert throttle.locked_for("ip:1") == 0
    throttle.fail("ip:1")
    assert throttle.locked_for("ip:1") == 0
    assert throttle.fail("ip:1") == 2
    assert throttle.locked_for("ip:1") >= 1
    throttle.reset("ip:1")
    assert throttle.locked_for("ip:1") == 0
