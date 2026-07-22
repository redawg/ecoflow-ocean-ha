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
        setVehicle(null, false);
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
   * Build roof PV module grids once, mapped onto the panel arrays baked into
   * house-aerial-closed-garage-v8.png (viewBox 1000×625). Coordinates from
   * the 1536×1024 plate with object-fit:cover / object-position:center 40%.
   *
   * String layout (5 rows, back-to-front on the roof):
   *   String 1: 11 panels (longest row, near the tall ridge)
   *   String 2:  7 panels
   *   String 3:  7 panels
   *   String 4:  7 panels
   *   String 5:  6 panels (front row, nearest parapet edge)
   */
  function clearPvModules() {
    el("pvArrayMain")?.replaceChildren();
    el("pvArraySec")?.replaceChildren();
  }

  function ensurePvModules() {
    const main = el("pvArrayMain");
    if (!main || main.childElementCount) return;
    if (isForestSite()) return;

    // Roof quad corners in viewBox coords (TL, TR, BR, BL) — upper deck
    // inside parapets on the v8 attached plate.
    const roofQuad = [
      [338.5, 178.6],
      [703.1, 159.1],
      [729.2, 263.3],
      [364.6, 282.8],
    ];

    // 5 strings as rows: [stringId, panelCount]
    const strings = [
      [1, 11],
      [2, 7],
      [3, 7],
      [4, 7],
      [5, 6],
    ];
    const totalRows = strings.length;
    const rowGap = 0.05;
    const colGap = 0.035;

    for (let ri = 0; ri < totalRows; ri++) {
      const [stringId, cols] = strings[ri];
      const v0 = ri / totalRows + rowGap;
      const v1 = (ri + 1) / totalRows - rowGap;

      for (let ci = 0; ci < cols; ci++) {
        const u0 = ci / cols + colGap / cols;
        const u1 = (ci + 1) / cols - colGap / cols;
        const pts = [
          quadPoint(roofQuad, u0, v0),
          quadPoint(roofQuad, u1, v0),
          quadPoint(roofQuad, u1, v1),
          quadPoint(roofQuad, u0, v1),
        ];
        const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
        poly.setAttribute("class", "pv-mod");
        poly.setAttribute("data-string", String(stringId));
        poly.setAttribute(
          "points",
          pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ")
        );
        main.appendChild(poly);
      }
    }
  }

  function colorPvModules(flow, solarW) {
    // EcoFlow-style plates already have flush panels baked in. Skip SVG
    // overlays until string tints are re-calibrated to the new camera.
    if (isForestSite() || el("houseStage")?.classList.contains("desert-attached")) {
      clearPvModules();
      return;
    }
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
      // Soft tint over baked roof panels — never opaque floating slabs.
      const t = Math.min(1, w / 2500);
      mod.style.fill = `rgba(${Math.round(40 + t * 215)}, ${Math.round(50 + t * 140)}, ${Math.round(20 + t * 20)}, ${0.08 + t * 0.42})`;
    });
  }

  const MAX_BATTERY_PACKS = 8;

  function updateInverterFace(s) {
    const face = el("invFaceSoc");
    if (!face) return;
    const soc = s.soc;
    const live = soc != null;
    face.classList.toggle("live", live);
    face.classList.toggle("discharging", !!s.discharging);
    face.classList.toggle("charging", !!s.charging);
    face.classList.toggle("exporting", !!s.exporting);
    face.classList.toggle("importing", !!s.importing);
    face.classList.toggle("flowing", !!(s.discharging || s.charging || (s.homeW || 0) > 40));

    const pct = live ? Math.max(0, Math.min(100, soc)) : 0;
    el("invFacePct").textContent = live ? `${pct.toFixed(0)}` : "—";

    // Direction under the % (matches hardware “TO BATTERY” / “FROM BATTERY”)
    let sub = "READY";
    if (s.charging) sub = "TO BATTERY";
    else if (s.discharging) sub = "FROM BATTERY";
    else if (s.exporting) sub = "TO GRID";
    else if (s.importing) sub = "FROM GRID";
    el("invFaceSub").textContent = sub;

    // Primary watts in the face = battery power when active, else home load
    const batAbs = Math.abs(s.batteryW || 0);
    const homeW = Math.max(0, s.homeW || 0);
    const mainW = batAbs > 25 ? batAbs : homeW;
    el("invFaceBatW").textContent = mainW > 5 ? String(Math.round(mainW)) : "0";
    if (el("invFaceHomeW")) el("invFaceHomeW").textContent = fmtW(homeW);
    if (el("invFaceDir")) {
      el("invFaceDir").textContent = s.discharging
        ? "DISCHARGING"
        : s.charging
          ? "CHARGING"
          : "READY";
    }

    // Semi-circle SOC fill (pathLength=100)
    const ring = el("invFaceRing");
    if (ring) {
      ring.style.strokeDasharray = "100";
      ring.style.strokeDashoffset = String(100 - pct);
    }
  }

  function makeBatteryUnit(idx) {
    const fig = document.createElement("figure");
    fig.className = "gear-unit battery idle";
    fig.setAttribute("data-pack", String(idx));
    fig.innerHTML = `
      <div class="batt-visual gear-visual">
        <img src="/static/img/gear/battery-tower.png?v=20260722s" alt="Battery pack ${idx + 1}" draggable="false" />
        <div class="batt-track" aria-hidden="true"><div class="batt-fill"></div></div>
        <span class="gear-glow" aria-hidden="true"></span>
      </div>
      <figcaption>
        <span class="gear-name pack-id">P${idx + 1}</span>
        <span class="gear-value pack-soc">—</span>
        <span class="gear-sub pack-w">—</span>
      </figcaption>`;
    return fig;
  }

  function resolvePackCount(packs, bpPackCount) {
    const listed = Array.isArray(packs) ? packs.length : 0;
    const declared = Number(bpPackCount);
    const fromCount =
      Number.isFinite(declared) && declared > 0 ? Math.floor(declared) : 0;
    return Math.min(MAX_BATTERY_PACKS, Math.max(listed, fromCount, 0));
  }

  function syncBatteryPacks(packs, bpPackCount) {
    const row = el("gearBatRow");
    const rack = el("gearRack");
    if (!row) return;

    const list = Array.isArray(packs) ? packs : [];
    const count = resolvePackCount(list, bpPackCount);
    if (rack) rack.setAttribute("data-packs", String(count));

    const existing = row.querySelectorAll(".gear-unit.battery");
    if (existing.length !== count) {
      row.innerHTML = "";
      for (let i = 0; i < count; i++) row.appendChild(makeBatteryUnit(i));
    }

    row.querySelectorAll(".gear-unit.battery").forEach((node) => {
      const idx = Number(node.getAttribute("data-pack"));
      const pack = list[idx] || {};
      const packSoc = num(pack.soc);
      const packW = num(pack.power_w);
      const sn = String(pack.sn || "").slice(-4);
      const nameEl = node.querySelector(".gear-name");
      const socEl = node.querySelector(".pack-soc");
      const wEl = node.querySelector(".pack-w");
      // Match EcoFlow pack badge: "P1 - 0223" / green SOC / watts
      if (nameEl) nameEl.textContent = sn ? `P${idx + 1} - ${sn}` : `P${idx + 1}`;
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

    // Left-wall gear: inverter shows TOTAL system SOC; packs show per-pack.
    const invPower = Math.abs(battery) > 25 ? Math.abs(battery) : solar;
    const discharging = battery < -50;
    const charging = battery > 50;
    el("invSoc").textContent = soc != null ? `${soc.toFixed(0)}%` : "—";
    el("invSoc")?.classList.toggle("soc-live", soc != null);
    el("invW").textContent = fmtW(invPower);
    el("invMode").textContent = (() => {
      const m = String(flow.work_mode || "").toLowerCase();
      if (m === "self_use" || m === "selfuse") return "Self-powered";
      if (m === "intelligent" || m === "timer_mode") return "Intelligent";
      if (m === "backup") return "Emergency Backup";
      if (m === "time_of_use" || m === "tou") return "Time-of-use";
      return flow.work_mode || (flow.online ? "online" : "—");
    })();
    updateInverterFace({
      soc,
      batteryW: battery,
      homeW: essential,
      gridW: grid,
      solarW: solar,
      discharging,
      charging,
      exporting,
      importing,
    });
    el("gearInverter")?.classList.toggle("active", solar > 25 || Math.abs(battery) > 50);

    const stormWatchRaw = flow.storm_watch ?? panel.storm_watch;
    const stormActiveRaw = flow.storm_enabled ?? panel.storm_enabled;
    const stormWatch =
      stormWatchRaw === true ||
      stormWatchRaw === 1 ||
      stormWatchRaw === "1" ||
      stormWatchRaw === "true";
    const stormActive =
      stormActiveRaw === true ||
      stormActiveRaw === 1 ||
      stormActiveRaw === "1" ||
      stormActiveRaw === "true";
    const stormBadge = el("stormBadge");
    if (stormBadge) {
      if (stormActive) {
        stormBadge.hidden = false;
        stormBadge.textContent = "Storm mode";
        stormBadge.classList.add("storm-active");
      } else if (stormWatch) {
        stormBadge.hidden = false;
        stormBadge.textContent = "Storm Guard";
        stormBadge.classList.remove("storm-active");
      } else {
        stormBadge.hidden = true;
        stormBadge.textContent = "";
        stormBadge.classList.remove("storm-active");
      }
    }
    el("cardHome")?.classList.toggle("storm-active", stormActive);
    el("cardHome")?.classList.toggle("storm-watch", stormWatch && !stormActive);

    el("panelW").textContent = fmtW(essential);
    el("panelSub").textContent = "house circuits";
    el("gearPanel")?.classList.toggle("active", essential > 25);

    const invState = data.inverter?.state || {};
    const packs = Array.isArray(invState.battery_packs)
      ? invState.battery_packs
      : [];
    syncBatteryPacks(packs, invState.bp_pack_count);

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
    el("evSub").textContent =
      evBits.join(" · ") || (isForestSite() ? "double bay" : "charger");
    el("evBadge").textContent = ev > 25 ? "active" : "idle";
    el("evBadge").classList.toggle("on", ev > 25);
    el("cardEv")?.classList.toggle("active", ev > 25);

    // Forest second charger (single bay) — show when bay 1 has a vehicle.
    // Live watts arrive once a second EV device is wired; until then idle.
    const bay1Key = normalizeVehicleKey(el("vehicleSelectBay1")?.value);
    const cardEv2 = el("cardEv2");
    if (cardEv2) {
      const showEv2 = isForestSite() && bay1Key !== "none";
      cardEv2.hidden = !showEv2;
      if (showEv2) {
        el("ev2W").textContent = "—";
        el("ev2Badge").textContent = "idle";
        el("ev2Badge")?.classList.remove("on");
        const pretty = bay1Key.replace(/-/g, " ");
        el("ev2Sub").textContent = `${pretty} · single bay`;
        cardEv2.classList.remove("active");
      }
    }

    // EV charger → panel power flow path
    const evFlowPath = el("evFlowStroke");
    const evFlowLabel = el("evFlowLabel");
    const evCharging = ev > 25;
    if (evFlowPath) {
      evFlowPath.classList.toggle("active", evCharging);
      evFlowPath.style.strokeOpacity = evCharging ? "1" : "0";
    }
    if (evFlowLabel) {
      evFlowLabel.textContent = evCharging ? fmtW(ev) : "";
    }

    // Roof → wall-gear glowing conduits (EcoFlow app style).
    const solarLive = solar > 40;
    const batLive = Math.abs(battery) > 50;
    const solarStroke = isForestSite()
      ? el("powerSolarForest")
      : el("powerSolarDesert");
    const batStroke = isForestSite()
      ? el("powerBatForest")
      : el("powerBatDesert");
    solarStroke?.classList.toggle("on", solarLive);
    batStroke?.classList.toggle("on", batLive);

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

  const V = "v=20260722s";
  const VEHICLE_KEYS = [
    "none",
    "tesla-model3",
    "tesla-modely",
    "rivian-r1s",
    "rivian-r1t",
  ];

  // Desert pulled-back plates: full house + garage + cars.
  const DESERT_WIDE_IMAGES = {
    none: `/static/img/house-desert-wide-v1.png?${V}`,
    "tesla-model3": `/static/img/house-desert-garage-tesla-model3-v3.png?${V}`,
    "tesla-modely": `/static/img/house-desert-garage-tesla-modely-v3.png?${V}`,
    "rivian-r1s": `/static/img/house-desert-garage-rivian-r1s-v2.png?${V}`,
    "rivian-r1t": `/static/img/house-desert-garage-rivian-r1t-v2.png?${V}`,
  };

  // Desert zoom plates: left-wall EcoFlow system layout (iterate here).
  const DESERT_GEAR_IMAGES = {
    none: `/static/img/house-desert-gear-detail-v1.png?${V}`,
    "tesla-model3": `/static/img/house-desert-gear-detail-v1.png?${V}`,
    "tesla-modely": `/static/img/house-desert-gear-detail-v1.png?${V}`,
    "rivian-r1s": `/static/img/house-desert-gear-detail-v1.png?${V}`,
    "rivian-r1t": `/static/img/house-desert-gear-detail-v1.png?${V}`,
  };

  // Back-compat alias (gear/installed plates used while zoomed).
  const DESERT_VEHICLE_IMAGES = DESERT_GEAR_IMAGES;

  // Forest bay 2 = wide double door; bay 1 = narrow single door.
  const FOREST_BAY2_IMAGES = {
    none: `/static/img/house-forest-installed-v1.png?${V}`,
    "tesla-model3": `/static/img/house-forest-installed-bay2-tesla-model3-v1.png?${V}`,
    "tesla-modely": `/static/img/house-forest-installed-bay2-tesla-modely-v1.png?${V}`,
    "rivian-r1s": `/static/img/house-forest-installed-bay2-rivian-r1s-v1.png?${V}`,
    "rivian-r1t": `/static/img/house-forest-installed-bay2-rivian-r1t-v1.png?${V}`,
  };
  const FOREST_BAY1_IMAGES = {
    none: `/static/img/house-forest-installed-v1.png?${V}`,
    "tesla-model3": `/static/img/house-forest-installed-bay1-tesla-model3-v1.png?${V}`,
    "tesla-modely": `/static/img/house-forest-installed-bay1-tesla-modely-v1.png?${V}`,
    "rivian-r1s": `/static/img/house-forest-installed-bay1-rivian-r1s-v1.png?${V}`,
    "rivian-r1t": `/static/img/house-forest-installed-bay1-rivian-r1t-v1.png?${V}`,
  };

  function isForestSite(siteId = activeSiteId) {
    return String(siteId || "").toLowerCase().includes("forest");
  }

  function normalizeVehicleKey(key) {
    const k = String(key || "none");
    return VEHICLE_KEYS.includes(k) ? k : "none";
  }

  function forestPlateUrl(bay1, bay2) {
    const a = normalizeVehicleKey(bay1);
    const b = normalizeVehicleKey(bay2);
    if (a === "none" && b === "none") return FOREST_BAY2_IMAGES.none;
    if (a === "none") return FOREST_BAY2_IMAGES[b] || FOREST_BAY2_IMAGES.none;
    if (b === "none") return FOREST_BAY1_IMAGES[a] || FOREST_BAY1_IMAGES.none;
    return `/static/img/house-forest-installed-dual-${a}-${b}-v1.png?${V}`;
  }

  function vehicleStorageKey(bay = "main") {
    if (isForestSite()) {
      return bay === "bay1"
        ? "ecoflow_house_vehicle_forest_bay1"
        : "ecoflow_house_vehicle_forest_bay2";
    }
    return "ecoflow_house_vehicle_desert";
  }

  function initVehiclePicker() {
    const selectMain = el("vehicleSelect");
    const selectBay1 = el("vehicleSelectBay1");
    selectMain?.addEventListener("change", () => applyGarageSelection(true));
    selectBay1?.addEventListener("change", () => applyGarageSelection(true));
    applyGarageSelection(false);
  }

  function syncGaragePickerUi() {
    const forest = isForestSite();
    const bay1Wrap = el("vpBay1Wrap");
    const bay2Label = el("vpBay2Label");
    const cardEv2 = el("cardEv2");
    const evLabel = el("evLabel");
    if (bay1Wrap) bay1Wrap.hidden = !forest;
    if (bay2Label) bay2Label.textContent = forest ? "Double" : "Garage";
    if (evLabel) evLabel.textContent = forest ? "EV 1" : "EV";
    if (cardEv2) {
      const bay1 = normalizeVehicleKey(el("vehicleSelectBay1")?.value);
      cardEv2.hidden = !(forest && bay1 !== "none");
    }
    el("vehiclePicker")?.classList.toggle("forest-site", forest);
  }

  function applyGarageSelection(save) {
    const forest = isForestSite();
    let bay2 = normalizeVehicleKey(
      el("vehicleSelect")?.value ||
        localStorage.getItem(vehicleStorageKey("bay2")) ||
        localStorage.getItem("ecoflow_house_vehicle_forest") ||
        localStorage.getItem("ecoflow_house_vehicle_desert") ||
        "none"
    );
    let bay1 = "none";
    if (forest) {
      bay1 = normalizeVehicleKey(
        el("vehicleSelectBay1")?.value ||
          localStorage.getItem(vehicleStorageKey("bay1")) ||
          "none"
      );
    }

    if (!save) {
      const main = el("vehicleSelect");
      const b1 = el("vehicleSelectBay1");
      if (main && main.value !== bay2) main.value = bay2;
      if (b1 && b1.value !== bay1) b1.value = bay1;
    }

    const stage = el("houseStage");
    const zoomed = !forest && !!stage?.classList.contains("zoomed-gear");
    const desertUrl = zoomed
      ? DESERT_GEAR_IMAGES[bay2] || DESERT_GEAR_IMAGES.none
      : DESERT_WIDE_IMAGES[bay2] || DESERT_WIDE_IMAGES.none;
    const url = forest ? forestPlateUrl(bay1, bay2) : desertUrl;
    const bg = el("houseBg");
    if (bg) {
      const abs = new URL(url, location.origin).href;
      if (bg.src !== abs) bg.src = url;
      bg.alt = forest
        ? "Aerial view of forest house with dual garage bays and EV chargers"
        : zoomed
          ? "Desert left-wall EcoFlow panel, inverter, and battery packs"
          : "Full desert house with garage, vehicles, and roof solar";
    }

    stage?.classList.toggle("desert-attached", !forest);
    stage?.classList.toggle("forest-site", forest);
    // Gear is baked into plates — keep live labels only.
    stage?.classList.add("gear-baked");
    clearPvModules();
    syncGaragePickerUi();
    syncGearZoomUi();

    if (save) {
      localStorage.setItem(vehicleStorageKey("bay2"), bay2);
      if (forest) localStorage.setItem(vehicleStorageKey("bay1"), bay1);
      else localStorage.setItem("ecoflow_house_vehicle_desert", bay2);
    }
  }

  // Back-compat alias used by site-switch handler.
  function setVehicle(_key, save) {
    applyGarageSelection(!!save);
  }

  initVehiclePicker();

  function syncGearZoomUi() {
    const stage = el("houseStage");
    const hint = el("zoomHint");
    const btn = el("gearZoomBtn");
    if (!stage) return;
    const forest = isForestSite();
    const zoomed = stage.classList.contains("zoomed-gear");
    // Desert: inspect hotspot only in wide view; full-stage click to zoom out.
    // Forest: keep existing CSS zoom behavior on the installed plate.
    if (btn) {
      btn.hidden = forest && false;
      btn.setAttribute(
        "aria-label",
        zoomed ? "Zoom out to full house" : "Zoom into left-wall equipment"
      );
    }
    if (hint) {
      hint.textContent = zoomed
        ? "↩ Click to zoom out"
        : forest
          ? "🔍 Click to inspect"
          : "🔍 Click left wall to inspect system";
    }
    // Garage vehicle picker only makes sense in the pulled-back house view.
    const picker = el("vehiclePicker");
    if (picker) picker.hidden = zoomed;
  }

  // --- Gear zoom toggle ---
  // Desert: wide garage plate ↔ gear-detail plate.
  // Forest: CSS scale into left-wall region of installed plate.
  (function initGearZoom() {
    const btn = el("gearZoomBtn");
    const stage = el("houseStage");
    if (!btn || !stage) return;

    btn.addEventListener("click", () => {
      stage.classList.toggle("zoomed-gear");
      syncGearZoomUi();
      // Refresh plate src for Desert wide/gear swap.
      applyGarageSelection(false);
    });
    syncGearZoomUi();
  })();

  // --- Time-of-day ambient lighting ---
  // Simulates sun position at Desert Hot Springs, CA (lat 33.96, lon -116.50)
  // by applying CSS filters + gradient overlays to the house background image.
  const LATITUDE = 33.96;

  function solarElevation(date) {
    const dayOfYear = Math.floor((date - new Date(date.getFullYear(), 0, 0)) / 86400000);
    const declination = 23.45 * Math.sin((2 * Math.PI / 365) * (dayOfYear - 81));
    const hour = date.getHours() + date.getMinutes() / 60;
    const hourAngle = (hour - 12) * 15;
    const latRad = LATITUDE * Math.PI / 180;
    const decRad = declination * Math.PI / 180;
    const haRad = hourAngle * Math.PI / 180;
    const sinElev = Math.sin(latRad) * Math.sin(decRad) +
                    Math.cos(latRad) * Math.cos(decRad) * Math.cos(haRad);
    return Math.asin(sinElev) * 180 / Math.PI;
  }

  function applyDaylight() {
    const bg = el("houseBg");
    const overlay = el("dayOverlay");
    if (!bg) return;

    const now = new Date();
    const elev = solarElevation(now);

    let brightness, saturate, warmth, overlayColor;

    if (elev > 30) {
      // Full daylight
      brightness = 1.0;
      saturate = 1.0;
      warmth = 0;
      overlayColor = "transparent";
    } else if (elev > 10) {
      // Late afternoon / early morning
      const t = (elev - 10) / 20;
      brightness = 0.88 + 0.12 * t;
      saturate = 1.05 - 0.05 * t;
      warmth = (1 - t) * 8;
      overlayColor = `rgba(255, 160, 60, ${(1 - t) * 0.06})`;
    } else if (elev > 0) {
      // Golden hour / sunrise-sunset
      const t = elev / 10;
      brightness = 0.6 + 0.28 * t;
      saturate = 0.9 + 0.15 * t;
      warmth = 8 + (1 - t) * 12;
      overlayColor = `rgba(255, 120, 40, ${(1 - t) * 0.14 + 0.06})`;
    } else if (elev > -6) {
      // Civil twilight
      const t = (elev + 6) / 6;
      brightness = 0.25 + 0.35 * t;
      saturate = 0.4 + 0.5 * t;
      warmth = (1 - t) * -5;
      overlayColor = `rgba(30, 40, 90, ${(1 - t) * 0.35 + 0.1})`;
    } else if (elev > -12) {
      // Nautical twilight
      const t = (elev + 12) / 6;
      brightness = 0.12 + 0.13 * t;
      saturate = 0.2 + 0.2 * t;
      warmth = -5;
      overlayColor = `rgba(10, 15, 40, ${(1 - t) * 0.2 + 0.45})`;
    } else {
      // Night
      brightness = 0.10;
      saturate = 0.15;
      warmth = -8;
      overlayColor = "rgba(5, 8, 25, 0.7)";
    }

    bg.style.filter = `brightness(${brightness}) saturate(${saturate}) hue-rotate(${warmth}deg)`;
    bg.style.transition = "filter 60s linear";

    if (overlay) {
      overlay.style.background = overlayColor;
      overlay.style.transition = "background 60s linear";
    }
  }

  applyDaylight();
  setInterval(applyDaylight, 60000);

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
