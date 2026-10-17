(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const state = { csrf: null, offset: 0, limit: 25, total: 0, loading: false };
  const signedOut = byId("signed-out");
  const dashboard = byId("dashboard");
  const environmentPill = byId("environment-pill");
  const logoutButton = byId("logout-button");

  function setNotice(id, message) {
    const node = byId(id);
    node.textContent = message || "";
    node.hidden = !message;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (response.status === 401) {
      state.csrf = null;
      showSignedOut();
      throw new Error("Your session has expired. Sign in again to continue.");
    }
    if (!response.ok) {
      let message = "The request could not be completed.";
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch (_) { /* Keep the generic message for non-JSON responses. */ }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function showSignedOut() {
    signedOut.hidden = false;
    dashboard.hidden = true;
    logoutButton.hidden = true;
    environmentPill.textContent = "Sign-in required";
    environmentPill.className = "environment-pill";
  }

  function renderSummary(overview) {
    const grid = byId("summary-grid");
    grid.replaceChildren();
    const integrationEntries = [
      ["Google Sheets", overview.integrations.google_sheets.status,
        overview.integrations.google_sheets.note],
      ["Telegram", overview.integrations.telegram.status,
        overview.integrations.telegram.configured ? "Bot token is configured; live connectivity not tested." : "No bot token configured."],
      ["Environment", overview.service.environment, "Application deployment environment."],
      ["Run mode", overview.service.dry_run ? "Dry run" : "Live mode",
        overview.service.dry_run ? "Write-safety default is active." : "Dry run is off; individual gates still apply."],
    ];
    for (const [label, value, note] of integrationEntries) {
      const card = element("article", "summary-card");
      card.append(element("p", "card-label", label));
      const valueNode = element("p", "card-value");
      const dot = element("span", "status-dot " + (String(value).includes("not_configured") ? "warn" : "good"));
      dot.setAttribute("aria-hidden", "true");
      valueNode.append(dot, document.createTextNode(String(value).replaceAll("_", " ")));
      card.append(valueNode, element("p", "card-note", note));
      grid.append(card);
    }
    byId("generated-at").textContent = "Updated " + new Date(overview.generated_at).toLocaleString(undefined, { timeZone: "UTC", timeZoneName: "short" });
    byId("welcome-line").textContent = "Signed in as " + overview.admin.email + ". This workspace is currently read-only.";
  }

  function renderSafety(overview) {
    const grid = byId("safety-grid");
    grid.replaceChildren();
    const labels = [
      ["Dry run", overview.service.dry_run],
      ["Team workflows", overview.safety.team_workflows_enabled],
      ["Team writes", overview.safety.team_writes_enabled],
      ["Scheduled workflows", overview.safety.scheduled_workflows_enabled],
      ["Analytics", overview.safety.analytics_enabled],
      ["Webhooks", overview.safety.webhooks_enabled],
      ["Safe Telegram replies", overview.safety.safe_telegram_replies_enabled],
      ["Dry-run team replies", overview.safety.dry_run_team_replies_enabled],
      ["Live Sheets mutations", overview.safety.live_sheet_mutations_available],
    ];
    for (const [label, enabled] of labels) {
      const card = element("div", "safety-card");
      card.append(element("span", "", label));
      let text = enabled ? "Enabled" : "Disabled";
      let klass = enabled ? "flag on" : "flag";
      if (label === "Dry run") {
        text = enabled ? "Active" : "Inactive";
        klass = enabled ? "flag" : "flag on";
      }
      if (label === "Live Sheets mutations" && !enabled) {
        text = "Unavailable";
        klass = "flag";
      }
      card.append(element("span", klass, text));
      grid.append(card);
    }
  }

  function renderAudit(items) {
    const body = byId("audit-body");
    body.replaceChildren();
    if (!items.length) {
      const row = element("tr");
      const cell = element("td", "empty-cell", "No audit events match these filters.");
      cell.colSpan = 6;
      row.append(cell);
      body.append(row);
      return;
    }
    for (const item of items) {
      const row = document.createElement("tr");
      const time = item.created_at ? new Date(item.created_at).toISOString().replace("T", " ").replace("Z", "") : "—";
      const changedFields = Array.isArray(item.changed_fields) ? item.changed_fields.join(", ") : "";
      const before = item.before && typeof item.before === "object" ? item.before : {};
      const after = item.after && typeof item.after === "object" ? item.after : {};
      const changes = changedFields ? changedFields.split(", ").map((field) =>
        field + ": " + JSON.stringify(before[field] ?? "") + " → " + JSON.stringify(after[field] ?? "")
      ).join("\n") : "—";
      const values = [
        element("td", "", time),
        element("td", "", item.action || "—"),
        element("td", "", (item.worksheet || "—") + " / " + (item.record_id || "—")),
        element("td", "", changedFields || "—"),
        element("td", "change-summary", changes),
      ];
      const outcomeCell = element("td");
      outcomeCell.append(element("span", "outcome " + String(item.outcome || "").toLowerCase(), item.outcome || "unknown"));
      values.push(outcomeCell);
      for (const cell of values) row.append(cell);
      body.append(row);
    }
  }

  async function loadOverview() {
    const overview = await requestJson("/api/v1/admin/overview");
    renderSummary(overview);
    renderSafety(overview);
    setNotice("global-error", "");
    setNotice("global-notice", "");
  }

  async function loadAudit() {
    const error = byId("audit-error");
    error.hidden = true;
    const params = new URLSearchParams({ limit: String(state.limit), offset: String(state.offset) });
    const outcome = byId("outcome-filter").value;
    const worksheet = byId("worksheet-filter").value.trim();
    if (outcome) params.set("outcome", outcome);
    if (worksheet) params.set("worksheet", worksheet);
    try {
      const result = await requestJson("/api/v1/admin/audit-events?" + params.toString());
      state.total = result.total;
      renderAudit(result.items);
      const first = result.total ? state.offset + 1 : 0;
      const last = Math.min(state.offset + result.items.length, result.total);
      byId("audit-count").textContent = "Showing " + first + "–" + last + " of " + result.total + " events";
      byId("previous-page").disabled = state.offset <= 0;
      byId("next-page").disabled = state.offset + state.limit >= result.total;
    } catch (errorValue) {
      if (dashboard.hidden) return;
      error.textContent = errorValue.message || "Audit history could not be loaded.";
      error.hidden = false;
      byId("audit-body").replaceChildren();
      const row = element("tr");
      const cell = element("td", "empty-cell", "Audit history is unavailable. Try refreshing.");
      cell.colSpan = 6;
      row.append(cell);
      byId("audit-body").append(row);
      byId("audit-count").textContent = "Audit history unavailable";
    }
  }

  async function refreshAll() {
    if (state.loading || dashboard.hidden) return;
    state.loading = true;
    byId("refresh-button").disabled = true;
    byId("refresh-button").textContent = "Refreshing…";
    try {
      await loadOverview();
      await loadAudit();
    } catch (error) {
      if (!dashboard.hidden) setNotice("global-error", error.message || "Could not load Polaris status.");
    } finally {
      state.loading = false;
      byId("refresh-button").disabled = false;
      byId("refresh-button").textContent = "Refresh data";
    }
  }

  async function start() {
    try {
      const session = await requestJson("/admin/session");
      state.csrf = session.csrf_token;
      signedOut.hidden = true;
      dashboard.hidden = false;
      logoutButton.hidden = false;
      environmentPill.textContent = "Authenticated · read-only";
      environmentPill.className = "environment-pill";
      await refreshAll();
    } catch (error) {
      if (!dashboard.hidden) setNotice("global-error", error.message || "Unable to verify your session.");
      else showSignedOut();
    }
  }

  byId("refresh-button").addEventListener("click", refreshAll);
  byId("audit-filter-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.offset = 0;
    loadAudit();
  });
  byId("previous-page").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadAudit();
  });
  byId("next-page").addEventListener("click", () => {
    if (state.offset + state.limit < state.total) {
      state.offset += state.limit;
      loadAudit();
    }
  });
  logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    try {
      await requestJson("/auth/logout", { method: "POST", headers: { "X-CSRF-Token": state.csrf || "" } });
      state.csrf = null;
      window.location.assign("/control/");
    } catch (error) {
      setNotice("global-error", error.message || "Sign out failed. Refresh and try again.");
    } finally {
      logoutButton.disabled = false;
    }
  });

  start();
})();
