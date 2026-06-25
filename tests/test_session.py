import jellyfin_monitor
from unittest.mock import patch
from jellyfin_monitor import MetricsCollector

# ── Fixtures ──────────────────────────────────────────────────────────────────

RAW_SESSION = {
    "UserName": "jcbla",
    "Client": "Jellyfin Web",
    "DeviceName": "Chrome",
    "PlayState": {"PositionTicks": 5_400_000_000, "IsPaused": False},
    "NowPlayingItem": {
        "Id": "abc123",
        "Name": "Beater",
        "SeriesName": "Sword Art Online",
        "ParentIndexNumber": 1,
        "IndexNumber": 3,
        "RunTimeTicks": 84_780_000_000,
        "MediaStreams": [
            {"Type": "Video", "Codec": "h264", "Width": 1920, "Height": 1080, "BitRate": 12_000_000},
            {"Type": "Audio", "Codec": "flac", "Channels": 2, "Language": "spa"},
        ],
    },
}


def make_collector():
    with patch("jellyfin_monitor.find_jellyfin_process", return_value=None):
        return MetricsCollector()


def make_raw(user, item_id, title, device="Chrome", series=None):
    """Construye un dict de sesión cruda mínimo para tests de detección de cambios."""
    item = {
        "Id": item_id,
        "Name": title,
        "Type": "Episode" if series else "Movie",
        "RunTimeTicks": 100_000_000_000,
        "MediaStreams": [],
    }
    if series:
        item["SeriesName"] = series
        item["ParentIndexNumber"] = 1
        item["IndexNumber"] = 1
    return {
        "UserName": user,
        "Client": "Jellyfin Web",
        "DeviceName": device,
        "PlayState": {"PositionTicks": 10_000_000_000, "IsPaused": False},
        "NowPlayingItem": item,
    }


def poll(collector, sessions):
    """Ejecuta _jellyfin_metrics con una lista de sesiones controlada."""
    with patch("jellyfin_monitor.API_KEY", "test-key"), \
         patch.object(collector, "_get_json", side_effect=lambda ep: {} if "Info" in ep else sessions):
        collector._jellyfin_metrics()


def event_messages(collector):
    return [ev["message"] for ev in collector.events if ev["source"] == "Sesión"]


# ── _session_payload ───────────────────────────────────────────────────────────

def test_session_payload_direct_play():
    c = make_collector()
    result = c._session_payload(RAW_SESSION)
    assert result["user"] == "jcbla"
    assert result["title"] == "Beater"
    assert result["subtitle"] == "Sword Art Online - S01E03"
    assert result["method"] == "Direct Play"
    assert result["isTranscoding"] is False
    assert result["imageUrl"] == "/api/image/abc123"
    assert 0 < result["progress"] < 100


def test_session_payload_transcoding():
    session = {**RAW_SESSION, "TranscodingInfo": {
        "VideoCodec": "h264", "AudioCodec": "aac",
        "HardwareAccelerationType": "nvenc", "Bitrate": 8_000_000,
        "Framerate": 24.0, "TranscodeReasons": ["ContainerNotSupported"],
    }}
    result = make_collector()._session_payload(session)
    assert result["method"] == "Transcoding"
    assert result["isTranscoding"] is True
    assert "contenedor no soportado" in result["detail"]


def test_session_payload_movie():
    raw = {
        "UserName": "ana",
        "Client": "Infuse",
        "DeviceName": "Apple TV",
        "PlayState": {"PositionTicks": 36_000_000_000, "IsPaused": False},
        "NowPlayingItem": {
            "Id": "movie42",
            "Name": "Inception",
            "Type": "Movie",
            "RunTimeTicks": 85_800_000_000,
            "MediaStreams": [
                {"Type": "Video", "Codec": "h265", "Width": 3840, "Height": 2160, "BitRate": 40_000_000},
                {"Type": "Audio", "Codec": "dts", "Channels": 6, "Language": "eng"},
            ],
        },
    }
    result = make_collector()._session_payload(raw)
    assert result["title"] == "Inception"
    assert result["subtitle"] == "Movie"
    assert result["imageUrl"] == "/api/image/movie42"
    assert result["isTranscoding"] is False
    assert 0 < result["progress"] < 100


