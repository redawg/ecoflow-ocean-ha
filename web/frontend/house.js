(() => {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") || localStorage.getItem("ecoflow_web_token") || "";
  if (params.get("token")) localStorage.setItem("ecoflow_web_token", params.get("token"));

  const headers = {};
  if (token) headers["X-API-Token"] = token;

  const el = (id) => document.getElementById(id);
  let overviewCache = null;
  let energyCache = null;
  let sitesCache = [];
  let activeSiteId =
    params.get("site") || localStorage.getItem("ecoflow_web_site") || "";
  let ws;

  function pageHref(path) {
    const qs = new URLSearchParams();
    if (token) qs.set("token", token);
    if (activeSiteId) qs.set("site", activeSiteId);
    const q = qs.toString();
    return q ? `${path}?${q}` : path;
  }

  function syncNav() {
    el("navDash")?.setAttribute("href", pageHref("/"));
    el("navHouse")?.setAttribute("href", pageHref("/house"));
  }

  function setConn(ok, text) {
    const pill = el("connStatus");
    pill.textContent = text;
    pill.classList.toggle("ok", !!ok);
    pill.classList.toggle("bad", !ok);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function fmtW(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const w = Number(value);
    if (Math.abs(w) >= 1000) return `${(w / 1000).toFixed(2)} kW`;
    return `${Math.round(w)} W`;
  }

  function fmtKwh(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    return `${Number(value).toFixed(1)} kWh`;
  }

  function num(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function renderSiteSwitch(sites, defaultId) {
    sitesCache = sites || [];
    if (!activeSiteId || !sitesCache.some((s) => s.id === activeSiteId)) {
      activeSiteId = defaultId || sitesCache[0]?.id || "";
    }
    if (activeSiteId) localStorage.setItem("ecoflow_web_site", activeSiteId);
    syncNav();

    const nav = el("siteSwitch");
    nav.innerHTML = sitesCache
      .map((s) => {
        const online = s.online_count > 0;
        const ready = !!s.ready;
        const dotClass = !ready ? "off" : online ? "on" : "";
        return `<button type="button" class="site-btn ${
          s.id === activeSiteId ? "active" : ""
        }" data-site="${escapeHtml(s.id)}">
          <span class="dot ${dotClass}"></span>${escapeHtml(s.label)}
        </button>`;
      })
      .join("");

    nav.querySelectorAll(".site-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = btn.getAttribute("data-site");
        if (!next || next === activeSiteId) return;
        activeSiteId = next;
        localStorage.setItem("ecoflow_web_site", activeSiteId);
        renderSiteSwitch(sitesCache, activeSiteId);
        Promise.all([loadOverview(), loadEnergy()])
          .then(() => connectWs(true))
          .catch((err) => setConn(false, String(err.message || err)));
      });
    });
  }

  /**
   * Bilinear point inside a quad defined by 4 corners (u=0,v=0 / u=1,v=0 /
   * u=1,v=1 / u=0,v=1), used so each PV module cell follows the roof's
   * perspective/rotation instead of sitting in an axis-aligned box.
   */
  function quadPoint(corners, u, v) {
    const [c00, c10, c11, c01] = corners;
    const x =
      (1 - u) * (1 - v) * c00[0] +
      u * (1 - v) * c10[0] +
      u * v * c11[0] +
      (1 - u) * v * c01[0];
    const y =
      (1 - u) * (1 - v) * c00[1] +
      u * (1 - v) * c10[1] +
      u * v * c11[1] +
      (1 - u) * v * c01[1];
    return [x, y];
  }

  function buildPvGrid(group, corners, rows, cols, gapFrac, stringOf) {
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const u0 = c / cols + gapFrac / cols;
        const u1 = (c + 1) / cols - gapFrac / cols;
        const v0 = r / rows + gapFrac / rows;
        const v1 = (r + 1) / rows - gapFrac / rows;
        const pts = [
          quadPoint(corners, u0, v0),
          quadPoint(corners, u1, v0),
          quadPoint(corners, u1, v1),
          quadPoint(corners, u0, v1),
        ];
        const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
        poly.setAttribute("class", "pv-mod");
        poly.setAttribute("data-string", String(stringOf(r, c)));
        poly.setAttribute("points", pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" "));
        group.appendChild(poly);
      }
    }
  }

  /**
   * Build roof PV module grids once, mapped onto the panel arrays visible in
   * house-aerial-left-v3.png (viewBox 1000×625). Corners were measured
   * directly on the source photo, then converted from image-pixel space into
   * viewBox space accounting for the object-fit: cover / object-position
   * 40% crop the <img> uses.
   *
   * The main roof array is L-shaped (a 4-wide/2-row upper block plus a
   * 3-wide bottom row missing the far corner), so it's built from two
   * quads sharing an edge. The secondary array is a plain 3×2 block.
   */
  function ensurePvModules() {
    const main = el("pvArrayMain");
    const sec = el("pvArraySec");
    if (!main || main.childElementCount) return;

    // Corners in order: top-left, top-right, bottom-right, bottom-left.
    const mainUpperCorners = [
      [343.8, 124.0],
      [444.7, 114.9],
      [455.7, 175.4],
      [354.8, 184.5],
    ];
    const mainLowerCorners = [
      [354.8, 184.5],
      [430.3, 176.0],
      [436.2, 206.7],
      [360.7, 215.1],
    ];
    const secCorners = [
      [517.6, 178.7],
      [597.7, 167.6],
      [605.5, 226.8],
      [525.1, 238.5],
    ];

    buildPvGrid(main, mainUpperCorners, 2, 4, 0.08, () => 1);
    buildPvGrid(main, mainLowerCorners, 1, 3, 0.09, () => 2);
    buildPvGrid(sec, secCorners, 2, 3, 0.08, () => 3);
  }

  function colorPvModules(flow, solarW) {
    ensurePvModules();
    const strings = flow.solar_strings || [];
    const byId = {};
    for (const s of strings) {
      const id = Number(s.id);
      if (Number.isFinite(id)) byId[id] = Math.max(0, Number(s.power_w) || 0);
    }
    const total = Math.max(0, solarW || 0);
    const fallback = total / 5;

    document.querySelectorAll(".pv-mod").forEach((mod) => {
      const sid = Number(mod.getAttribute("data-string"));
      const w = byId[sid] != null ? byId[sid] : fallback;
      mod.classList.toggle("on", w > 40);
      mod.classList.toggle("hot", w > 800);
      const t = Math.min(1, w / 2500);
      mod.style.fill = `rgba(${Math.round(40 + t * 215)}, ${Math.round(50 + t * 140)}, ${Math.round(20 + t * 20)}, ${0.25 + t * 0.55})`;
    });
  }

  function renderHouse(data) {
    overviewCache = data;
    const label = data.site_label || data.site_id || "EcoFlow Ocean";
    el("pageTitle").textContent = label;
    syncNav();

    const flow = data.power_flow || {};
    const panel = data.panel?.state || {};
    const energy = energyCache?.totals || {};

    const solar = Math.max(0, num(flow.solar_w) ?? 0);
    const battery = num(flow.battery_w) ?? 0;
    const grid = num(flow.grid_w);
    const soc = num(flow.soc);
    const homeTotal = Math.max(0, num(flow.home_w) ?? 0);
    const ev = Math.max(0, num(flow.ev_charge_w) ?? 0);
    const essential = Math.max(0, homeTotal - ev);
    const gridAbs = Math.abs(grid ?? 0);
    const exporting = grid != null && grid < -50;
    const importing = grid != null && grid > 50;

    el("solarW").textContent = fmtW(solar);
    el("solarDay").textContent = `today ${fmtKwh(energy.solar_kwh)}`;
    el("cardSolar")?.classList.toggle("active", solar > 25);
    colorPvModules(flow, solar);

    // Left-wall gear: inverter + panel + 4 packs
    el("invW").textContent = fmtW(solar || Math.abs(battery));
    el("invMode").textContent = flow.work_mode || (flow.online ? "online" : "—");
    el("gearInverter")?.classList.toggle("active", solar > 25 || Math.abs(battery) > 50);

    el("panelW").textContent = fmtW(essential);
    el("panelSub").textContent =
      soc != null ? `bank ${soc.toFixed(0)}%` : "smart panel";
    el("gearPanel")?.classList.toggle("active", essential > 25);

    const packs = data.inverter?.state?.battery_packs || [];
    document.querySelectorAll(".gear-unit.battery").forEach((node) => {
      const idx = Number(node.getAttribute("data-pack"));
      const pack = packs[idx] || {};
      const packSoc = num(pack.soc);
      const packW = num(pack.power_w);
      const sn = String(pack.sn || "").slice(-4);
      const nameEl = node.querySelector(".gear-name");
      const socEl = node.querySelector(".pack-soc");
      const wEl = node.querySelector(".pack-w");
      if (nameEl) nameEl.textContent = sn ? `P${idx + 1} · ${sn}` : `Bat ${idx + 1}`;
      if (socEl) socEl.textContent = packSoc != null ? `${packSoc.toFixed(0)}%` : "—";
      if (wEl) {
        wEl.textContent =
          packW != null && Math.abs(packW) > 20 ? fmtW(Math.abs(packW)) : "idle";
      }
      const isCharging = packW != null && packW > 50;
      const isDischarging = packW != null && packW < -50;
      node.classList.toggle("charging", isCharging);
      node.classList.toggle("discharging", isDischarging);
      node.classList.toggle("idle", !isCharging && !isDischarging);
      const fillEl = node.querySelector(".batt-fill");
      if (fillEl) {
        const pct = packSoc != null ? Math.max(0, Math.min(100, packSoc)) : 0;
        fillEl.style.setProperty("--pct", `${pct.toFixed(1)}%`);
      }
    });

    el("gridW").textContent = fmtW(gridAbs);
    let gridDir = "balanced";
    if (exporting) gridDir = "exporting";
    else if (importing) gridDir = "importing";
    el("gridDir").textContent = gridDir;
    el("gridBadge").textContent = flow.online === false ? "offline" : "grid on";
    el("gridBadge").classList.toggle("on", flow.online !== false);
    el("gridImport").textContent = fmtKwh(energy.grid_import_kwh);
    el("gridExport").textContent = fmtKwh(energy.grid_export_kwh);

    const v =
      num(panel.grid_voltage_v) ??
      num(panel.grid_voltage_l1_v) ??
      num(flow.grid_voltage_v);
    el("gridV").textContent = v != null ? `${v.toFixed(1)} V` : "—";

    const gridCard = el("cardGrid");
    gridCard?.classList.toggle("exporting", exporting);
    gridCard?.classList.toggle("importing", importing);

    el("homeW").textContent = fmtW(essential);
    el("homeHint").textContent = ev > 25 ? "essential (ex-EV)" : "house load";
    el("cardHome")?.classList.toggle("active", essential > 25);

    el("evW").textContent = fmtW(ev);
    const evBits = [];
    if (flow.vehicle_connected === true) evBits.push("vehicle in");
    if (flow.charging_active === true) evBits.push("charging");
    el("evSub").textContent = evBits.join(" · ") || "charger";
    el("evBadge").textContent = ev > 25 ? "active" : "idle";
    el("evBadge").classList.toggle("on", ev > 25);
    el("cardEv")?.classList.toggle("active", ev > 25);

    el("houseMeta").textContent = flow.updated_at
      ? `Updated ${new Date(flow.updated_at).toLocaleString()}`
      : "Waiting for telemetry…";
  }

  async function loadSites() {
    const res = await fetch("/api/sites", { headers });
    if (!res.ok) throw new Error(`sites ${res.status}`);
    const data = await res.json();
    renderSiteSwitch(data.sites || [], data.default_site_id);
    return data;
  }

  async function loadOverview() {
    const qs = new URLSearchParams();
    if (activeSiteId) qs.set("site", activeSiteId);
    const res = await fetch(`/api/overview?${qs}`, { headers });
    if (res.status === 401) {
      setConn(false, "Auth required — open with ?token=...");
      throw new Error("unauthorized");
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    renderHouse(data);
    setConn(true, "Live");
    return data;
  }

  async function loadEnergy() {
    const qs = new URLSearchParams({ hours: "24" });
    if (activeSiteId) qs.set("site", activeSiteId);
    const inverter = overviewCache?.inverter?.serial;
    if (inverter) qs.set("serial", inverter);
    const res = await fetch(`/api/history/energy?${qs}`, { headers });
    if (!res.ok) throw new Error(`energy ${res.status}`);
    energyCache = await res.json();
    if (overviewCache) renderHouse(overviewCache);
  }

  function connectWs(force) {
    if (ws && !force) return;
    if (ws) {
      try {
        ws.close();
      } catch (_) {
        /* ignore */
      }
      ws = null;
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const qs = new URLSearchParams();
    if (token) qs.set("token", token);
    if (activeSiteId) qs.set("site", activeSiteId);
    ws = new WebSocket(`${proto}://${location.host}/api/ws?${qs}`);
    ws.onopen = () => setConn(true, "Live");
    ws.onclose = () => {
      setConn(false, "Reconnecting…");
      setTimeout(() => connectWs(true), 3000);
    };
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "sites") {
          renderSiteSwitch(msg.sites || [], msg.default_site_id || activeSiteId);
        }
        if (msg.type === "overview" && (!msg.site_id || msg.site_id === activeSiteId)) {
          renderHouse(msg.data);
        }
      } catch (_) {
        /* ignore */
      }
    };
  }

  syncNav();
  loadSites()
    .then(() => loadOverview())
    .then(() => loadEnergy())
    .then(() => connectWs(true))
    .catch((err) => {
      console.error(err);
      setConn(false, String(err.message || err));
    });

  setInterval(() => {
    loadEnergy().catch(() => {});
    loadSites().catch(() => {});
  }, 60000);
})();
