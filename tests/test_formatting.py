from jellyfin_monitor import format_bytes, format_seconds, ticks_to_str, pct_color


def test_format_bytes_units():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1024 ** 3) == "1.0 GB"


def test_format_seconds_none_and_negative():
    assert format_seconds(None) == "--"
    assert format_seconds(-1) == "--"


def test_format_seconds_values():
    assert format_seconds(0) == "0:00"
    assert format_seconds(90) == "1:30"
    assert format_seconds(3661) == "1:01:01"
    assert format_seconds(86400) == "1d 00h"


def test_ticks_none():
    assert ticks_to_str(None) == "0:00"
    assert ticks_to_str(0) == "0:00"
    assert ticks_to_str(10_000_000 * 90) == "1:30"


def test_pct_color_thresholds():
    assert pct_color(0) == "ok"
    assert pct_color(69.9) == "ok"
    assert pct_color(70) == "warn"
    assert pct_color(89.9) == "warn"
    assert pct_color(90) == "danger"
    assert pct_color(100) == "danger"


def test_pct_color_custom_thresholds():
    assert pct_color(79, warn=80, danger=92) == "ok"
    assert pct_color(80, warn=80, danger=92) == "warn"
    assert pct_color(92, warn=80, danger=92) == "danger"
