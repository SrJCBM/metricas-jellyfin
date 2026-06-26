"""
Jellyfin Monitor - dashboard web local.

Ejecuta:
  python jellyfin_monitor.py

Configuracion:
  Edita el archivo .env junto a este script.
"""

from __future__ import annotations

import csv
import io
import json
import os
import socket
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import psutil
import requests

try:
    import pynvml

    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_NAME = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
    if isinstance(_GPU_NAME, bytes):
        _GPU_NAME = _GPU_NAME.decode("utf-8", errors="replace")
    GPU_OK = True
except Exception:
    pynvml = None
    _GPU_HANDLE = None
    _GPU_NAME = "No disponible"
    GPU_OK = False


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
HISTORY_POINTS = 90
EXPORT_MAXLEN = 1800  # ~60 min a 2 s por poll

CSV_COLUMNS = [
    "hora",
    "cpu_pct", "ram_pct", "disco_pct",
    "disco_lectura_mbs", "disco_escritura_mbs",
    "red_bajada_mbs", "red_subida_mbs",
    "gpu_pct", "gpu_temp_c",
    "jf_cpu_pct", "jf_ram_mb", "jf_disco_lectura_mbs",
    "stream_mbps", "sesiones", "transcoding",
]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    value = os.getenv(name, "").strip()
    try:
        parsed = int(value) if value else default
    except ValueError:
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


load_env_file(BASE_DIR / ".env")

JF_URL = os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip("/")
API_KEY = os.getenv("JELLYFIN_API_KEY", "").strip()
REFRESH_MS = env_int("JELLYFIN_REFRESH_MS", 2000, minimum=1000)
MEDIA_PATH = Path(os.getenv("JELLYFIN_MEDIA_PATH", r"E:\Multimedia"))
SERVER_HOST = os.getenv("JELLYFIN_MONITOR_HOST", "127.0.0.1")
SERVER_PORT = env_int("JELLYFIN_MONITOR_PORT", 8765, minimum=1024)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def pct_color(value: float, warn: float = 70.0, danger: float = 90.0) -> str:
    if value >= danger:
        return "danger"
    if value >= warn:
        return "warn"
    return "ok"


def format_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size:.0f} B"
        size /= 1024
    return f"{size:.1f} TB"


def format_rate(bytes_per_second: float) -> str:
    return f"{format_bytes(bytes_per_second)}/s"


