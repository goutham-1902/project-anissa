const mins = value => `${(Number(value || 0) / 60).toFixed(1)} h`;
const pct = value => `${(Number(value || 0) * 100).toFixed(0)}%`;
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const humanDate = value => value ? new Intl.DateTimeFormat("en-IN", {day:"numeric", month:"short"}).format(new Date(`${value}T12:00:00`)) : "No date";
const humanDateTime = value => value ? new Intl.DateTimeFormat("en-IN", {dateStyle:"medium", timeStyle:"short", timeZone:"Asia/Kolkata"}).format(new Date(value)) : "Not yet available";
const relativeDay = days => days == null ? "No date" : days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? "Today" : days === 1 ? "Tomorrow" : `${days}d`;
const state = {data:null, enabled:new Set(["Research", "Study", "Applications"]), selected:null};
const palette = {Research:"#2393ff", Study:"#f2d325", Applications:"#111317"};

function observed(totals) {
  return Number(totals.total_minutes || 0) - Number(totals.applications_estimated_proxy_minutes || 0);
}

function previousDate(value) {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() - 1);
  return date.toISOString().slice(0, 10);
}

function deadlineKpi(pressure) {
  if (pressure.nearest) {
    const days = pressure.nearest.days_to_deadline;
    return [days === 0 ? "Today" : `${days}d`, pressure.nearest.institution || pressure.nearest.programme || "Nearest deadline"];
  }
  if (pressure.overdue_active) return [`${pressure.overdue_active}`, "active record(s) past deadline"];
  return ["—", `${pressure.undated_active || 0} active without dates`];
}

function renderKpis(data) {
  const deadline = deadlineKpi(data.campaign.deadline_pressure);
  const proxy = Number(data.month.applications_estimated_proxy_minutes || 0);
  const latestDay = data.daily_series[data.daily_series.length - 1];
  const latestIsYesterday = latestDay?.date === previousDate(data.operational_date);
  const cards = [
    [latestIsYesterday ? "Observed focus yesterday" : "Observed focus latest day", latestDay ? mins(latestDay.observed_total) : "—", latestDay ? `${humanDate(latestDay.date)} · ${mins(latestDay.Research)} research · ${mins(latestDay.Study)} study` : "No confirmed Forest day"],
    ["Observed focus this week", mins(observed(data.week)), `${data.week.active_days} active days · prior ${mins(observed(data.previous_week))}`],
    ["Application credit", mins(data.month.applications_minutes), `${mins(data.month.applications_actual_minutes)} actual${proxy ? ` · ${mins(proxy)} proxy` : ""}`],
    ["Nearest deadline", deadline[0], deadline[1]],
  ];
  document.querySelector("#kpis").innerHTML = `${cards.map(([label,value,small]) => `<article class="kpi"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(small)}</small></article>`).join("")}<p class="kpi-ingestion">Most recent data ingestion <strong>${esc(humanDateTime(data.status.last_success))} IST</strong></p>`;
}

function enabledOrder() {
  return ["Applications", "Research", "Study"].filter(category => state.enabled.has(category));
}

function visibleTotal(day) {
  return enabledOrder().reduce((total, category) => total + Number(day[category] || 0), 0);
}

