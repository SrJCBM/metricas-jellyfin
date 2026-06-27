const state = {
  view: "overview",
  refreshMs: 2000,
  timer: null,
  featuredIndex: 0,
  lastSessions: [],
};

const charts = {
  jfCpu:      { title: "Jellyfin · CPU",     sub: "proceso · % de núcleo",   unit: "%",    color: "#4fd1b5", floorMax: 100 },
  jfRamMb:    { title: "Jellyfin · RAM",     sub: "proceso · memoria RSS",   unit: " MB",  color: "#a78bfa" },
  jfDiskRead: { title: "Jellyfin · Lectura", sub: "i/o del proceso",         unit: " MB/s",color: "#4fd1b5" },
  streamMbps: { title: "Bitrate sesiones",   sub: "salida total a clientes", unit: " Mbps",color: "#a78bfa" },
};

// sparkline config for metric cards (indices 4–7 of data.cards)
const metricSparklines = [
  { key: "jfCpu",      color: "#4fd1b5" },
  { key: null,         color: "#a78bfa" },
  { key: "jfDiskRead", color: "#4fd1b5" },
  { key: "streamMbps", color: "#a78bfa" },
];

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function sparkPath(values, W, H) {
  if (!values || values.length < 2) return "";
  const lo = Math.min(...values), hi = Math.max(...values);
  const pad = (hi - lo) * 0.18 || 1;
  const a = lo - pad, b = hi + pad, rg = (b - a) || 1;
  const step = W / (values.length - 1);
  return values
    .map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(H - ((v - a) / rg) * H).toFixed(1)}`)
    .join(" ");
}

function toneColor(tone) {
  const map = {
    ok:     "color:var(--teal)",
    warn:   "color:var(--amber)",
    danger: "color:var(--red)",
    info:   "color:var(--text)",
    muted:  "color:var(--muted)",
  };
  return map[tone] || "";
}

function setView(view) {
  state.view = view;
  $$(".nav-btn").forEach((btn) => {
    const active = btn.dataset.view === view;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-current", active ? "page" : "false");
  });
  $$(".view").forEach((sec) => {
    const active = sec.id === `${view}-view`;
    sec.classList.toggle("active", active);
    sec.setAttribute("aria-hidden", active ? "false" : "true");
  });
  const meta = {
    overview: ["Resumen",  "Consumo del proceso Jellyfin en tiempo real"],
    sessions: ["Sesiones", "Reproducciones activas en el servidor"],
    system:   ["Sistema",  "Host del equipo vs proceso Jellyfin"],
    status:   ["Estado",   "Configuración, alertas y eventos recientes"],
  };
  const [title, sub] = meta[view] || [view, ""];
  $("#view-title").textContent = title;
  $("#view-subtitle").textContent = sub;
}

function scheduleNext() {
  clearTimeout(state.timer);
  state.timer = setTimeout(fetchMetrics, state.refreshMs);
}

async function fetchMetrics() {
  try {
    const res = await fetch("/api/metrics", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    render(await res.json());
  } catch (err) {
    renderFetchError(err);
  } finally {
    scheduleNext();
  }
}

function render(data) {
  state.refreshMs = data.config?.refreshMs || state.refreshMs;

  $("#server-url").textContent = data.config?.url || "--";
  $("#clock").textContent = data.time || "--:--:--";

  const online = !!data.jellyfin?.online;
  const statusLabel = data.jellyfin?.status || "API";

  // Topbar pill
  const pill = $("#server-pill");
  pill.className = `pill ${online ? "ok" : "warn"}`;
  $("#pill-label").textContent = statusLabel;

  // Sidebar status
  const dot = $("#sidebar-dot");
  dot.className = `status-dot ${online ? "online" : "offline"}`;
  const sideText = $("#sidebar-status-text");
  sideText.textContent = statusLabel;
  sideText.style.color = online ? "var(--teal)" : "var(--red)";

  const serverName = data.jellyfin?.serverName || data.server?.ServerName || "";
  const version = data.jellyfin?.version || data.server?.Version || "";
  $("#sidebar-version").textContent = serverName
    ? `${serverName}${version ? " · " + version : ""}`
    : "--";

  renderCards(data.cards || [], data.history || {});
  renderSessions(data.jellyfin || {});
  renderMetrics(data);
  renderStatus(data);
  buildChartPanels();
  renderCharts(data.history || {});
}

function renderCards(cards, history) {
  const summary = cards.slice(0, 4);
  const metrics = cards.slice(4, 8);

  $("#summary-cards").innerHTML = summary
    .map(
      (card) => `
      <article class="card">
        <span class="label">${escapeHtml(card.title)}</span>
        <strong class="value" style="${toneColor(card.tone)}">${escapeHtml(card.value)}</strong>
        <span class="sub">${escapeHtml(card.subtitle)}</span>
      </article>`,
    )
    .join("");

  $("#metric-cards").innerHTML = metrics
    .map((card, i) => {
      const spark = metricSparklines[i];
      const values = spark?.key ? history[spark.key] || [] : [];
      const path = sparkPath(values, 56, 20);
      const sparkSvg = path
        ? `<svg viewBox="0 0 56 20" width="56" height="20" preserveAspectRatio="none" style="flex:none">` +
          `<path d="${path}" fill="none" stroke="${escapeHtml(spark.color)}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`
        : "";
      return `
      <article class="card metric-card">
        <span class="label">${escapeHtml(card.title)}</span>
        <div class="card-value-row">
          <strong class="value" style="${toneColor(card.tone)}">${escapeHtml(card.value)}</strong>
          ${sparkSvg}
        </div>
        <span class="sub">${escapeHtml(card.subtitle)}</span>
      </article>`;
    })
    .join("");
}