# ── Detección de cambios de sesión ────────────────────────────────────────────

def test_session_start_is_logged():
    c = make_collector()
    poll(c, [make_raw("ana", "i1", "Inception")])
    assert any("ana" in m and "inició" in m for m in event_messages(c))


def test_session_end_is_logged():
    c = make_collector()
    poll(c, [make_raw("ana", "i1", "Inception")])
    poll(c, [])
    assert any("ana" in m and "detuvo" in m for m in event_messages(c))


def test_only_stopped_user_is_logged():
    """Ana para, Bob sigue — solo ana debe aparecer en eventos de 'detuvo'."""
    c = make_collector()
    poll(c, [make_raw("ana", "i1", "Inception"), make_raw("bob", "i2", "Matrix")])
    poll(c, [make_raw("bob", "i2", "Matrix")])
    msgs = event_messages(c)
    assert any("ana" in m and "detuvo" in m for m in msgs)
    assert not any("bob" in m and "detuvo" in m for m in msgs)


def test_two_users_start_simultaneously():
    """Dos usuarios inician en el mismo poll — se registran dos eventos de inicio."""
    c = make_collector()
    poll(c, [make_raw("ana", "i1", "Inception"), make_raw("bob", "i2", "Matrix")])
    msgs = event_messages(c)
    assert any("ana" in m and "inició" in m for m in msgs)
    assert any("bob" in m and "inició" in m for m in msgs)


def test_three_concurrent_sessions():
    """Tres usuarios simultáneos: conteos correctos y tres eventos de inicio."""
    c = make_collector()
    sessions = [
        make_raw("ana",    "i1", "Inception",    device="Chrome"),
        make_raw("bob",    "i2", "Matrix",        device="TV"),
        make_raw("carlos", "i3", "Interstellar",  device="Kodi"),
    ]
    with patch("jellyfin_monitor.API_KEY", "test-key"), \
         patch.object(c, "_get_json", side_effect=lambda ep: {} if "Info" in ep else sessions):
        result = c._jellyfin_metrics()

    assert result["counts"]["active"] == 3
    assert result["counts"]["transcoding"] == 0
    assert result["counts"]["direct"] == 3
    msgs = event_messages(c)
    assert any("ana"    in m and "inició" in m for m in msgs)
    assert any("bob"    in m and "inició" in m for m in msgs)
    assert any("carlos" in m and "inició" in m for m in msgs)


def test_three_sessions_one_stops():
    """Con tres usuarios, cuando uno para solo él aparece en eventos de 'detuvo'."""
    c = make_collector()
    s_ana    = make_raw("ana",    "i1", "Inception")
    s_bob    = make_raw("bob",    "i2", "Matrix")
    s_carlos = make_raw("carlos", "i3", "Interstellar")
    poll(c, [s_ana, s_bob, s_carlos])
    poll(c, [s_bob, s_carlos])
    msgs = event_messages(c)
    assert any("ana"    in m and "detuvo" in m for m in msgs)
    assert not any("bob"    in m and "detuvo" in m for m in msgs)
    assert not any("carlos" in m and "detuvo" in m for m in msgs)


def test_same_user_same_item_two_devices_two_events():
    """
    El mismo usuario reproduce el mismo ítem en dos dispositivos distintos.
    La clave user|itemId|device es única por dispositivo,
    por lo que se generan dos eventos de inicio independientes.
    """
    c = make_collector()
    poll(c, [
        make_raw("ana", "i1", "Inception", device="Chrome"),
        make_raw("ana", "i1", "Inception", device="TV"),
    ])
    inicio_ana = [m for m in event_messages(c) if "ana" in m and "inició" in m]
    assert len(inicio_ana) == 2
