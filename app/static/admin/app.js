(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const state = { csrf: null, offset: 0, limit: 25, total: 0, loading: false, analyticsLoading: false, analytics: null };
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

  function fmt(value, suffix = "") {
    if (value === null || value === undefined || value === "") return "—";
    return String(value) + suffix;
  }

  function number(value) {
    return value === null || value === undefined ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  }

  function rate(value) {
    return value === null || value === undefined ? "N/A" : number(value) + "%";
  }

  function localISO(date) {
    const offset = date.getTimezoneOffset();
    return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 10);
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(options.headers || {}) },
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
      ["Google Sheets", overview.integrations.google_sheets.status, overview.integrations.google_sheets.note],
      ["Telegram", overview.integrations.telegram.status, overview.integrations.telegram.configured ? "Bot token is configured; live connectivity not tested." : "No bot token configured."],
      ["Environment", overview.service.environment, "Application deployment environment."],
      ["Run mode", overview.service.dry_run ? "Dry run" : "Live mode", overview.service.dry_run ? "Write-safety default is active." : "Dry run is off; individual gates still apply."],
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
      ["Growth analytics flag", overview.safety.analytics_enabled],
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
      if (label === "Live Sheets mutations" && !enabled) text = "Unavailable";
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

  function appendCell(row, value, className = "") {
    row.append(element("td", className, value));
  }

  function renderEmptyRow(bodyId, columns, message) {
    const body = byId(bodyId);
    body.replaceChildren();
    const row = element("tr");
    const cell = element("td", "empty-cell", message);
    cell.colSpan = columns;
    row.append(cell);
    body.append(row);
  }

  function renderMetricCards(summary) {
    const grid = byId("performance-summary");
    grid.replaceChildren();
    const cards = [
      ["Active members", number(summary.active_members), "Active people in selected scope"],
      ["Tasks in scope", number(summary.tasks), "Non-cancelled assigned tasks"],
      ["Completion rate", rate(summary.completion_rate), number(summary.completed) + " completed"],
      ["Overdue", number(summary.overdue), rate(summary.overdue_rate) + " of tasks"],
      ["In progress", number(summary.in_progress), "Currently being worked on"],
      ["Blocked", number(summary.blocked), "Needs a blocker check-in"],
      ["KPI observations", number(summary.kpi_observations), number(summary.kpis_below_target) + " below target"],
      ["Stale updates", number(summary.stale_updates), "Last update older than 7 days"],
      ["Due dates present", number(summary.tasks_with_due_dates), "Missing dates cannot be scored overdue"],
    ];
    for (const [label, value, note] of cards) {
      const card = element("article", "summary-card analytics-metric");
      card.append(element("p", "card-label", label), element("p", "card-value", value), element("p", "card-note", note));
      grid.append(card);
    }
  }

  function renderTeams(teams) {
    const body = byId("teams-body");
    if (!teams.length) return renderEmptyRow("teams-body", 9, "No team records match these filters.");
    body.replaceChildren();
    for (const item of teams) {
      const row = element("tr");
      appendCell(row, item.team);
      appendCell(row, number(item.members));
      appendCell(row, number(item.task_count));
      appendCell(row, number(item.completed));
      appendCell(row, number(item.in_progress));
      appendCell(row, number(item.open));
      appendCell(row, number(item.overdue), item.overdue ? "cell-alert" : "");
      appendCell(row, number(item.blocked), item.blocked ? "cell-alert" : "");
      appendCell(row, rate(item.completion_rate));
      body.append(row);
    }
  }

  function renderMembers(members) {
    const body = byId("members-body");
    if (!members.length) return renderEmptyRow("members-body", 10, "No active members match these filters.");
    body.replaceChildren();
    for (const item of members) {
      const row = element("tr");
      appendCell(row, item.name);
      appendCell(row, item.team);
      appendCell(row, number(item.task_count));
      appendCell(row, number(item.completed));
      appendCell(row, number(item.in_progress));
      appendCell(row, number(item.open));
      appendCell(row, number(item.overdue), item.overdue ? "cell-alert" : "");
      appendCell(row, number(item.blocked), item.blocked ? "cell-alert" : "");
      appendCell(row, rate(item.completion_rate));
      appendCell(row, item.follow_up_reasons.length ? item.follow_up_reasons.map((reason) => reason.replaceAll("_", " ")).join(", ") : "No current signals");
      body.append(row);
    }
  }

  function renderFollowups(tasks) {
    const body = byId("followup-body");
    if (!tasks.length) return renderEmptyRow("followup-body", 6, "No overdue or blocked tasks in the selected scope.");
    body.replaceChildren();
    for (const item of tasks) {
      const row = element("tr");
      appendCell(row, item.name);
      appendCell(row, item.owner_name);
      appendCell(row, item.team);
      appendCell(row, item.status_label);
      appendCell(row, item.due_date || "No due date");
      const reasons = [];
      if (item.overdue) reasons.push("Overdue");
      if (item.blocked) reasons.push("Blocked / blocker reported");
      if (item.stale_update) reasons.push("No recent update (>7 days)");
      appendCell(row, reasons.join(" · "), "cell-alert");
      body.append(row);
    }
  }

  function renderKpis(items) {
    const body = byId("kpis-body");
    if (!items.length) return renderEmptyRow("kpis-body", 8, "No KPI observations match this period and scope.");
    body.replaceChildren();
    for (const item of items) {
      const row = element("tr");
      appendCell(row, item.kpi);
      appendCell(row, item.owner_name);
      appendCell(row, item.team);
      appendCell(row, item.week_start || "Undated");
      appendCell(row, number(item.target));
      appendCell(row, number(item.actual));
      appendCell(row, rate(item.attainment));
      const signal = item.below_target === true ? "Below target" : item.below_target === false ? "At / above target" : "Not comparable";
      appendCell(row, signal, item.below_target === true ? "cell-alert" : "");
      body.append(row);
    }
  }

  function renderTeamChart(teams) {
    const chart = byId("team-chart");
    chart.replaceChildren();
    if (!teams.length) {
      chart.append(element("p", "empty-cell", "No team metrics to chart."));
      return;
    }
    for (const item of teams) {
      const line = element("div", "bar-row");
      const label = element("div", "bar-label", item.team);
      const track = element("div", "bar-track");
      const fill = element("div", "bar-fill");
      fill.style.width = Math.max(0, Math.min(100, item.completion_rate || 0)) + "%";
      track.append(fill);
      const value = element("div", "bar-value", rate(item.completion_rate) + " · " + item.task_count + " tasks");
      line.append(label, track, value);
      chart.append(line);
    }
  }

  function renderTrend(trend) {
    const chart = byId("trend-chart");
    chart.replaceChildren();
    if (!trend.length) {
      chart.append(element("p", "empty-cell", "No dated task activity."));
      return;
    }
    const max = Math.max(1, ...trend.map((item) => item.tasks_with_dates));
    for (const item of trend) {
      const column = element("div", "trend-column");
      const value = element("span", "trend-value", String(item.tasks_with_dates));
      const barWrap = element("div", "trend-bar-wrap");
      const bar = element("div", "trend-bar");
      bar.style.height = Math.max(3, item.tasks_with_dates / max * 100) + "%";
      barWrap.append(bar);
      const label = element("span", "trend-label", item.week_start.slice(5));
      const caption = element("span", "trend-caption", item.completed + " completed");
      column.append(value, barWrap, label, caption);
      chart.append(column);
    }
  }

  function renderQuality(quality) {
    const counts = byId("quality-counts");
    counts.replaceChildren();
    const entries = Object.entries(quality.counts || {});
    if (!entries.length) counts.append(element("span", "quality-ok", "No data-quality issues detected in the records examined."));
    for (const [key, value] of entries) {
      const pill = element("span", "quality-pill", key.replaceAll("_", " ") + ": " + value);
      counts.append(pill);
    }
    const notes = byId("quality-notes");
    notes.replaceChildren();
    for (const note of quality.notes || []) notes.append(element("li", "", note));
  }

  function populateFilters(data) {
    const teamSelect = byId("analytics-team");
    const personSelect = byId("analytics-person");
    const selectedTeam = teamSelect.value;
    const selectedPerson = personSelect.value;
    const teams = [...new Set((data.teams || []).map((item) => item.team))].sort();
    teamSelect.replaceChildren(new Option("All teams", ""));
    for (const team of teams) teamSelect.add(new Option(team, team));
    teamSelect.value = teams.includes(selectedTeam) ? selectedTeam : "";
    const people = [...(data.members || [])].sort((a, b) => a.name.localeCompare(b.name));
    personSelect.replaceChildren(new Option("All members", ""));
    for (const person of people) personSelect.add(new Option(person.name + " · " + person.team, person.id));
    personSelect.value = people.some((item) => item.id === selectedPerson) ? selectedPerson : "";
  }

  function renderAnalytics(data) {
    state.analytics = data;
    byId("analytics-source").textContent = "Live read-only data";
    byId("analytics-period-note").textContent = "Reporting period: " + data.period.start + " to " + data.period.end + ". " + data.period.basis + ". Generated " + new Date(data.generated_at).toLocaleString();
    renderMetricCards(data.summary);
    renderTeams(data.teams);
    renderMembers(data.members);
    renderFollowups(data.tasks);
    renderKpis(data.kpis);
    renderTeamChart(data.teams);
    renderTrend(data.trend);
    renderQuality(data.data_quality);
    populateFilters(data);
    byId("analytics-content").hidden = false;
    byId("analytics-error").hidden = true;
  }

  async function loadAnalytics() {
    if (state.analyticsLoading || dashboard.hidden) return;
    state.analyticsLoading = true;
    byId("analytics-loading").hidden = false;
    byId("analytics-error").hidden = true;
    const params = new URLSearchParams();
    for (const [id, key] of [["analytics-start", "start_date"], ["analytics-end", "end_date"], ["analytics-team", "team"], ["analytics-person", "person"], ["analytics-status", "status"]]) {
      const value = byId(id).value;
      if (value) params.set(key, value);
    }
    try {
      const data = await requestJson("/api/v1/admin/team-performance?" + params.toString());
      renderAnalytics(data);
    } catch (error) {
      if (!dashboard.hidden) {
        byId("analytics-error").textContent = error.message || "Team performance data could not be loaded.";
        byId("analytics-error").hidden = false;
        byId("analytics-source").textContent = "Data unavailable";
      }
    } finally {
      state.analyticsLoading = false;
      byId("analytics-loading").hidden = true;
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
      await Promise.all([loadAudit(), loadAnalytics()]);
    } catch (error) {
      if (!dashboard.hidden) setNotice("global-error", error.message || "Could not load Polaris status.");
    } finally {
      state.loading = false;
      byId("refresh-button").disabled = false;
      byId("refresh-button").textContent = "Refresh data";
    }
  }

  async function start() {
    const now = new Date();
    const monthAgo = new Date(now);
    monthAgo.setDate(now.getDate() - 29);
    byId("analytics-start").value = localISO(monthAgo);
    byId("analytics-end").value = localISO(now);
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
  byId("analytics-filter-form").addEventListener("submit", (event) => {
    event.preventDefault();
    loadAnalytics();
  });
  byId("analytics-reset").addEventListener("click", () => {
    const now = new Date();
    const monthAgo = new Date(now);
    monthAgo.setDate(now.getDate() - 29);
    byId("analytics-start").value = localISO(monthAgo);
    byId("analytics-end").value = localISO(now);
    byId("analytics-team").value = "";
    byId("analytics-person").value = "";
    byId("analytics-status").value = "";
    loadAnalytics();
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