def format_seconds(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "--"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def ticks_to_str(ticks: int | None) -> str:
    if not ticks:
        return "0:00"
    return format_seconds(ticks // 10_000_000)


def now_label() -> str:
    return datetime.now().strftime("%H:%M:%S")


def find_jellyfin_process() -> psutil.Process | None:
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        if name in {"jellyfin.exe", "jellyfin"}:
            return proc
    return None


def safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


TRANSCODE_REASON_LABELS = {
    "ContainerNotSupported": "contenedor no soportado",
    "VideoCodecNotSupported": "video no soportado",
    "AudioCodecNotSupported": "audio no soportado",
    "SubtitleCodecNotSupported": "subtitulos no soportados",
    "DirectPlayError": "direct play fallo",
    "ContainerBitrateExceedsLimit": "bitrate del contenedor excede el limite",
    "VideoBitrateNotSupported": "bitrate de video no soportado",
    "AudioBitrateNotSupported": "bitrate de audio no soportado",
    "VideoResolutionNotSupported": "resolucion no soportada",
    "AudioChannelsNotSupported": "canales de audio no soportados",
}


def reason_label(reason: Any) -> str:
    text = str(reason)
    return TRANSCODE_REASON_LABELS.get(text, text)


class MetricsCollector:
    def __init__(self) -> None:
        self.http = requests.Session()
        if API_KEY:
            self.http.headers.update({"X-Emby-Token": API_KEY})

        self.lock = threading.Lock()
        self.events: deque[dict[str, str]] = deque(maxlen=50)
        self.export_log: deque[dict[str, Any]] = deque(maxlen=EXPORT_MAXLEN)
        self.last_error: dict[str, float] = {}
        self.last_success: str | None = None
        self._prev_session_keys: dict[str, str] = {}
        self.server_info: dict[str, Any] = {}
        self.jf_proc = find_jellyfin_process()
        self.prev_time = time.perf_counter()
        self.prev_disk = psutil.disk_io_counters()
        self.prev_net = psutil.net_io_counters()
        self.prev_proc_io: Any = None
        self.prev_proc_pid: int | None = None
        self.sample_elapsed = REFRESH_MS / 1000
        self.history: dict[str, deque[float]] = {
            "cpu": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "ram": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "diskRead": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "diskWrite": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "netDown": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "netUp": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "gpu": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "jfCpu": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "jfRamMb": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "jfDiskRead": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "jfDiskWrite": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
            "streamMbps": deque([0.0] * HISTORY_POINTS, maxlen=HISTORY_POINTS),
        }

    def collect(self) -> dict[str, Any]:
        with self.lock:
            started = time.perf_counter()
            system = self._system_metrics()
            process = self._process_metrics()
            gpu = self._gpu_metrics()
            jellyfin = self._jellyfin_metrics()
            latency_ms = (time.perf_counter() - started) * 1000

            alerts = self._alerts(system, process, gpu, jellyfin)
            cards = self._summary_cards(system, process, gpu, jellyfin, latency_ms)

            self.export_log.append({
                "ts": time.time(),
                "hora": now_label(),
                "cpu_pct": round(system["cpu"]["percent"], 1),
                "ram_pct": round(system["ram"]["percent"], 1),
                "disco_pct": round(system["disk"]["percent"], 1),
                "disco_lectura_mbs": round(system["diskRead"]["bytes"] / 1024 ** 2, 3),
                "disco_escritura_mbs": round(system["diskWrite"]["bytes"] / 1024 ** 2, 3),
                "red_bajada_mbs": round(system["netDown"]["bytes"] / 1024 ** 2, 3),
                "red_subida_mbs": round(system["netUp"]["bytes"] / 1024 ** 2, 3),
                "gpu_pct": round(gpu["util"]["percent"], 1) if gpu.get("available") else "",
                "gpu_temp_c": gpu["temp"]["value"] if gpu.get("available") else "",
                "jf_cpu_pct": round(process["cpu"]["percent"], 1) if process.get("running") else "",
                "jf_ram_mb": round(process["ram"]["privateBytes"] / 1024 ** 2, 1) if process.get("running") else "",
                "jf_disco_lectura_mbs": round(process["diskRead"]["bytes"] / 1024 ** 2, 3) if process.get("running") else "",
                "stream_mbps": round(jellyfin["traffic"]["mbps"], 2),
                "sesiones": jellyfin["counts"]["active"],
                "transcoding": jellyfin["counts"]["transcoding"],
            })

            return {
                "config": {
                    "url": JF_URL,
                    "apiKeyConfigured": bool(API_KEY),
                    "refreshMs": REFRESH_MS,
                    "mediaPath": str(MEDIA_PATH),
                    "monitorPort": SERVER_PORT,
                },
                "time": now_label(),
                "cards": cards,
                "jellyfin": jellyfin,
                "system": system,
                "process": process,
                "gpu": gpu,
                "history": {key: list(values) for key, values in self.history.items()},
                "alerts": alerts,
                "events": list(self.events),
                "lastSuccess": self.last_success,
                "server": self.server_info,
            }

    def _system_metrics(self) -> dict[str, Any]:
        current_time = time.perf_counter()
        elapsed = max(current_time - self.prev_time, 0.001)
        self.prev_time = current_time
        self.sample_elapsed = elapsed

        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk_root = self._disk_root()
        disk_usage = psutil.disk_usage(str(disk_root))
        disk_now = psutil.disk_io_counters()
        net_now = psutil.net_io_counters()

        disk_read = max(0.0, (disk_now.read_bytes - self.prev_disk.read_bytes) / elapsed)
        disk_write = max(0.0, (disk_now.write_bytes - self.prev_disk.write_bytes) / elapsed)
        net_down = max(0.0, (net_now.bytes_recv - self.prev_net.bytes_recv) / elapsed)
        net_up = max(0.0, (net_now.bytes_sent - self.prev_net.bytes_sent) / elapsed)

        self.prev_disk = disk_now
        self.prev_net = net_now

        self.history["cpu"].append(cpu)
        self.history["ram"].append(ram.percent)
        self.history["diskRead"].append(disk_read / 1024**2)
        self.history["diskWrite"].append(disk_write / 1024**2)
        self.history["netDown"].append(net_down / 1024**2)
        self.history["netUp"].append(net_up / 1024**2)

        return {
            "cpu": {"percent": cpu, "label": f"{cpu:.1f}%", "tone": pct_color(cpu)},
            "ram": {
                "percent": ram.percent,
                "label": f"{format_bytes(ram.used)} / {format_bytes(ram.total)}",
                "tone": pct_color(ram.percent),
            },
            "swap": {
                "percent": swap.percent,
                "label": f"{format_bytes(swap.used)} / {format_bytes(swap.total)}",
                "tone": pct_color(swap.percent),
            },
            "disk": {
                "percent": disk_usage.percent,
                "label": f"{format_bytes(disk_usage.used)} / {format_bytes(disk_usage.total)}",
                "free": format_bytes(disk_usage.free),
                "root": str(disk_root),
                "tone": pct_color(disk_usage.percent, warn=80, danger=92),
            },
            "diskRead": {"bytes": disk_read, "label": format_rate(disk_read)},
            "diskWrite": {"bytes": disk_write, "label": format_rate(disk_write)},
            "netDown": {"bytes": net_down, "label": format_rate(net_down)},
            "netUp": {"bytes": net_up, "label": format_rate(net_up)},
        }

    def _disk_root(self) -> Path:
        if MEDIA_PATH.exists():
            return Path(MEDIA_PATH.anchor or MEDIA_PATH)
        return Path.cwd().anchor and Path(Path.cwd().anchor) or Path.cwd()

    def _process_metrics(self) -> dict[str, Any]:
        if not self.jf_proc:
            self.jf_proc = find_jellyfin_process()
        if not self.jf_proc:
            self.prev_proc_io = None
            self.prev_proc_pid = None
            self.history["jfCpu"].append(0.0)
            self.history["jfRamMb"].append(0.0)
            self.history["jfDiskRead"].append(0.0)
            self.history["jfDiskWrite"].append(0.0)
            return {"running": False, "label": "No corre"}

        try:
            cpu = self.jf_proc.cpu_percent()
            memory = self.jf_proc.memory_info()
            rss = memory.rss
            private = getattr(memory, "private", rss)
            total_ram = psutil.virtual_memory().total
            ram_pct = private / total_ram * 100 if total_ram else 0.0
            handles_fn = getattr(self.jf_proc, "num_handles", None)
            handles = handles_fn() if handles_fn else None
            disk_read = 0.0
            disk_write = 0.0
            try:
                proc_io = self.jf_proc.io_counters()
                if self.prev_proc_io and self.prev_proc_pid == self.jf_proc.pid:
                    disk_read = max(0.0, (proc_io.read_bytes - self.prev_proc_io.read_bytes) / self.sample_elapsed)
                    disk_write = max(0.0, (proc_io.write_bytes - self.prev_proc_io.write_bytes) / self.sample_elapsed)
                self.prev_proc_io = proc_io
                self.prev_proc_pid = self.jf_proc.pid
            except (AttributeError, psutil.AccessDenied):
                self.prev_proc_io = None
                self.prev_proc_pid = self.jf_proc.pid

            self.history["jfCpu"].append(cpu)
            self.history["jfRamMb"].append(private / 1024**2)
            self.history["jfDiskRead"].append(disk_read / 1024**2)
            self.history["jfDiskWrite"].append(disk_write / 1024**2)

            return {
                "running": True,
                "pid": self.jf_proc.pid,
                "cpu": {"percent": cpu, "label": f"{cpu:.1f}%", "tone": pct_color(cpu)},
                "ram": {
                    "percent": ram_pct,
                    "label": f"{format_bytes(private)} priv / {format_bytes(rss)} rss",
                    "privateBytes": private,
                    "rssBytes": rss,
                },
                "diskRead": {"bytes": disk_read, "label": format_rate(disk_read)},
                "diskWrite": {"bytes": disk_write, "label": format_rate(disk_write)},
                "uptime": format_seconds(time.time() - self.jf_proc.create_time()),
                "threads": self.jf_proc.num_threads(),
                "handles": handles,
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
            self.jf_proc = None
            self.prev_proc_io = None
            self.prev_proc_pid = None
            self.history["jfCpu"].append(0.0)
            self.history["jfRamMb"].append(0.0)
            self.history["jfDiskRead"].append(0.0)
            self.history["jfDiskWrite"].append(0.0)
            self._log("Proceso", str(exc))
            return {"running": False, "label": "Sin acceso"}

    def _gpu_metrics(self) -> dict[str, Any]:
        if not GPU_OK or pynvml is None or _GPU_HANDLE is None:
            self.history["gpu"].append(0.0)
            return {"available": False, "name": _GPU_NAME, "label": "N/A"}

        try:
            util = self._nvml_call("nvmlDeviceGetUtilizationRates")
            mem = self._nvml_call("nvmlDeviceGetMemoryInfo")
            temp = self._nvml_call("nvmlDeviceGetTemperature", pynvml.NVML_TEMPERATURE_GPU)

            gpu_pct = float(getattr(util, "gpu", 0.0) or 0.0)
            mem_used = float(getattr(mem, "used", 0.0) or 0.0)
            mem_total = float(getattr(mem, "total", 0.0) or 0.0)
            vram_pct = mem_used / mem_total * 100 if mem_total else 0.0
            encoder = self._nvml_pair("nvmlDeviceGetEncoderUtilization")
            decoder = self._nvml_pair("nvmlDeviceGetDecoderUtilization")
            power = self._nvml_value("nvmlDeviceGetPowerUsage")
            fan = self._nvml_value("nvmlDeviceGetFanSpeed")
            self.history["gpu"].append(gpu_pct)
            return {
                "available": True,
                "name": str(_GPU_NAME),
                "partial": util is None or mem is None or temp is None,
                "util": {"percent": gpu_pct, "label": f"{gpu_pct:.0f}%" if util is not None else "N/A", "tone": pct_color(gpu_pct)},
                "vram": {
                    "percent": vram_pct,
                    "label": f"{format_bytes(mem_used)} / {format_bytes(mem_total)}" if mem is not None else "N/A",
                    "tone": pct_color(vram_pct),
                },
                "temp": {
                    "value": temp if temp is not None else 0,
                    "label": f"{temp} C" if temp is not None else "N/A",
                    "tone": pct_color(float(temp or 0), warn=72, danger=84),
                },
                "encoder": {"percent": encoder, "label": f"{encoder:.0f}%" if encoder is not None else "N/A"},
                "decoder": {"percent": decoder, "label": f"{decoder:.0f}%" if decoder is not None else "N/A"},
                "power": f"{power / 1000:.1f} W" if power is not None else "N/A",
                "fan": f"{fan:.0f}%" if fan is not None else "N/A",
            }
        except Exception as exc:
            self.history["gpu"].append(0.0)
            self._log("GPU", str(exc), throttle_seconds=30)
            return {"available": False, "name": str(_GPU_NAME), "label": "Error GPU"}

    def _nvml_call(self, method_name: str, *args: Any) -> Any:
        method = getattr(pynvml, method_name, None)
        if not method:
            return None
        try:
            return method(_GPU_HANDLE, *args)
        except Exception:
            return None

    def _nvml_pair(self, method_name: str) -> float | None:
        method = getattr(pynvml, method_name, None)
        if not method:
            return None
        try:
            value = method(_GPU_HANDLE)
            return float(value[0] if isinstance(value, tuple) else value)
        except Exception:
            return None

    def _nvml_value(self, method_name: str) -> float | None:
        method = getattr(pynvml, method_name, None)
        if not method:
            return None
        try:
            return float(method(_GPU_HANDLE))
        except Exception:
            return None

    def _jellyfin_metrics(self) -> dict[str, Any]:
        if not API_KEY:
            self.history["streamMbps"].append(0.0)
            return {
                "online": False,
                "status": "Sin API key",
                "sessions": [],
                "counts": {"active": 0, "paused": 0, "direct": 0, "transcoding": 0},
                "traffic": {"kbps": 0, "mbps": 0.0, "label": "0.0 Mbps"},
            }

        errors: list[str] = []
        info: dict[str, Any] = {}
        raw_sessions: list[dict[str, Any]] = []

        try:
            info = self._get_json("/System/Info")
            self.server_info = info if isinstance(info, dict) else {}
        except Exception as exc:
            errors.append(f"/System/Info: {exc}")

        try:
            sessions = self._get_json("/Sessions")
            raw_sessions = sessions if isinstance(sessions, list) else []
        except Exception as exc:
            errors.append(f"/Sessions: {exc}")

        for error in errors:
            self._log("API", error, throttle_seconds=15)

        active = [session for session in raw_sessions if session.get("NowPlayingItem")]
        parsed = [self._session_payload(session) for session in active]

        current_keys: dict[str, str] = {}
        for s in parsed:
            key = f"{s['user']}|{s['itemId']}|{s['device']}"
            label = s["title"] + (f" · {s['subtitle']}" if s.get("subtitle") else "")
            current_keys[key] = label
        for key, label in current_keys.items():
            if key not in self._prev_session_keys:
                self._log_event("Sesión", f"{key.split('|')[0]} inició: {label}")
        for key, label in self._prev_session_keys.items():
            if key not in current_keys:
                self._log_event("Sesión", f"{key.split('|')[0]} detuvo: {label}")
        self._prev_session_keys = current_keys

        paused = sum(1 for session in parsed if session["paused"])
        transcoding = sum(1 for session in parsed if session["isTranscoding"])
        direct = len(parsed) - transcoding
        total_kbps = sum(session.get("bitrateKbps", 0) for session in parsed)
        total_mbps = total_kbps / 1000
        self.history["streamMbps"].append(total_mbps)

        if not errors:
            self.last_success = now_label()

        return {
            "online": bool(info or parsed) and not errors,
            "status": "Online" if not errors else "Degradado",
            "version": self.server_info.get("Version") or "N/A",
            "serverName": self.server_info.get("ServerName") or self.server_info.get("LocalAddress") or "N/A",
            "sessions": parsed,
            "counts": {
                "active": len(parsed),
                "paused": paused,
                "direct": direct,
                "transcoding": transcoding,
            },
            "traffic": {
                "kbps": total_kbps,
                "mbps": total_mbps,
                "label": f"{total_mbps:.1f} Mbps",
            },
        }

    def _get_json(self, endpoint: str) -> Any:
        response = self.http.get(f"{JF_URL}{endpoint}", timeout=3)
        response.raise_for_status()
        return response.json()

    def _session_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        item = session.get("NowPlayingItem") or {}
        state = session.get("PlayState") or {}
        trans = session.get("TranscodingInfo")
        pos = state.get("PositionTicks") or 0
        dur = item.get("RunTimeTicks") or 0
        progress = pos / dur if dur else 0.0
        title, subtitle = self._title(item)
        method, detail, bitrate_kbps = self._stream_detail(item, trans)
        item_id = item.get("Id")
        return {
            "itemId": item_id,
            "itemType": item.get("Type") or "",
            "imageUrl": f"/api/image/{item_id}" if item_id else "",
            "user": session.get("UserName") or "?",
            "client": session.get("Client") or "?",
            "device": session.get("DeviceName") or "?",
            "title": title,
            "subtitle": subtitle,
            "paused": bool(state.get("IsPaused")),
            "isTranscoding": bool(trans),
            "method": method,
            "detail": detail,
            "bitrateKbps": bitrate_kbps,
            "progress": clamp(progress * 100),
            "position": ticks_to_str(pos),
            "duration": ticks_to_str(dur),
        }

    def _title(self, item: dict[str, Any]) -> tuple[str, str]:
        name = item.get("Name") or "Sin titulo"
        series = item.get("SeriesName")
        season = item.get("ParentIndexNumber")
        episode = item.get("IndexNumber")
        if series and season is not None and episode is not None:
            return name, f"{series} - S{season:02d}E{episode:02d}"
        if series:
            return name, str(series)
        return name, item.get("Type") or ""

    def _stream_detail(self, item: dict[str, Any], trans: dict[str, Any] | None) -> tuple[str, str, int]:
        if trans:
            video = str(trans.get("VideoCodec") or "?").upper()
            audio = str(trans.get("AudioCodec") or "?").upper()
            hw = trans.get("VideoDecodingAcceleration") or trans.get("HardwareAccelerationType") or "CPU"
            bitrate = int(safe_number(trans.get("Bitrate"))) // 1000
            fps = safe_number(trans.get("Framerate"))
            reasons = trans.get("TranscodeReasons") or trans.get("TranscodingReasons") or []
            if isinstance(reasons, list):
                reason_text = ", ".join(reason_label(reason) for reason in reasons[:3])
            else:
                reason_text = reason_label(reasons)
            parts = [f"{video} ({hw})", audio, f"{bitrate} kbps"]
            if fps > 0:
                parts.append(f"{fps:.0f} fps")
            detail = " - ".join(parts)
            return "Transcoding", f"{detail} - {reason_text}" if reason_text else detail, bitrate

        streams = item.get("MediaStreams") or []
        video_stream = next((stream for stream in streams if stream.get("Type") == "Video"), {})
        audio_stream = next((stream for stream in streams if stream.get("Type") == "Audio"), {})
        video = str(video_stream.get("Codec") or "?").upper()
        audio = str(audio_stream.get("Codec") or "?").upper()
        width = video_stream.get("Width") or 0
        height = video_stream.get("Height") or 0
        resolution = f"{width}x{height}" if width and height else "?"
        channels = audio_stream.get("Channels") or "?"
        language = str(audio_stream.get("Language") or "").upper()
        bitrate = int(safe_number(video_stream.get("BitRate") or item.get("Bitrate"))) // 1000
        br_text = f" - {bitrate} kbps" if bitrate else ""
        return "Direct Play", f"{video} - {resolution}{br_text} | {audio} {language} {channels}ch", bitrate

    def _summary_cards(
        self,
        system: dict[str, Any],
        process: dict[str, Any],
        gpu: dict[str, Any],
        jellyfin: dict[str, Any],
        latency_ms: float,
    ) -> list[dict[str, str]]:
        counts = jellyfin["counts"]
        process_read = process.get("diskRead", {}).get("label", "--")
        process_write = process.get("diskWrite", {}).get("label", "--")
        traffic = jellyfin.get("traffic", {"label": "0.0 Mbps"})
        gpu_label = gpu.get("util", {}).get("label") if gpu.get("available") else gpu.get("label", "N/A")
        gpu_sub = (
            f"{'Datos parciales - ' if gpu.get('partial') else ''}{gpu.get('temp', {}).get('label', '--')} - VRAM {gpu.get('vram', {}).get('label', '--')}"
            if gpu.get("available")
            else gpu.get("name", "GPU no disponible")
        )
        return [
            {
                "title": "Servidor",
                "value": jellyfin["status"],
                "subtitle": f"{jellyfin.get('serverName', 'N/A')} - v{jellyfin.get('version', 'N/A')}",
                "tone": "ok" if jellyfin["online"] else "warn",
            },
            {"title": "Latencia", "value": f"{latency_ms:.0f} ms", "subtitle": "API + proceso", "tone": pct_color(latency_ms, 700, 1500)},
            {"title": "Sesiones", "value": str(counts["active"]), "subtitle": f"{counts['paused']} pausadas - {counts['direct']} direct", "tone": "info"},
            {"title": "Transcoding", "value": str(counts["transcoding"]), "subtitle": f"{counts['direct']} direct play", "tone": "warn" if counts["transcoding"] else "ok"},
            {
                "title": "Jellyfin CPU",
                "value": process.get("cpu", {}).get("label", "No corre"),
                "subtitle": process.get("ram", {}).get("label", "Proceso no encontrado"),
                "tone": process.get("cpu", {}).get("tone", "muted"),
            },
            {"title": "GPU", "value": gpu_label, "subtitle": gpu_sub, "tone": gpu.get("util", {}).get("tone", "muted")},
            {"title": "I/O Jellyfin", "value": process_read, "subtitle": f"write {process_write}", "tone": "warn"},
            {"title": "Bitrate", "value": traffic["label"], "subtitle": "estimado por sesiones", "tone": "info"},
        ]

    def _alerts(
        self,
        system: dict[str, Any],
        process: dict[str, Any],
        gpu: dict[str, Any],
        jellyfin: dict[str, Any],
    ) -> list[str]:
        alerts: list[str] = []
        if not API_KEY:
            alerts.append("Falta JELLYFIN_API_KEY en .env.")
        if not jellyfin["online"]:
            alerts.append("Jellyfin no respondio completamente.")
        if not process.get("running"):
            alerts.append("Proceso jellyfin.exe no encontrado.")
        if not MEDIA_PATH.exists():
            alerts.append(f"Unidad multimedia no detectada: {MEDIA_PATH}.")
        if system["cpu"]["percent"] >= 90:
            alerts.append(f"CPU alta: {system['cpu']['label']}.")
        if system["ram"]["percent"] >= 90:
            alerts.append(f"RAM alta: {system['ram']['label']}.")
        if system["disk"]["percent"] >= 92:
            alerts.append(f"Disco casi lleno: {system['disk']['label']}.")
        if gpu.get("available") and gpu.get("temp", {}).get("value", 0) >= 84:
            alerts.append(f"GPU caliente: {gpu['temp']['label']}.")
        return alerts

    def _log(self, source: str, message: str, throttle_seconds: int = 5) -> None:
        key = f"{source}:{message}"
        now = time.time()
        if now - self.last_error.get(key, 0) < throttle_seconds:
            return
        self.last_error[key] = now
        self.events.appendleft({"time": now_label(), "source": source, "message": message})

    def _log_event(self, source: str, message: str) -> None:
        self.events.appendleft({"time": now_label(), "source": source, "message": message})


collector = MetricsCollector()


class MonitorHandler(BaseHTTPRequestHandler):
    server_version = "JellyfinMonitor/2.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/metrics":
            self._send_json(collector.collect())
            return
        if parsed.path.startswith("/api/image/"):
            item_id = parsed.path.rsplit("/", 1)[-1].strip()
            self._send_jellyfin_image(item_id)
            return
        if parsed.path == "/api/export/csv":
            qs = parse_qs(parsed.query)
            try:
                minutes = max(1, min(int(qs.get("minutes", ["60"])[0]), 120))
            except (ValueError, IndexError):
                minutes = 60
            self._send_csv(minutes)
            return

        relative = "index.html" if parsed.path in {"/", ""} else parsed.path.lstrip("/")
        target = WEB_DIR / relative
        try:
            resolved = target.resolve()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not resolved.is_relative_to(WEB_DIR.resolve()):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        if resolved.is_file():
            self._send_file(resolved)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_jellyfin_image(self, item_id: str) -> None:
        if not API_KEY or not item_id or any(char in item_id for char in "/\\?&"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        image_url = f"{JF_URL}/Items/{item_id}/Images/Primary"
        try:
            response = collector.http.get(
                image_url,
                params={"maxWidth": 220, "quality": 82},
                timeout=4,
            )
            if response.status_code == 404:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            response.raise_for_status()
        except requests.RequestException:
            self.send_error(HTTPStatus.BAD_GATEWAY)
            return

        content_type = response.headers.get("Content-Type", "image/jpeg")
        if not content_type.startswith("image/"):
            self.send_error(HTTPStatus.BAD_GATEWAY)
            return

        body = response.content
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_csv(self, minutes: int) -> None:
        with collector.lock:
            rows = list(collector.export_log)
        cutoff = time.time() - minutes * 60
        rows = [r for r in rows if r["ts"] >= cutoff]
        if not rows:
            self.send_error(HTTPStatus.NO_CONTENT)
            return
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        # utf-8-sig agrega BOM para que Excel reconozca el encoding sin configuración
        body = out.getvalue().encode("utf-8-sig")
        filename = f"jellyfin-{datetime.now().strftime('%Y-%m-%d-%H-%M')}.csv"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)


def pick_port(host: str, preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _startup_check() -> None:
    ok  = "\033[32m[OK]  \033[0m"
    warn = "\033[33m[WARN]\033[0m"
    info = "\033[36m[INFO]\033[0m"
    print("=== Jellyfin Monitor ===")
    print(f"{ok}   URL del servidor:  {JF_URL}")
    if API_KEY:
        print(f"{ok}   API Key:           Configurada")
    else:
        print(f"{warn}  API Key:           No configurada — sesiones no disponibles")
        print(f"         Configura JELLYFIN_API_KEY en el archivo .env")
    media_ok = MEDIA_PATH.exists()
    if media_ok:
        print(f"{ok}   Ruta multimedia:   {MEDIA_PATH}")
    else:
        print(f"{warn}  Ruta multimedia:   {MEDIA_PATH} — no encontrada (métricas de disco limitadas)")
    print(f"{info}  Refresco:          {REFRESH_MS} ms")
    if SERVER_HOST not in ("127.0.0.1", "localhost", "::1"):
        print(f"{warn}  Host:              {SERVER_HOST} — el monitor está expuesto en la red, sin autenticación")
    print()


def main() -> None:
    if not WEB_DIR.exists():
        raise SystemExit(f"No existe la carpeta web: {WEB_DIR}")

    _startup_check()
    port = pick_port(SERVER_HOST, SERVER_PORT)
    server = ThreadingHTTPServer((SERVER_HOST, port), MonitorHandler)
    url = f"http://{SERVER_HOST}:{port}"
    print(f"Servidor listo en {url}")
    print("Presiona Ctrl+C para cerrar.")

    if os.getenv("JELLYFIN_MONITOR_OPEN_BROWSER", "1") != "0":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando Jellyfin Monitor...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
