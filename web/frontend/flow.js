(() => {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") || localStorage.getItem("ecoflow_web_token") || "";
  if (params.get("token")) localStorage.setItem("ecoflow_web_token", params.get("token"));

  const headers = {};
  if (token) headers["X-API-Token"] = token;

  const el = (id) => document.getElementById(id);
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
    el("navDash")?.setAttribute("href", pageHref("/"));
    el("navFlow")?.setAttribute("href", pageHref("/flow"));
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
    overviewCache = data;
    const label = data.site_label || data.site_id || "EcoFlow Ocean";
    el("pageTitle").textContent = label;
    syncNav();
    window.EcoFlowFlow?.render(data);
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
    window.EcoFlowFlow?.setEnergy(await res.json());
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
          overviewCache = msg.data;
          const label = msg.data.site_label || msg.data.site_id || "EcoFlow Ocean";
          el("pageTitle").textContent = label;
          window.EcoFlowFlow?.render(msg.data);
        }
      } catch (_) {
        /* ignore */
      }
    };
  }

  window.EcoFlowFlow?.start();
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
