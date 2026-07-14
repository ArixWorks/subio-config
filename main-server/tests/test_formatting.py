from app.formatting import format_bytes, format_expiry, format_volume_pair


def test_format_bytes_gb() -> None:
    assert format_bytes(1_073_741_824) == "1 GB"
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536) == "1.5 KB"


def test_format_volume_pair() -> None:
    assert format_volume_pair(0, 1_073_741_824) == "0 B / 1 GB"


def test_format_expiry_strips_micros() -> None:
    text = format_expiry("2026-07-15 02:15:32.447733+00:00")
    assert "447733" not in text
    assert "2026-07-15" in text
