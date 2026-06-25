# API Reference

El backend expone cuatro endpoints HTTP, todos con `GET`.

---

## `GET /api/metrics`

Devuelve todas las métricas en un único objeto JSON. El frontend hace polling a este endpoint en el intervalo configurado (`JELLYFIN_REFRESH_MS`).

### Respuesta

```jsonc
{
  "config": { ... },
  "time": "14:32:05",
  "cards": [ ... ],
  "jellyfin": { ... },
  "system": { ... },
  "process": { ... },
  "gpu": { ... },
  "history": { ... },
  "alerts": [ ... ],
  "events": [ ... ],
  "lastSuccess": "14:31:58",
  "server": { ... }
}
```

---

### `config`

Refleja la configuración activa leída desde `.env`.

```jsonc
{
  "url": "http://localhost:8096",
  "apiKeyConfigured": true,
  "refreshMs": 2000,
  "mediaPath": "E:\\Multimedia",
  "monitorPort": 8765
}
```

---

### `cards`

Array de 8 tarjetas de resumen para la vista Overview.

```jsonc
[
  { "title": "Servidor",       "value": "Online",    "subtitle": "MyServer - v10.9.7", "tone": "ok"   },
  { "title": "Latencia",       "value": "42 ms",     "subtitle": "API + proceso",      "tone": "ok"   },
  { "title": "Sesiones",       "value": "2",         "subtitle": "0 pausadas - 2 direct", "tone": "info" },
  { "title": "Transcoding",    "value": "0",         "subtitle": "2 direct play",      "tone": "ok"   },
  { "title": "Jellyfin CPU",   "value": "3.2%",      "subtitle": "1.4 GB",             "tone": "ok"   },
  { "title": "GPU",            "value": "18%",       "subtitle": "54 °C - VRAM 2.1 GB", "tone": "ok" },
  { "title": "I/O Jellyfin",   "value": "12.0 MB/s", "subtitle": "write 0 B/s",        "tone": "warn" },
  { "title": "Bitrate",        "value": "24.0 Mbps", "subtitle": "estimado por sesiones", "tone": "info" }
]
```

**`tone`** — estado visual: `"ok"` / `"warn"` / `"danger"` / `"info"` / `"muted"`

---

### `jellyfin`

Estado del servidor Jellyfin y sesiones activas.

```jsonc
{
  "online": true,
  "status": "Online",
  "version": "10.9.7",
  "serverName": "MyServer",
  "sessions": [
    {
      "itemId": "a1b2c3d4e5f6",
      "itemType": "Episode",
      "imageUrl": "/api/image/a1b2c3d4e5f6",
      "user": "jcbla",
      "client": "Jellyfin Web",
      "device": "Chrome",
      "title": "Beater",
      "subtitle": "Sword Art Online - S01E03",
      "paused": false,
      "isTranscoding": false,
      "method": "Direct Play",
      "detail": "H264 - 1920x1080 - 12000 kbps | FLAC SPA 2ch",
      "bitrateKbps": 12000,
      "progress": 42.3,
      "position": "7:23",
      "duration": "23:30"
    }
  ],
  "counts": {
    "active": 1,
    "paused": 0,
    "direct": 1,
    "transcoding": 0
  },
  "traffic": {
    "kbps": 12000,
    "mbps": 12.0,
    "label": "12.0 Mbps"
  }
}
```

`status` puede ser `"Online"`, `"Degradado"` o `"Sin API key"`.

---

### `system`

Métricas globales del host (no solo del proceso Jellyfin).

```jsonc
{
  "cpu":      { "percent": 18.4, "label": "18%",             "tone": "ok"   },
  "ram":      { "percent": 61.2, "label": "9.8 GB / 16 GB",  "tone": "ok"   },
  "swap":     { "percent": 0.0,  "label": "0 B / 2.0 GB"                     },
  "disk":     { "percent": 74.3, "label": "744 GB / 1.0 TB", "tone": "ok"   },
  "diskRead": { "label": "0 B/s"  },
  "diskWrite":{ "label": "4.0 KB/s" },
  "netDown":  { "label": "1.2 MB/s" },
  "netUp":    { "label": "3.4 MB/s" }
}
```

---

### `process`

Métricas del proceso `jellyfin.exe` aisladas del resto del sistema.

```jsonc
{
  "running": true,
  "pid": 4812,
  "cpu":      { "percent": 3.2, "label": "3.2%",   "tone": "ok" },
  "ram":      { "mb": 1433.6,   "label": "1.4 GB",  "tone": "ok" },
  "handles":  1284,
  "threads":  48,
  "diskRead": { "label": "12.0 MB/s" },
  "diskWrite":{ "label": "0 B/s"     }
}
```

Si el proceso no está corriendo: `{ "running": false }`.

---

### `gpu`

Métricas de GPU NVIDIA vía `pynvml`. Si no hay GPU o no está instalado `pynvml`:

```jsonc
{ "available": false, "name": "GPU no disponible", "label": "N/A" }
```

Con GPU disponible:

```jsonc
{
  "available": true,
  "partial": false,
  "name": "NVIDIA GeForce RTX 3060",
  "util":    { "percent": 18.0, "label": "18%",     "tone": "ok"   },
  "temp":    { "value": 54,     "label": "54 °C",   "tone": "ok"   },
  "vram":    { "percent": 26.0, "label": "2.1 / 8.0 GB" },
  "encoder": { "percent": 0.0,  "label": "0%"  },
  "decoder": { "percent": 5.0,  "label": "5%"  },
  "power": "85.3 W",
  "fan":   "42%"
}
```