function sessionCard(session, featured = false) {
  const isPaused = session.paused;
  const isTranscoding = session.isTranscoding;
  const initial = escapeHtml((session.user || "?").slice(0, 2).toUpperCase());
  const stateClass = isPaused ? "pause" : "play";
  const stateLabel = isPaused ? "Pausa" : "Reproduciendo";
  const typeClass = isTranscoding ? "transcoding" : "direct";
  const progress = Math.max(0, Math.min(100, session.progress || 0));

  // Split "H264 · 1920x1080 · kbps | FLAC …" into video / audio chips
  const [videoPart, audioPart] = (session.detail || "").split(" | ");
  const videoChip = videoPart ? `<span class="chip">${escapeHtml(videoPart)}</span>` : "";
  const audioChip = audioPart ? `<span class="chip">${escapeHtml(audioPart)}</span>` : "";

  if (featured) {
    return `
      <div class="featured-session">
        <div class="session-user">
          <div class="session-avatar">${initial}</div>
          <div class="session-user-info">
            <div class="session-user-name">${escapeHtml(session.user)}</div>
            <div class="session-user-device">${escapeHtml(session.device)} · ${escapeHtml(session.client)}</div>
          </div>
        </div>
        <div class="session-media">
          ${session.imageUrl
            ? `<img class="session-thumb" src="${escapeHtml(session.imageUrl)}" alt="" onerror="this.onerror=null;this.removeAttribute('src');this.style.backgroundImage='none'">`
            : `<div class="session-thumb"></div>`}
          <div class="session-media-info">
            <h4 class="session-title">${escapeHtml(session.title)}</h4>
            ${session.subtitle ? `<p class="session-subtitle">${escapeHtml(session.subtitle)}</p>` : ""}
            <span class="session-type-badge ${typeClass}">${escapeHtml(session.method || "")}</span>
          </div>
        </div>
        ${videoChip || audioChip ? `
        <div class="chips-section">
          <div class="chips-label">Detalle técnico</div>
          <div class="chips">${videoChip}${audioChip}</div>
        </div>` : ""}
        <div class="progress-section">
          <div class="progress-meta">
            <span>${escapeHtml(session.position)} · ${Math.round(progress)}%</span>
            <span>${escapeHtml(session.duration)}</span>
          </div>
          <div class="progress"><span style="width:${progress}%"></span></div>
        </div>
      </div>`;
  }

  return `
    <div class="session-card">
      <div class="session-card-top">
        <div class="session-avatar-lg">${initial}</div>
        <div class="session-card-info">
          <div class="session-card-title-row">
            <span class="session-card-title">${escapeHtml(session.title)}</span>
            <span class="session-type-badge ${typeClass}">${escapeHtml(session.method || "")}</span>
          </div>
          <div class="session-card-meta">${escapeHtml(session.user)} · ${escapeHtml(session.device)} · ${escapeHtml(session.client)}</div>
        </div>
        <div class="session-card-right">
          <span class="session-state-inline ${stateClass}">${stateLabel}</span>
          <span class="session-pct">${Math.round(progress)}%</span>
        </div>
      </div>
      <div class="progress"><span style="width:${progress}%"></span></div>
    </div>`;
}

