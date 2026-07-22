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
      unit: { x: 1, y: 0 }, period: 80,
      gradIds: [
        "gradBatDischarge",
        "gradBatCharge",
        "gradBatChargeSolar",
        "gradBatChargeGrid",
        "gradBatChargeSolarGrid",
      ],
    },
    invPanel: {
      pathId: "railInvPanel", on: false, reverse: false, watts: 0, kind: "panel", maxW: 15000,
      unit: { x: 1, y: 0 }, period: 40,
      gradIds: [
        "gradInvPanelSolar",
        "gradInvPanelBattery",
        "gradInvPanelMixed",
        "gradInvPanelGrid",
        "gradInvPanelSolarGrid",
      ],
    },
    panelGrid: {
      pathId: "railPanelGrid", on: false, reverse: false, watts: 0, kind: "export", maxW: 15000,
      unit: { x: 0.7431, y: -0.6688 }, period: 40,
      gradIds: ["gradGridExport", "gradGridImport", "gradGridExportMixed", "gradGridExportSolar"],
    },
    panelHouse: {
      // House now sits directly below the Panel hub — straight vertical rail.
      pathId: "railPanelHouse", on: false, reverse: false, watts: 0, kind: "home", maxW: 12000,
      unit: { x: 0, y: 1 }, period: 40,
      gradIds: [
        "gradPanelHouse",
        "gradPanelHouseSolar",
        "gradPanelHouseBattery",
        "gradPanelHouseGrid",
        "gradPanelHouseMixed",
        "gradPanelHouseSolarGrid",
      ],
    },
    panelEv: {
      pathId: "railPanelEv", on: false, reverse: false, watts: 0, kind: "ev", maxW: 12000,
      unit: { x: 1, y: 0 }, period: 40, gradIds: ["gradPanelEv"],
    },
    // Pack towers → Battery box: shared vertical rails, sized in renderPacks.
    packWires: {
      pathId: null, on: false, reverse: false, watts: 0, kind: "pack", maxW: 5000,
      unit: { x: 0, y: 1 }, period: 55, gradIds: ["gradPackDischarge", "gradPackCharge"],
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

  function fmtV(value, digits = 0) {
    const v = num(value);
    if (v == null) return null;
    return `${v.toFixed(digits)} V`;
  }

  function fmtSplitV(l1, l2, avg) {
    const a = num(l1);
    const b = num(l2);
    if (a != null && b != null) {
      return `${a.toFixed(0)} / ${b.toFixed(0)} V`;
    }
    return fmtV(avg ?? a ?? b, 0);
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
  // wattage climbs toward the channel's maxW. Floor is high enough that
  // even a quiet leg (a few hundred watts on a multi‑kW channel) still
  // reads as clearly moving; without that floor the band crawls so slowly
  // it looks static.
  function flowPxPerSec(watts, maxW) {
    const t = Math.min(1, Math.abs(watts) / maxW);
    return 55 + t * 220;
  }

  function lineWidth(watts, maxW) {
    const t = Math.min(1, Math.abs(watts) / maxW);
    return 4 + t * 7;
  }

  function setStroke(id, ch) {
    const path = el(id);
    if (!path) return;
    let kindClass = ch.kind || "";
    if (ch.kind === "battery" || (ch.kind && ch.kind.startsWith("battery-"))) {
      // battery-discharge → "battery discharge", battery-solar → "battery solar", …
      kindClass = ch.kind.replace(/-/g, " ");
    } else if (
      ch.kind === "export" ||
      ch.kind === "import" ||
      ch.kind === "export-mixed" ||
      ch.kind === "export-solar"
    ) {
      kindClass = `grid ${ch.kind}`;
    }
    // panel / panel-battery / panel-mixed / home-* keep their kind as the class.
    const secondary = /Grid$/.test(id);
    path.className.baseVal = `flow-stroke${secondary ? " flow-stroke-secondary" : ""} ${kindClass}`;
    path.classList.toggle("on", !!ch.on);
    path.classList.toggle("reverse", !!ch.reverse);
    const width = lineWidth(ch.watts, ch.maxW) * (secondary ? 0.78 : 1);
    path.style.strokeWidth = ch.on ? `${width}` : "3";
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

  // Sweeps each active channel's gradient continuously along its line in the
  // direction of power flow (source → target). Each gradient is a short
  // saturated "pulse" anchored at the source end of the path that travels
  // toward the destination — never oscillates backward.
  function tick(ts) {
    animFrame = requestAnimationFrame(tick);
    if (!lastTs) lastTs = ts;
    const dt = Math.min(0.05, (ts - lastTs) / 1000);
    lastTs = ts;
    for (const ch of Object.values(channels)) {
      if (!ch.on) continue;
      const dir = ch.reverse ? -1 : 1;
      const speed = flowPxPerSec(ch.watts, ch.maxW);
      ch.phase = ((ch.phase || 0) + dir * speed * dt) % ch.period;
      if (ch.phase < 0) ch.phase += ch.period;
      const tx = (ch.unit.x * ch.phase).toFixed(2);
      const ty = (ch.unit.y * ch.phase).toFixed(2);
      for (const gradId of ch.gradIds) {
        const grad = document.getElementById(gradId);
        if (grad) grad.setAttribute("gradientTransform", `translate(${tx} ${ty})`);
      }
    }
  }

  function renderPvStrings(strings) {
    const g = el("pvStrings");
    if (!g) return;
    const MAX_PV = 8;
    const raw = Array.isArray(strings) ? strings : [];
    // Prefer API-provided slots (1..N, N≤8). Fall back to five empty slots
    // so a quiet inverter still shows the usual Ocean Pro string layout.
    let ids = raw
      .map((s) => Number(s.id))
      .filter((id) => Number.isFinite(id) && id >= 1 && id <= MAX_PV)
      .sort((a, b) => a - b);
    if (!ids.length) ids = [1, 2, 3, 4, 5];
    const count = Math.min(MAX_PV, Math.max(ids[ids.length - 1], ids.length));
    const items = [];
    for (let id = 1; id <= count; id++) {
      const found = raw.find((s) => Number(s.id) === id) || {};
      items.push({ id, power_w: Number(found.power_w) || 0 });
    }

    const solarX = 420;
    const solarTopY = 118; // Solar node top edge (center 159 − half-height 41)
    // Keep attach ports inside the rounded Solar box (±110 wide → inset).
    const attachSpan = count === 1 ? 0 : 190;
    const chipHalfW = count >= 7 ? 30 : count >= 6 ? 34 : 40;
    const chipGap = count >= 7 ? 68 : count >= 6 ? 78 : 96;
    const chipY = 48;
    const chipBottom = chipY + 20; // rect spans y -24..+20
    const elbowY = chipBottom + 12;
    const chipSpan = (count - 1) * chipGap;
    const chipStartX = solarX - chipSpan / 2;

    const parts = items.map((s, i) => {
      const chipX = count === 1 ? solarX : chipStartX + i * chipGap;
      const attachX =
        count === 1 ? solarX : solarX - attachSpan / 2 + (attachSpan * i) / (count - 1);
      const on = s.power_w > 25;
      const strokeClass = on ? "flow-stroke solar on" : "flow-rails";
      const strokeStyle = on
        ? 'style="stroke-width:2"'
        : 'style="stroke:rgba(80,100,120,0.25);stroke-width:1.5;fill:none"';
      // One dedicated connector per string: drop from the PV chip, jog to the
      // Solar-box attach point, then enter the top edge (no shared bus).
      const wire =
        Math.abs(chipX - attachX) < 0.5
          ? `<path class="${strokeClass}" d="M${chipX.toFixed(1)} ${chipBottom + 5} L${attachX.toFixed(1)} ${solarTopY}" ${strokeStyle} />`
          : `<path class="${strokeClass}" d="M${chipX.toFixed(1)} ${chipBottom + 5} L${chipX.toFixed(1)} ${elbowY} L${attachX.toFixed(1)} ${elbowY} L${attachX.toFixed(1)} ${solarTopY}" ${strokeStyle} />`;
      return `${wire}
        <path class="flow-port" d="${portPath(attachX, solarTopY, "up")}" />
        <g class="pv-chip ${on ? "on" : ""}" transform="translate(${chipX.toFixed(1)} ${chipY})">
          <rect class="pv-frame" x="-${chipHalfW}" y="-24" width="${chipHalfW * 2}" height="44" rx="10" />
          <line class="pv-cell-line" x1="-${chipHalfW - 8}" y1="-24" x2="-${chipHalfW - 8}" y2="20" />
          <line class="pv-cell-line" x1="${chipHalfW - 8}" y1="-24" x2="${chipHalfW - 8}" y2="20" />
          <path class="pv-port flow-port" d="${portPath(0, 20, "down")}" />
          <text class="pv-label" y="-8">PV${s.id}</text>
          <text class="pv-value" y="11">${fmtW(s.power_w)}</text>
        </g>`;
    });

    g.innerHTML = parts.join("");
  }

  function renderPacks(packs) {
    const g = el("packRow");
    if (!g) return;
    const packCh = channels.packWires;
    if (!Array.isArray(packs) || !packs.length) {
      g.innerHTML = "";
      if (packCh) packCh.on = false;
      return;
    }
    // Same vertical battery-tower indicator used on /house, sized close to
    // the inverter product photo, with animated rails into the Battery node.
    const count = Math.min(packs.length, 4);
    const gap = 72;
    const chipW = 64;
    const chipH = 168;
    const batBottomY = 480; // Battery box bottom (center 390, half-height 90)
    const packY = 502; // close under the Battery box
    const wireLen = packY - batBottomY;
    const rawStartX = 150 - ((count - 1) * gap) / 2 - chipW / 2;
    const startX = Math.max(4, rawStartX);

    const bankW = num(overviewCache?.power_flow?.battery_w) ?? 0;
    const bankCharging = bankW > 50;
    const bankDischarging = bankW < -50;

    // Size shared pack-wire gradients to this rail length (vertical).
    packCh.period = wireLen;
    for (const gradId of packCh.gradIds) {
      const grad = document.getElementById(gradId);
      if (!grad) continue;
      grad.setAttribute("x1", "0");
      grad.setAttribute("y1", String(batBottomY));
      grad.setAttribute("x2", "0");
      grad.setAttribute("y2", String(packY));
    }

    let anyWireOn = false;
    let wireWatts = Math.abs(bankW);
    // Path is drawn battery→pack (down). Charging = forward (down into packs);
    // discharging = reverse (up into the Battery box). Prefer the bank sign —
    // individual pack power_w can disagree briefly and was flipping the
    // animation to the wrong direction.
    let wireCharging = bankCharging;
    let wireDischarging = bankDischarging;

    const wires = packs
      .slice(0, 4)
      .map((p, i) => {
        const cx = startX + i * gap + chipW / 2;
        const watts = num(p.power_w);
        const packCharging = watts != null && watts > 50;
        const packDischarging = watts != null && watts < -50;
        const packActive =
          packCharging || packDischarging || bankCharging || bankDischarging;
        if (packActive) {
          anyWireOn = true;
          if (watts != null && Math.abs(watts) > wireWatts) wireWatts = Math.abs(watts);
          if (!wireCharging && !wireDischarging) {
            if (packDischarging) wireDischarging = true;
            else if (packCharging) wireCharging = true;
          }
        }
        const cls = packActive
          ? `flow-stroke pack on${wireCharging ? " charging" : " discharge"}`
          : "pack-rail";
        const sw = packActive ? 'style="stroke-width:3.5"' : "";
        return `<path class="${cls}" d="M${cx.toFixed(1)} ${batBottomY} L${cx.toFixed(1)} ${packY}" fill="none" stroke-linecap="round" ${sw} />
          <path class="flow-port" d="${portPath(cx, batBottomY, "down")}" />
          <path class="flow-port" d="${portPath(cx, packY, "up")}" />`;
      })
      .join("");

    packCh.on = anyWireOn;
    packCh.reverse = wireDischarging; // discharge: animate up into Battery box
    packCh.watts = wireWatts || 200;

    const chips = packs
      .slice(0, 4)
      .map((p, i) => {
        const x = startX + i * gap;
        const soc = num(p.soc);
        const watts = num(p.power_w);
        const sn = String(p.sn || "").slice(-4);
        const pct = soc != null ? Math.max(0, Math.min(100, soc)) : 0;
        // Bank direction wins so stripe animation matches the Battery node.
        let isCharging = bankCharging;
        let isDischarging = bankDischarging;
        if (!isCharging && !isDischarging) {
          if (watts != null && watts > 50) isCharging = true;
          else if (watts != null && watts < -50) isDischarging = true;
        }
        const dir = isCharging ? "charging" : isDischarging ? "discharging" : "idle";
        const volts = num(p.voltage_v);
        const wText =
          watts != null && Math.abs(watts) > 20 ? fmtW(Math.abs(watts)) : dir === "idle" ? "idle" : dir;
        const detail = [wText, volts != null ? fmtV(volts, 1) : null].filter(Boolean).join(" · ");
        const idx = p.index || i + 1;
        return `<foreignObject x="${x}" y="${packY}" width="${chipW}" height="${chipH}">
          <div xmlns="http://www.w3.org/1999/xhtml" class="flow-pack-unit ${dir}">
            <div class="batt-visual">
              <img src="/static/img/gear/battery-tower.png?v=20260722e" alt="" draggable="false" />
              <div class="batt-track" aria-hidden="true">
                <div class="batt-fill" style="--pct:${pct.toFixed(1)}%"></div>
              </div>
            </div>
            <div class="flow-pack-cap">
              <span class="flow-pack-name">P${idx} ·</span>
              <span class="flow-pack-sn">${escapeHtml(sn || "—")}</span>
              <span class="flow-pack-soc">${soc != null ? `${soc.toFixed(0)}%` : "—"}</span>
              <span class="flow-pack-w">${escapeHtml(detail)}</span>
            </div>
          </div>
        </foreignObject>`;
      })
      .join("");

    g.innerHTML = wires + chips;
  }

  // CDO labels for the mini circuit chips under House (same names as the
  // main circuits panel). MQTT names override when present.
  const HOUSE_CIRCUIT_NAMES = {
    1: "Master Bedroom Plug",
    2: "Microwave",
    3: "Bedroom 2&3",
    4: "Refrigerator / Router",
    5: "Right side Lights",
    6: "Washing machine",
    7: "Casita / Doorbell",
    8: "Kitchen",
    9: "Garage",
    10: "Garage / Main vent",
    11: "Dishwasher",
    12: "Living room plugs",
    13: "Left side Lights",
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
  };
  const FEED_CIRCUIT_CHS = new Set([38, 40]);
  const EV_CIRCUIT_CHS = new Set([23, 25]);
  const HOUSE_SPLIT_PAIRS = [
    { a: 24, b: 26, title: "Range" },
    { a: 20, b: 22, title: "A/C" },
    { a: 19, b: 21, title: "Dryer" },
    { a: 16, b: 18, title: "Furnace" },
    { a: 15, b: 17, title: "Water heater" },
  ];

  function normalizeCircuitMap(map) {
    const out = {};
    for (const [key, value] of Object.entries(map || {})) {
      out[Number(key)] = value;
    }
    return out;
  }

  function shortCircuitName(name, maxLen = 16) {
    const text = String(name || "").trim();
    if (!text) return "Circuit";
    if (text.length <= maxLen) return text;
    return `${text.slice(0, maxLen - 1)}…`;
  }

  // Branch chips under House: treat a channel as "running" above this
  // draw. Keep it low so always-on loads (vent fans, routers, LED strips)
  // still show; idle/noise channels typically sit near 0 and stay out.
  const CIRCUIT_RUNNING_W = 5;

  function collectRunningCircuits(panel) {
    const state = panel?.state || {};
    const powers = normalizeCircuitMap(state.circuit_power_w || {});
    const volts = normalizeCircuitMap(state.circuit_voltage_v || {});
    const mqttNames = normalizeCircuitMap(state.circuit_names || {});
    const names = { ...HOUSE_CIRCUIT_NAMES, ...mqttNames };
    const active = normalizeCircuitMap(state.circuit_active || {});
    const paired = new Set(HOUSE_SPLIT_PAIRS.flatMap((p) => [p.a, p.b]));
    const items = [];

    for (const pair of HOUSE_SPLIT_PAIRS) {
      const watts =
        Math.abs(Number(powers[pair.a]) || 0) + Math.abs(Number(powers[pair.b]) || 0);
      const isOn =
        active[pair.a] === true ||
        active[pair.b] === true ||
        watts > CIRCUIT_RUNNING_W;
      if (!isOn) continue;
      const vA = num(volts[pair.a]);
      const vB = num(volts[pair.b]);
      items.push({
        id: `p${pair.a}-${pair.b}`,
        name: pair.title,
        watts,
        voltage_v: vA != null && vB != null ? (vA + vB) / 2 : vA ?? vB,
      });
    }

    const singles = new Set([
      ...Object.keys(powers).map(Number),
      ...Object.keys(names).map(Number),
    ]);
    for (const ch of singles) {
      if (!Number.isFinite(ch)) continue;
      if (paired.has(ch) || FEED_CIRCUIT_CHS.has(ch) || EV_CIRCUIT_CHS.has(ch)) continue;
      const watts = Math.abs(Number(powers[ch]) || 0);
      const isOn = active[ch] === true || watts > CIRCUIT_RUNNING_W;
      if (!isOn) continue;
      items.push({
        id: `c${ch}`,
        name: names[ch] || `Circuit ${ch}`,
        watts,
        voltage_v: num(volts[ch]),
      });
    }

    items.sort((a, b) => b.watts - a.watts);
    return items.slice(0, 8);
  }

  function renderHouseCircuits(panel) {
    const g = el("circuitRow");
    if (!g) return;
    const items = collectRunningCircuits(panel);
    const homeBox = document.querySelector(".node-g.home .node-box");
    const houseLeftLocal = -110; // keep left edge fixed; grow right
    const housePad = 20;
    const minHouseW = 220;

    if (!items.length) {
      g.innerHTML = "";
      g.removeAttribute("data-count");
      if (homeBox) {
        homeBox.setAttribute("x", String(houseLeftLocal));
        homeBox.setAttribute("width", String(minHouseW));
      }
      return;
    }

    // House box bottom edge (center 580, half-height 35).
    const houseBottomY = 615;
    const chipY = 648;
    const chipW = 108;
    const chipH = 46;
    const maxPerRow = 4;
    const rows = Math.ceil(items.length / maxPerRow);
    // Wider gaps when a second row needs vertical lanes between chips.
    const gap = rows > 1 ? 18 : 12;
    const rowPitch = chipH + (rows > 1 ? 40 : 28);
    const firstRowCount = Math.min(items.length, maxPerRow);
    const firstRowW = firstRowCount * chipW + (firstRowCount - 1) * gap;
    const houseW = Math.max(minHouseW, firstRowW + housePad * 2);
    if (homeBox) {
      homeBox.setAttribute("x", String(houseLeftLocal));
      homeBox.setAttribute("width", String(houseW));
    }
    const chipStartX = 700 + houseLeftLocal + housePad;

    // Lane X positions through the first row: midpoints of the gaps between
    // chips (plus outer sides) so second-row drops never cross a chip box.
    const firstRowLanes = [];
    firstRowLanes.push(chipStartX - gap / 2);
    for (let i = 0; i < firstRowCount - 1; i++) {
      firstRowLanes.push(chipStartX + (i + 1) * chipW + i * gap + gap / 2);
    }
    firstRowLanes.push(chipStartX + firstRowW + gap / 2);

    const firstRowBottom = chipY + chipH;
    const jogY = firstRowBottom + 14;
    const usedLanes = new Set();
    const parts = [];

    function claimLane(targetX) {
      let best = null;
      let bestD = Infinity;
      for (const lx of firstRowLanes) {
        if (usedLanes.has(lx)) continue;
        const d = Math.abs(lx - targetX);
        if (d < bestD) {
          bestD = d;
          best = lx;
        }
      }
      if (best == null) {
        // All lanes taken — fall back to nearest even if shared.
        for (const lx of firstRowLanes) {
          const d = Math.abs(lx - targetX);
          if (d < bestD) {
            bestD = d;
            best = lx;
          }
        }
      }
      if (best != null) usedLanes.add(best);
      return best ?? targetX;
    }

    for (let row = 0; row < rows; row++) {
      const rowItems = items.slice(row * maxPerRow, (row + 1) * maxPerRow);
      const rowW = rowItems.length * chipW + (rowItems.length - 1) * gap;
      const startX =
        row === 0
          ? chipStartX
          : 700 + houseLeftLocal + (houseW - rowW) / 2;
      const y = chipY + row * rowPitch;

      rowItems.forEach((item, i) => {
        const x = startX + i * (chipW + gap);
        const cx = x + chipW / 2;
        let railPath;
        let portX;
        let portY;
        if (row === 0) {
          // Direct vertical drop from House into first-row chip.
          portX = cx;
          portY = houseBottomY;
          railPath = `M${cx.toFixed(1)} ${houseBottomY} L${cx.toFixed(1)} ${y}`;
        } else {
          // Drop through a first-row gap, jog under that row, then into chip.
          const laneX = claimLane(cx);
          portX = laneX;
          portY = houseBottomY;
          railPath =
            `M${laneX.toFixed(1)} ${houseBottomY} L${laneX.toFixed(1)} ${jogY.toFixed(1)} ` +
            `L${cx.toFixed(1)} ${jogY.toFixed(1)} L${cx.toFixed(1)} ${y}`;
        }
        parts.push(`
          <path class="circ-rail" d="${railPath}" />
          <path class="flow-port" d="${portPath(portX, portY, "down")}" />
          <path class="flow-port" d="${portPath(cx, y, "up")}" />
          <g class="circ-chip" transform="translate(${x.toFixed(1)} ${y})">
            <rect class="circ-box" x="0" y="0" width="${chipW}" height="${chipH}" rx="10" />
            <text class="circ-name" x="${chipW / 2}" y="17">${escapeHtml(shortCircuitName(item.name))}</text>
            <text class="circ-w" x="${chipW / 2}" y="35">${escapeHtml(
              [fmtW(item.watts), item.voltage_v != null ? fmtV(item.voltage_v, 0) : null]
                .filter(Boolean)
                .join(" · ")
            )}</text>
          </g>`);
      });
    }

    g.dataset.count = String(items.length);
    g.innerHTML = parts.join("");
  }

  function workModeLabel(mode) {
    const m = String(mode || "").toLowerCase();
    if (m === "backup") return "Emergency Backup";
    if (m === "self_use" || m === "selfuse" || m === "self_powered") return "Self-powered";
    if (m === "intelligent" || m === "timer_mode" || m === "timer") return "Intelligent";
    if (m === "time_of_use" || m === "tou") return "Time-of-use";
    if (m === "debug") return "Debug";
    if (m === "standby") return "Standby";
    if (m === "ac_makeup") return "AC makeup";
    return mode ? String(mode).replace(/_/g, " ") : null;
  }

  function stormFlags(flow, panel) {
    const pan = panel?.state || {};
    const modeRaw = flow.storm_mode ?? pan.storm_mode;
    const mode = modeRaw != null && modeRaw !== "" ? Number(modeRaw) : null;
    const watchRaw = flow.storm_watch ?? pan.storm_watch;
    const activeRaw = flow.storm_enabled ?? pan.storm_enabled;
    const asBool = (raw) => {
      if (raw === true || raw === 1 || raw === "1" || raw === "true") return true;
      if (raw === false || raw === 0 || raw === "0" || raw === "false") return false;
      return null;
    };
    let watch = asBool(watchRaw);
    let active = asBool(activeRaw);
    // Prefer backend flags. Raw storm_mode is diagnostic only — Storm Guard
    // armed state comes from panel field 467 via storm_watch.
    return { mode, watch, active };
  }

  /** Storm Watch / active storm mode + EMS backup chips above the diagram. */
  function renderModeBadges(flow, panel) {
    const root = el("modeBadges");
    if (!root) return;
    const pan = panel?.state || {};
    const { watch, active } = stormFlags(flow, panel);
    const mode = flow.work_mode;
    const modeLabel = workModeLabel(mode);
    const backupMode = String(mode || "").toLowerCase() === "backup";
    const reserve =
      num(flow.backup_reserve_soc) ?? num(pan.backup_reserve_soc);
    const chips = [];
    if (active === true) {
      chips.push(
        `<span class="mode-badge storm-active" title="Storm mode active — system preparing for / in a storm event">Storm mode · Active</span>`
      );
    }
    if (watch === true) {
      chips.push(
        `<span class="mode-badge storm" title="Storm Guard is armed — will enter storm mode when a storm is inbound">Storm Guard · On</span>`
      );
    } else if (watch === false) {
      chips.push(
        `<span class="mode-badge storm-off" title="Storm Guard is off">Storm Guard · Off</span>`
      );
    }
    if (backupMode) {
      chips.push(
        `<span class="mode-badge backup" title="Inverter EMS work mode = backup">Emergency Backup</span>`
      );
    } else if (modeLabel) {
      chips.push(
        `<span class="mode-badge mode" title="Inverter EMS work mode">${escapeHtml(modeLabel)}</span>`
      );
    }
    if (reserve != null && (watch === true || active === true || backupMode)) {
      chips.push(
        `<span class="mode-badge reserve" title="Backup reserve SOC">Reserve ${Math.round(reserve)}%</span>`
      );
    }
    root.innerHTML = chips.join("");
    root.hidden = chips.length === 0;

    const home = document.querySelector(".node-g.home");
    home?.classList.toggle("storm-active", active === true);
    home?.classList.toggle("storm-watch", watch === true && active !== true);
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
    const phaseV = fmtSplitV(flow.phase_a_voltage_v, flow.phase_b_voltage_v, null);
    const selfBits = [`Self Sufficient ~${self.toFixed(0)}%`];
    if (invAux != null) selfBits.push(`${fmtW(invAux)} inverter self-use`);
    if (phaseV) selfBits.push(phaseV);
    setText("nSelfSuf", selfBits.join(" · "));

    const runningCircuits = collectRunningCircuits(data.panel);
    setText(
      "nPanelOnline",
      data.panel?.online === false
        ? "offline"
        : runningCircuits.length
          ? `${runningCircuits.length} circuit${runningCircuits.length === 1 ? "" : "s"} running`
          : data.panel?.online === true
            ? "online · circuits idle"
            : "—"
    );
    setText(
      "nPanelLoad",
      runningCircuits.length
        ? runningCircuits.map((c) => shortCircuitName(c.name, 20)).join(", ")
        : "—"
    );
    setText(
      "nPanelHint",
      data.panel?.online === true ? "live MQTT" : data.panel?.online === false ? "panel offline" : "—"
    );

    const gridAbs = Math.abs(grid ?? 0);
    setText("nGridW", fmtW(gridAbs));
    let gridDir = "balanced";
    if (grid != null && grid < -50) gridDir = "exporting";
    else if (grid != null && grid > 50) gridDir = "importing";
    setText("nGridDir", gridDir);
    setText("nGridImport", `import ${fmtKwh(energy.grid_import_kwh)}`);
    setText("nGridExport", `export ${fmtKwh(energy.grid_export_kwh)}`);
    const gridV = fmtSplitV(
      flow.grid_voltage_l1_v ?? data.panel?.state?.grid_voltage_l1_v,
      flow.grid_voltage_l2_v ?? data.panel?.state?.grid_voltage_l2_v,
      flow.grid_voltage_v ?? data.panel?.state?.grid_voltage_v
    );
    setText("nGridFreq", gridV || "");

    setText("nHomeW", fmtW(loads.essential));
    setText("nHomeHint", loads.ev > 25 ? "essential (ex-EV)" : "essential load");
    setText("nHomeDay", `today ${fmtKwh(energy.home_kwh)}`);

    setText("nEvW", fmtW(loads.ev));
    const evBits = [];
    if (flow.vehicle_connected === true) evBits.push("vehicle in");
    if (flow.charging_active === true) evBits.push("charging");
    const evVRaw = num(
      flow.ev_output_voltage_v ?? data.ev?.state?.output_voltage_v
    );
    // EV charger often reports a low idle voltage when not charging — only
    // surface it as detail when it's in a real AC range or actively charging.
    const evV =
      evVRaw != null && (evVRaw >= 80 || flow.charging_active === true)
        ? fmtV(evVRaw, 0)
        : null;
    if (evV) evBits.push(evV);
    setText("nEvSub", evBits.join(" · ") || "non-essential");
    setText("nEvDay", `today ${fmtKwh(energy.ev_charge_kwh)}`);

    setText(
      "flowMeta",
      flow.updated_at
        ? `Updated ${new Date(flow.updated_at).toLocaleString()}`
        : "Waiting for telemetry…"
    );
    renderModeBadges(flow, data.panel);

    document.querySelector(".node-g.solar")?.classList.toggle("active", solar > 25);
    const batNode = document.querySelector(".node-g.battery");
    const batCharging = battery > 50;
    const batDischarging = battery < -50;
    batNode?.classList.toggle("charging", batCharging);
    batNode?.classList.toggle("discharging", batDischarging);
    // Solar is feeding the charge — match the yellow bat←inverter flow.
    batNode?.classList.toggle("solar-charging", batCharging && solar > 25);
    const gridNode = document.querySelector(".node-g.grid");
    gridNode?.classList.toggle("exporting", grid != null && grid < -50);
    gridNode?.classList.toggle("importing", grid != null && grid > 50);
    // Tint the Grid node yellow (instead of green) while solar is a
    // meaningful contributor to the export, so the node matches the
    // yellow flow running through the rest of the system.
    gridNode?.classList.toggle("solar-sourced", grid != null && grid < -50 && solar > 25);
    const homeNode = document.querySelector(".node-g.home");
    const houseOn = loads.essential > 25;
    homeNode?.classList.toggle("active", houseOn);
    document.querySelector(".node-g.ev")?.classList.toggle("active", loads.ev > 25);
    const invNode = document.querySelector(".node-g.inverter");
    invNode?.classList.toggle("online", flow.online === true);

    // Net solar that can still reach site loads after battery charge absorbs it.
    // Example: solar 4.7 kW + bat charge 20 kW → excess 0 → loads are not
    // solar-fed (grid passthrough / charge path is serving the house).
    // solar 4.7 kW + bat charge 3.5 kW → excess 1.2 kW → solar can feed loads.
    const solarExcess = Math.max(0, solar - Math.max(0, battery));
    const solarToLoads = solarExcess > 25;
    const solarIntoInv = solar > 25;
    const batteryPowered = batDischarging;
    const gridPowered = grid != null && grid > 50;
    // Inverter is yellow while PV is arriving (even if all of it is going
    // into the battery); panel/house use solarToLoads so they don't stay
    // yellow during grid+solar forced charge.
    invNode?.classList.toggle("solar-powered", solarIntoInv);
    invNode?.classList.toggle("battery-powered", batteryPowered);
    invNode?.classList.toggle("grid-powered", gridPowered && !solarToLoads && !batteryPowered);

    const panelNode = document.querySelector(".node-g.panel");
    const panelLive =
      loads.essential > 25 ||
      loads.ev > 25 ||
      Math.abs(grid ?? 0) > 25 ||
      solarToLoads ||
      batteryPowered;
    panelNode?.classList.toggle("active", panelLive);
    panelNode?.classList.toggle("solar-powered", panelLive && solarToLoads);
    panelNode?.classList.toggle("battery-powered", panelLive && batteryPowered);
    panelNode?.classList.toggle("grid-powered", panelLive && gridPowered && !solarToLoads);

    // House feed sources — solar only when excess remains after charging.
    const houseSolar = houseOn && solarToLoads;
    const houseBattery = houseOn && batteryPowered;
    const houseGrid = houseOn && gridPowered;
    homeNode?.classList.toggle("solar-powered", houseSolar);
    homeNode?.classList.toggle("battery-powered", houseBattery);
    homeNode?.classList.toggle("grid-powered", houseGrid && !houseSolar && !houseBattery);

    const circuitRow = el("circuitRow");
    circuitRow?.classList.toggle("solar-powered", houseSolar);
    circuitRow?.classList.toggle("battery-powered", houseBattery);
    circuitRow?.classList.toggle("grid-powered", houseGrid);
    renderHouseCircuits(data.panel);

    // Inverter → panel / site: solar leftover after charge, plus any discharge.
    const invOut = Math.max(0, solar - Math.max(0, battery)) + Math.max(0, -battery);
    const dischargingActive = batDischarging;
    const solarActive = solarIntoInv;
    updateChannel("solarInv", { on: solarActive, reverse: false, watts: solar, kind: "solar" });
    // Battery ↔ Inverter: green discharge; yellow solar charge; blue grid
    // charge; yellow+blue when solar AND grid both charge (combined rail).
    const solarCharge = batCharging && solar > 25;
    const gridCharge = batCharging && grid != null && grid > 50;
    let batKind = "battery";
    if (batDischarging) batKind = "battery-discharge";
    else if (solarCharge && gridCharge) batKind = "battery-solar-grid";
    else if (solarCharge) batKind = "battery-solar";
    else if (gridCharge) batKind = "battery-grid";
    else if (batCharging) batKind = "battery-charge";
    updateChannel("batInv", {
      on: Math.abs(battery) > 50,
      reverse: batCharging,
      watts: battery,
      kind: batKind,
    });
    // Inverter → Panel: yellow only when solar excess reaches the site;
    // during grid forced-charge with no excess, reverse blue toward battery.
    const exporting = grid != null && grid < -50;
    const importing = grid != null && grid > 50;
    const gridForcedCharge = importing && batCharging && !solarToLoads;
    let panelKind = "panel";
    if (invOut > 25 && solarToLoads && dischargingActive) panelKind = "panel-mixed";
    else if (invOut > 25 && solarToLoads && importing) panelKind = "panel-solar-grid";
    else if (invOut > 25 && dischargingActive && !solarToLoads) panelKind = "panel-battery";
    else if (invOut > 25 && importing && !solarToLoads) panelKind = "panel-grid";
    else if (gridForcedCharge) panelKind = "panel-grid";
    updateChannel("invPanel", {
      on: invOut > 25 || gridForcedCharge,
      reverse: gridForcedCharge,
      watts: invOut > 25 ? invOut : gridForcedCharge ? gridAbs : 0,
      kind: panelKind,
    });
    // Export currently drawing from both solar and a discharging battery at
    // once — surface that as a dual-color line instead of a single color.
    // Pure solar → yellow (continuing the same leg from Solar/Inverter/Panel);
    // pure battery discharge → green; both at once → yellow+green mixed.
    const exportMixed = exporting && solarToLoads && dischargingActive;
    const exportSolarOnly = exporting && solarToLoads && !dischargingActive;
    let exportKind = "export";
    if (exportMixed) exportKind = "export-mixed";
    else if (exportSolarOnly) exportKind = "export-solar";
    updateChannel("panelGrid", {
      on: exporting || importing,
      reverse: importing,
      watts: gridAbs,
      kind: exporting ? exportKind : "import",
    });
    // Panel → House: yellow / green / blue / yellow+green / yellow+blue.
    let houseKind = "home";
    if (houseSolar && houseGrid) houseKind = "home-solar-grid";
    else if (houseSolar && houseBattery) houseKind = "home-mixed";
    else if (houseBattery && !houseSolar) houseKind = "home-battery";
    else if (houseSolar) houseKind = "home-solar";
    else if (houseGrid) houseKind = "home-grid";
    updateChannel("panelHouse", {
      on: houseOn,
      reverse: false,
      watts: loads.essential,
      kind: houseKind,
    });
    updateChannel("panelEv", {
      on: loads.ev > 25,
      reverse: false,
      watts: loads.ev,
      kind: "ev",
    });

    setStroke("strokeSolarInv", channels.solarInv);
    setStroke("strokeBatInv", channels.batInv);
    setStroke("strokeInvPanel", channels.invPanel);
    setStroke("strokePanelGrid", channels.panelGrid);
    setStroke("strokePanelHouse", channels.panelHouse);
    setStroke("strokePanelEv", channels.panelEv);

    setLabel("lblSolarInv", solar, solar > 25);
    setLabel("lblBatInv", battery, Math.abs(battery) > 50);
    setLabel("lblInvPanel", invOut > 25 ? invOut : gridForcedCharge ? gridAbs : 0, invOut > 25 || gridForcedCharge);
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