function areaPath(upper, lower, x, y) {
  const top = upper.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(" ");
  const bottom = lower.map((value, index) => ({value,index})).reverse().map(point => `L${x(point.index).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
  return `${top} ${bottom} Z`;
}

function boundaryPath(values, x, y) {
  return values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(" ");
}

function showTooltip(day, index, event) {
  const tooltip = document.querySelector("#tooltip");
  const proxy = Number(day.ApplicationsProxy || 0);
  tooltip.innerHTML = `<strong>${esc(humanDate(day.date))}</strong><span>Research ${mins(day.Research)}</span><span>Study ${mins(day.Study)}</span><span>Applications ${mins(day.Applications)}${proxy ? " · proxy included" : " · actual"}</span><b>${mins(day.total)} combined credit</b>`;
  tooltip.hidden = false;
  const wrap = document.querySelector("#chartWrap").getBoundingClientRect();
  const pointerX = event && Number.isFinite(event.clientX) ? event.clientX - wrap.left : ((index + .5) / state.data.daily_series.length) * wrap.width;
  tooltip.style.left = `${Math.max(112, Math.min(wrap.width - 112, pointerX))}px`;
}

function hideTooltip() { document.querySelector("#tooltip").hidden = true; }

function renderDayDetail(day) {
  const root = document.querySelector("#dayDetail");
  if (!day) { root.innerHTML = "<span>No daily records in the current Forest interval.</span>"; return; }
  const proxy = Number(day.ApplicationsProxy || 0);
  root.innerHTML = `<strong>${esc(humanDate(day.date))}</strong><span><i class="dot research"></i>${mins(day.Research)} Research</span><span><i class="dot study"></i>${mins(day.Study)} Study</span><span><i class="dot applications"></i>${mins(day.Applications)} Applications ${proxy ? '<em class="proxy-chip">proxy</em>' : ""}</span><b>${mins(day.total)} combined</b>`;
}

function renderChart() {
  const series = state.data.daily_series;
  const svg = document.querySelector("#chart");
  const width = 980, height = 410;
  const margin = {top:20, right:16, bottom:48, left:48};
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const count = Math.max(1, series.length);
  const x = index => series.length <= 1 ? margin.left + innerW / 2 : margin.left + index * innerW / (series.length - 1);
  const max = Math.max(60, ...series.map(visibleTotal));
  const y = value => margin.top + innerH - value / max * innerH;
  let markup = [0,.25,.5,.75,1].map(tick => {
    const py = y(max * tick);
    return `<line class="grid-line" x1="${margin.left}" x2="${width-margin.right}" y1="${py}" y2="${py}"/><text class="axis-label" x="${margin.left-9}" y="${py+4}" text-anchor="end">${(max*tick/60).toFixed(tick ? 1 : 0)}h</text>`;
  }).join("");

  let cumulative = series.map(() => 0);
  enabledOrder().forEach(category => {
    const lower = [...cumulative];
    cumulative = series.map((day,index) => lower[index] + Number(day[category] || 0));
    markup += `<path class="area ${category.toLowerCase()}" d="${areaPath(cumulative, lower, x, y)}" fill="${palette[category]}"/>`;
    markup += `<path class="boundary ${category.toLowerCase()}" d="${boundaryPath(cumulative, x, y)}"/>`;
  });

  const every = Math.max(1, Math.ceil(series.length / 9));
  const hitWidth = Math.max(3, innerW / count);
  series.forEach((day,index) => {
    const selected = day.date === state.selected;
    const label = index % every === 0 || index === series.length - 1 ? `<text class="axis-label" x="${x(index)}" y="${height-15}" text-anchor="middle">${esc(humanDate(day.date))}</text>` : "";
    markup += `<g class="day-group${selected ? " selected" : ""}" tabindex="0" role="button" aria-label="${esc(humanDate(day.date))}, ${mins(day.total)} combined" data-index="${index}"><rect class="hit-area" x="${x(index)-hitWidth/2}" y="${margin.top}" width="${hitWidth}" height="${innerH}"/><line class="selection-line" x1="${x(index)}" x2="${x(index)}" y1="${margin.top}" y2="${margin.top+innerH}"/>${label}</g>`;
  });
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = markup;
  svg.querySelectorAll(".day-group").forEach(group => {
    const index = Number(group.dataset.index);
    const day = series[index];
    group.addEventListener("mouseenter", event => showTooltip(day,index,event));
    group.addEventListener("mousemove", event => showTooltip(day,index,event));
    group.addEventListener("mouseleave", hideTooltip);
    group.addEventListener("focus", event => showTooltip(day,index,event));
    group.addEventListener("blur", hideTooltip);
    group.addEventListener("click", () => { state.selected = day.date; renderChart(); renderDayDetail(day); });
    group.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); state.selected = day.date; renderChart(); renderDayDetail(day); }
    });
  });
  renderDayDetail(series.find(day => day.date === state.selected) || series[series.length-1]);
}

function renderPie(distribution) {
  const svg = document.querySelector("#weeklyPie");
  const total = Number(distribution.denominator_minutes || 0);
  const radius = 72, circumference = Math.PI * 2 * radius;
  let offset = 0;
  let rings = '<circle class="pie-track" cx="110" cy="110" r="72"/>';
  ["Research","Study","Applications"].forEach(category => {
    const share = Number(distribution.shares[category] || 0);
    if (share > 0) {
      rings += `<circle class="pie-slice ${category.toLowerCase()}" cx="110" cy="110" r="72" stroke="${palette[category]}" stroke-dasharray="${share*circumference} ${circumference}" stroke-dashoffset="${-offset*circumference}"/>`;
      offset += share;
    }
  });
  svg.innerHTML = `${rings}<text class="pie-total" x="110" y="106" text-anchor="middle">${esc(mins(total))}</text><text class="pie-caption" x="110" y="126" text-anchor="middle">THIS WEEK</text>`;
  document.querySelector("#weekTotal").textContent = mins(total);
  document.querySelector("#pieLegend").innerHTML = ["Research","Study","Applications"].map(category => `<div><i class="${category.toLowerCase()}"></i><span>${category}</span><strong>${pct(distribution.shares[category])}</strong><small>${mins(distribution.minutes[category])}</small></div>`).join("");
  document.querySelector("#pieBasis").textContent = distribution.basis;
}

function renderVerdict(audit) {
  const root = document.querySelector("#verdict");
  if (!audit) { root.innerHTML = '<p class="empty">No verdict recorded this week.</p>'; return; }
  const score = audit.tasks_assigned ? `${audit.tasks_done || 0}/${audit.tasks_assigned} tasks done` : "Weekly audit recorded";
  root.innerHTML = `<p class="audit-period">${esc(humanDate(audit.week_start))} — ${esc(humanDate(audit.week_end))} · ${esc(audit.effort_basis || "recorded evidence")}</p><h3>${esc(score)}</h3>${audit.strongest_achievement ? `<div class="verdict-line"><span>Strongest achievement</span><p>${esc(audit.strongest_achievement)}</p></div>` : ""}${audit.main_failure_pattern ? `<div class="verdict-line"><span>Failure pattern</span><p>${esc(audit.main_failure_pattern)}</p></div>` : ""}<div class="next-command"><span>Next command</span><strong>${esc(audit.exact_next_action || audit.next_priorities || "No next action recorded")}</strong></div>${audit.summary ? `<details><summary>Full audit summary</summary><p>${esc(audit.summary)}</p></details>` : ""}`;
}

function telemetryCoverageState(entry) {
  const coverage = entry?.telemetry?.coverage;
  const raw = coverage && typeof coverage === "object" ? coverage.state : coverage;
  const normalized = String(raw ?? "pending").trim().toLowerCase();
  if (normalized === "complete") return "complete";
  if (normalized === "partial") return "partial";
  return "pending";
}

function historyCredit(value, coverage) {
  if (coverage === "pending" || value === null || value === undefined || value === "") return "Pending";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return coverage === "partial" ? "Partial" : "Pending";
  if (coverage === "partial") return numeric > 0 ? `${(numeric / 60).toFixed(1)} h · partial` : "Partial";
  return mins(numeric);
}

function historyAuditLine(label, value) {
  return value ? `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>` : "";
}

function weeklyHistoryMarkup(rows) {
  if (!Array.isArray(rows) || !rows.length) return '<p class="empty">No completed weekly verdicts recorded.</p>';
  return rows.slice(0, 6).map((entry, index) => {
    const audit = entry.audit || {};
    const workload = entry.workload || {};
    const coverage = telemetryCoverageState(entry);
    const score = audit.tasks_assigned
      ? `${audit.tasks_done ?? "—"}/${audit.tasks_assigned} tasks done`
      : "Weekly audit recorded";
    const auditLines = [
      historyAuditLine("Summary", audit.summary),
      historyAuditLine("Strongest achievement", audit.strongest_achievement),
      historyAuditLine("Failure pattern", audit.main_failure_pattern),
      historyAuditLine("Next command", audit.exact_next_action || audit.next_priorities),
    ].join("");
    const totals = [
      ["research", "Research", workload.research_minutes],
      ["study", "Study", workload.study_minutes],
      ["applications", "Applications", workload.applications_minutes],
      ["combined", "Combined", workload.combined_workload_credit_minutes],
    ].map(([className, label, value]) => `<div><dt><i class="${className}" aria-hidden="true"></i>${label}</dt><dd>${esc(historyCredit(value, coverage))}</dd></div>`).join("");
    return `<article class="history-entry${index === 0 ? " newest" : ""}"><header class="history-heading"><div><p class="history-period">${esc(humanDate(entry.week_start))} — ${esc(humanDate(entry.week_end))} · ${esc(audit.effort_basis || "recorded evidence")}</p><h3>${esc(score)}</h3></div><span class="coverage-chip ${coverage}">Telemetry ${coverage}</span></header><div class="history-body"><dl class="history-totals" aria-label="Weekly workload credit">${totals}</dl><dl class="history-audit">${auditLines || '<div><dt>Audit</dt><dd>No narrative facts recorded.</dd></div>'}</dl></div></article>`;
  }).join("");
}

function renderWeeklyHistory(rows) {
  document.querySelector("#weeklyHistory").innerHTML = weeklyHistoryMarkup(rows);
}

function renderApplications(rows) {
  document.querySelector("#applications").innerHTML = rows.length ? rows.map(row => `<div class="item application-item"><div><div class="item-topline"><strong>${esc(row.programme || "Untitled application")}</strong><span class="status-chip">${esc(row.status)}</span></div><p>${esc(row.institution)} · ${esc(row.route || "Route unrecorded")} · ${esc(row.cycle || "Cycle unrecorded")}</p><p>${esc(row.funding_status || row.funding_gate || "Funding unrecorded")}</p><p class="next-action">${esc(row.next_action || "No next action recorded")}</p></div><span class="deadline"><strong>${esc(humanDate(row.deadline))}</strong><small>${esc(relativeDay(row.days_to_deadline))}</small></span></div>`).join("") : '<div class="empty">No active applications recorded.</div>';
}

function renderTasks(rows) {
  const root = document.querySelector("#tasks");
  if (!rows.length) { root.innerHTML = '<div class="empty">No weekly campaign tasks.</div>'; return; }
  const groups = new Map();
  rows.forEach(row => {
    const key = row.task_date || "Unscheduled";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  root.innerHTML = [...groups.entries()].map(([day, tasks]) => `<section class="task-day"><div class="task-day-heading"><span>${esc(day === "Unscheduled" ? day : humanDate(day))}</span><small>${tasks.length} ${tasks.length === 1 ? "task" : "tasks"}</small></div>${tasks.map(row => `<div class="item task-item"><div><div class="item-topline"><strong>${esc(row.title)}</strong><span class="status-chip">${esc(row.status)}</span></div><p>${esc(row.campaign)} · ${esc(row.category || "Campaign task")} · ${esc(row.priority || "Priority unrecorded")}${row.estimated_minutes ? ` · ${esc(mins(row.estimated_minutes))}` : ""}${row.blocker ? ` · Blocked: ${esc(row.blocker)}` : ""}</p></div><span class="deadline"><strong>${row.status === "Done" ? "Done" : esc(relativeDay(row.days_to_due))}</strong><small>${row.due ? esc(humanDate(row.due)) : ""}</small></span></div>`).join("")}</section>`).join("");
}

function bindControls() {
  document.querySelectorAll("[data-category]").forEach(button => button.addEventListener("click", () => {
    const category = button.dataset.category;
    if (state.enabled.has(category) && state.enabled.size > 1) state.enabled.delete(category); else state.enabled.add(category);
    button.setAttribute("aria-pressed", String(state.enabled.has(category)));
    renderChart();
  }));
}

async function load() {
  const health = document.querySelector("#health");
  try {
    const response = await fetch("/api/dashboard", {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.data = data;
    const good = data.status.state === "COMPLETE" && data.status.freshness === "fresh";
    const pending = data.status.state === "STALE";
    health.className = `health ${good ? "good" : pending ? "pending" : "bad"}`;
    health.textContent = good ? "DATA CURRENT" : pending ? "FOREST EXPORT PENDING" : "SYNC NEEDS ATTENTION";
    document.querySelector("#statusMeaning").textContent = good
      ? "Latest Forest export processed successfully."
      : pending
        ? "No newer manual export was found. The latest confirmed metrics remain visible."
        : "The latest processing attempt did not complete. Historical data remains preserved.";
    document.querySelector("#coverage").textContent = data.status.coverage_through ? `Last confirmed ${new Date(data.status.coverage_through).toLocaleString("en-IN", {dateStyle:"medium", timeStyle:"short"})}` : "No confirmed coverage timestamp";
    document.querySelector("#seriesRange").textContent = `${humanDate(data.series_coverage.start)} — ${humanDate(data.series_coverage.end)} · ${data.series_coverage.days} operational days · ${data.series_coverage.anchor}`;
    document.querySelector("#basisNote").innerHTML = `${data.status.usable_for_judgment ? "Current feed" : "Last confirmed history"} · Forest is observed focus; Applications is completion credit.${data.month.applications_estimated_proxy_minutes ? ` <span class="proxy-chip">${mins(data.month.applications_estimated_proxy_minutes)} proxy this month</span>` : ""}`;
    renderKpis(data); renderChart(); renderPie(data.weekly_distribution);
    renderVerdict(data.campaign.latest_weekly_audit);
    renderApplications(data.campaign.applications); renderTasks(data.campaign.weekly_plan);
    renderWeeklyHistory(data.weekly_history);
  } catch (error) {
    health.className = "health bad";
    health.textContent = `Dashboard error · ${error.message}`;
  }
}

if (typeof document !== "undefined") {
  bindControls();
  load();
  setInterval(load, 60000);
}

if (typeof module !== "undefined") {
  module.exports = {historyCredit, telemetryCoverageState, weeklyHistoryMarkup};
}
