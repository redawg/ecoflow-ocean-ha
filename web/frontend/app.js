(() => {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") || localStorage.getItem("ecoflow_web_token") || "";
  if (params.get("token")) {
    localStorage.setItem("ecoflow_web_token", params.get("token"));
  }

  const headers = {};
  if (token) headers["X-API-Token"] = token;

  const el = (id) => document.getElementById(id);
  let chart;
  let overheadChart;
  let historyHours = 24;
  let overviewCache = null;
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
    const dash = el("navDash");
    const house = el("navHouse");
    if (dash) dash.setAttribute("href", pageHref("/"));
    if (house) house.setAttribute("href", pageHref("/house"));
  }

  function fmtW(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const w = Number(value);
    if (Math.abs(w) >= 1000) return `${(w / 1000).toFixed(2)} kW`;
    return `${Math.round(w)} W`;
  }

  /** Grid: show magnitude; direction is in the subtitle (export/import). */
  function fmtGridW(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    return fmtW(Math.abs(Number(value)));
  }

  /** Panel circuit watts are signed; negative = load/usage — show as positive. */
  function circuitUsageW(value) {
    const w = Number(value);
    if (!Number.isFinite(w)) return 0;
    return Math.abs(w);
  }

  /**
   * Dark→white fill for circuit load.
   * Singles step every 500 W; split-phase "big" pairs stay smoother (1 kW steps).
   */
  function circuitLoadStyle(watts, { maxW = 3000, stepW = 500 } = {}) {
    const w = Math.max(0, Number(watts) || 0);
    const steps = Math.max(1, Math.ceil(maxW / stepW));
    const step = Math.min(steps, Math.floor(w / stepW)); // 0…steps
    const t = step / steps;
    const r = Math.round(14 + (248 - 14) * t);
    const g = Math.round(18 + (250 - 18) * t);
    const b = Math.round(24 + (252 - 24) * t);
    const ink = t > 0.52 ? "#10151c" : "#e8eef5";
    const muted = t > 0.52 ? "rgba(16,21,28,0.62)" : "rgba(232,238,245,0.65)";
    return `--load:${t.toFixed(3)};--box:${r},${g},${b};--box-ink:${ink};--box-muted:${muted}`;
  }

  const STRING_COUNT = 5;
  const SOLAR_STEP_W = 250;
  const DEFAULT_STRING_MAX_W = 3000;
  const STRING_MAX_KEY = "ecoflow_string_max_ws";

  function clampStringMaxW(next) {
    const value = Math.max(500, Math.min(20000, Math.round(Number(next) || DEFAULT_STRING_MAX_W)));
    return Math.max(SOLAR_STEP_W, Math.round(value / SOLAR_STEP_W) * SOLAR_STEP_W);
  }

  function parseStringMaxList(raw) {
    if (raw == null || raw === "") return null;
    if (Array.isArray(raw)) {
      const nums = raw.map((v) => Number(v)).filter((n) => Number.isFinite(n) && n >= 500);
      if (!nums.length) return null;
      if (nums.length === 1) return Array(STRING_COUNT).fill(clampStringMaxW(nums[0]));
      return Array.from({ length: STRING_COUNT }, (_, i) =>
        clampStringMaxW(nums[i] ?? nums[nums.length - 1] ?? DEFAULT_STRING_MAX_W)
      );
    }
    if (typeof raw === "number" && Number.isFinite(raw) && raw >= 500) {
      return Array(STRING_COUNT).fill(clampStringMaxW(raw));
    }
    if (typeof raw === "string") {
      const parts = raw.split(/[,\s]+/).map((p) => p.trim()).filter(Boolean);
      if (!parts.length) return null;
      const nums = parts.map((p) => Number(p));
      if (nums.every((n) => Number.isFinite(n) && n >= 500)) return parseStringMaxList(nums);
      const single = Number(raw);
      if (Number.isFinite(single) && single >= 500) return parseStringMaxList(single);
    }
    return null;
  }

  function readStringMaxWs() {
    const fromUrl = parseStringMaxList(params.get("string_max_w"));
    if (fromUrl) return fromUrl;
    try {
      const storedJson = localStorage.getItem(STRING_MAX_KEY);
      if (storedJson) {
        const parsed = parseStringMaxList(JSON.parse(storedJson));
        if (parsed) return parsed;
      }
    } catch {
      /* ignore */
    }
    // Migrate legacy single max.
    const legacy = Number(localStorage.getItem("ecoflow_string_max_w"));
    if (Number.isFinite(legacy) && legacy >= 500) {
      return Array(STRING_COUNT).fill(clampStringMaxW(legacy));
    }
    return Array(STRING_COUNT).fill(DEFAULT_STRING_MAX_W);
  }

  let stringMaxWs = readStringMaxWs();

  function totalStringMaxW() {
    return stringMaxWs.reduce((sum, w) => sum + w, 0);
  }

  // Inverter DC→AC conversion efficiency, applied to the nameplate string
  // total to estimate max AC output (not a live measurement — it's the same
  // idea as a spec-sheet "max efficiency" derate).
  const DEFAULT_INVERTER_EFF_PCT = 97;
  const INVERTER_EFF_KEY = "ecoflow_inverter_eff_pct";

  function clampInverterEffPct(next) {
    const value = Number(next);
    if (!Number.isFinite(value)) return DEFAULT_INVERTER_EFF_PCT;
    return Math.max(50, Math.min(100, Math.round(value * 10) / 10));
  }

  function readInverterEffPct() {
    const fromUrl = Number(params.get("inverter_eff_pct"));
    if (Number.isFinite(fromUrl) && fromUrl > 0) return clampInverterEffPct(fromUrl);
    const stored = Number(localStorage.getItem(INVERTER_EFF_KEY));
    if (Number.isFinite(stored) && stored > 0) return clampInverterEffPct(stored);
    return DEFAULT_INVERTER_EFF_PCT;
  }

  let inverterEffPct = readInverterEffPct();

  function setInverterEffPct(next) {
    inverterEffPct = clampInverterEffPct(next);
    localStorage.setItem(INVERTER_EFF_KEY, String(inverterEffPct));
    const input = el("inverterEffPct");
    if (input && Number(input.value) !== inverterEffPct) input.value = String(inverterEffPct);
    if (overviewCache) renderOverview(overviewCache);
  }

  function mountInverterEffInput() {
    const input = el("inverterEffPct");
    if (!input || input.dataset.ready === "1") return;
    input.value = String(inverterEffPct);
    input.addEventListener("change", () => setInverterEffPct(input.value));
    input.addEventListener("keydown", (evt) => {
      if (evt.key !== "Enter") return;
      evt.preventDefault();
      setInverterEffPct(input.value);
      input.blur();
    });
    input.dataset.ready = "1";
  }

  function setStringMaxW(index, next) {
    const i = Number(index);
    if (!Number.isInteger(i) || i < 0 || i >= STRING_COUNT) return;
    stringMaxWs = stringMaxWs.slice();
    stringMaxWs[i] = clampStringMaxW(next);
    localStorage.setItem(STRING_MAX_KEY, JSON.stringify(stringMaxWs));
    const input = document.querySelector(`#stringMaxes input[data-string-index="${i}"]`);
    if (input && Number(input.value) !== stringMaxWs[i]) input.value = String(stringMaxWs[i]);
    if (overviewCache) renderOverview(overviewCache);
  }

  function mountStringMaxInputs() {
    const root = el("stringMaxes");
    if (!root || root.dataset.ready === "1") return;
    root.innerHTML = stringMaxWs
      .map(
        (maxW, i) => `<label class="string-max">
          <span class="smax-label">S${i + 1}</span>
          <input
            type="number"
            data-string-index="${i}"
            min="500"
            max="20000"
            step="250"
            value="${maxW}"
            inputmode="numeric"
            aria-label="String ${i + 1} max watts"
          />
          <span class="smax-unit">W</span>
        </label>`
      )
      .join("");
    root.addEventListener("change", (evt) => {
      const input = evt.target.closest("input[data-string-index]");
      if (!input) return;
      setStringMaxW(input.dataset.stringIndex, input.value);
    });
    root.addEventListener("keydown", (evt) => {
      const input = evt.target.closest("input[data-string-index]");
      if (!input || evt.key !== "Enter") return;
      evt.preventDefault();
      setStringMaxW(input.dataset.stringIndex, input.value);
      input.blur();
    });
    root.dataset.ready = "1";
  }

  /**
   * Clear→bright yellow for solar production.
   * Steps every 250 W up to maxW (per-string max, or string_max × string count for total).
   */
  function solarProduceStyle(watts, { maxW = DEFAULT_STRING_MAX_W, stepW = SOLAR_STEP_W } = {}) {
    const w = Math.max(0, Number(watts) || 0);
    if (w < 25) {
      return { producing: false, style: "--solar-step:0;--solar-a:0", bandW: 0 };
    }
    const steps = Math.max(1, Math.ceil(maxW / stepW));
    const step = Math.min(steps, Math.floor(w / stepW));
    // step 0 still producing a little: faint; full max: bright yellow
    const alpha = step <= 0 ? 0.18 : Math.min(1, 0.22 + step * (0.78 / steps));
    return {
      producing: true,
      style: `--solar-step:${step};--solar-a:${alpha.toFixed(3)};--solar-band:${step * stepW}`,
      bandW: step * stepW,
    };
  }

  /** Clear→bright green for grid export; steps every 1000 W. */
  function gridExportStyle(gridW) {
    const g = Number(gridW);
    if (!Number.isFinite(g) || g >= -50) {
      return { exporting: false, style: "--export-step:0;--export-a:0" };
    }
    const exportW = Math.abs(g);
    const step = Math.min(12, Math.floor(exportW / 1000)); // 0–999 → 0, 1000–1999 → 1, …
    // step 0 still exporting a little: faint; step 12 (~12 kW+): full bright green
    const alpha = step <= 0 ? 0.18 : Math.min(1, 0.22 + step * (0.78 / 12));
    return {
      exporting: true,
      style: `--export-step:${step};--export-a:${alpha.toFixed(3)};--export-kw:${(exportW / 1000).toFixed(2)}`,
    };
  }

  /**
   * Inverter feed (ch38/40): green while exporting to grid;
   * dark→white while powering house / charging batteries.
   */
  function inverterFeedStyle(totalW) {
    const gridW = Number(overviewCache?.power_flow?.grid_w);
    const exporting = Number.isFinite(gridW) ? gridW < -50 : false;
    if (exporting) {
      // Intensity from feed watts (or site export), 1 kW steps.
      const exportProxy = -Math.max(Math.abs(totalW), Math.abs(gridW));
      return {
        dirClass: "feed-export",
        style: gridExportStyle(exportProxy).style,
      };
    }
    return {
      dirClass: "feed-import",
      style: circuitLoadStyle(totalW, { maxW: 12000, stepW: 1000 }),
    };
  }

  function fmtKwh(value) {
    if (value == null) return "—";
    return `${Number(value).toFixed(2)} kWh`;
  }

  function setConn(ok, text) {
    const pill = el("connStatus");
    pill.textContent = text;
    pill.classList.toggle("ok", !!ok);
    pill.classList.toggle("bad", !ok);
  }

  function renderSiteSwitch(sites, defaultId) {
    sitesCache = sites || [];
    if (!activeSiteId || !sitesCache.some((s) => s.id === activeSiteId)) {
      activeSiteId = defaultId || sitesCache[0]?.id || "";
    }
    if (activeSiteId) localStorage.setItem("ecoflow_web_site", activeSiteId);

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
        loadOverview()
          .then(() =>
            Promise.all([
              loadEnergy(),
              loadCircuitEnergy(circuitEnergyHours),
              loadHistory(historyHours),
              loadOverheadHistory(),
            ])
          )
          .then(() => connectWs(true))
          .catch((err) => setConn(false, String(err.message || err)));
      });
    });
  }

  function renderOverview(data) {
    overviewCache = data;
    const label = data.site_label || data.site_id || "EcoFlow Ocean";
    el("pageTitle").textContent = label;
    syncNav();

    const flow = data.power_flow || {};
    if (window.EcoFlowFlow) {
      window.EcoFlowFlow.render(data);
    }

    const list = el("deviceList");
    const devices = data.devices || [];
    list.innerHTML = devices.length
      ? devices
          .map((d) => {
            const online = d.online === true ? "on" : d.online === false ? "off" : "";
            const status =
              d.online === true ? "online" : d.online === false ? "offline" : "unknown";
            return `<li>
          <div>
            <div class="name">${escapeHtml(d.name || d.kind)}</div>
            <div class="sn">${escapeHtml(d.serial)} · ${escapeHtml(d.kind)}${
              d.mqtt ? " · mqtt" : ""
            }</div>
          </div>
          <span class="badge ${online}">${status}</span>
        </li>`;
          })
          .join("")
      : `<li><div class="name">Not installed yet</div><div class="sn">When this home's Ocean gear is online, set SITE_${(
          data.site_id || ""
        ).toUpperCase()}_SERIALS (or its own EcoFlow login) and restart the container</div></li>`;

    renderSolarStrings(flow.solar_strings || []);
    renderCircuits(data.panel);
    renderComponentPower(flow);
    renderBatteryPacks(data.inverter?.state || {});
  }

  function renderBatteryPacks(invState) {
    const root = el("packStrip");
    const meta = el("packMeta");
    if (!root) return;
    const packs = Array.isArray(invState.battery_packs) ? invState.battery_packs : [];
    const bank = Number(invState.battery_soc);
    if (!packs.length) {
      root.innerHTML = `<div class="empty">Waiting for per-pack MQTT (bank ${
        Number.isFinite(bank) ? `${bank.toFixed(0)}%` : "—"
      })</div>`;
      if (meta) meta.textContent = "No pack detail yet";
      return;
    }
    if (meta) {
      meta.textContent = `${packs.length} pack${packs.length === 1 ? "" : "s"}${
        Number.isFinite(bank) ? ` · bank ${bank.toFixed(1)}%` : ""
      }`;
    }
    root.innerHTML = packs
      .map((p, i) => {
        const idx = Number(p.index) || i + 1;
        const soc = Number(p.soc);
        const watts = Number(p.power_w);
        const sn = String(p.sn || "").replace(/^P(\d+)$/, "slot $1");
        const snShort =
          sn.startsWith("slot ") || sn.length <= 6 ? sn : sn.slice(-4);
        const pct = Number.isFinite(soc) ? Math.max(0, Math.min(100, soc)) : 0;
        let dir = "idle";
        if (Number.isFinite(watts) && watts > 50) {
          dir = "charging";
        } else if (Number.isFinite(watts) && watts < -50) {
          dir = "discharging";
        }
        const wText =
          Number.isFinite(watts) && Math.abs(watts) > 20 ? fmtW(Math.abs(watts)) : dir;
        const volts = Number(p.voltage_v);
        const vText = Number.isFinite(volts) ? `${volts.toFixed(1)} V` : null;
        return `<div class="pack-tile ${dir}">
          <div class="pack-visual">
            <img src="/static/img/gear/battery-tower.png?v=20260722e" alt="" draggable="false" />
            <div class="pack-batt-track" aria-hidden="true">
              <div class="pack-batt-fill" style="--pct:${pct.toFixed(1)}%"></div>
            </div>
          </div>
          <div class="pack-info">
            <div class="clabel">Pack ${idx}${snShort ? ` · ${escapeHtml(snShort)}` : ""}</div>
            <div class="cval">${Number.isFinite(soc) ? `${soc.toFixed(1)}%` : "—"}</div>
            <div class="csub">${escapeHtml([wText, vText].filter(Boolean).join(" · "))}${
              p.soh != null ? ` · SOH ${Number(p.soh).toFixed(0)}%` : ""
            }</div>
          </div>
        </div>`;
      })
      .join("");
  }

  function renderComponentPower(flow) {
    const strip = el("componentStrip");
    const note = el("overheadNote");
    const live = el("overheadLive");
    if (!flow) return;

    const night = !!flow.overhead_night;
    const tiles = [
      ["Solar", flow.solar_w, "", ""],
      ["Battery", flow.battery_w, "", ""],
      [
        "Grid",
        flow.grid_w != null ? Math.abs(flow.grid_w) : null,
        "",
        flow.grid_voltage_l1_v != null && flow.grid_voltage_l2_v != null
          ? `${Number(flow.grid_voltage_l1_v).toFixed(0)} / ${Number(flow.grid_voltage_l2_v).toFixed(0)} V`
          : flow.grid_voltage_v != null
            ? `${Number(flow.grid_voltage_v).toFixed(0)} V`
            : "",
      ],
      ["House", flow.home_w, "", ""],
      ["Branch circuits", flow.branch_load_w, "", ""],
      ["Inverter feed", flow.inverter_feed_w != null ? Math.abs(flow.inverter_feed_w) : null, "", ""],
      ["System overhead", flow.system_overhead_w, "", ""],
      ["Panel aux", flow.panel_overhead_w, "live", ""],
      ["Inverter aux", flow.inverter_overhead_w, "live", ""],
    ];
    if (strip) {
      strip.innerHTML = tiles
        .map(([label, watts, cls, detail]) => {
          // "live" tiles (panel/inverter aux) are readings available any
          // time of day now — only dim them when there's genuinely no value.
          const stateCls = watts == null ? "dim" : cls === "live" ? "night" : cls;
          return `<div class="comp-tile ${stateCls}">
            <div class="clabel">${escapeHtml(label)}</div>
            <div class="cval">${watts == null ? "—" : fmtW(watts)}</div>
            ${detail ? `<div class="csub">${escapeHtml(detail)}</div>` : ""}
          </div>`;
        })
        .join("");
    }
    if (note) {
      note.textContent = flow.overhead_note || "—";
    }
    if (live) {
      const panelReady = flow.panel_overhead_w != null;
      const invReady = flow.inverter_overhead_w != null;
      live.innerHTML = `
        <div class="oh-card panel ${panelReady ? "ready" : ""}">
          <div class="clabel">Panel aux</div>
          <div class="cval">${flow.panel_overhead_w == null ? "—" : fmtW(flow.panel_overhead_w)}</div>
          <div class="csub">hall_total − channel_sum (panel reading)</div>
        </div>
        <div class="oh-card inverter ${invReady ? "ready" : ""}">
          <div class="clabel">Inverter aux</div>
          <div class="cval">${flow.inverter_overhead_w == null ? "—" : fmtW(flow.inverter_overhead_w)}</div>
          <div class="csub">solar − battery − feed${night ? " · night split" : ""}</div>
        </div>
        <div class="oh-card system">
          <div class="clabel">System overhead</div>
          <div class="cval">${flow.system_overhead_w == null ? "—" : fmtW(flow.system_overhead_w)}</div>
          <div class="csub">home − branch${
            flow.conversion_loss_est_w != null
              ? ` · conv≈${fmtW(flow.conversion_loss_est_w)}`
              : ""
          }</div>
        </div>`;
    }
  }

  function renderSolarStrings(strings) {
    const root = el("solarStrings");
    const meta = el("stringMeta");
    const summary = el("stringSummary");
    if (!root) return;
    if (summary) {
      const dcTotalW = totalStringMaxW();
      const acDeratedW = dcTotalW * (inverterEffPct / 100);
      summary.textContent =
        `DC array ${fmtW(dcTotalW)} nameplate → ` +
        `AC max ~${fmtW(acDeratedW)} @ ${inverterEffPct}% inverter eff.`;
    }
    const items = Array.isArray(strings) && strings.length ? strings : [1, 2, 3, 4, 5].map((id) => ({
      id,
      label: `String ${id}`,
      power_w: null,
      active: true,
    }));
    // CDO has five installed strings — always treat slots 1–5 as active.
    const normalized = [1, 2, 3, 4, 5].map((id) => {
      const found = items.find((s) => Number(s.id) === id) || {};
      return {
        id,
        label: found.label || `String ${id}`,
        power_w: found.power_w,
        active: true,
      };
    });
    const activeCount = normalized.filter((s) => Number(s.power_w) > 25).length;
    const total = normalized.reduce((sum, s) => sum + Math.max(Number(s.power_w) || 0, 0), 0);
    if (meta) {
      meta.textContent = `${activeCount}/5 producing · ${fmtW(total)}`;
    }
    root.innerHTML = normalized
      .map((s, idx) => {
        const watts = s.power_w;
        const maxW = stringMaxWs[idx] ?? DEFAULT_STRING_MAX_W;
        const look = solarProduceStyle(watts, { maxW, stepW: SOLAR_STEP_W });
        const band =
          watts == null
            ? "—"
            : look.producing
              ? `${look.bandW} W band · max ${maxW}`
              : `idle · max ${maxW}`;
        return `<div class="solar-string ${look.producing ? "producing" : ""}" style="${look.style}">
          <div class="cname">${escapeHtml(s.label || `String ${s.id}`)}</div>
          <div class="cw">${watts == null ? "—" : fmtW(watts)}</div>
          <div class="cmeta">${band}</div>
        </div>`;
      })
      .join("");
  }

  // CDO panel labels (fallback while waiting for MQTT config frames).
  // Only real names — unlabeled channels stay hidden unless "Show all" is on.
  const CDO_CIRCUIT_NAMES = {
    1: "Master Bedroom Plug",
    2: "Microwave",
    3: "Bedroom 2&3",
    4: "Refrigerator / Router",
    5: "Right side of House Lights Fans",
    6: "Washing machine",
    7: "Casita and Doorbell plugs",
    8: "Kitchen",
    9: "Garage",
    10: "Garage and Main vent fan",
    11: "Dishwasher",
    12: "Living room plugs",
    13: "Left side of House Lights Fans",
    14: "Living room",
    15: "Water heater",
    17: "Water heater",
    18: "Furnace",
    19: "Dryer",
    20: "A/C",
    21: "Dryer",
    22: "A/C",
    23: "EV charger",
    24: "Range",
    25: "OCEAN EV Charger",
    26: "Range",
    38: "Inverter feed L1",
    40: "Inverter feed L2",
  };
  const FORCE_ACTIVE_CIRCUITS = new Set([1, 2, 3, 4, 5]);

  function readShowAllCircuits() {
    const fromUrl = params.get("show_all_circuits");
    if (fromUrl === "1" || fromUrl === "true") return true;
    if (fromUrl === "0" || fromUrl === "false") return false;
    return localStorage.getItem("ecoflow_show_all_circuits") === "1";
  }

  let showAllCircuits = readShowAllCircuits();

  function setShowAllCircuits(next) {
    showAllCircuits = !!next;
    localStorage.setItem("ecoflow_show_all_circuits", showAllCircuits ? "1" : "0");
    const input = el("showAllCircuits");
    if (input) input.checked = showAllCircuits;
    if (overviewCache) renderOverview(overviewCache);
  }

  /** True when the circuit has a meaningful label (not blank / "Circuit 12"). */
  function hasCircuitLabel(name, id) {
    if (name == null) return false;
    const text = String(name).trim();
    if (!text) return false;
    if (/^circuit\s*\d+$/i.test(text)) return false;
    if (id != null && text.toLowerCase() === `circuit ${id}`.toLowerCase()) return false;
    return true;
  }

  function renderCircuits(panel) {
    const root = el("circuits");
    const state = panel?.state || {};
    const powers = normalizeCircuitMap(state.circuit_power_w || {});
    const mqttNames = normalizeCircuitMap(state.circuit_names || {});
    const names = {
      ...CDO_CIRCUIT_NAMES,
      ...mqttNames,
    };
    const active = normalizeCircuitMap(state.circuit_active || {});

    // CDO split-phase pairs (both legs + total).
    const splitPairs = [
      { a: 38, b: 40, title: "Inverter feed", feed: true },
      { a: 25, b: 23, title: "EV charger" },
      { a: 24, b: 26, title: "Range" },
      { a: 20, b: 22, title: "A/C" },
      { a: 19, b: 21, title: "Dryer" },
      { a: 16, b: 18, title: "Furnace" },
      { a: 15, b: 17, title: "Water heater" },
    ];
    const paired = new Set(splitPairs.flatMap((p) => [p.a, p.b]));

    const allIds = new Set([
      ...Object.keys(powers).map(Number),
      ...Object.keys(names).map(Number),
      ...Object.keys(mqttNames).map(Number),
      ...FORCE_ACTIVE_CIRCUITS,
    ]);

    const visiblePairs = splitPairs.filter((pair) => {
      if (showAllCircuits || pair.title) return true;
      return (
        hasCircuitLabel(names[pair.a], pair.a) || hasCircuitLabel(names[pair.b], pair.b)
      );
    });

    const singles = [...allIds]
      .filter((id) => !paired.has(id))
      .filter((id) => showAllCircuits || hasCircuitLabel(names[id], id))
      .sort((a, b) => a - b);

    const hiddenCount =
      splitPairs.length -
      visiblePairs.length +
      [...allIds].filter((id) => !paired.has(id) && !hasCircuitLabel(names[id], id)).length;

    el("panelMeta").textContent = panel
      ? showAllCircuits
        ? `${panel.name || "Panel"} · ${allIds.size} circuits · ${splitPairs.length} pairs`
        : `${panel.name || "Panel"} · ${visiblePairs.length + singles.length} labeled` +
          (hiddenCount ? ` · ${hiddenCount} hidden` : "")
      : "No panel device";

    if (!allIds.size) {
      root.innerHTML = `<div class="empty">No circuit telemetry yet. MQTT usually fills this within ~20s.</div>`;
      return;
    }

    if (!showAllCircuits && !visiblePairs.length && !singles.length) {
      root.innerHTML = `<div class="empty">No labeled circuits yet. Turn on “Show all” to list every channel.</div>`;
      return;
    }

    const pairHtml = visiblePairs
      .map((pair) => {
        const wattsA = circuitUsageW(powers[pair.a] ?? 0);
        const wattsB = circuitUsageW(powers[pair.b] ?? 0);
        const total = wattsA + wattsB;
        const nameA = names[pair.a] || `Circuit ${pair.a}`;
        const nameB = names[pair.b] || `Circuit ${pair.b}`;
        const title = pairTitle(pair, nameA, nameB);
        const isActive =
          active[pair.a] === true ||
          active[pair.b] === true ||
          wattsA > 5 ||
          wattsB > 5;
        const feedLook = pair.feed
          ? inverterFeedStyle(total)
          : {
              dirClass: "",
              style: circuitLoadStyle(total, { maxW: 6000, stepW: 1000 }),
            };
        return `<div class="circuit-pair ${isActive ? "active" : ""} ${
          pair.feed ? `feed ${feedLook.dirClass}` : ""
        }" style="${feedLook.style}">
          <div class="pair-head">
            <div class="cname">${escapeHtml(title)}</div>
            <div class="pair-ch">ch${pair.a}/${pair.b}</div>
          </div>
          <div class="pair-total">${fmtW(total)}</div>
          <div class="pair-legs">
            <div class="leg">
              <span>L1 · ch${pair.a}</span>
              <span class="leg-name">${escapeHtml(nameA)}</span>
              <strong>${fmtW(wattsA)}</strong>
            </div>
            <div class="leg">
              <span>L2 · ch${pair.b}</span>
              <span class="leg-name">${escapeHtml(nameB)}</span>
              <strong>${fmtW(wattsB)}</strong>
            </div>
          </div>
        </div>`;
      })
      .join("");

    const singleHtml = singles
      .map((id) => {
        const watts = circuitUsageW(powers[id] ?? 0);
        const name = names[id] || `Circuit ${id}`;
        const isActive =
          FORCE_ACTIVE_CIRCUITS.has(id) ||
          active[id] === true ||
          watts > 5;
        return `<div class="circuit ${isActive ? "active" : ""}" style="${circuitLoadStyle(watts, {
          maxW: 3000,
          stepW: 500,
        })}">
          <div class="cname">${escapeHtml(name)}</div>
          <div class="cmeta">ch${id}</div>
          <div class="cw">${fmtW(watts)}</div>
        </div>`;
      })
      .join("");

    root.innerHTML = `
      <div class="circuit-pairs">${pairHtml}</div>
      <div class="circuits-singles">${singleHtml}</div>
    `;
  }

  function normalizeCircuitMap(map) {
    const out = {};
    for (const [key, value] of Object.entries(map || {})) {
      out[Number(key)] = value;
    }
    return out;
  }

  function pairTitle(pair, nameA, nameB) {
    if (pair.title) return pair.title;
    const a = String(nameA || "").replace(/\s*L[12]\s*$/i, "").trim();
    const b = String(nameB || "").replace(/\s*L[12]\s*$/i, "").trim();
    if (a && b && a.toLowerCase() === b.toLowerCase()) return a;
    if (a && b) return `${a} / ${b}`;
    return a || b || `Circuits ${pair.a}/${pair.b}`;
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
    renderOverview(data);
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
    const data = await res.json();
    if (window.EcoFlowFlow) window.EcoFlowFlow.setEnergy(data);
    const totals = data.totals || {};
    const items = [
      ["Solar", totals.solar_kwh],
      ["Home", totals.home_kwh],
      ["Grid import", totals.grid_import_kwh],
      ["Grid export", totals.grid_export_kwh],
      ["Battery charge", totals.battery_charge_kwh],
      ["Battery discharge", totals.battery_discharge_kwh],
      ["EV charge", totals.ev_charge_kwh],
    ];
    el("energyTotals").innerHTML = items
      .map(
        ([label, value]) =>
          `<div class="energy-item"><span>${label}</span><strong>${fmtKwh(
            value
          )}</strong></div>`
      )
      .join("");
  }

  let circuitEnergyHours = 24;

  const CIRCUIT_SPLIT_PAIRS = [
    { a: 38, b: 40, title: "Inverter feed", feed: true },
    { a: 25, b: 23, title: "EV charger" },
    { a: 24, b: 26, title: "Range" },
    { a: 20, b: 22, title: "A/C" },
    { a: 19, b: 21, title: "Dryer" },
    { a: 16, b: 18, title: "Furnace" },
    { a: 15, b: 17, title: "Water heater" },
  ];

  function circuitNameMap() {
    const mqttNames = normalizeCircuitMap(
      overviewCache?.panel?.state?.circuit_names || {}
    );
    return { ...CDO_CIRCUIT_NAMES, ...mqttNames };
  }

  function groupCircuitEnergy(circuits) {
    const byCh = new Map(
      (circuits || []).map((c) => [Number(c.channel), c])
    );
    const paired = new Set(CIRCUIT_SPLIT_PAIRS.flatMap((p) => [p.a, p.b]));
    const rows = [];

    for (const pair of CIRCUIT_SPLIT_PAIRS) {
      const a = byCh.get(pair.a);
      const b = byCh.get(pair.b);
      if (!a && !b) continue;
      const kwh = (a?.kwh || 0) + (b?.kwh || 0);
      const avgW = (a?.avg_w || 0) + (b?.avg_w || 0);
      rows.push({
        key: `p-${pair.a}-${pair.b}`,
        name: pair.title,
        channels: `ch${pair.a}/${pair.b}`,
        kwh,
        avg_w: avgW,
        feed: !!pair.feed,
      });
    }

    const names = circuitNameMap();
    for (const c of circuits || []) {
      const ch = Number(c.channel);
      if (paired.has(ch)) continue;
      if (!showAllCircuits && !hasCircuitLabel(names[ch], ch) && !(c.kwh > 0.01)) {
        continue;
      }
      rows.push({
        key: `c-${ch}`,
        name: names[ch] || `Circuit ${ch}`,
        channels: `ch${ch}`,
        kwh: Number(c.kwh) || 0,
        avg_w: Number(c.avg_w) || 0,
        feed: !!c.feed,
      });
    }

    rows.sort((a, b) => b.kwh - a.kwh || a.name.localeCompare(b.name));
    return rows;
  }

  function renderCircuitEnergy(data) {
    const root = el("circuitUsage");
    const meta = el("circuitEnergyMeta");
    if (!root) return;

    const sampleCount = Number(data.sample_count) || 0;
    const branch = Number(data.branch_kwh) || 0;
    const feed = Number(data.feed_kwh) || 0;
    const hours = Number(data.hours) || circuitEnergyHours;
    const windowLabel =
      hours >= 700 ? "30d" : hours >= 160 ? "7d" : `${Math.round(hours)}h`;

    if (sampleCount < 2) {
      if (meta) {
        meta.textContent =
          "Collecting samples — usage appears after a few minutes of live data.";
      }
      root.innerHTML = `<div class="empty">No circuit history yet for this window.</div>`;
      return;
    }

    const rows = groupCircuitEnergy(data.circuits || []);
    const maxKwh = Math.max(...rows.map((r) => r.kwh), 0.001);
    if (meta) {
      meta.textContent = `${windowLabel} · ${sampleCount} samples · branch ${fmtKwh(
        branch
      )}${feed > 0.001 ? ` · feed ${fmtKwh(feed)}` : ""}`;
    }
    if (!rows.length) {
      root.innerHTML = `<div class="empty">No labeled circuit usage in this window.</div>`;
      return;
    }

    root.innerHTML = rows
      .map((r) => {
        const pct = Math.min(100, (r.kwh / maxKwh) * 100);
        return `<div class="usage-row ${r.feed ? "feed" : ""}">
          <div>
            <div class="uname">${escapeHtml(r.name)}</div>
            <div class="uch">${escapeHtml(r.channels)}</div>
          </div>
          <div class="ukwh">${fmtKwh(r.kwh)}</div>
          <div class="uavg">${fmtW(r.avg_w)} avg</div>
          <div class="usage-bar" aria-hidden="true"><span style="--pct:${pct.toFixed(
            1
          )}%"></span></div>
        </div>`;
      })
      .join("");
  }

  async function loadCircuitEnergy(hours) {
    if (hours != null) circuitEnergyHours = Number(hours) || 24;
    const qs = new URLSearchParams({ hours: String(circuitEnergyHours) });
    if (activeSiteId) qs.set("site", activeSiteId);
    const panel = overviewCache?.panel?.serial;
    if (panel) qs.set("serial", panel);
    const res = await fetch(`/api/history/circuits?${qs}`, { headers });
    if (!res.ok) throw new Error(`circuits ${res.status}`);
    const data = await res.json();
    renderCircuitEnergy(data);
    return data;
  }

  async function loadHistory(hours) {
    historyHours = hours;
    const qs = new URLSearchParams({
      hours: String(hours),
      bucket_minutes: hours > 48 ? "30" : "5",
    });
    if (activeSiteId) qs.set("site", activeSiteId);
    const inverter = overviewCache?.inverter?.serial;
    if (inverter) qs.set("serial", inverter);
    const res = await fetch(`/api/history/power?${qs}`, { headers });
    if (!res.ok) throw new Error(`history ${res.status}`);
    const data = await res.json();
    const points = data.points || [];
    const labels = points.map((p) =>
      new Date(p.ts * 1000).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    );

    const datasets = [
      { key: "solar_w", label: "Solar", color: "#f0b429" },
      { key: "home_w", label: "Home", color: "#e07a5f" },
      { key: "grid_w", label: "Grid", color: "#5b9fd4" },
      { key: "battery_w", label: "Battery", color: "#3ecf8e" },
    ].map((d) => ({
      label: d.label,
      data: points.map((p) => p[d.key]),
      borderColor: d.color,
      backgroundColor: "transparent",
      tension: 0.25,
      pointRadius: 0,
      borderWidth: 2,
    }));

    const ctx = el("historyChart").getContext("2d");
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: "#c9d6e4" } },
        },
        scales: {
          x: {
            ticks: { color: "#8fa3b8", maxTicksLimit: 8 },
            grid: { color: "rgba(45,59,76,0.6)" },
          },
          y: {
            ticks: { color: "#8fa3b8" },
            grid: { color: "rgba(45,59,76,0.6)" },
            title: { display: true, text: "Watts", color: "#8fa3b8" },
          },
        },
      },
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function loadOverheadHistory() {
    const canvas = el("overheadChart");
    if (!canvas || typeof Chart === "undefined") return;
    const qs = new URLSearchParams({ hours: "18", bucket_minutes: "5" });
    if (activeSiteId) qs.set("site", activeSiteId);
    const res = await fetch(`/api/history/overhead?${qs}`, { headers });
    if (!res.ok) throw new Error(`overhead ${res.status}`);
    const data = await res.json();
    const points = data.points || [];
    const stats = data.overhead_stats || {};
    const meta = el("overheadMeta");
    if (meta) {
      const n = stats.sample_buckets || 0;
      if (n < 2) {
        meta.textContent = "Tracking every ~30s — waiting for enough readings to summarize";
      } else {
        meta.textContent = `Last 18h · ${n} buckets · panel med ${
          stats.panel_overhead_w_median != null
            ? fmtW(stats.panel_overhead_w_median)
            : "—"
        } · inverter med ${
          stats.inverter_overhead_w_median != null
            ? fmtW(stats.inverter_overhead_w_median)
            : "—"
        }`;
      }
    }

    const labels = points.map((p) =>
      new Date(p.ts * 1000).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    );
    const datasets = [
      { key: "panel_overhead_w", label: "Panel aux", color: "#9b7ede" },
      { key: "inverter_overhead_w", label: "Inverter aux", color: "#5b9fd4" },
      { key: "system_overhead_w", label: "System overhead", color: "#f0b429" },
      { key: "branch_w", label: "Branch load", color: "#e07a5f" },
    ].map((d) => ({
      label: d.label,
      data: points.map((p) => p[d.key]),
      borderColor: d.color,
      backgroundColor: "transparent",
      tension: 0.25,
      pointRadius: 0,
      borderWidth: 2,
      spanGaps: true,
    }));

    const ctx = canvas.getContext("2d");
    if (overheadChart) overheadChart.destroy();
    overheadChart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: "#c9d6e4" } },
        },
        scales: {
          x: {
            ticks: { color: "#8fa3b8", maxTicksLimit: 8 },
            grid: { color: "rgba(45,59,76,0.6)" },
          },
          y: {
            ticks: { color: "#8fa3b8" },
            grid: { color: "rgba(45,59,76,0.6)" },
            title: { display: true, text: "Watts", color: "#8fa3b8" },
          },
        },
      },
    });
  }

  async function bootstrap() {
    syncNav();
    const health = await fetch("/api/health");
    const healthData = await health.json();
    if (!healthData.ok) {
      setConn(false, healthData.error || "Backend not ready");
    }
    await loadSites();
    await loadOverview();
    await Promise.all([
      loadEnergy(),
      loadCircuitEnergy(circuitEnergyHours),
      loadHistory(historyHours),
      loadOverheadHistory(),
    ]);
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
          renderOverview(msg.data);
        }
      } catch (_) {
        /* ignore */
      }
    };
  }

  el("refreshEnergy").addEventListener("click", () => {
    loadEnergy().catch((err) => console.warn(err));
    loadCircuitEnergy(circuitEnergyHours).catch((err) => console.warn(err));
  });
  mountStringMaxInputs();
  mountInverterEffInput();
  if (window.EcoFlowFlow) window.EcoFlowFlow.start();
  const showAllCircuitsInput = el("showAllCircuits");
  if (showAllCircuitsInput) {
    showAllCircuitsInput.checked = showAllCircuits;
    showAllCircuitsInput.addEventListener("change", () => {
      setShowAllCircuits(showAllCircuitsInput.checked);
      loadCircuitEnergy(circuitEnergyHours).catch(() => {});
    });
  }
  const historyTabs = el("historyTabs");
  if (historyTabs) {
    historyTabs.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        historyTabs.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        loadHistory(Number(btn.dataset.hours)).catch((err) => console.warn(err));
      });
    });
  }
  const circuitTabs = el("circuitEnergyTabs");
  if (circuitTabs) {
    circuitTabs.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        circuitTabs.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        loadCircuitEnergy(Number(btn.dataset.hours)).catch((err) => console.warn(err));
      });
    });
  }

  bootstrap()
    .then(() => connectWs(true))
    .catch((err) => {
      console.error(err);
      setConn(false, String(err.message || err));
    });

  setInterval(() => {
    loadEnergy().catch(() => {});
    loadCircuitEnergy(circuitEnergyHours).catch(() => {});
    loadHistory(historyHours).catch(() => {});
    loadOverheadHistory().catch(() => {});
    loadSites().catch(() => {});
  }, 60000);
})();