function renderFeatured() {
  const sessions = state.lastSessions;
  const idx = state.featuredIndex;
  const total = sessions.length;

  const nav = $("#carousel-nav");
  if (nav) nav.style.display = total > 1 ? "flex" : "none";

  const posEl = $("#carousel-pos");
  if (posEl) posEl.textContent = `${idx + 1} / ${total}`;

  const badge = $("#session-state-badge");
  if (badge) {
    if (total > 0) {
      const s = sessions[idx];
      badge.textContent = s.paused ? "Pausa" : "Reproduciendo";
      badge.className = `session-state-badge ${s.paused ? "pause" : "play"}`;
    } else {
      badge.textContent = "";
      badge.className = "session-state-badge";
    }
  }

  const featuredEl = $("#featured-session");
  if (!featuredEl) return;
  if (total > 0) {
    featuredEl.innerHTML = sessionCard(sessions[idx], true);
    featuredEl.className = "";
  } else {
    featuredEl.innerHTML = "Sin sesiones activas";
    featuredEl.className = "featured-empty";
  }
}

function renderSessions(jellyfin) {
  const sessions = jellyfin.sessions || [];
  const counts = jellyfin.counts || { active: 0, paused: 0, direct: 0, transcoding: 0 };

  state.lastSessions = [...sessions].sort((a, b) =>
    `${a.user}${a.device}`.localeCompare(`${b.user}${b.device}`)
  );
  state.featuredIndex = Math.min(state.featuredIndex, Math.max(0, state.lastSessions.length - 1));
  renderFeatured();

  $("#sessions-summary").textContent =
    `${sessions.length} sesiones · ${counts.active} activas · ${counts.transcoding} transcoding`;

  $("#sessions-list").innerHTML = state.lastSessions.length
    ? state.lastSessions.map((s) => sessionCard(s, false)).join("")
    : `<div class="featured-empty">Sin sesiones activas</div>`;
}

function metricRowHtml(name, metric, opts = {}) {
  const percent = metric?.percent;
  const label = metric?.label ?? "--";
  const sub = opts.sub || "";
  const tone = opts.tone || metric?.tone || opts.color || "info";
  const width = percent == null ? 0 : Math.max(2, Math.min(100, Number(percent)));
  const hasBar = percent != null;
  return `
    <div class="metric-row">
      <div class="metric-row-top">
        <span class="name">${escapeHtml(name)}</span>
        <strong class="metric-value ${escapeHtml(tone)}">${escapeHtml(label)}</strong>
      </div>
      ${hasBar ? `<div class="bar"><span class="${escapeHtml(tone)}" style="width:${width}%"></span></div>` : ""}
      ${sub ? `<span class="metric-sub">${escapeHtml(sub)}</span>` : ""}
    </div>`;
}

function valueRowHtml(name, value, sub = "") {
  return `
    <div class="metric-row">
      <div class="metric-row-top">
        <span class="name">${escapeHtml(name)}</span>
        <strong class="metric-value">${escapeHtml(value ?? "--")}</strong>
      </div>
      ${sub ? `<span class="metric-sub">${escapeHtml(sub)}</span>` : ""}
    </div>`;
}

