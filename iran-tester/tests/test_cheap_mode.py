from app.xray import compute_health_score


def test_cheap_mode_score_floor() -> None:
    result = {
        "reachable": True,
        "latency_ms": 120,
        "checks": {"http": True, "cloudflare": True},
        "mode": "cheap",
    }
    score = compute_health_score(result)
    assert score >= 55


def test_unreachable_zero() -> None:
    assert compute_health_score({"reachable": False, "mode": "cheap"}) == 0.0
