from app.sub_store import SubscriptionStore


def test_public_feed_without_expiry_is_available(tmp_path) -> None:
    store = SubscriptionStore(str(tmp_path))
    store.upsert("public-token", ["vless://example"], expires_at=None)

    assert store.get_body("public-token") == "vless://example"


def test_expired_feed_is_removed(tmp_path) -> None:
    store = SubscriptionStore(str(tmp_path))
    store.upsert(
        "expired-token",
        ["vless://example"],
        expires_at="2020-01-01T00:00:00+00:00",
    )

    assert store.get_body("expired-token") is None
    assert not (tmp_path / "expired-token.json").exists()


def test_empty_feed_is_removed(tmp_path) -> None:
    store = SubscriptionStore(str(tmp_path))
    store.upsert("empty-token", [], expires_at=None)

    assert store.get_body("empty-token") is None
    assert not (tmp_path / "empty-token.json").exists()