function renderMetrics(data) {
  const system  = data.system  || {};
  const process = data.process || {};
  const gpu     = data.gpu     || {};

  $("#system-metrics").innerHTML = [
    metricRowHtml("CPU total",      system.cpu,  { sub: "núcleos del sistema" }),
    metricRowHtml("Memoria",        system.ram,  { sub: "RAM física" }),
    metricRowHtml("Swap",           system.swap, { sub: "memoria de intercambio" }),
    metricRowHtml("Disco",          system.disk, { sub: "almacenamiento principal" }),
    valueRowHtml("Lectura disco",   system.diskRead?.label,  "i/o de lectura"),
    valueRowHtml("Escritura disco", system.diskWrite?.label, "i/o de escritura"),
    valueRowHtml("Red bajada",      system.netDown?.label,   "tráfico entrante"),
    valueRowHtml("Red subida",      system.netUp?.label,     "tráfico saliente"),
  ].join("");

  const gpuRows = gpu.available
    ? [
        metricRowHtml("GPU",     gpu.util, { sub: gpu.name || "" }),
        metricRowHtml("VRAM",    gpu.vram, { color: "purple", sub: "memoria de vídeo" }),
        metricRowHtml("Encoder", { percent: gpu.encoder?.percent, label: gpu.encoder?.label }, { color: "ok",   sub: "NVENC" }),
        metricRowHtml("Decoder", { percent: gpu.decoder?.percent, label: gpu.decoder?.label }, { color: "info", sub: "NVDEC" }),
        valueRowHtml("GPU temp",  gpu.temp?.label, "temperatura"),
        valueRowHtml("GPU power", gpu.power,       "consumo"),
      ]
    : [valueRowHtml("GPU", gpu.label || "N/A", gpu.name || "No disponible")];

  $("#server-metrics").innerHTML = [
    metricRowHtml("CPU del proceso",     process.cpu  || { percent: 0, label: process.label || "No corre" }, { color: "info", sub: process.pid ? `PID ${process.pid}` : "" }),
    metricRowHtml("Memoria del proceso", process.ram  || { percent: 0, label: "--" },                        { color: "info", sub: "memoria RSS" }),
    valueRowHtml("Lectura disco",        process.diskRead?.label  || "--", "i/o del proceso"),
    valueRowHtml("Escritura disco",      process.diskWrite?.label || "--", "remux temporal"),
    valueRowHtml("Uptime",               process.uptime,   "proceso activo"),
    valueRowHtml("Threads",              process.threads != null ? String(process.threads) : "--", "hilos activos"),
    valueRowHtml("Handles",              process.handles != null ? String(process.handles) : "N/A", "manejadores"),
    ...gpuRows,
  ].join("");
}

function renderStatus(data) {
  const config   = data.config   || {};
  const server   = data.server   || {};
  const jellyfin = data.jellyfin || {};
  const online   = !!jellyfin.online;
  const alerts   = data.alerts   || [];

  // Health chips
  const chips = [
    { label: "Servidor",   value: online ? "Online" : "Offline",                                   color: online ? "var(--teal)" : "var(--red)" },
    { label: "Transcoder", value: (jellyfin.counts?.transcoding || 0) > 0 ? "Activo" : "Inactivo", color: (jellyfin.counts?.transcoding || 0) > 0 ? "var(--teal)" : "var(--muted)" },
    { label: "Disco",      value: data.system?.disk?.label || "--",                                 color: data.system?.disk?.tone === "danger" ? "var(--red)" : data.system?.disk?.tone === "warn" ? "var(--amber)" : "var(--teal)" },
    { label: "Última OK",  value: data.lastSuccess || "--",                                         color: "var(--muted)" },
  ];
  $("#health-chips").innerHTML = chips
    .map(
      (c) => `
      <div class="health-chip">
        <span class="health-chip-label">${escapeHtml(c.label)}</span>
        <span class="health-chip-value" style="color:${c.color}">
          <span class="dot"></span>${escapeHtml(c.value)}
        </span>
      </div>`,
    )
    .join("");

  // Config list
  const srvName = server.ServerName || jellyfin.serverName || "N/A";
  const srvVer  = server.Version    || jellyfin.version    || "";
  const cfgRows = [
    { label: "URL Jellyfin", value: config.url,                                                sub: "dirección del servidor" },
    { label: "API Key",      value: config.apiKeyConfigured ? "Configurada" : "Falta API key", sub: "autenticación" },
    { label: "Refresh",      value: `${config.refreshMs} ms`,                                 sub: "intervalo de actualización" },
    { label: "Media path",   value: config.mediaPath,                                          sub: "almacenamiento multimedia" },
    { label: "Monitor",      value: `127.0.0.1:${config.monitorPort}`,                        sub: "este servidor" },
    { label: "Servidor",     value: `${srvName}${srvVer ? " · " + srvVer : ""}`,             sub: "nombre y versión" },
  ];
  $("#config-list").innerHTML = cfgRows
    .map(
      ({ label, value, sub }) => `
      <div class="kv-row">
        <div>
          <div class="kv-key">${escapeHtml(label)}</div>
          ${sub ? `<div class="kv-sub">${escapeHtml(sub)}</div>` : ""}
        </div>
        <strong class="kv-value">${escapeHtml(value ?? "--")}</strong>
      </div>`,
    )
    .join("");

  // Alerts
  $("#alerts-list").innerHTML = alerts.length
    ? alerts
        .map(
          (alert) =>
            `<div class="alert-item"><span class="alert-dot"></span><span class="alert-msg">${escapeHtml(alert)}</span></div>`,
        )
        .join("")
    : `<div class="alert-item ok"><span class="alert-dot"></span><span class="alert-msg">Sin alertas</span></div>`;

  // Events
  const events = data.events || [];
  const eventSourceColor = (src) =>
    src === "Sesión" ? "var(--teal)" : src === "GPU" ? "var(--amber)" : "var(--red)";
  $("#events-list").innerHTML = events.length
    ? events
        .map(
          (ev) => `
          <div class="event-item">
            <span class="event-time">${escapeHtml(ev.time)}</span>
            <span class="event-source" style="color:${eventSourceColor(ev.source)}">${escapeHtml(ev.source || "")}</span>
            <span class="event-message">${escapeHtml(ev.message)}</span>
          </div>`,
        )
        .join("")
    : `<div class="event-item"><span class="event-message">Sin eventos recientes.</span></div>`;
}

