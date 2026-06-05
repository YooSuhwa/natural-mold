from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agent_api.security import generate_api_key, parse_api_key, verify_secret


def test_api_key_round_trip_verification():
    generated = generate_api_key()

    assert generated.cleartext.startswith("moldy_sk_")
    assert generated.prefix.startswith("moldy_sk_")
    assert generated.last_four == generated.cleartext[-4:]

    parsed = parse_api_key(generated.cleartext)

    assert parsed == (generated.key_id, generated.secret)
    assert verify_secret(generated.key_id, generated.secret, generated.secret_hash)
    assert not verify_secret(generated.key_id, "wrong-secret", generated.secret_hash)


def test_parse_api_key_rejects_unknown_prefix():
    generated = generate_api_key()
    bad = generated.cleartext.replace("moldy_sk_", "other_sk_", 1)

    assert parse_api_key(bad) is None


def test_expiration_timestamp_is_naive_utc():
    from app.agent_api.service import expires_at_from_days

    expires_at = expires_at_from_days(7)

    assert expires_at is not None
    assert expires_at.tzinfo is None
    delta = expires_at - datetime.now(UTC).replace(tzinfo=None)
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, minutes=1)


def test_expiration_none_means_no_expiry():
    from app.agent_api.service import expires_at_from_days

    assert expires_at_from_days(None) is None
