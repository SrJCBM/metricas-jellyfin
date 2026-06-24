from unittest.mock import MagicMock, patch
from jellyfin_monitor import MetricsCollector

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