function renderFetchError(error) {
  const pill = $("#server-pill");
  pill.className = "pill warn";
  $("#pill-label").textContent = "Monitor offline";
  $("#clock").textContent = "--:--:--";

  const dot = $("#sidebar-dot");
  if (dot) dot.className = "status-dot offline";

  $("#summary-cards").innerHTML = `
    <article class="card">
      <span class="label">Error</span>
      <strong class="value" style="color:var(--red)">Sin conexión</strong>
      <span class="sub">${escapeHtml(error.message)}</span>
    </article>`;
}

function buildChartPanels() {
  const el = $("#chart-panels");
  if (!el || el.children.length > 0) return;
  el.innerHTML = Object.entries(charts)
    .map(
      ([key, cfg]) => `
      <div class="chart-panel">
        <div class="chart-panel-head">
          <div>
            <div class="chart-title">${escapeHtml(cfg.title)}</div>
            <div class="chart-sub">${escapeHtml(cfg.sub)}</div>
          </div>
          <span class="chart-current mono" id="${key}-current" style="color:${cfg.color}">--</span>
        </div>
        <canvas data-chart="${key}" role="img" aria-label="${escapeHtml(cfg.title)} — historial"></canvas>
      </div>`,
    )
    .join("");
}

function renderCharts(history) {
  Object.entries(charts).forEach(([key, config]) => {
    const canvas = $(`canvas[data-chart="${key}"]`);
    if (!canvas) return;
    const values = history[key] || [];
    drawChart(canvas, values, config);
    const curEl = document.getElementById(`${key}-current`);
    if (curEl && values.length) {
      curEl.textContent = `${values[values.length - 1].toFixed(1)}${config.unit}`;
    }
  });
}

function drawChart(canvas, values, config) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width  = Math.max(1, Math.floor(rect.width  * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const W = rect.width, H = rect.height;
  const pad = { t: 4, r: 4, b: 4, l: 4 };
  const gW = W - pad.l - pad.r;
  const gH = H - pad.t - pad.b;
  const maxVal = Math.max(...values, config.floorMax || 1);

  ctx.clearRect(0, 0, W, H);

  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const y = pad.t + (gH / 3) * i;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(W - pad.r, y);
    ctx.stroke();
  }

  if (values.length < 2) return;

  const xOf = (i) => pad.l + (i / (values.length - 1)) * gW;
  const yOf = (v) => pad.t + gH - (v / maxVal) * gH;

  ctx.strokeStyle = config.color;
  ctx.lineWidth = 1.6;
  ctx.lineJoin = "round";
  ctx.beginPath();
  values.forEach((v, i) => (i === 0 ? ctx.moveTo(xOf(i), yOf(v)) : ctx.lineTo(xOf(i), yOf(v))));
  ctx.stroke();

  const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
  grad.addColorStop(0, `${config.color}28`);
  grad.addColorStop(1, `${config.color}00`);
  ctx.lineTo(W - pad.r, H - pad.b);
  ctx.lineTo(pad.l,     H - pad.b);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
}

$$(".nav-btn").forEach((btn) => btn.addEventListener("click", () => setView(btn.dataset.view)));

$("#carousel-prev").addEventListener("click", () => {
  if (state.featuredIndex > 0) {
    state.featuredIndex--;
    renderFeatured();
  }
});
$("#carousel-next").addEventListener("click", () => {
  if (state.featuredIndex < state.lastSessions.length - 1) {
    state.featuredIndex++;
    renderFeatured();
  }
});

window.addEventListener("resize", () => {
  fetch("/api/metrics", { cache: "no-store" })
    .then((r) => r.json())
    .then((data) => renderCharts(data.history || {}))
    .catch(() => {});
});

buildChartPanels();
setView("overview");
fetchMetrics();
