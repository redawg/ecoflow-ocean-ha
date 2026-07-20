/**
 * Shared animated power-flow widget (Sunsynk-style).
 * Requires #flowSvg markup from the page. Parent feeds overview + energy.
 */
(() => {
  const el = (id) => document.getElementById(id);

  // `unit` is each rail's dominant direction (unit vector, start→end of its
  // path `d`) and doubles as the "forward"/non-reversed flow direction.
  // `period` matches the repeat length baked into that channel's
  // <linearGradient> x1/y1/x2/y2 in the page markup, so translating the
  // gradient by exactly one period (in the tick loop below) loops seamlessly.
  // `t` (the oscillation clock, advanced in tick()) is intentionally omitted
  // here — it starts undefined and tick() treats that as 0 on first use.
  const channels = {
    solarInv: {
      pathId: "railSolarInv", on: false, reverse: false, watts: 0, kind: "solar", maxW: 15000,
      unit: { x: 0, y: 1 }, period: 40, gradIds: ["gradSolarInv"],
    },
    batInv: {
      pathId: "railBatInv", on: false, reverse: false, watts: 0, kind: "battery", maxW: 10000,
      unit: { x: 1, y: 0 }, period: 40, gradIds: ["gradBatDischarge", "gradBatCharge"],
    },
    invPanel: {
      pathId: "railInvPanel", on: false, reverse: false, watts: 0, kind: "panel", maxW: 15000,
      unit: { x: 1, y: 0 }, period: 40, gradIds: ["gradInvPanelSolar", "gradInvPanelBattery"],
    },
    panelGrid: {
      pathId: "railPanelGrid", on: false, reverse: false, watts: 0, kind: "export", maxW: 15000,
      unit: { x: 0.7431, y: -0.6688 }, period: 40,
      gradIds: ["gradGridExport", "gradGridImport", "gradGridExportMixed", "gradGridExportSolar"],
    },
    panelHouse: {
      // House now sits directly below the Panel hub — straight vertical rail.
      pathId: "railPanelHouse", on: false, reverse: false, watts: 0, kind: "home", maxW: 12000,
      unit: { x: 0, y: 1 }, period: 40, gradIds: ["gradPanelHouse"],
    },
    panelEv: {
      pathId: "railPanelEv", on: false, reverse: false, watts: 0, kind: "ev", maxW: 12000,
      unit: { x: 0.7182, y: 0.6958 }, period: 40, gradIds: ["gradPanelEv"],
    },
  };

  let energyCache = null;
  let overviewCache = null;
  let showDaily = localStorage.getItem("ecoflow_flow_daily") !== "0";
  let animFrame = 0;
  let lastTs = 0;
  let started = false;

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

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  // Pixels/second the color band travels along the line — faster as
  // wattage climbs toward the channel's maxW.
  function flowPxPerSec(watts, maxW) {
    const t = Math.min(1, Math.abs(watts) / maxW);
    return 16 + t * 70;
  }

  function lineWidth(watts, maxW) {
    const t = Math.min(1, Math.abs(watts) / maxW);
    return 4 + t * 7;
  }

  function setStroke(id, ch) {
    const path = el(id);
    if (!path) return;
    const kindClass =
      ch.kind === "battery" && ch.reverse === false && ch.watts < 0
        ? "battery discharge"
        : ch.kind === "export" ||
            ch.kind === "import" ||
            ch.kind === "export-mixed" ||
            ch.kind === "export-solar"
          ? `grid ${ch.kind}`
          : ch.kind === "battery" && ch.watts > 0
            ? "battery"
            : ch.kind === "battery"
              ? "battery discharge"
              : ch.kind;
    path.className.baseVal = `flow-stroke ${kindClass}`;
    path.classList.toggle("on", !!ch.on);
    path.classList.toggle("reverse", !!ch.reverse);
    path.style.strokeWidth = ch.on ? `${lineWidth(ch.watts, ch.maxW)}` : "3";
  }

  function setLabel(id, watts, on) {
    const node = el(id);
    if (!node) return;
    node.textContent = on ? fmtW(Math.abs(watts)) : "";
    node.classList.toggle("on", !!on);
  }

  function updateChannel(key, { on, reverse, watts, kind }) {
    const ch = channels[key];
    ch.on = !!on;
    ch.reverse = !!reverse;
    ch.watts = Number(watts) || 0;
    if (kind) ch.kind = kind;
  }

  // Sweeps each active channel's gradient smoothly back and forth along its
  // line, in the direction of power flow, and writes it to the
  // <linearGradient>'s gradientTransform.
  //
  // Each gradient is a single pad-clamped (no spreadMethod="repeat") fade
  // spanning the *entire* visible line (see sizeGradients()), so a hard
  // modulo-wrapped translate would eventually slide the whole fade past the
  // line and then snap back — an instant flash from solid white back to
  // "colored start", which is exactly the "trickle" artifact reported
  // earlier. A smooth sine excursion has no such jump (it's continuous by
  // construction), so it's safe to size it as a large, clearly-visible
  // fraction of the line's length — small excursions (as this used to be)
  // just aren't perceptible as "flowing" at all.
  function tick(ts) {
    animFrame = requestAnimationFrame(tick);
    if (!lastTs) lastTs = ts;
    const dt = Math.min(0.05, (ts - lastTs) / 1000);
    lastTs = ts;
    for (const ch of Object.values(channels)) {
      if (!ch.on) continue;
      const dir = ch.reverse ? -1 : 1;
      const speed = flowPxPerSec(ch.watts, ch.maxW);
      // Sweep roughly a third of the line's length (capped so very long
      // lines don't swing so far the fade reads as disconnected from its
      // anchored end) — clearly visible motion either way.
      const amplitude = Math.min(ch.period * 0.32, 90);
      // Angular rate is driven by wattage-derived speed but kept independent
      // of amplitude (a fixed reference length) so higher power still
      // visibly flows faster instead of just swinging further at the same
      // rate.
      ch.t = (ch.t || 0) + dir * speed * dt;
      const phase = amplitude * (1 + Math.sin(ch.t / 55));
      const tx = (ch.unit.x * phase).toFixed(2);
      const ty = (ch.unit.y * phase).toFixed(2);
      for (const gradId of ch.gradIds) {
        const grad = document.getElementById(gradId);
        if (grad) grad.setAttribute("gradientTransform", `translate(${tx} ${ty})`);
      }
    }
  }

  function renderPvStrings(strings) {
    const g = el("pvStrings");
    if (!g) return;
    const items = [1, 2, 3, 4, 5].map((id) => {
      const found = (strings || []).find((s) => Number(s.id) === id) || {};
      return { id, power_w: Number(found.power_w) || 0 };
    });
    const gap = 96;
    const solarX = 420;
    const startX = solarX - ((items.length - 1) * gap) / 2;
    // Chips sit at y=48 (not the viewBox edge) so their rounded-rect top
    // (y=48-24=24) keeps real headroom above y=0 — the glow filter's blur
    // plus any browser sub-pixel rounding on the viewBox→viewport scale
    // otherwise had almost nothing to work with at the old y=36 (top=12).
    const chipY = 48;
    const chipHalfW = 40;
    const chipBottom = chipY + 20; // rect spans y -24..+20
    const busY = chipBottom + 14; // shared horizontal "manifold" the per-string drops feed into
    const solarTopY = 90; // Solar node's top edge (see node-g.solar rect)
    const anyOn = items.some((s) => s.power_w > 25);

    const chips = items
      .map((s, i) => {
        const x = startX + i * gap;
        const on = s.power_w > 25;
        // Per-string right-angle drop from the panel's junction-box port down
        // to the shared bus, matching the L-shaped routing used everywhere
        // else in the diagram instead of a diagonal line.
        const drop = on
          ? `<path class="flow-stroke solar on" d="M${x} ${chipBottom + 5} L${x} ${busY}" style="stroke-width:2" />`
          : `<path class="flow-rails" d="M${x} ${chipBottom + 5} L${x} ${busY}" style="stroke:rgba(80,100,120,0.25);stroke-width:1.5;fill:none" />`;
        // Solar-panel look: rectangular (near-square-cornered) frame with a
        // couple of cell-divider lines, plus a grey junction-box "port" nub
        // at the bottom-center — the same connector language (portPath) used
        // for every other node box on this page.
        return `${drop}
          <g class="pv-chip ${on ? "on" : ""}" transform="translate(${x} ${chipY})">
            <rect class="pv-frame" x="-${chipHalfW}" y="-24" width="${chipHalfW * 2}" height="44" rx="4" />
            <line class="pv-cell-line" x1="-${chipHalfW - 10}" y1="-24" x2="-${chipHalfW - 10}" y2="20" />
            <line class="pv-cell-line" x1="${chipHalfW - 10}" y1="-24" x2="${chipHalfW - 10}" y2="20" />
            <path class="pv-port flow-port" d="${portPath(0, 20, "down")}" />
            <text class="pv-label" y="-8">PV${s.id}</text>
            <text class="pv-value" y="11">${fmtW(s.power_w)}</text>
          </g>`;
      })
      .join("");

    // One shared bus line across all string drop-points, then a single
    // right-angle drop into the Solar node's top port — a clean "manifold"
    // instead of five lines converging on a point via diagonals.
    const busClass = anyOn ? "flow-stroke solar on" : "flow-rails";
    const busStyle = anyOn
      ? 'style="stroke-width:2"'
      : 'style="stroke:rgba(80,100,120,0.25);stroke-width:1.5;fill:none"';
    const firstX = startX;
    const lastX = startX + (items.length - 1) * gap;
    const bus = `<path class="${busClass}" d="M${firstX} ${busY} L${lastX} ${busY}" ${busStyle} />
      <path class="${busClass}" d="M${solarX} ${busY} L${solarX} ${solarTopY}" ${busStyle} />`;

    g.innerHTML = bus + chips;
  }

  function renderPacks(packs) {
    const g = el("packRow");
    if (!g) return;
    if (!Array.isArray(packs) || !packs.length) {
      g.innerHTML = "";
      return;
    }
    // Sized/spaced (and clamped) so 4 chips centered under the Battery node
    // (x=150) never run off the left edge of the viewBox (x=0) or collide
    // with the Inverter node's left edge (x≈335) to the right.
    const gap = 74;
    const chipHalfWidth = 33;
    const count = Math.min(packs.length, 4);
    const rawStartX = 150 - ((count - 1) * gap) / 2;
    // +20 (not just +chipHalfWidth) so the leftmost chip keeps real margin
    // from the viewBox edge (x=0) instead of sitting right on top of it.
    const startX = Math.max(chipHalfWidth + 20, rawStartX);
    g.innerHTML = packs
      .slice(0, 4)
      .map((p, i) => {
        const x = startX + i * gap;
        const soc = num(p.soc);
        const sn = String(p.sn || "").slice(-4);
        return `<g class="pack-chip" transform="translate(${x} 520)">
          <rect x="-33" y="-18" width="66" height="36" rx="8" />
          <text y="-2">P${p.index || i + 1} · ${escapeHtml(sn)}</text>
          <text y="13">${soc != null ? `${soc.toFixed(0)}%` : "—"}</text>
        </g>`;
      })
      .join("");
  }

  function splitLoads(flow) {
    const homeTotal = Math.max(0, num(flow.home_w) ?? 0);
    const ev = Math.max(0, num(flow.ev_charge_w) ?? 0);
    return { essential: Math.max(0, homeTotal - ev), ev, homeTotal };
  }

  function setText(id, text) {
    const node = el(id);
    if (node) node.textContent = text;
  }

  function render(data) {
    if (!el("flowSvg") || !data) return;
    overviewCache = data;
    const flow = data.power_flow || {};
    const invState = data.inverter?.state || {};
    const solar = Math.max(0, num(flow.solar_w) ?? 0);
    const battery = num(flow.battery_w) ?? 0;
    const grid = num(flow.grid_w);
    const soc = num(flow.soc);
    const loads = splitLoads(flow);
    const energy = energyCache?.totals || {};

    renderPvStrings(flow.solar_strings || []);
    renderPacks(invState.battery_packs || []);

    setText("nSolarW", fmtW(solar));
    setText("nSolarDay", `today ${fmtKwh(energy.solar_kwh)}`);

    setText("nBatW", fmtW(Math.abs(battery)));
    setText(
      "nBatDir",
      battery > 50 ? "charging" : battery < -50 ? "discharging" : "idle"
    );
    setText("nBatSoc", soc != null ? `SOC ${soc.toFixed(1)}%` : "SOC —");
    setText(
      "nBatDay",
      `↑ ${fmtKwh(energy.battery_charge_kwh)}  ↓ ${fmtKwh(energy.battery_discharge_kwh)}`
    );
    const socFill = el("socFill");
    if (socFill) {
      const pct = Math.max(0, Math.min(100, soc ?? 0));
      socFill.setAttribute("width", String((160 * pct) / 100));
    }

    const home = loads.homeTotal || 1;
    const importW = grid != null && grid > 0 ? grid : 0;
    const self = Math.max(0, Math.min(100, 100 * (1 - importW / Math.max(home, 1))));
    const invAux = flow.inverter_overhead_w;
    setText(
      "nSelfSuf",
      `Self Sufficient ~${self.toFixed(0)}%${invAux != null ? ` · ${fmtW(invAux)} inverter self-use` : ""}`
    );

    const panelState = data.panel?.state || {};
    const circuitPowers = Object.values(panelState.circuit_power_w || {});
    const activeCircuits = circuitPowers.filter((w) => Number(w) > 5).length;
    setText("nPanelMode", "Smart Panel");
    setText(
      "nPanelOnline",
      data.panel?.online === true
        ? "online"
        : data.panel?.online === false
          ? "offline"
          : "—"
    );
    setText(
      "nPanelLoad",
      activeCircuits ? `${activeCircuits} circuit${activeCircuits === 1 ? "" : "s"} active` : "circuits idle"
    );

    const gridAbs = Math.abs(grid ?? 0);
    setText("nGridW", fmtW(gridAbs));
    let gridDir = "balanced";
    if (grid != null && grid < -50) gridDir = "exporting";
    else if (grid != null && grid > 50) gridDir = "importing";
    setText("nGridDir", gridDir);
    setText("nGridImport", `import ${fmtKwh(energy.grid_import_kwh)}`);
    setText("nGridExport", `export ${fmtKwh(energy.grid_export_kwh)}`);

    setText("nHomeW", fmtW(loads.essential));
    setText("nHomeHint", loads.ev > 25 ? "essential (ex-EV)" : "essential load");
    setText("nHomeDay", `today ${fmtKwh(energy.home_kwh)}`);

    setText("nEvW", fmtW(loads.ev));
    const evBits = [];
    if (flow.vehicle_connected === true) evBits.push("vehicle in");
    if (flow.charging_active === true) evBits.push("charging");
    setText("nEvSub", evBits.join(" · ") || "non-essential");
    setText("nEvDay", `today ${fmtKwh(energy.ev_charge_kwh)}`);

    setText(
      "flowMeta",
      flow.updated_at
        ? `Updated ${new Date(flow.updated_at).toLocaleString()}`
        : "Waiting for telemetry…"
    );

    document.querySelector(".node-g.solar")?.classList.toggle("active", solar > 25);
    const batNode = document.querySelector(".node-g.battery");
    batNode?.classList.toggle("charging", battery > 50);
    batNode?.classList.toggle("discharging", battery < -50);
    const gridNode = document.querySelector(".node-g.grid");
    gridNode?.classList.toggle("exporting", grid != null && grid < -50);
    gridNode?.classList.toggle("importing", grid != null && grid > 50);
    // Tint the Grid node yellow (instead of green) while solar is a
    // meaningful contributor to the export, so the node matches the
    // yellow flow running through the rest of the system.
    gridNode?.classList.toggle("solar-sourced", grid != null && grid < -50 && solar > 25);
    document.querySelector(".node-g.home")?.classList.toggle("active", loads.essential > 25);
    document.querySelector(".node-g.ev")?.classList.toggle("active", loads.ev > 25);
    document.querySelector(".node-g.inverter")?.classList.toggle("online", flow.online === true);

    const invOut = Math.max(0, solar - battery);
    const dischargingActive = battery < -50;
    updateChannel("solarInv", { on: solar > 25, reverse: false, watts: solar, kind: "solar" });
    updateChannel("batInv", {
      on: Math.abs(battery) > 50,
      reverse: battery > 50,
      watts: battery,
      kind: "battery",
    });
    // While the battery is discharging, keep that green flow visually
    // continuing through the inverter into the panel instead of switching to
    // the default solar-sourced yellow — the yellow leg intentionally
    // continues unbroken from Solar through the Inverter and Panel when
    // solar is the source, all the way out to the Grid export below.
    updateChannel("invPanel", {
      on: invOut > 25,
      reverse: false,
      watts: invOut,
      kind: dischargingActive ? "panel-battery" : "panel",
    });
    const exporting = grid != null && grid < -50;
    const importing = grid != null && grid > 50;
    // Export currently drawing from both solar and a discharging battery at
    // once — surface that as a dual-color line instead of a single color.
    // Pure solar → yellow (continuing the same leg from Solar/Inverter/Panel);
    // pure battery discharge → green; both at once → yellow+green mixed.
    const exportMixed = exporting && solar > 25 && dischargingActive;
    const exportSolarOnly = exporting && solar > 25 && !dischargingActive;
    let exportKind = "export";
    if (exportMixed) exportKind = "export-mixed";
    else if (exportSolarOnly) exportKind = "export-solar";
    updateChannel("panelGrid", {
      on: exporting || importing,
      reverse: importing,
      watts: gridAbs,
      kind: exporting ? exportKind : "import",
    });
    updateChannel("panelHouse", {
      on: loads.essential > 25,
      reverse: false,
      watts: loads.essential,
      kind: "home",
    });
    updateChannel("panelEv", {
      on: loads.ev > 25,
      reverse: false,
      watts: loads.ev,
      kind: "ev",
    });
    document
      .querySelector(".node-g.panel")
      ?.classList.toggle("active", invOut > 25 || loads.essential > 25 || loads.ev > 25 || gridAbs > 25);

    setStroke("strokeSolarInv", channels.solarInv);
    setStroke("strokeBatInv", channels.batInv);
    setStroke("strokeInvPanel", channels.invPanel);
    setStroke("strokePanelGrid", channels.panelGrid);
    setStroke("strokePanelHouse", channels.panelHouse);
    setStroke("strokePanelEv", channels.panelEv);

    setLabel("lblSolarInv", solar, solar > 25);
    setLabel("lblBatInv", battery, Math.abs(battery) > 50);
    setLabel("lblInvPanel", invOut, invOut > 25);
    setLabel("lblPanelGrid", gridAbs, exporting || importing);
    setLabel("lblPanelHouse", loads.essential, loads.essential > 25);
    setLabel("lblPanelEv", loads.ev, loads.ev > 25);
  }

  function setEnergy(energy) {
    energyCache = energy;
    if (overviewCache) render(overviewCache);
  }

  function setDailyVisible(on) {
    showDaily = !!on;
    localStorage.setItem("ecoflow_flow_daily", showDaily ? "1" : "0");
    document.body.classList.toggle("hide-daily", !showDaily);
    const input = el("showDaily");
    if (input) input.checked = showDaily;
  }

  // Stretch each channel's gradient(s) to span that channel's actual
  // on-screen path length (measured via SVG getTotalLength() on the rail,
  // which shares the same `d` as the animated stroke overlay) instead of
  // the short placeholder vector baked into the markup. That makes the
  // color→white fade run once, smoothly, across the *whole* visible line
  // instead of repeating in short bands along it.
  // Draws each connector as a small plug sitting astride the node box's
  // edge: flush/square on the side touching the box, rounded on the side
  // facing away from it (into the line). SVG <rect> can't have per-corner
  // radii, so the shape is built as an explicit path from each port's
  // data-orient ("up"/"down"/"left"/"right" = the outward-facing side).
  function portPath(cx, cy, orient) {
    const W = 16; // extent along the box edge (the flush side)
    const D = 10; // extent perpendicular to the edge (straddles it)
    const r = 4; // corner radius on the two outward corners
    const halfW = W / 2;
    const halfD = D / 2;
    if (orient === "up" || orient === "down") {
      const left = cx - halfW;
      const right = cx + halfW;
      const top = cy - halfD;
      const bottom = cy + halfD;
      if (orient === "up") {
        return `M${left} ${bottom} L${left} ${top + r} Q${left} ${top} ${left + r} ${top} L${right - r} ${top} Q${right} ${top} ${right} ${top + r} L${right} ${bottom} Z`;
      }
      return `M${left} ${top} L${right} ${top} L${right} ${bottom - r} Q${right} ${bottom} ${right - r} ${bottom} L${left + r} ${bottom} Q${left} ${bottom} ${left} ${bottom - r} Z`;
    }
    const left = cx - halfD;
    const right = cx + halfD;
    const top = cy - halfW;
    const bottom = cy + halfW;
    if (orient === "left") {
      return `M${right} ${top} L${left + r} ${top} Q${left} ${top} ${left} ${top + r} L${left} ${bottom - r} Q${left} ${bottom} ${left + r} ${bottom} L${right} ${bottom} Z`;
    }
    return `M${left} ${top} L${right - r} ${top} Q${right} ${top} ${right} ${top + r} L${right} ${bottom - r} Q${right} ${bottom} ${right - r} ${bottom} L${left} ${bottom} Z`;
  }

  function renderPorts() {
    document.querySelectorAll(".flow-port").forEach((port) => {
      const cx = Number(port.getAttribute("data-cx"));
      const cy = Number(port.getAttribute("data-cy"));
      const orient = port.getAttribute("data-orient");
      if (!Number.isFinite(cx) || !Number.isFinite(cy) || !orient) return;
      port.setAttribute("d", portPath(cx, cy, orient));
    });
  }

  function sizeGradients() {
    for (const ch of Object.values(channels)) {
      const rail = el(ch.pathId);
      if (!rail || typeof rail.getTotalLength !== "function") continue;
      let length = 0;
      try {
        length = rail.getTotalLength();
      } catch {
        continue;
      }
      if (!Number.isFinite(length) || length <= 0) continue;
      ch.period = length;
      // Anchor the gradient to the rail's actual start point in SVG user
      // space — NOT a hardcoded (0,0) — since every path here lives at its
      // own absolute coordinates (e.g. the Inverter→Panel rail sits around
      // x≈505-600, nowhere near the origin). x1/y1=0 previously meant the
      // gradient's 0%→100% span never overlapped the visible path at all;
      // it only "worked" by accident because spreadMethod="repeat" tiled
      // the pattern back into range. That tiling is exactly what produced
      // the "trickle" once the gradient was animated: an extra tile
      // boundary could land mid-path, showing a stray colored band. With
      // the vector anchored correctly, no repeat/tiling is needed at all.
      let start = { x: 0, y: 0 };
      try {
        start = rail.getPointAtLength(0);
      } catch {
        // fall back to origin if unsupported — shouldn't happen in any
        // real browser.
      }
      const x1 = start.x.toFixed(2);
      const y1 = start.y.toFixed(2);
      const x2 = (start.x + ch.unit.x * length).toFixed(2);
      const y2 = (start.y + ch.unit.y * length).toFixed(2);
      for (const gradId of ch.gradIds) {
        const grad = document.getElementById(gradId);
        if (!grad) continue;
        grad.setAttribute("x1", x1);
        grad.setAttribute("y1", y1);
        grad.setAttribute("x2", x2);
        grad.setAttribute("y2", y2);
      }
    }
  }

  function start() {
    if (started || !el("flowSvg")) return;
    started = true;
    const dailyInput = el("showDaily");
    if (dailyInput) {
      dailyInput.checked = showDaily;
      dailyInput.addEventListener("change", () => setDailyVisible(dailyInput.checked));
    }
    setDailyVisible(showDaily);
    renderPorts();
    sizeGradients();
    cancelAnimationFrame(animFrame);
    lastTs = 0;
    requestAnimationFrame(tick);
  }

  window.EcoFlowFlow = {
    start,
    render,
    setEnergy,
    setDailyVisible,
  };
})();
