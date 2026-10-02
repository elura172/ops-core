"use strict";
const $ = id => document.getElementById(id);
let token = "", session = null, items = [], selected = null, generation = 0, selection = 0, busy = false;
const labels = {create_product_draft:"Create product draft", update_inventory:"Update stock", update_prices:"Update prices", publish:"Publish content", send_customer_response:"Send customer response"};
const human = text => String(text).replaceAll("_", " ");
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function message(text) { $("message").textContent = text; }
function disconnect() {
  generation++; selection++; token = ""; session = null; items = []; selected = null; busy = false;
  $("token").value = ""; $("items").replaceChildren(); $("detail").replaceChildren();
  $("identity").textContent = ""; $("workspace").hidden = true; $("logout").hidden = true;
  $("login").hidden = false; $("connect").querySelector("button").disabled = false;
  $("refresh").disabled = false;
}
async function api(path, options = {}) {
  const epoch = generation;
  const response = await fetch(path, {...options, cache:"no-store", headers:{"Authorization":`Bearer ${token}`, "Content-Type":"application/json"}});
  if (epoch !== generation) throw new Error("Session changed.");
  const body = await response.json();
  if (epoch !== generation) throw new Error("Session changed.");
  if (!response.ok) {
    if (response.status === 401 || response.status === 503) disconnect();
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`);
  }
  return body;
}
function renderQueue() {
  $("count").textContent = `${items.length} actions · ${items.filter(i=>i.status === "PENDING").length} awaiting review`;
  const list = $("items"); list.replaceChildren();
  const shown = items.filter(i=>$("filter").value === "ALL" || i.status === $("filter").value);
  if (!shown.length) list.append(node("p", items.length ? "No actions match this filter." : "Your queue is clear. New proposals will appear here when a connected workflow submits them.", "empty"));
  for (const item of shown) {
    const button = node("button", undefined, "item");
    button.setAttribute("aria-pressed", String(item.review_id === selected));
    button.append(node("span", human(item.status), `badge ${item.status.toLowerCase()}`), node("strong", labels[item.operation] || human(item.operation)), node("small", item.review_id));
    button.disabled = busy;
    button.onclick = ()=>select(item.review_id);
    list.append(button);
  }
}
function fields(parent, data) {
  const dl = node("dl");
  for (const [key, value] of Object.entries(data)) {
    if (value === null || value === undefined) continue;
    dl.append(node("dt", human(key)), node("dd", typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)));
  }
  parent.append(dl);
}
function renderDetail(item) {
  const detail = $("detail"); detail.replaceChildren();
  detail.append(node("p", "ACTION PREVIEW", "eyebrow"), node("h2", labels[item.operation] || human(item.operation)), node("span", human(item.status), `badge ${item.status.toLowerCase()}`));
  detail.append(node("h3", "Proposed content")); fields(detail, item.payload);
  const raw = node("details"); raw.append(node("summary", "View complete action data"), node("pre", JSON.stringify(item.payload, null, 2))); detail.append(raw);
  detail.append(node("h3", "Action history"));
  fields(detail, {review_id:item.review_id, created_at:item.created_at, reviewer:item.reviewer, reason:item.reason, decided_at:item.decided_at, executed_by:item.executed_by, executed_at:item.executed_at});
  if (item.result !== null) { detail.append(node("h3", "Stored result"), node("pre", JSON.stringify(item.result, null, 2))); }
  if (item.failure) detail.append(node("p", `Needs reconciliation: ${item.failure}`));
  if (item.execution_blocked) detail.append(node("p", `Execution unavailable: ${item.execution_blocked}`));
  const actions = node("div", undefined, "actions");
  if (item.status === "PENDING" && session.permissions.includes("review:decide")) {
    const label = node("label", "Decision note (optional)", "reason"); label.htmlFor = "reason";
    const reason = node("textarea"); reason.id = "reason"; reason.rows = 2;
    detail.append(label, reason);
    for (const approve of [true,false]) {
      const button = node("button", approve ? "Approve action" : "Reject action", approve ? "primary" : "");
      button.onclick = ()=>mutate(item, "decision", {approve, reason:reason.value});
      actions.append(button);
    }
  } else if (item.status === "PENDING") detail.append(node("p", "Waiting for a reviewer’s decision.", "muted"));
  if (item.status === "APPROVED" && session.permissions.includes("review:execute")) {
    detail.append(node("p", "Approval is recorded. Execution uses the configured adapter; publishing and customer email may still be simulated or disabled.", "muted"));
    const button = node("button", "Execute approved action", "primary"); button.disabled = Boolean(item.execution_blocked);
    button.onclick = ()=> {
      if (window.confirm(`Execute ${labels[item.operation] || human(item.operation)} (${item.review_id})? This may perform an external action using the configured adapter.`)) mutate(item,"execute");
    };
    actions.append(button);
  } else if (item.status === "APPROVED") detail.append(node("p", "Ready for an operator to execute.", "muted"));
  if (item.status === "FAILED") detail.append(node("p", "Reconcile the outcome before submitting a fresh proposal. This action cannot be retried.", "muted"));
  detail.append(actions);
}
async function select(id) {
  const epoch = generation, request = ++selection; selected = id; renderQueue();
  $("detail").replaceChildren(node("p", "Loading action…"));
  try {
    const item = await api(`/reviews/${encodeURIComponent(id)}`);
    if (epoch === generation && request === selection) renderDetail(item);
  } catch(error) { if (epoch === generation && request === selection) { $("detail").replaceChildren(); message(error.message); } }
}
async function refresh() {
  const epoch = generation;
  $("refresh").disabled = true;
  try {
    const loaded = await api("/reviews");
    if (epoch !== generation) return;
    items = loaded; renderQueue();
    if (selected && items.some(i=>i.review_id === selected)) await select(selected);
    else { selected = null; $("detail").replaceChildren(node("h2", "A moment of attention."),node("p", "Select an action to read its contents before making a decision.")); }
  } catch(error) {
    if (epoch === generation) {
      items = []; selected = null; selection++;
      $("items").replaceChildren(node("p", "Queue unavailable. Refresh to try again.", "empty"));
      $("detail").replaceChildren(); $("count").textContent = "Queue unavailable";
    }
    if (epoch === generation || !session) message(error.message);
  }
  finally { if (epoch === generation) $("refresh").disabled = false; }
}
async function mutate(item, action, body) {
  if (busy) return;
  const epoch = generation; busy = true; $("refresh").disabled = true; renderQueue();
  $("detail").querySelectorAll("button,textarea").forEach(b=>b.disabled = true);
  try {
    await api(`/reviews/${encodeURIComponent(item.review_id)}/${action}`, {method:"POST", ...(body ? {body:JSON.stringify(body)} : {})});
    if (epoch === generation) message(action === "decision" ? "Decision recorded." : "Execution returned. Review the stored result below.");
  } catch(error) {
    if (epoch === generation || !session) message(`${error.message} Refresh and inspect the recorded state before attempting another action.`);
  } finally {
    if (epoch === generation) { busy = false; await refresh(); }
  }
}
$("connect").onsubmit = async event => {
  event.preventDefault(); const epoch = ++generation;
  token = $("token").value; $("token").value = ""; $("connect").querySelector("button").disabled = true; message("");
  try {
    const identity = await api("/operator/session");
    if (epoch !== generation) return;
    session = identity; $("identity").textContent = identity.name;
    $("mode").textContent = identity.mode === "live" ? "LIVE CLIENT" : "DRY-RUN CLIENT";
    $("mode").className = `badge ${identity.mode === "live" ? "live" : ""}`;
    $("workspace").hidden = false; $("logout").hidden = false; $("login").hidden = true; await refresh();
  } catch(error) { disconnect(); message(error.message); }
  finally { $("connect").querySelector("button").disabled = false; }
};
$("logout").onclick = ()=>{disconnect(); message("Disconnected. Your token and displayed action data have been cleared.");};
$("refresh").onclick = ()=>{message(""); refresh();};
$("filter").onchange = renderQueue;
window.addEventListener("pagehide", disconnect);
