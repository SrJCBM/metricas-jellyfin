import jellyfin_monitor
from unittest.mock import patch
from jellyfin_monitor import MetricsCollector


def make_collector():
    with patch("jellyfin_monitor.find_jellyfin_process", return_value=None):
        return MetricsCollector()


def test_cpu_alert_triggers_at_90_pct():
    c = make_collector()
    system = {"cpu": {"percent": 90, "label": "90%"},
              "ram": {"percent": 0, "label": "0%"},
              "disk": {"percent": 0, "label": "0 GB"}}
    alerts = c._alerts(system, {"running": True}, {"available": False}, {"online": True})
    assert any("CPU" in a for a in alerts)


def test_ram_alert_triggers_at_90_pct():
    c = make_collector()
    system = {"cpu": {"percent": 0, "label": "0%"},
              "ram": {"percent": 90, "label": "14.4 GB / 16 GB"},
              "disk": {"percent": 0, "label": "0 GB"}}
    alerts = c._alerts(system, {"running": True}, {"available": False}, {"online": True})
    assert any("RAM" in a for a in alerts)


def test_disk_alert_triggers_at_92_pct():
    c = make_collector()
    system = {"cpu": {"percent": 0}, "ram": {"percent": 0},
              "disk": {"percent": 93, "label": "930 GB / 1.0 TB"}}
    alerts = c._alerts(system, {"running": True}, {"available": False}, {"online": True})
    assert any("Disco" in a for a in alerts)


def test_gpu_alert_triggers_at_84_c():
    c = make_collector()
    system = {"cpu": {"percent": 0}, "ram": {"percent": 0},
              "disk": {"percent": 0, "label": "0 GB"}}
    gpu = {"available": True, "temp": {"value": 84, "label": "84 °C"}}
    alerts = c._alerts(system, {"running": True}, gpu, {"online": True})
    assert any("GPU" in a for a in alerts)


def test_no_false_alerts_at_normal_load():
    c = make_collector()
    system = {"cpu": {"percent": 40}, "ram": {"percent": 50},
              "disk": {"percent": 60, "label": "600 GB / 1.0 TB"}}
    alerts = c._alerts(system, {"running": True}, {"available": False}, {"online": True})
    assert alerts == []


def test_alert_when_jellyfin_offline():
    c = make_collector()
    system = {"cpu": {"percent": 0}, "ram": {"percent": 0},
              "disk": {"percent": 0, "label": "0 GB"}}
    alerts = c._alerts(system, {"running": True}, {"available": False}, {"online": False})
    assert any("Jellyfin" in a for a in alerts)