`partial: true` indica que algunos valores no pudieron leerse pero otros sí.

---

### `history`

Últimos 90 puntos de cada métrica (un punto por poll). Útil para dibujar gráficas.

```jsonc
{
  "cpu":        [0.0, 1.2, 3.4, ...],
  "ram":        [61.0, 61.1, ...],
  "diskRead":   [0.0, 12.5, ...],
  "diskWrite":  [0.0, 0.0, ...],
  "netDown":    [1.2, 1.3, ...],
  "netUp":      [3.4, 3.5, ...],
  "gpu":        [18.0, 20.0, ...],
  "jfCpu":      [3.2, 3.1, ...],
  "jfRamMb":    [1433.6, 1434.0, ...],
  "jfDiskRead": [12.0, 11.8, ...],
  "jfDiskWrite":[0.0, 0.0, ...],
  "streamMbps": [12.0, 12.0, ...]
}
```

---

### `alerts`

Array de strings con las alertas activas. Vacío si todo está normal.

```jsonc
["CPU alta: 93%.", "Disco casi lleno: 940 GB / 1.0 TB."]
```

Umbrales: CPU/RAM ≥ 90% → alerta; disco ≥ 92%; GPU temp ≥ 84 °C.

---

### `events`

Log de eventos recientes — cambios de sesión y errores de API, ordenados del más reciente al más antiguo. Máximo 50 entradas.

```jsonc
[
  { "time": "14:32:01", "source": "Sesión", "message": "jcbla inició: Beater · Sword Art Online S01E03" },
  { "time": "14:31:45", "source": "Sesión", "message": "jcbla detuvo: Inception · Película" },
  { "time": "14:28:10", "source": "API",    "message": "/Sessions: Connection refused" }
]
```

**`source`** — `"Sesión"` (evento de reproducción) / `"API"` (error de red Jellyfin) / `"GPU"` (error pynvml)

---

### `lastSuccess`

Hora del último poll exitoso a la API de Jellyfin. `null` si nunca se conectó.

```jsonc
"lastSuccess": "14:31:58"
```

---

### `server`

Objeto crudo de `/System/Info` de Jellyfin. Útil para obtener campos adicionales no expuestos en el resto del payload.

```jsonc
{
  "ServerName": "MyServer",
  "Version": "10.9.7",
  "Id": "...",
  "LocalAddress": "http://192.168.1.10:8096",
  ...
}
```

Vacío (`{}`) si no se pudo conectar.

---

## `GET /api/image/<itemId>`

Proxy que descarga la portada primaria de un ítem de Jellyfin y la sirve al navegador. La API Key nunca sale del servidor.

- **`itemId`** — ID del ítem (string hexadecimal de Jellyfin, ej. `a1b2c3d4e5f6`)
- Responde con `image/jpeg` o `image/png` con `Cache-Control: private, max-age=300`
- Devuelve `404` si el ítem no existe o no tiene portada
- Devuelve `400` si el `itemId` contiene caracteres inválidos (`/ \ ? &`)

---

## `GET /api/export/csv`

Descarga el historial de métricas acumulado en memoria como archivo CSV. El navegador abre el diálogo de guardar archivo directamente.

### Parámetros

| Parámetro | Tipo | Default | Descripción |
|---|---|---|---|
| `minutes` | int | `60` | Minutos hacia atrás a exportar (mín. 1, máx. 120) |

Ejemplo: `/api/export/csv?minutes=30`

### Respuesta

- **Content-Type**: `text/csv; charset=utf-8`
- **Content-Disposition**: `attachment; filename="jellyfin-YYYY-MM-DD-HH-MM.csv"`
- Devuelve `204 No Content` si no hay datos aún (el monitor acaba de iniciar)

### Columnas del CSV

| Columna | Unidad | Descripción |
|---|---|---|
| `hora` | HH:MM:SS | Hora del registro |
| `cpu_pct` | % | CPU global del host |
| `ram_pct` | % | RAM global del host |
| `disco_pct` | % | Uso del disco del host |
| `disco_lectura_mbs` | MB/s | Lectura de disco del host |
| `disco_escritura_mbs` | MB/s | Escritura de disco del host |
| `red_bajada_mbs` | MB/s | Tráfico de red entrante |
| `red_subida_mbs` | MB/s | Tráfico de red saliente |
| `gpu_pct` | % | Uso de GPU (vacío si no disponible) |
| `gpu_temp_c` | °C | Temperatura de GPU (vacío si no disponible) |
| `jf_cpu_pct` | % | CPU del proceso Jellyfin (vacío si no corre) |
| `jf_ram_mb` | MB | RAM privada del proceso Jellyfin |
| `jf_disco_lectura_mbs` | MB/s | Lectura de disco del proceso Jellyfin |
| `stream_mbps` | Mbps | Bitrate total estimado de sesiones activas |
| `sesiones` | — | Número de sesiones activas |
| `transcoding` | — | Número de sesiones en transcoding |

El archivo incluye BOM UTF-8 para compatibilidad directa con Microsoft Excel.

El buffer en memoria guarda hasta 1800 muestras (~60 minutos a 2 s de intervalo). Con intervalos más cortos, el buffer cubre menos tiempo.

---

## `GET /` y archivos estáticos

Sirve los archivos de la carpeta `web/`. Todas las rutas que no empiezan con `/api/` son tratadas como archivos estáticos. El servidor valida que el path resuelto esté dentro de `web/` para prevenir path traversal.
