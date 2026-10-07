"use strict";
const $ = id => document.getElementById(id);
let checkedItems = new Set();
let token = "", session = null, items = [], selected = null, generation = 0, selection = 0, busy = false;
const labels = {intake_book:"Intake Book", create_product_draft:"Create product draft", update_inventory:"Update stock", update_prices:"Update prices", publish:"Publish content", send_customer_response:"Send customer response", sync_inventory:"Cross-Channel Sync", fulfill_order:"Fulfill Order", resolve_order_contention:"Resolve Contention"};
const human = text => String(text).replaceAll("_", " ");
function formatError(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(d => d.msg || (typeof d === "object" ? JSON.stringify(d) : String(d))).join(", ");
  }
  if (detail && typeof detail === "object") {
    return detail.msg || JSON.stringify(detail);
  }
  return "An unexpected error occurred.";
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function message(text) { $("message").textContent = text; }
function disconnect() {
  generation++; selection++; token = ""; session = null; items = []; selected = null; busy = false;
  $("items").replaceChildren(); $("detail").replaceChildren();
  $("identity").textContent = ""; $("workspace").hidden = true; $("logout").hidden = true;
  $("login-section").hidden = false; 
  if ($("billing-section")) $("billing-section").hidden = true;
  if ($("onboarding-section")) $("onboarding-section").hidden = true;
  if (typeof switchTab === "function") switchTab("queue");
  $("refresh").disabled = false;
}
async function api(path, options = {}) {
  const epoch = generation;
  const response = await fetch(path, {...options, cache:"no-store", headers:{"Authorization":`Bearer ${token}`, "Content-Type":"application/json"}});
  if (epoch !== generation) throw new Error("Session changed.");
  const text = await response.text();
  if (epoch !== generation) throw new Error("Session changed.");
  let body;
  try { body = JSON.parse(text); } catch(_) { body = { detail: text || `Request failed (${response.status}).` }; }
  if (epoch !== generation) throw new Error("Session changed.");
  if (!response.ok) {
    if (response.status === 401 || response.status === 503) disconnect();
    throw new Error(formatError(body.detail) || `Request failed (${response.status}).`);
  }
  return body;
}

function renderBatchDetail() {
  if (checkedItems.size === 0) {
    if (selected) select(selected);
    else {
      $("detail").replaceChildren(node("h2", "A moment of attention."),node("p", "Select an action to read its contents before making a decision."));
    }
    return;
  }
  
  const detail = $("detail");
  detail.replaceChildren(node("h2", `Batch Mode: ${checkedItems.size} Selected`));
  const selectedItems = items.filter(i => checkedItems.has(i.review_id));
  
  const pendingCount = selectedItems.filter(i => i.status === "PENDING").length;
  const approvedCount = selectedItems.filter(i => i.status === "APPROVED").length;
  
  if (pendingCount > 0) {
    detail.append(node("p", `${pendingCount} items are awaiting your decision. Batch approval skips inline editing and approves them as-is.`));
    const btnBox = node("div", undefined, "actions");
    const approveBtn = node("button", `Approve ${pendingCount} Actions`, "primary");
    approveBtn.onclick = async () => {
      busy = true; $("refresh").disabled = true;
      let success = 0, fail = 0;
      detail.replaceChildren(node("p", "Processing batch approvals..."));
      for (const item of selectedItems.filter(i => i.status === "PENDING")) {
        try {
          await api(`/reviews/${encodeURIComponent(item.review_id)}/decision`, { method: "POST", body: JSON.stringify({approve: true, reason: ""}) });
          checkedItems.delete(item.review_id);
          success++;
        } catch (e) { fail++; }
      }
      busy = false; message(`Batch completed: ${success} approved, ${fail} failed.`); refresh();
    };
    btnBox.append(approveBtn);
    detail.append(btnBox);
  }
  
  if (approvedCount > 0) {
    detail.append(node("p", `${approvedCount} items are ready for execution.`));
    const execBox = node("div", undefined, "actions");
    const execBtn = node("button", `Execute ${approvedCount} Actions`, "primary");
    execBtn.onclick = async () => {
      busy = true; $("refresh").disabled = true;
      let success = 0, fail = 0;
      detail.replaceChildren(node("p", "Processing batch executions..."));
      for (const item of selectedItems.filter(i => i.status === "APPROVED")) {
        try {
          await api(`/reviews/${encodeURIComponent(item.review_id)}/execute`, { method: "POST" });
          checkedItems.delete(item.review_id);
          success++;
        } catch (e) { fail++; }
      }
      busy = false; message(`Batch execution completed: ${success} executed, ${fail} failed.`); refresh();
    };
    execBox.append(execBtn);
    detail.append(execBox);
  }

  const delBox = node("div", undefined, "actions");
  delBox.style.marginTop = "16px";
  delBox.style.paddingTop = "12px";
  delBox.style.borderTop = "1px solid var(--line-subtle)";
  const delBatchBtn = node("button", `🗑️ Delete ${selectedItems.length} Selected Proposals`, "danger-sm");
  delBatchBtn.onclick = async () => {
    const count = selectedItems.length;
    if (!confirm(`Are you sure you want to delete ${count} items? This action cannot be reversed.`)) return;
    busy = true; $("refresh").disabled = true;
    let success = 0, fail = 0;
    detail.replaceChildren(node("p", "Deleting proposals..."));
    for (const item of selectedItems) {
      try {
        await api(`/reviews/${encodeURIComponent(item.review_id)}`, { method: "DELETE" });
        checkedItems.delete(item.review_id);
        success++;
      } catch (e) { fail++; }
    }
    busy = false; message(`Batch delete completed: ${success} removed, ${fail} failed.`);
    selected = null;
    refresh();
  };
  delBox.append(delBatchBtn);
  detail.append(delBox);
}

function renderQueue() {
  const pendingCount = items.filter(i => i.status === "PENDING").length;
  $("count").textContent = `${items.length} actions · ${pendingCount} awaiting review`;
  if ($("tab-queue-badge")) {
    $("tab-queue-badge").textContent = pendingCount ? String(pendingCount) : "";
  }

  const list = $("items"); list.replaceChildren();
  const shown = items.filter(i => $("filter").value === "ALL" || i.status === $("filter").value);
  
  if (shown.length) {
    const queueHeader = node("div", undefined, "queue-header-actions");
    queueHeader.style.display = "flex";
    queueHeader.style.gap = "8px";
    queueHeader.style.marginBottom = "8px";

    const selAll = node("button", "Toggle Select All", "ghost-sm");
    selAll.style.flex = "1";
    selAll.onclick = () => {
      const allChecked = shown.every(i => checkedItems.has(i.review_id));
      if (allChecked) checkedItems.clear();
      else shown.forEach(i => checkedItems.add(i.review_id));
      renderQueue();
      renderBatchDetail();
    };

    const clearInactive = node("button", "Clear Inactive 🗑️", "danger-sm");
    clearInactive.title = "Permanently delete rejected, executed, and failed proposals";
    clearInactive.onclick = async () => {
      const inactiveCount = items.filter(i => ["REJECTED", "EXECUTED", "FAILED"].includes(i.status)).length;
      if (inactiveCount === 0) {
        message("No inactive proposals to clear.");
        return;
      }
      if (!confirm(`Are you sure you want to delete ${inactiveCount} items? This action cannot be reversed.`)) return;
      clearInactive.disabled = true;
      try {
        const res = await api("/reviews?status=INACTIVE", { method: "DELETE" });
        message(`Database purged: ${res.count} inactive proposals removed.`);
        checkedItems.clear();
        selected = null;
        refresh();
      } catch(e) {
        message(e.message);
        clearInactive.disabled = false;
      }
    };

    queueHeader.append(selAll, clearInactive);
    list.append(queueHeader);
  }
  
  if (!shown.length) {
    list.append(node("p", items.length ? "No actions match this filter." : "Your queue is clear. New proposals will appear here when an intake scan or workflow submits them.", "empty"));
  }

  for (const item of shown) {
    const isSelected = item.review_id === selected;
    const row = node("div", undefined, `item-row${isSelected ? " selected" : ""}`);
    
    const cb = node("input");
    cb.type = "checkbox";
    cb.checked = checkedItems.has(item.review_id);
    cb.onclick = (e) => {
      e.stopPropagation();
      if (cb.checked) checkedItems.add(item.review_id);
      else checkedItems.delete(item.review_id);
      renderBatchDetail();
    };
    
    const button = node("button", undefined, "item");
    button.setAttribute("aria-pressed", String(isSelected));
    
    const headerRow = node("div", undefined, "item-header");
    let displayTitle = labels[item.operation] || human(item.operation);
    let subDetail = item.review_id;

    if (item.operation === "intake_book" && item.payload) {
      const best = item.payload.disambiguation?.best_candidate?.title;
      const title = best || item.payload.extraction?.title;
      const price = item.payload.valuation?.online_price_cad;
      if (title) displayTitle = title;
      
      const authors = item.payload.extraction?.authors;
      const cond = item.payload.valuation?.condition_grade;
      const authStr = authors && authors.length ? authors.join(", ") : "";
      subDetail = `${authStr ? `${authStr} · ` : ""}${cond || "Antiquarian"} · ${price ? `$${Number(price).toFixed(2)} CAD` : item.review_id}`;
    } else if (item.operation === "sync_inventory" && item.payload?.sku) {
      displayTitle = `Sync SKU: ${item.payload.sku}`;
      subDetail = `Sold on ${human(item.payload.source_channel || "marketplace")} · Delist everywhere`;
    }

    const titleEl = node("strong", displayTitle);
    const badgeEl = node("span", human(item.status), `badge ${item.status.toLowerCase()}`);
    headerRow.append(titleEl, badgeEl);

    const subRow = node("div", undefined, "item-sub");
    subRow.append(node("span", subDetail), node("small", item.review_id));

    button.append(headerRow, subRow);
    button.disabled = busy;
    button.onclick = () => {
      if (checkedItems.size > 0) {
        cb.checked = !cb.checked;
        cb.onclick({ stopPropagation: () => {} });
      } else {
        select(item.review_id);
      }
    };
    
    row.append(cb, button);
    list.append(row);
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
  
  if (item.operation === "intake_book" && item.payload.valuation) {
    const val = item.payload.valuation;
    const ext = item.payload.extraction;
    const dis = item.payload.disambiguation;
    
    detail.append(node("h3", "Book Intake Details"));
    
    if (item.status === "PENDING" && session.permissions.includes("review:decide")) {
      const dl = node("dl");
      
      const createInput = (label, type, val, path, opts=[]) => {
        dl.append(node("dt", label));
        const dd = node("dd");
        if (type === "readonly") dd.textContent = val;
        else if (type === "select") {
          const sel = node("select"); sel.dataset.path = path;
          opts.forEach(o => { const opt = node("option", o); opt.value = o; if (o === val) opt.selected = true; sel.append(opt); });
          dd.append(sel);
        }
        else {
          const inp = node("input"); inp.type = type; inp.value = val; inp.dataset.path = path;
          inp.style.width = "100%";
          dd.append(inp);
        }
        dl.append(dd);
      };
      
      createInput("Title", "text", dis.best_candidate ? dis.best_candidate.title : ext.title, "title");
      createInput("Author(s)", "text", ext.authors && ext.authors.length ? ext.authors.join(", ") : "Unknown", "authors");
      createInput("Format", "select", ext.format || "hardcover", "format", ["hardcover", "paperback", "mass_market", "oversized"]);
      createInput("Condition", "select", val.condition_grade || "Good", "condition", ["As New", "Fine", "Near Fine", "Very Good", "Good", "Fair", "Poor"]);
      createInput("Strategy", "select", val.channel_strategy || "in_store_and_online", "strategy", ["in_store_and_online", "online_only_collectible", "bargain_bin_cart", "multi channel antiquarian"]);
      createInput("In-Store Price (CAD)", "number", val.in_store_price_cad, "in_store_price");
      createInput("Online Price (CAD)", "number", val.online_price_cad, "online_price");
      createInput("Match Score", "readonly", dis.best_candidate ? `${Math.round(dis.best_candidate.match_score * 100)}%` : "N/A");
      
      detail.append(dl);
      detail.append(node("p", val.pricing_rationale, "muted"));
      if (dis.triage_reason) detail.append(node("p", `Triage: ${dis.triage_reason}`, "muted"));
    } else {
      const summaryData = {
        "Title": dis.best_candidate ? dis.best_candidate.title : ext.title,
        "Author(s)": ext.authors && ext.authors.length ? ext.authors.join(", ") : "Unknown",
        "Format": human(ext.format),
        "Condition": val.condition_grade,
        "Strategy": human(val.channel_strategy),
        "In-Store Price": `$${val.in_store_price_cad}`,
        "Online Price": `$${val.online_price_cad}`,
        "Match Score": dis.best_candidate ? `${Math.round(dis.best_candidate.match_score * 100)}%` : "N/A"
      };
      fields(detail, summaryData);
      detail.append(node("p", val.pricing_rationale, "muted"));
      if (dis.triage_reason) detail.append(node("p", `Triage: ${dis.triage_reason}`, "muted"));
    }

    if (item.payload.pricing_evidence) {
      const ev = item.payload.pricing_evidence;
      const card = node("div", undefined, "pricing-evidence-card");

      const headerRow = node("div", undefined, "evidence-header");
      headerRow.append(node("h4", "Marketplace Pricing Evidence", "eyebrow"));
      const badgeText = ev.confidence_label || (ev.comps_found ? `${ev.comps_found} Comps Found` : "Catalog Baseline");
      const badgeStatus = (ev.comps_found && ev.comps_found > 0) ? "approved" : "pending";
      headerRow.append(node("span", badgeText, `badge ${badgeStatus}`));
      card.append(headerRow);

      if (ev.min_comp_cad !== undefined && ev.min_comp_cad !== null && ev.p30_comp_cad !== undefined && ev.p30_comp_cad !== null && ev.median_comp_cad !== undefined && ev.median_comp_cad !== null) {
        const spreadBar = node("div", undefined, "market-spread-bar");

        const minItem = node("div", undefined, "spread-item");
        minItem.append(node("span", "Lowest Landed", "spread-lbl"), node("span", `$${Number(ev.min_comp_cad).toFixed(2)}`, "spread-val"));

        const p30Item = node("div", undefined, "spread-item highlight");
        p30Item.append(node("span", "Target Benchmark (30th Pct)", "spread-lbl"), node("span", `$${Number(ev.p30_comp_cad).toFixed(2)}`, "spread-val"));

        const medItem = node("div", undefined, "spread-item");
        medItem.append(node("span", "Market Median", "spread-lbl"), node("span", `$${Number(ev.median_comp_cad).toFixed(2)}`, "spread-val"));

        spreadBar.append(minItem, node("span", "→", "spread-divider"), p30Item, node("span", "→", "spread-divider"), medItem);
        card.append(spreadBar);

        card.append(node("p", "🎯 Priced to win Buy Box turnover (30th percentile of landed competitor copies).", "muted"));
      }

      if (ev.top_comps && ev.top_comps.length) {
        const wrap = node("div", undefined, "comp-table-wrap");
        const tbl = node("table", undefined, "comp-table");
        
        const thead = node("thead");
        const trH = node("tr");
        ["Marketplace", "Book Price", "Shipping", "Landed Total CAD"].forEach(h => trH.append(node("th", h)));
        thead.append(trH);
        tbl.append(thead);

        const tbody = node("tbody");
        ev.top_comps.forEach(c => {
          const tr = node("tr");
          tr.append(
            node("td", c.source),
            node("td", `$${Number(c.item_price_cad).toFixed(2)}`),
            node("td", `$${Number(c.shipping_price_cad).toFixed(2)}`),
            node("td", `$${Number(c.total_price_cad).toFixed(2)} CAD`)
          );
          tbody.append(tr);
        });
        tbl.append(tbody);
        wrap.append(tbl);
        card.append(wrap);
      }

      if ((ev.min_comp_cad === undefined || ev.min_comp_cad === null) && (ev.amazon_lowest_fba || ev.ebay_recent_average)) {
        const evData = {};
        if (ev.amazon_lowest_fba) evData["Amazon Lowest FBA"] = `$${Number(ev.amazon_lowest_fba).toFixed(2)}`;
        if (ev.ebay_recent_average) evData["eBay Recent Avg"] = `$${Number(ev.ebay_recent_average).toFixed(2)}`;
        if (ev.comps_found) evData["Comps Found"] = ev.comps_found;
        if (ev.confidence_score) evData["Confidence Score"] = `${Math.round(ev.confidence_score * 100)}%`;
        fields(card, evData);
      } else if ((ev.min_comp_cad === undefined || ev.min_comp_cad === null) && (!ev.top_comps || !ev.top_comps.length)) {
        const evData = {
          "Confidence Score": `${Math.round((ev.confidence_score || 0.35) * 100)}%`,
          "Status": ev.confidence_label || "Catalog Heuristic"
        };
        fields(card, evData);
      }

      if (ev.search_url) {
        const actRow = node("div", undefined, "verify-action-row");
        const countTxt = ev.comps_found ? ` ${ev.comps_found}` : "";
        const a = node("a", `🔍 Verify${countTxt} Live Listings on AbeBooks ↗`, "verify-link");
        a.href = ev.search_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        actRow.append(a);
        card.append(actRow);
      }

      detail.append(card);
    }
    
    const raw = node("details"); raw.append(node("summary", "View complete action data"), node("pre", JSON.stringify(item.payload, null, 2))); detail.append(raw);
  } else if (item.operation === "sync_inventory") {
    detail.append(node("h3", "Inventory Sync Details"));
    fields(detail, {
      "SKU Sold": item.payload.sku,
      "Sold On": human(item.payload.source_channel)
    });
    detail.append(node("p", "Approving this action will explicitly delist this SKU from all other configured marketplace adapters.", "muted"));
  } else if (item.operation === "fulfill_order") {
    detail.append(node("h3", "Fulfillment Details"));
    fields(detail, {
      "Order ID": item.payload.order_id,
      "Sales Channel": human(item.payload.source_channel),
      "Items": item.payload.line_items ? item.payload.line_items.map(i => `${i.sku} (Qty: ${i.quantity})`).join(", ") : "Unknown",
      "Customer": item.payload.shipping_address ? item.payload.shipping_address.name : "Unknown",
      "Destination": item.payload.shipping_address ? `${item.payload.shipping_address.city}, ${item.payload.shipping_address.country}` : "Unknown",
      "Requested Provider": item.payload.preferred_provider
    });
    detail.append(node("p", "Executing this action will generate a shipping label and notify the source channel of the tracking number.", "muted"));
    const raw = node("details"); raw.append(node("summary", "View complete action data"), node("pre", JSON.stringify(item.payload, null, 2))); detail.append(raw);
  } else if (item.operation === "resolve_order_contention") {
    detail.append(node("h3", "Cross-Marketplace Order Contention"));
    fields(detail, {
      "Contended SKU": item.payload.sku,
      "Winner Order": `${item.payload.winner_order_id} (${human(item.payload.winner_channel)})`,
      "Loser Order": `${item.payload.loser_order_id} (${human(item.payload.loser_channel)})`,
      "Action Required": human(item.payload.action),
      "Substitute SKU": item.payload.substitute_sku || "None Available"
    });
    detail.append(node("p", `Reason: ${item.payload.reason || item.payload.contention_reason}`, "muted"));
    const raw = node("details"); raw.append(node("summary", "View complete action data"), node("pre", JSON.stringify(item.payload, null, 2))); detail.append(raw);
  } else {
    detail.append(node("h3", "Proposed content")); fields(detail, item.payload);
    const raw = node("details"); raw.append(node("summary", "View complete action data"), node("pre", JSON.stringify(item.payload, null, 2))); detail.append(raw);
  }

  detail.append(node("h3", "Action history"));
  fields(detail, {review_id:item.review_id, created_at:item.created_at, reviewer:item.reviewer, reason:item.reason, decided_at:item.decided_at, executed_by:item.executed_by, executed_at:item.executed_at});
  if (item.reviewer === "agent:corvus" || item.executed_by === "agent:corvus") {
    const corvusNote = node("div", undefined, "corvus-sanctuary-note");
    corvusNote.innerHTML = `🪶 <span>Guarded by Corvus Warden · <em>Sanctuary tribute recorded in rafters</em> ↗</span>`;
    corvusNote.title = "View Corvus Sanctuary & Tributes";
    corvusNote.onclick = () => showCorvusSanctuaryModal();
    detail.append(corvusNote);
  }
  if (item.result !== null) {
    detail.append(node("h3", "Stored result"));
    if (item.result.packing_slip_base64) {
      const dl = node("a");
      dl.href = "data:application/pdf;base64," + item.result.packing_slip_base64;
      dl.download = `packing_slip_${item.payload.order_id || 'order'}.pdf`;
      dl.append(node("button", "Download Packing Slip (PDF)", "primary"));
      detail.append(dl);
      const { packing_slip_base64, ...rest } = item.result;
      detail.append(node("pre", JSON.stringify(rest, null, 2)));
    } else {
      detail.append(node("pre", JSON.stringify(item.result, null, 2)));
    }
  }
  if (item.failure) detail.append(node("p", `Needs reconciliation: ${item.failure}`));
  if (item.execution_blocked) detail.append(node("p", `Execution unavailable: ${item.execution_blocked}`));
  const actions = node("div", undefined, "actions");
  if (item.status === "PENDING" && session.permissions.includes("review:decide")) {
    const label = node("label", "Decision note (optional)", "reason"); label.htmlFor = "reason";
    const reason = node("textarea"); reason.id = "reason"; reason.rows = 2;
    detail.append(label, reason);
    for (const approve of [true,false]) {
      const button = node("button", approve ? "Approve action" : "Reject action", approve ? "primary" : "");
      button.onclick = ()=>{
        const body = {approve, reason:reason.value};
        if (approve && item.operation === "intake_book") {
          const payload_update = { valuation: {}, extraction: {} };
          const inputs = detail.querySelectorAll("input[data-path], select[data-path]");
          inputs.forEach(el => {
            const p = el.dataset.path;
            if (p === "title") payload_update.extraction.title = el.value;
            if (p === "authors") payload_update.extraction.authors = el.value.split(",").map(s=>s.trim());
            if (p === "format") payload_update.extraction.format = el.value;
            if (p === "condition") payload_update.valuation.condition_grade = el.value;
            if (p === "strategy") payload_update.valuation.channel_strategy = el.value;
            if (p === "in_store_price") payload_update.valuation.in_store_price_cad = String(el.value);
            if (p === "online_price") payload_update.valuation.online_price_cad = String(el.value);
          });
          // Also update disambiguation if it exists
          if (item.payload.disambiguation && item.payload.disambiguation.best_candidate) {
             payload_update.disambiguation = { best_candidate: { title: payload_update.extraction.title, authors: payload_update.extraction.authors } };
          }
          body.payload_update = payload_update;
        }
        mutate(item, "decision", body);
      };
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

  if (session && session.permissions.includes("review:decide")) {
    const delProposalBtn = node("button", "🗑️ Delete Proposal", "danger-sm");
    delProposalBtn.style.marginLeft = "auto";
    delProposalBtn.onclick = async () => {
      if (!confirm(`Are you sure you want to delete 1 item (${item.review_id})? This action cannot be reversed.`)) return;
      delProposalBtn.disabled = true;
      try {
        await api(`/reviews/${encodeURIComponent(item.review_id)}`, { method: "DELETE" });
        message(`Proposal ${item.review_id} permanently deleted.`);
        selected = null;
        refresh();
      } catch(e) {
        message(e.message);
        delProposalBtn.disabled = false;
      }
    };
    actions.append(delProposalBtn);
  }

  detail.append(actions);
}

async function showCorvusSanctuaryModal() {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "sanctuary-modal");
  
  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", "🪶 Corvus Vault Sanctuary"));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const loadingP = node("p", "Consulting the high rafters…", "muted");
  modal.append(loadingP);
  overlay.append(modal);
  document.body.append(overlay);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  try {
    const data = await api("/agents/corvus/sanctuary");
    loadingP.remove();

    const covenant = node("p", data.ethical_covenant, "muted");
    covenant.style.fontStyle = "italic";
    covenant.style.fontSize = "12px";
    modal.append(covenant);

    const stats = node("div", undefined, "sanctuary-stats");
    const stat1 = node("div", undefined, "sanctuary-stat-box");
    stat1.append(node("div", `${data.obsidian_feathers_count} 🪶`, "sanctuary-stat-num"), node("div", "Obsidian Feathers", "sanctuary-stat-lbl"));
    
    const stat2 = node("div", undefined, "sanctuary-stat-box");
    stat2.append(node("div", `${data.glinting_tokens?.silver_baubles || 0} 🪙`, "sanctuary-stat-num"), node("div", "Silver Baubles", "sanctuary-stat-lbl"));
    stats.append(stat1, stat2);
    modal.append(stats);

    const statusP = node("p");
    statusP.innerHTML = `<strong>State:</strong> ${data.sanctuary_state} · <em>${data.vigilance}</em>`;
    modal.append(statusP);

    if (data.recent_tributes && data.recent_tributes.length) {
      modal.append(node("h4", "Recent Guardian Tributes", "eyebrow"));
      const list = node("div", undefined, "sanctuary-tribute-list");
      data.recent_tributes.slice(0, 5).forEach(t => {
        const item = node("div", undefined, "sanctuary-tribute-item");
        item.innerHTML = `<strong>${t.token_type === "silver_bauble" ? "🪙" : "🪶"} ${t.subject}</strong><br>${t.inscription}`;
        list.append(item);
      });
      modal.append(list);
    }

    modal.append(node("h4", "Bestow Operator Blessing", "eyebrow"));
    const blessRow = node("div");
    blessRow.style.display = "flex";
    blessRow.style.gap = "8px";
    blessRow.style.marginTop = "8px";

    const input = node("input");
    input.type = "text";
    input.placeholder = "Speak a quiet blessing to the watcher in the rafters...";
    input.style.flex = "1";

    const sendBtn = node("button", "Bestow 🪶", "primary");
    sendBtn.onclick = async () => {
      const msg = input.value.trim();
      if (!msg) return;
      sendBtn.disabled = true;
      try {
        await api("/agents/corvus/sanctuary/blessings", {
          method: "POST",
          body: JSON.stringify({ message: msg })
        });
        message("Blessing received by Corvus in the high rafters.");
        overlay.remove();
      } catch (err) {
        alert(err.message);
        sendBtn.disabled = false;
      }
    };
    blessRow.append(input, sendBtn);
    modal.append(blessRow);

  } catch (err) {
    loadingP.textContent = `Unable to reach the rafters: ${err.message}`;
  }
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
    api("/holds").then(holds => {
      const activeHolds = (holds || []).filter(h => h.status === "active").length;
      const b = $("tab-holds-badge");
      if (b) {
        b.textContent = activeHolds ? String(activeHolds) : "";
        b.style.display = activeHolds ? "inline-block" : "none";
      }
    }).catch(() => {});
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

$("show-signup").onclick = (e) => { e.preventDefault(); $("login-panel").hidden = true; $("signup-panel").hidden = false; };
$("show-login").onclick = (e) => { e.preventDefault(); $("signup-panel").hidden = true; $("login-panel").hidden = false; };
if ($("btn-fill-demo")) {
  $("btn-fill-demo").onclick = () => {
    $("login-email").value = "operator@opscore.io";
    $("login-password").value = "demo1234";
  };
}

async function handleAuth(path, body, button) {
  const epoch = ++generation;
  button.disabled = true; message("");
  try {
    const res = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
    });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch(_) { data = { detail: text || `Authentication failed (${res.status})` }; }
    if (!res.ok) throw new Error(formatError(data.detail) || "Authentication failed");
    
    token = data.access_token;
    
    // Fetch identity
    const identity = await api("/auth/me");
    if (epoch !== generation) return;
    session = {name: identity.sub, permissions: ["review:read", "review:decide", "review:execute"]}; 
    $("identity").textContent = identity.sub;
    $("mode").textContent = "MULTI-TENANT";
    $("mode").className = `badge live`;
    $("login-section").hidden = true;
    $("logout").hidden = false;

    if (identity.subscription_status !== "active") {
        $("workspace").hidden = true;
        $("onboarding-section").hidden = true;
        $("billing-section").hidden = false;
        
        // Setup checkout flow
        $("btn-subscribe").onclick = async () => {
          $("btn-subscribe").disabled = true;
          $("btn-subscribe").textContent = "Loading checkout...";
          try {
            const checkout = await api("/billing/checkout", {
              method: "POST",
              body: JSON.stringify({
                success_url: window.location.href,
                cancel_url: window.location.href
              })
            });
            window.location.href = checkout.checkout_url;
          } catch(e) {
            message(e.message);
            $("btn-subscribe").disabled = false;
            $("btn-subscribe").textContent = "Subscribe Now ($49/mo) →";
          }
        };
    } else {
        $("billing-section").hidden = true;
        // Check integrations for onboarding
        const integrations = await api("/integrations");
        if (epoch !== generation) return;
        
        if (Object.keys(integrations.connected_providers || []).length === 0) {
            $("workspace").hidden = true;
            $("onboarding-section").hidden = false;
        } else {
            $("onboarding-section").hidden = true;
            $("workspace").hidden = false;
            await refresh();
        }
    }
  } catch(error) {
    disconnect();
    message(error.message);
  } finally {
    button.disabled = false;
  }
}

$("form-login").onsubmit = async event => {
  event.preventDefault(); 
  handleAuth("/auth/login", {email: $("login-email").value, password: $("login-password").value}, event.target.querySelector("button"));
  $("login-password").value = "";
};

$("form-signup").onsubmit = async event => {
  event.preventDefault(); 
  handleAuth("/auth/signup", {store_name: $("signup-store").value, email: $("signup-email").value, password: $("signup-password").value}, event.target.querySelector("button"));
  $("signup-password").value = "";
};

if ($("form-onboarding")) {
  $("form-onboarding").onsubmit = async event => {
    event.preventDefault();
    const provider = $("onboarding-provider").value;
    const key = $("onboarding-key").value;
    const button = $("form-onboarding").querySelector("button");
    button.disabled = true;
    try {
      await api("/integrations", {
        method: "POST",
        body: JSON.stringify({ provider: provider, api_key: key })
      });
      message(`Connected ${provider} successfully! Welcome to Corvus.`);
      $("onboarding-key").value = "";
      $("onboarding-section").hidden = true;
      $("workspace").hidden = false;
      await refresh();
    } catch(e) {
      message(e.message);
    } finally {
      button.disabled = false;
    }
  };
}

$("logout").onclick = ()=>{disconnect(); message("Disconnected. Your token and displayed action data have been cleared.");};
$("refresh").onclick = ()=>{message(""); refresh();};
$("filter").onchange = renderQueue;
$("propose-sync").onsubmit = async event => {
  event.preventDefault();
  const sku = $("sync-sku").value.trim();
  const channel = $("sync-channel").value;
  if (!sku || !channel) return;
  const button = $("propose-sync").querySelector("button");
  button.disabled = true;
  try {
    await api("/reviews", {
      method: "POST",
      body: JSON.stringify({
        operation: "sync_inventory",
        payload: { sku: sku, source_channel: channel }
      })
    });
    $("sync-sku").value = "";
    $("sync-channel").value = "";
    message("Sync action proposed successfully.");
    refresh();
  } catch(error) {
    message(error.message);
  } finally {
    button.disabled = false;
  }
};
window.addEventListener("pagehide", disconnect);


async function prepareImageFile(file) {
  if (!file) return file;

  // Strategy 1: Modern off-thread createImageBitmap
  if (typeof window.createImageBitmap === "function") {
    try {
      const bitmap = await window.createImageBitmap(file);
      const canvas = document.createElement("canvas");
      let width = bitmap.width;
      let height = bitmap.height;
      const maxDim = 1600;
      if (width > maxDim || height > maxDim) {
        if (width > height) {
          height = Math.round((height * maxDim) / width);
          width = maxDim;
        } else {
          width = Math.round((width * maxDim) / height);
          height = maxDim;
        }
      }
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(bitmap, 0, 0, width, height);
      if (typeof bitmap.close === "function") bitmap.close();

      const blob = await new Promise(res => canvas.toBlob(res, "image/jpeg", 0.82));
      if (blob && blob.size > 0) {
        const cleanName = (file.name || "stack_photo").replace(/\.[^.]+$/, "") + ".jpg";
        return new File([blob], cleanName, { type: "image/jpeg" });
      }
    } catch (e) {
      console.warn("createImageBitmap compression bypassed:", e);
    }
  }

  // Strategy 2: HTML Image() element with safe timeout fallback
  try {
    const compressed = await new Promise((resolve) => {
      let objectUrl = "";
      try {
        objectUrl = URL.createObjectURL(file);
      } catch (_) {
        return resolve(file);
      }

      const img = new Image();
      let done = false;

      const finish = (result) => {
        if (done) return;
        done = true;
        try { URL.revokeObjectURL(objectUrl); } catch (_) {}
        resolve(result);
      };

      const timer = setTimeout(() => finish(file), 3500);

      img.onload = () => {
        clearTimeout(timer);
        try {
          const canvas = document.createElement("canvas");
          let width = img.width;
          let height = img.height;
          const maxDim = 1600;
          if (width > maxDim || height > maxDim) {
            if (width > height) {
              height = Math.round((height * maxDim) / width);
              width = maxDim;
            } else {
              width = Math.round((width * maxDim) / height);
              height = maxDim;
            }
          }
          canvas.width = width;
          canvas.height = height;
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, width, height);
          canvas.toBlob(blob => {
            if (blob && blob.size > 0) {
              const cleanName = (file.name || "stack_photo").replace(/\.[^.]+$/, "") + ".jpg";
              finish(new File([blob], cleanName, { type: "image/jpeg" }));
            } else {
              finish(file);
            }
          }, "image/jpeg", 0.82);
        } catch (_) {
          finish(file);
        }
      };

      img.onerror = () => {
        clearTimeout(timer);
        finish(file); // NEVER throw or reject; fall back to raw file
      };

      img.src = objectUrl;
    });

    return compressed || file;
  } catch (_) {
    return file;
  }
}

$("camera-file").onchange = async event => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;

  const statusEl = $("camera-status");
  const button = $("camera-upload").querySelector("label");

  statusEl.textContent = `Preparing image (${(file.size / 1024).toFixed(0)} KB)...`;
  if (button) {
    button.style.opacity = "0.5";
    button.style.pointerEvents = "none";
  }

  try {
    let fileToUpload = file;
    try {
      fileToUpload = await prepareImageFile(file);
    } catch (prepErr) {
      console.warn("Client preparation failed, using raw file:", prepErr);
      fileToUpload = file;
    }

    if (fileToUpload.size > 4.2 * 1024 * 1024) {
      throw new Error(`Image is too large (${(fileToUpload.size / (1024 * 1024)).toFixed(1)} MB). Vercel payload limit is 4.5 MB. Please choose a photo under 4 MB.`);
    }

    statusEl.textContent = `Analyzing book stack with Gemini Vision...`;

    const formData = new FormData();
    formData.append("file", fileToUpload);
    if ($("intake-section")) formData.append("section", $("intake-section").value.trim() || "General");
    if ($("intake-shelf")) formData.append("shelf", $("intake-shelf").value.trim() || "");

    const epoch = generation;
    const response = await fetch("/intake/camera", {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` },
      body: formData
    });

    if (epoch !== generation) return;

    const text = await response.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch (_) {
      body = { detail: text || `Upload failed (${response.status})` };
    }

    if (!response.ok) {
      const errDetail = formatError(body.detail) || `Upload failed (${response.status})`;
      throw new Error(errDetail);
    }

    if (!body.processed || body.processed === 0) {
      statusEl.textContent = "No book spines identified.";
      message("No book spines were detected in this image. Ensure book spines and titles are clearly visible and facing the camera, or try 'Load Sample Book Stack'.");
    } else {
      statusEl.textContent = `Extracted ${body.processed} books (${human(body.orientation)}).`;
      message(`Vision AI extracted ${body.processed} books from the stack and queued them for review.`);
      refresh();
    }
  } catch (error) {
    const msg = error && error.message ? error.message : String(error || "Upload failed");
    statusEl.textContent = `Upload failed: ${msg}`;
    message(`Upload failed: ${msg}`);
  } finally {
    $("camera-file").value = "";
    if (button) {
      button.style.opacity = "1";
      button.style.pointerEvents = "auto";
    }
  }
};

if ($("btn-sample-stack")) {
  $("btn-sample-stack").onclick = async () => {
    const btn = $("btn-sample-stack");
    btn.disabled = true;
    btn.textContent = "Extracting sample stack...";
    $("camera-status").textContent = "Processing sample stack...";
    try {
      const body = await api("/intake/sample", { method: "POST" });
      $("camera-status").textContent = `Extracted ${body.processed} books (${human(body.orientation)}).`;
      message(`Vision AI extracted ${body.processed} books from the sample stack and queued them for review.`);
      refresh();
    } catch(err) {
      $("camera-status").textContent = "Sample load failed.";
      message(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "📚 Load Sample Book Stack";
    }
  };
};

let inventoryItems = [];
async function renderBookshelf() {
  const list = $("bookshelf-items"); list.replaceChildren();

  // Populate section dropdown
  const sectionFilter = $("bookshelf-filter-section");
  if (sectionFilter) {
    const currentSec = sectionFilter.value || "ALL";
    const sections = Array.from(new Set(inventoryItems.map(i => i.section || "General"))).filter(Boolean).sort();
    sectionFilter.innerHTML = '<option value="ALL">All Sections</option>';
    sections.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      if (s === currentSec) opt.selected = true;
      sectionFilter.appendChild(opt);
    });
  }

  const shelfFilterVal = ($("bookshelf-filter-shelf") ? $("bookshelf-filter-shelf").value.trim().toLowerCase() : "");
  const searchVal = ($("bookshelf-search") ? $("bookshelf-search").value.trim().toLowerCase() : "");
  const filterSecVal = ($("bookshelf-filter-section") ? $("bookshelf-filter-section").value : "ALL");

  const filtered = inventoryItems.filter(item => {
    if (filterSecVal !== "ALL" && (item.section || "General") !== filterSecVal) return false;
    if (shelfFilterVal && !(item.shelf || "").toLowerCase().includes(shelfFilterVal)) return false;
    if (searchVal) {
      const matchTitle = (item.title || "").toLowerCase().includes(searchVal);
      const matchAuthor = (item.authors || []).join(" ").toLowerCase().includes(searchVal);
      const matchSku = (item.sku || "").toLowerCase().includes(searchVal);
      const matchShelf = (item.shelf || "").toLowerCase().includes(searchVal);
      if (!matchTitle && !matchAuthor && !matchSku && !matchShelf) return false;
    }
    return true;
  });

  if (!filtered.length) {
    const msg = inventoryItems.length ? "No books matched current shelf filters." : "Your bookshelf is empty. Approved book intakes will appear here once executed.";
    list.append(node("p", msg, "empty"));
    return;
  }

  for (const item of filtered) {
    const card = node("div", undefined, "bookshelf-card");
    
    card.append(node("h3", item.title));
    const meta = node("div", undefined, "bookshelf-meta");
    meta.innerHTML = `<strong>${item.authors.join(", ")}</strong><br>SKU: <code>${item.sku}</code> · Format: ${human(item.format)} · Grade: ${item.condition} · In-Store: $${Number(item.in_store_price_cad).toFixed(2)} CAD`;
    
    // Physical location badge
    const locRow = node("div");
    locRow.style.margin = "6px 0";
    const locBadge = node("span", undefined, "bookshelf-location-badge");
    locBadge.innerHTML = `📍 <strong>${item.section || "General"}</strong> · ${item.shelf || "Unassigned"} <small style="opacity:0.6; margin-left:4px;">✏️</small>`;
    locBadge.title = "Click to update physical section or shelf";
    locBadge.onclick = async () => {
      const newSec = prompt(`Update Section for "${item.title}":`, item.section || "General");
      if (newSec === null) return;
      const newShelf = prompt(`Update Shelf for "${item.title}":`, item.shelf || "");
      if (newShelf === null) return;
      try {
        await api(`/inventory/${encodeURIComponent(item.sku)}`, {
          method: "PUT",
          body: JSON.stringify({ section: newSec.trim() || "General", shelf: newShelf.trim() })
        });
        message(`Updated location for ${item.sku} to ${newSec} / ${newShelf}.`);
        loadBookshelf();
      } catch(err) {
        alert(err.message);
      }
    };
    locRow.append(locBadge);

    // Audit freshness badge
    const auditBadge = node("span", undefined, "bookshelf-audit-badge");
    if (item.last_audited_at) {
      const auditDate = new Date(item.last_audited_at).toLocaleDateString();
      auditBadge.className = "bookshelf-audit-badge fresh";
      auditBadge.innerHTML = `✓ Audited ${auditDate}`;
    } else {
      auditBadge.className = "bookshelf-audit-badge stale";
      auditBadge.innerHTML = `⚠️ Unaudited`;
    }
    locRow.append(auditBadge);
    meta.append(locRow);
    card.append(meta);
    
    if (item.status === "active") {
      const actions = node("div", undefined, "bookshelf-actions");
      const priceLabel = node("label", "Online: $");
      const priceInput = node("input");
      priceInput.type = "number";
      priceInput.step = "0.50";
      priceInput.value = item.online_price_cad;
      
      const saveBtn = node("button", "Save Price", "ghost-sm");
      saveBtn.onclick = async () => {
        saveBtn.disabled = true;
        try {
          await api(`/inventory/${item.sku}`, {
            method: "PUT",
            body: JSON.stringify({
              in_store_price_cad: item.in_store_price_cad,
              online_price_cad: parseFloat(priceInput.value)
            })
          });
          message(`Price updated and "Update prices" proposal queued for ${item.sku}.`);
        } catch(e) { message(e.message); }
        finally { saveBtn.disabled = false; }
      };
      
      const delistBtn = node("button", "Delist", "ghost-sm");
      delistBtn.onclick = async () => {
        if (!confirm(`Are you sure you want to delist ${item.sku}? This will set stock to 0 across external channels.`)) return;
        delistBtn.disabled = true;
        try {
          await api(`/inventory/${item.sku}`, { method: "DELETE" });
          message(`Book delisted and "Update stock" action queued for ${item.sku}.`);
          loadBookshelf();
        } catch(e) { message(e.message); }
      };

      const checkoutBtn = node("button", "💳 Checkout", "ghost-sm");
      checkoutBtn.style.color = "var(--green)";
      checkoutBtn.title = "Generate Square in-store payment checkout link";
      checkoutBtn.onclick = () => showSquareCheckoutModal(item);

      const pushSquareBtn = node("button", "📲 Square", "ghost-sm");
      pushSquareBtn.title = "Push book to Square POS Register Catalog";
      pushSquareBtn.onclick = async () => {
        pushSquareBtn.disabled = true;
        try {
          const res = await api(`/inventory/${encodeURIComponent(item.sku)}/square/catalog`, { method: "POST" });
          message(`Synced "${item.title}" to Square POS Catalog.`);
          pushSquareBtn.textContent = "✓ Synced";
        } catch(e) {
          alert(e.message);
          pushSquareBtn.disabled = false;
        }
      };

      const pushEbayBtn = node("button", "🛍️ eBay", "ghost-sm");
      pushEbayBtn.title = "Push draft listing to eBay Shop";
      pushEbayBtn.onclick = async () => {
        pushEbayBtn.disabled = true;
        try {
          const res = await api(`/inventory/${encodeURIComponent(item.sku)}/ebay/draft`, { method: "POST" });
          message(`Drafted "${item.title}" for eBay Shop (${res.listing_draft_id || "draft"}).`);
          pushEbayBtn.textContent = "✓ Drafted";
        } catch(e) {
          alert(e.message);
          pushEbayBtn.disabled = false;
        }
      };
      
      actions.append(priceLabel, priceInput, saveBtn, delistBtn, checkoutBtn, pushSquareBtn, pushEbayBtn);
    } else {
      actions.append(node("span", `Status: ${human(item.status)}`, "muted"));
    }

    const deleteBookBtn = node("button", "🗑️ Delete Book", "danger-sm");
    deleteBookBtn.style.marginLeft = "auto";
    deleteBookBtn.title = "Permanently delete book from database";
    deleteBookBtn.onclick = async () => {
      if (!confirm(`Are you sure you want to delete 1 item ("${item.title}")? This action cannot be reversed.`)) return;
      deleteBookBtn.disabled = true;
      try {
        await api(`/inventory/${encodeURIComponent(item.sku)}?hard=true`, { method: "DELETE" });
        message(`Book "${item.title}" (${item.sku}) permanently deleted from database.`);
        loadBookshelf();
      } catch(e) {
        message(e.message);
        deleteBookBtn.disabled = false;
      }
    };
    actions.append(deleteBookBtn);
    card.append(actions);
    
    list.append(card);
  }
}

async function loadBookshelf() {
  try {
    inventoryItems = await api("/inventory");
    renderBookshelf();
  } catch(e) { message(e.message); }
}

async function showShelfAuditModal() {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "audit-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", "🔍 Physical Shelf Audit & Reconciliation"));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const sub = node("p", "Snap or upload a photo of the physical books on this shelf to verify inventory, detect misplaced volumes, and flag missing items.", "muted");
  sub.style.fontSize = "12.5px";
  modal.append(sub);

  const targetRow = node("div");
  targetRow.style.display = "flex";
  targetRow.style.gap = "12px";
  targetRow.style.margin = "14px 0";

  const secInput = node("input");
  secInput.placeholder = "Target Section (e.g. Sci-Fi / Fantasy)";
  secInput.value = ($("bookshelf-filter-section") && $("bookshelf-filter-section").value !== "ALL") ? $("bookshelf-filter-section").value : "Sci-Fi / Fantasy";
  secInput.style.flex = "1";

  const shelfInput = node("input");
  shelfInput.placeholder = "Target Shelf (e.g. B-03)";
  shelfInput.value = ($("bookshelf-filter-shelf") ? $("bookshelf-filter-shelf").value.trim() : "") || "B-03";
  shelfInput.style.flex = "1";

  targetRow.append(secInput, shelfInput);
  modal.append(targetRow);

  const fileBox = node("div");
  fileBox.style.padding = "16px";
  fileBox.style.border = "2px dashed var(--line)";
  fileBox.style.borderRadius = "8px";
  fileBox.style.textAlign = "center";
  fileBox.style.margin = "14px 0";

  const fileInput = node("input");
  fileInput.type = "file";
  fileInput.accept = "image/*,.heic,.heif,.jpg,.jpeg,.png,.webp";
  fileInput.id = "audit-file-input";

  const statusP = node("p", "Select or snap shelf photo to begin audit", "muted");
  statusP.style.marginTop = "8px";

  const sampleAuditBtn = node("button", "📚 Audit with Sample Stack", "ghost-sm");
  sampleAuditBtn.style.marginTop = "8px";
  sampleAuditBtn.onclick = async () => {
    sampleAuditBtn.disabled = true;
    statusP.textContent = "Reconciling shelf with sample vision extraction...";
    try {
      const data = await api("/inventory/audit/direct", {
        method: "POST",
        body: JSON.stringify({
          section: secInput.value.trim() || "General",
          shelf: shelfInput.value.trim(),
          scanned_books: [
            { title: "Dune", authors: ["Frank Herbert"], raw_spine_text: "DUNE HERBERT" },
            { title: "Do Androids Dream of Electric Sheep?", authors: ["Philip K. Dick"], raw_spine_text: "DO ANDROIDS DREAM OF ELECTRIC SHEEP" },
            { title: "The Lord of the Rings", authors: ["J.R.R. Tolkien"], raw_spine_text: "TOLKIEN LOTR" }
          ]
        })
      });
      statusP.textContent = `Audit complete: ${data.verified_count} verified, ${data.missing_count} missing, ${data.misplaced_count} misplaced.`;
      renderAuditResults(data, resultsContainer, secInput.value.trim(), shelfInput.value.trim(), overlay);
      loadBookshelf();
    } catch(err) {
      statusP.textContent = `Audit failed: ${err.message}`;
      sampleAuditBtn.disabled = false;
    }
  };

  fileBox.append(fileInput, statusP, sampleAuditBtn);
  modal.append(fileBox);

  const resultsContainer = node("div");
  modal.append(resultsContainer);

  fileInput.onchange = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const sec = secInput.value.trim() || "General";
    const sh = shelfInput.value.trim();
    statusP.textContent = `Analyzing shelf spines with Corvus Vision...`;
    fileInput.disabled = true;

    try {
      const compressed = await prepareImageFile(file);
      const fd = new FormData();
      fd.append("file", compressed);
      fd.append("section", sec);
      fd.append("shelf", sh);

      const res = await fetch("/inventory/audit", {
        method: "POST",
        headers: { "Authorization": `Bearer ${token}` },
        body: fd
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Audit failed");

      statusP.textContent = `Audit completed: ${data.verified_count} verified, ${data.missing_count} missing, ${data.misplaced_count} misplaced.`;
      renderAuditResults(data, resultsContainer, sec, sh, overlay);
      loadBookshelf();
    } catch (err) {
      statusP.textContent = `Audit error: ${err.message}`;
      fileInput.disabled = false;
    }
  };

  overlay.append(modal);
  document.body.append(overlay);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
}

function renderAuditResults(data, container, section, shelf, overlay) {
  container.replaceChildren();

  const metrics = node("div", undefined, "audit-metrics-row");
  const m1 = node("div", undefined, "audit-metric-box");
  m1.append(node("div", String(data.verified_count), "audit-metric-num"), node("div", "Verified", "audit-metric-lbl"));
  m1.querySelector(".audit-metric-num").style.color = "var(--green)";

  const m2 = node("div", undefined, "audit-metric-box");
  m2.append(node("div", String(data.missing_count), "audit-metric-num"), node("div", "Missing", "audit-metric-lbl"));
  m2.querySelector(".audit-metric-num").style.color = "var(--red)";

  const m3 = node("div", undefined, "audit-metric-box");
  m3.append(node("div", String(data.misplaced_count), "audit-metric-num"), node("div", "Misplaced", "audit-metric-lbl"));
  m3.querySelector(".audit-metric-num").style.color = "var(--amber)";

  const m4 = node("div", undefined, "audit-metric-box");
  m4.append(node("div", `${Math.round(data.compliance_rate * 100)}%`, "audit-metric-num"), node("div", "Compliance", "audit-metric-lbl"));

  metrics.append(m1, m2, m3, m4);
  container.append(metrics);

  // Missing Items Actionable List
  if (data.missing_items && data.missing_items.length) {
    const group = node("div", undefined, "audit-group");
    group.append(node("div", `🔴 Missing From Shelf (${data.missing_items.length})`, "audit-group-title"));
    data.missing_items.forEach(item => {
      const row = node("div", undefined, "audit-item-row");
      const textDiv = node("div");
      textDiv.append(node("div", item.title, "audit-item-title"), node("div", `SKU: ${item.sku} · Registered on ${item.section} / ${item.shelf}`, "audit-item-sub"));
      
      const delistBtn = node("button", "Delist Stock", "danger-sm");
      delistBtn.title = "Sets stock to 0 across marketplaces to prevent phantom sales";
      delistBtn.onclick = async () => {
        delistBtn.disabled = true;
        try {
          await api(`/inventory/${encodeURIComponent(item.sku)}`, { method: "DELETE" });
          message(`Delisted missing volume ${item.sku} from connected marketplaces.`);
          delistBtn.textContent = "✓ Delisted";
        } catch(e) {
          alert(e.message);
          delistBtn.disabled = false;
        }
      };
      row.append(textDiv, delistBtn);
      group.append(row);
    });
    container.append(group);
  }

  // Misplaced Items Actionable List
  if (data.misplaced_items && data.misplaced_items.length) {
    const group = node("div", undefined, "audit-group");
    group.append(node("div", `🟡 Misplaced Here (${data.misplaced_items.length})`, "audit-group-title"));
    data.misplaced_items.forEach(item => {
      const row = node("div", undefined, "audit-item-row");
      const textDiv = node("div");
      textDiv.append(node("div", item.title, "audit-item-title"), node("div", `SKU: ${item.sku} · Registered at: ${item.registered_section} / ${item.registered_shelf}`, "audit-item-sub"));
      
      const relocateBtn = node("button", `Move to ${shelf || section}`, "ghost-sm");
      relocateBtn.onclick = async () => {
        relocateBtn.disabled = true;
        try {
          await api(`/inventory/${encodeURIComponent(item.sku)}`, {
            method: "PUT",
            body: JSON.stringify({ section: section, shelf: shelf })
          });
          message(`Moved ${item.sku} to ${section} / ${shelf}.`);
          relocateBtn.textContent = "✓ Relocated";
        } catch(e) {
          alert(e.message);
          relocateBtn.disabled = false;
        }
      };
      row.append(textDiv, relocateBtn);
      group.append(row);
    });
    container.append(group);
  }

  // Verified Items List
  if (data.verified_items && data.verified_items.length) {
    const group = node("div", undefined, "audit-group");
    group.append(node("div", `🟢 Verified in Place (${data.verified_items.length})`, "audit-group-title"));
    data.verified_items.forEach(item => {
      const row = node("div", undefined, "audit-item-row");
      row.append(node("div", `✓ ${item.title}`, "audit-item-title"), node("span", `Match: ${Math.round(item.match_score * 100)}%`, "muted"));
      group.append(row);
    });
    container.append(group);
  }

  // Uncataloged Items
  if (data.uncataloged_items && data.uncataloged_items.length) {
    const group = node("div", undefined, "audit-group");
    group.append(node("div", `⚪ Uncataloged Spines (${data.uncataloged_items.length})`, "audit-group-title"));
    data.uncataloged_items.forEach(item => {
      const row = node("div", undefined, "audit-item-row");
      row.append(node("div", `Spine: ${item.title || item.raw_spine_text}`, "audit-item-title"), node("span", "Not in catalog", "muted"));
      group.append(row);
    });
    container.append(group);
  }
}

async function showShelfAuditHistoryModal() {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "audit-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", "📜 Shelf Audit Log & Compliance History"));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const loadingP = node("p", "Loading audit history...", "muted");
  modal.append(loadingP);
  overlay.append(modal);
  document.body.append(overlay);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  try {
    const audits = await api("/inventory/audits");
    loadingP.remove();

    if (!audits.length) {
      modal.append(node("p", "No audits recorded yet. Run 'Audit Shelf' on any section to begin.", "muted"));
      return;
    }

    const list = node("div");
    list.style.display = "flex";
    list.style.flexDirection = "column";
    list.style.gap = "8px";
    list.style.marginTop = "12px";

    audits.forEach(a => {
      const row = node("div", undefined, "audit-item-row");
      const dateStr = new Date(a.created_at).toLocaleString();
      const compRate = a.total_expected > 0 ? Math.round((a.verified_count / a.total_expected) * 100) : 100;
      
      const left = node("div");
      left.innerHTML = `<strong>${a.section} · ${a.shelf || "General"}</strong><br><span class="muted">${dateStr} by ${a.audited_by}</span>`;
      
      const right = node("div");
      right.style.textAlign = "right";
      right.innerHTML = `<strong>${compRate}% Compliance</strong><br><span class="muted">${a.verified_count}✓ · ${a.missing_count} missing · ${a.misplaced_count} misplaced</span>`;
      
      row.append(left, right);
      list.append(row);
    });
    modal.append(list);
  } catch(err) {
    loadingP.textContent = `Failed to load audit history: ${err.message}`;
  }
}

async function showSquareCheckoutModal(item) {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "audit-modal");
  modal.style.maxWidth = "460px";

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", "💳 In-Store Square Checkout"));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const loadingP = node("p", `Generating Square payment link for "${item.title}"...`, "muted");
  modal.append(loadingP);
  overlay.append(modal);
  document.body.append(overlay);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  try {
    const data = await api(`/inventory/${encodeURIComponent(item.sku)}/square/checkout`, { method: "POST" });
    loadingP.remove();

    const bookSummary = node("div");
    bookSummary.style.background = "var(--surface-subtle)";
    bookSummary.style.padding = "14px";
    bookSummary.style.borderRadius = "8px";
    bookSummary.style.marginBottom = "16px";
    bookSummary.innerHTML = `
      <div style="font-weight:700; font-size:16px;">${item.title}</div>
      <div class="muted" style="font-size:12px; margin-top:2px;">${item.authors.join(", ")} · SKU: <code>${item.sku}</code> · 📍 ${item.section || "General"} · ${item.shelf || ""}</div>
      <div style="display:flex; justify-content:space-between; align-items:baseline; margin-top:12px;">
        <span class="muted" style="font-size:12px;">In-Store Price:</span>
        <span style="font-size:22px; font-weight:750; color:var(--ink);">$${Number(data.amount_cad || item.in_store_price_cad).toFixed(2)} CAD</span>
      </div>
    `;
    modal.append(bookSummary);

    const payRow = node("div");
    payRow.style.display = "flex";
    payRow.style.flexDirection = "column";
    payRow.style.gap = "10px";

    const payBtn = node("a", `Open Square Payment Page ($${Number(data.amount_cad || item.in_store_price_cad).toFixed(2)}) →`, "primary full-width");
    payBtn.href = data.payment_url;
    payBtn.target = "_blank";
    payBtn.style.textAlign = "center";
    payBtn.style.display = "block";
    payBtn.style.padding = "12px";
    payBtn.style.textDecoration = "none";
    payRow.append(payBtn);

    const copyBtn = node("button", "📋 Copy Checkout Link", "ghost-sm full-width");
    copyBtn.onclick = () => {
      navigator.clipboard.writeText(data.payment_url);
      message("Square payment link copied to clipboard.");
      copyBtn.textContent = "✓ Link Copied";
    };
    payRow.append(copyBtn);
    modal.append(payRow);

    const hint = node("p", undefined, "muted");
    hint.style.fontSize = "11.5px";
    hint.style.marginTop = "16px";
    hint.style.borderTop = "1px solid var(--line-subtle)";
    hint.style.paddingTop = "12px";
    hint.innerHTML = `<strong>Autonomous Delist Protection:</strong> When the customer taps to pay on Square, the Square webhook automatically notifies Ops Core, causing Corvus Warden to instantly delist this volume across all online channels.`;
    modal.append(hint);

  } catch(err) {
    loadingP.textContent = `Checkout error: ${err.message}`;
  }
}

function switchTab(active) {
  const tabs = {
    queue: $("tab-queue"),
    bookshelf: $("tab-bookshelf"),
    collectors: $("tab-collectors"),
    intake: $("tab-intake"),
    settings: $("tab-settings"),
    analytics: $("tab-analytics")
  };
  const panels = {
    queue: $("view-desk"),
    bookshelf: $("bookshelf-panel"),
    collectors: $("collectors-panel"),
    intake: $("intake-panel"),
    settings: $("settings-panel"),
    analytics: $("analytics-panel")
  };

  for (const [key, btn] of Object.entries(tabs)) {
    if (btn) {
      if (key === active) btn.className = "nav-tab active";
      else btn.className = "nav-tab";
    }
  }

  for (const [key, panel] of Object.entries(panels)) {
    if (panel) {
      panel.hidden = (key !== active);
    }
  }

  if (active === "bookshelf") loadBookshelf();
  else if (active === "collectors") loadCollectorsView();
  else if (active === "analytics") loadAnalytics();
  else if (active === "settings") loadSettings();
}

if ($("tab-queue")) $("tab-queue").onclick = () => switchTab("queue");
if ($("tab-bookshelf")) $("tab-bookshelf").onclick = () => switchTab("bookshelf");
if ($("tab-collectors")) $("tab-collectors").onclick = () => switchTab("collectors");
if ($("btn-sync-square-all")) {
  $("btn-sync-square-all").onclick = async () => {
    const btn = $("btn-sync-square-all");
    btn.disabled = true;
    btn.textContent = "Syncing with Square...";
    try {
      const res = await api("/inventory/square/catalog/sync-all", { method: "POST" });
      message(`Synced ${res.synced_count} active volume(s) to Square POS Catalog.`);
      btn.textContent = `✓ Synced (${res.synced_count})`;
    } catch(err) {
      alert(`Square catalog sync error: ${err.message}`);
      btn.textContent = "📲 Sync Square POS";
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = "📲 Sync Square POS"; }, 3500);
    }
  };
}
if ($("btn-sync-ebay-all")) {
  $("btn-sync-ebay-all").onclick = async () => {
    const btn = $("btn-sync-ebay-all");
    btn.disabled = true;
    btn.textContent = "Syncing with eBay...";
    try {
      const res = await api("/inventory/ebay/sync-all", { method: "POST" });
      message(`Synced ${res.synced_count} active volume(s) to eBay Shop.`);
      btn.textContent = `✓ Synced (${res.synced_count})`;
    } catch(err) {
      alert(`eBay sync error: ${err.message}`);
      btn.textContent = "🛍️ Sync eBay Shop";
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = "🛍️ Sync eBay Shop"; }, 3500);
    }
  };
}
if ($("btn-sync-abebooks")) {
  $("btn-sync-abebooks").onclick = async () => {
    const btn = $("btn-sync-abebooks");
    btn.disabled = true;
    btn.textContent = "Checking AbeBooks...";
    try {
      const res = await api("/inventory/abebooks/sync-orders", { method: "POST" });
      message(`AbeBooks sync complete: checked ${res.orders_checked} order(s), processed ${res.orders_processed} sale(s).`);
      btn.textContent = `✓ Checked (${res.orders_processed})`;
    } catch(err) {
      alert(`AbeBooks sync error: ${err.message}`);
      btn.textContent = "📖 Sync AbeBooks";
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = "📖 Sync AbeBooks"; }, 3500);
    }
  };
}
if ($("btn-export-uiee")) {
  $("btn-export-uiee").onclick = () => {
    const sec = $("bookshelf-filter-section") ? $("bookshelf-filter-section").value : "ALL";
    const token = session ? session.token : "";
    const url = `/inventory/export/uiee?status=active&section=${encodeURIComponent(sec)}`;
    fetch(url, { headers: { "Authorization": `Bearer ${token}` } })
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.blob();
      })
      .then(blob => {
        const u = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = u;
        link.download = `catalog_${sec.toLowerCase()}_active.uiee`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        message(`UIEE catalog exported successfully for ${sec} section.`);
      })
      .catch(err => alert(`Export error: ${err.message}`));
  };
}
if ($("btn-audit-shelf")) $("btn-audit-shelf").onclick = () => showShelfAuditModal();
if ($("btn-audit-history")) $("btn-audit-history").onclick = () => showShelfAuditHistoryModal();
if ($("bookshelf-filter-section")) $("bookshelf-filter-section").onchange = () => renderBookshelf();
if ($("bookshelf-filter-shelf")) $("bookshelf-filter-shelf").oninput = () => renderBookshelf();
if ($("bookshelf-search")) $("bookshelf-search").oninput = () => renderBookshelf();
if ($("tab-intake")) $("tab-intake").onclick = () => switchTab("intake");
if ($("tab-settings")) $("tab-settings").onclick = () => switchTab("settings");
if ($("tab-analytics")) $("tab-analytics").onclick = () => switchTab("analytics");

if ($("desk-btn-intake")) {
  $("desk-btn-intake").onclick = () => {
    switchTab("intake");
    $("camera-file")?.click();
  };
}
if ($("desk-btn-sample")) {
  $("desk-btn-sample").onclick = () => {
    $("btn-sample-stack")?.click();
  };
}

// Power operator keyboard shortcuts
window.addEventListener("keydown", event => {
  if (!$("workspace") || $("workspace").hidden) return;
  const tag = (event.target && event.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

  const shown = items.filter(i => $("filter").value === "ALL" || i.status === $("filter").value);
  if (!shown.length) return;

  const currentIndex = shown.findIndex(i => i.review_id === selected);

  if (event.key === "j" || event.key === "ArrowDown") {
    event.preventDefault();
    const nextIndex = currentIndex < shown.length - 1 ? currentIndex + 1 : 0;
    select(shown[nextIndex].review_id);
  } else if (event.key === "k" || event.key === "ArrowUp") {
    event.preventDefault();
    const prevIndex = currentIndex > 0 ? currentIndex - 1 : shown.length - 1;
    select(shown[prevIndex].review_id);
  } else if (event.key === "a" || event.key === "A") {
    const approveBtn = Array.from($("detail").querySelectorAll("button")).find(b => b.textContent.includes("Approve"));
    if (approveBtn && !approveBtn.disabled) {
      event.preventDefault();
      approveBtn.click();
    }
  } else if (event.key === "x" || event.key === "X") {
    const rejectBtn = Array.from($("detail").querySelectorAll("button")).find(b => b.textContent.includes("Reject"));
    if (rejectBtn && !rejectBtn.disabled) {
      event.preventDefault();
      rejectBtn.click();
    }
  } else if (event.key === "e" || event.key === "E") {
    const execBtn = Array.from($("detail").querySelectorAll("button")).find(b => b.textContent.includes("Execute"));
    if (execBtn && !execBtn.disabled) {
      event.preventDefault();
      execBtn.click();
    }
  }
});

async function loadAnalytics() {
  try {
    const data = await api("/analytics");
    $("stat-total").textContent = data.total_automated_actions;
    $("stat-syncs").textContent = data.inventory_syncs;
    $("stat-collisions").textContent = data.collisions_prevented;
    $("stat-revenue").textContent = "$" + data.revenue_protected_cad.toFixed(2);
  } catch(e) {
    message("Failed to load analytics: " + e.message);
  }
}

async function loadSettings() {
  const list = $("connected-integrations-list");
  try {
    const integrations = await api("/integrations");
    list.replaceChildren();
    if (Object.keys(integrations).length === 0) {
      list.append(node("li", "No integrations connected yet.", "muted"));
    } else {
      for (const [provider, active] of Object.entries(integrations)) {
        if (active) {
          const li = node("li", `✓ ${provider.toUpperCase()}`);
          li.style.color = "green";
          li.style.fontWeight = "bold";
          list.append(li);
        }
      }
    }
  } catch(e) {
    list.replaceChildren(node("li", "Failed to load integrations: " + e.message, "muted"));
  }
}

if ($("form-integration")) {
  $("form-integration").onsubmit = async event => {
    event.preventDefault();
    const provider = $("integration-provider").value;
    const key = $("integration-key").value;
    const button = $("form-integration").querySelector("button");
    button.disabled = true;
    try {
      await api("/integrations", {
        method: "POST",
        body: JSON.stringify({ provider: provider, api_key: key })
      });
      $("integration-key").value = "";
      message(`Integration saved successfully for ${provider}.`);
      loadSettings();
    } catch(e) {
      message(e.message);
    } finally {
      button.disabled = false;
    }
  };
}

// =============================================================================
// COLLECTOR CRM & WANT LISTS & PRIORITY COURTESY HOLDS
// =============================================================================

let cachedCollectors = [];
let cachedWants = [];
let cachedHolds = [];

async function loadCollectorsView() {
  const desk = $("holds-desk-list");
  const grid = $("collectors-grid");
  const tblWrap = $("wants-table-wrap");

  if (desk) desk.innerHTML = '<p class="muted">Loading courtesy holds...</p>';
  if (grid) grid.innerHTML = '<p class="muted">Loading collectors...</p>';
  if (tblWrap) tblWrap.innerHTML = '<p class="muted">Loading wants...</p>';

  try {
    const [holds, collectors, wants] = await Promise.all([
      api("/holds"),
      api("/collectors"),
      api("/wants?status=all"),
    ]);

    cachedHolds = holds || [];
    cachedCollectors = collectors || [];
    cachedWants = wants || [];

    renderHolds(cachedHolds);
    renderCollectors(cachedCollectors);
    renderWants(cachedWants);

    const activeHolds = cachedHolds.filter(h => h.status === "active").length;
    const badge = $("tab-holds-badge");
    if (badge) {
      badge.textContent = activeHolds ? String(activeHolds) : "";
      badge.style.display = activeHolds ? "inline-block" : "none";
    }
    const chip = $("holds-active-count-chip");
    if (chip) chip.textContent = `${activeHolds} Active Hold${activeHolds === 1 ? '' : 's'}`;

    const wChip = $("wants-count-chip");
    if (wChip) {
      const actW = cachedWants.filter(w => w.status === "active").length;
      wChip.textContent = `${actW} Active`;
    }
  } catch (err) {
    message(`Error loading collector data: ${err.message}`);
  }
}

function renderHolds(holds) {
  const container = $("holds-desk-list");
  if (!container) return;
  container.replaceChildren();

  const activeHolds = holds.filter(h => h.status === "active");
  if (!activeHolds.length) {
    const empty = node("div", undefined, "empty-state-box");
    empty.style.gridColumn = "1 / -1";
    empty.innerHTML = `
      <p style="margin: 0; font-size: 14px;"><strong>No active courtesy holds.</strong></p>
      <p style="margin: 4px 0 0; font-size: 12px;">When newly photographed or cataloged books match an active collector want, they are intercepted here under a 48-hour marketplace embargo.</p>
    `;
    container.append(empty);
    return;
  }

  for (const h of activeHolds) {
    const card = node("div", undefined, "hold-card");

    // Header: Title, SKU, Price
    const header = node("div", undefined, "hold-card-header");
    const titleCol = node("div");
    const title = node("div", h.book_title || "Untitled Volume", "hold-book-title");
    const author = node("div", `by ${h.book_author || "Unknown"} · SKU: ${h.sku}`, "hold-book-author");
    titleCol.append(title, author);

    const price = node("div", h.price_cad ? `$${Number(h.price_cad).toFixed(2)} CAD` : "Price TBD", "hold-price");
    header.append(titleCol, price);

    // Collector info
    const colBox = node("div", undefined, "hold-collector-box");
    const colName = node("div", undefined, "hold-collector-name");
    colName.innerHTML = `<span>👤</span> <span>${h.collector_name}</span>`;
    const colContact = node("div", `${h.collector_phone || "No phone"} · ${h.collector_email || "No email"}`, "hold-collector-contact");
    colBox.append(colName, colContact);

    // Countdown timer
    const timerBar = node("div", undefined, "hold-timer-bar");
    if (h.is_expired) {
      timerBar.innerHTML = `<span class="hold-timer-expired">🚨 EXPIRED (48h reached)</span> <span>Ready to release</span>`;
    } else {
      timerBar.innerHTML = `<span class="hold-timer-active">⏳ ${h.remaining_hours}h remaining</span> <span>Embargo active</span>`;
    }

    // Actions
    const actions = node("div", undefined, "hold-actions");
    const claimBtn = node("button", "✅ Claim / Sold", "primary");
    claimBtn.onclick = () => claimHold(h.id, h.book_title, h.collector_name);

    const releaseBtn = node("button", "🌐 Release to Floor", "secondary");
    releaseBtn.onclick = () => releaseHold(h.id, h.book_title);

    const extendBtn = node("button", "+24h", "ghost-sm");
    extendBtn.title = "Extend hold by 24 hours";
    extendBtn.onclick = () => extendHold(h.id);

    actions.append(claimBtn, releaseBtn, extendBtn);

    card.append(header, colBox, timerBar, actions);
    container.append(card);
  }
}

async function claimHold(holdId, title, collectorName) {
  if (!confirm(`Confirm acquisition: Mark '${title}' as claimed/sold to ${collectorName}?\n\nThis will record the sale and delist the volume across all connected online channels.`)) return;
  try {
    await api(`/holds/${holdId}/claim`, { method: "POST", body: JSON.stringify({ notes: `Claimed by ${collectorName}` }) });
    message(`Acquisition recorded: '${title}' claimed for ${collectorName}. Delisted everywhere.`);
    loadCollectorsView();
    if (typeof loadBookshelf === "function") loadBookshelf();
  } catch (err) {
    alert(`Claim error: ${err.message}`);
  }
}

async function releaseHold(holdId, title) {
  if (!confirm(`Release courtesy hold on '${title}'?\n\nThis will re-activate the book on the physical floor and publish listings across eBay, AbeBooks, and Square.`)) return;
  try {
    await api(`/holds/${holdId}/release`, { method: "POST", body: JSON.stringify({ reason: "collector_passed" }) });
    message(`Hold released: '${title}' returned to active floor & marketplaces.`);
    loadCollectorsView();
    if (typeof loadBookshelf === "function") loadBookshelf();
  } catch (err) {
    alert(`Release error: ${err.message}`);
  }
}

async function extendHold(holdId) {
  try {
    const res = await api(`/holds/${holdId}/extend`, { method: "POST", body: JSON.stringify({ hours: 24 }) });
    message(`Hold extended by 24 hours (Expires: ${new Date(res.expires_at).toLocaleString()}).`);
    loadCollectorsView();
  } catch (err) {
    alert(`Extend error: ${err.message}`);
  }
}

function renderCollectors(collectors) {
  const container = $("collectors-grid");
  if (!container) return;
  container.replaceChildren();

  const q = $("collector-search-input") ? $("collector-search-input").value.trim().toLowerCase() : "";
  const filtered = q
    ? collectors.filter(c => c.name.toLowerCase().includes(q) || (c.email && c.email.toLowerCase().includes(q)) || (c.notes && c.notes.toLowerCase().includes(q)))
    : collectors;

  if (!filtered.length) {
    container.innerHTML = '<div class="empty-state-box" style="grid-column: 1 / -1;">No collectors found matching search query.</div>';
    return;
  }

  for (const c of filtered) {
    const card = node("div", undefined, "collector-card");
    const top = node("div", undefined, "collector-card-top");
    const nameCol = node("div");
    const name = node("div", c.name, "collector-name");
    const contact = node("div", `${c.phone || "No phone"} · ${c.email || "No email"}`, "collector-contact-line");
    nameCol.append(name, contact);

    const wantsCount = node("span", `${c.active_wants_count || 0} Wants`, "pill-badge pill-green");
    top.append(nameCol, wantsCount);

    const notes = node("div", c.notes || "General antiquarian collector.", "collector-notes");

    const footer = node("div", undefined, "collector-card-footer");
    const addWantBtn = node("button", "+ Add Want", "ghost-sm");
    addWantBtn.onclick = () => showNewWantModal(c.id);

    const viewWantsBtn = node("button", "Filter Wants", "ghost-sm");
    viewWantsBtn.onclick = () => {
      const filteredWants = cachedWants.filter(w => w.collector_id === c.id);
      renderWants(filteredWants);
      message(`Showing ${filteredWants.length} want(s) for ${c.name}.`);
    };

    footer.append(viewWantsBtn, addWantBtn);
    card.append(top, notes, footer);
    container.append(card);
  }
}

function renderWants(wants) {
  const container = $("wants-table-wrap");
  if (!container) return;
  container.replaceChildren();

  if (!wants.length) {
    container.innerHTML = '<div class="empty-state-box">No active wants registered. Click "+ Add Want" to record a collector request.</div>';
    return;
  }

  const table = node("table", undefined, "wants-table");
  const thead = node("thead");
  thead.innerHTML = `
    <tr>
      <th>Wanted Title</th>
      <th>Author</th>
      <th>Collector</th>
      <th>Max Budget</th>
      <th>Min Grade</th>
      <th>Binding</th>
      <th>Keywords / Notes</th>
      <th>Status</th>
      <th>Actions</th>
    </tr>
  `;
  table.append(thead);

  const tbody = node("tbody");
  for (const w of wants) {
    const tr = node("tr");
    const budgetStr = w.max_price_cad ? `$${Number(w.max_price_cad).toFixed(2)} CAD` : "Open Budget";
    const statusPill = w.status === "active" ? '<span class="pill-badge pill-green">Active</span>' : `<span class="pill-badge">${w.status}</span>`;

    tr.innerHTML = `
      <td><strong>${w.title}</strong></td>
      <td>${w.author || '<span class="muted">—</span>'}</td>
      <td>${w.collector_name || "Collector"}</td>
      <td style="font-weight: 600; color: var(--copper);">${budgetStr}</td>
      <td>${w.min_condition || '<span class="muted">Any</span>'}</td>
      <td>${w.binding || '<span class="muted">Any</span>'}</td>
      <td style="max-width: 200px; font-size: 11.5px; color: var(--muted);">${w.keywords || "—"}</td>
      <td>${statusPill}</td>
      <td></td>
    `;

    const actionsTd = tr.querySelector("td:last-child");
    if (w.status === "active") {
      const delBtn = node("button", "✕", "ghost-sm");
      delBtn.title = "Delete want";
      delBtn.onclick = async () => {
        if (!confirm(`Delete want '${w.title}'?`)) return;
        try {
          await api(`/wants/${w.id}`, { method: "DELETE" });
          message(`Want '${w.title}' deleted.`);
          loadCollectorsView();
        } catch (err) {
          alert(`Delete error: ${err.message}`);
        }
      };
      actionsTd.append(delBtn);
    }

    tbody.append(tr);
  }
  table.append(tbody);
  container.append(table);
}

function showNewCollectorModal() {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "audit-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", "📇 Register New Collector"));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const form = node("form", undefined, "standard-form");
  form.innerHTML = `
    <div>
      <label for="new-col-name">Collector Full Name *</label>
      <input id="new-col-name" type="text" required placeholder="e.g. Dr. Arthur Pendelton" style="width: 100%;">
    </div>
    <div style="display: flex; gap: 12px; margin-top: 12px;">
      <div style="flex: 1;">
        <label for="new-col-email">Email Address</label>
        <input id="new-col-email" type="email" placeholder="collector@domain.com" style="width: 100%;">
      </div>
      <div style="flex: 1;">
        <label for="new-col-phone">Phone Number</label>
        <input id="new-col-phone" type="tel" placeholder="(613) 555-0144" style="width: 100%;">
      </div>
    </div>
    <div style="margin-top: 12px;">
      <label for="new-col-notes">Collecting Interests & Profile Notes</label>
      <textarea id="new-col-notes" rows="3" placeholder="e.g. Early Canadian literature, Hugh MacLennan first editions, fine bindings..." style="width: 100%;"></textarea>
    </div>
    <div style="margin-top: 18px; display: flex; justify-content: flex-end; gap: 8px;">
      <button type="button" class="ghost-sm" id="modal-col-cancel">Cancel</button>
      <button type="submit" class="primary">Save Collector Profile →</button>
    </div>
  `;

  form.querySelector("#modal-col-cancel").onclick = () => overlay.remove();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const name = form.querySelector("#new-col-name").value.trim();
    const email = form.querySelector("#new-col-email").value.trim();
    const phone = form.querySelector("#new-col-phone").value.trim();
    const notes = form.querySelector("#new-col-notes").value.trim();

    try {
      await api("/collectors", {
        method: "POST",
        body: JSON.stringify({ name, email, phone, notes }),
      });
      message(`Collector '${name}' registered successfully.`);
      overlay.remove();
      loadCollectorsView();
    } catch (err) {
      alert(`Error creating collector: ${err.message}`);
    }
  };

  modal.append(form);
  overlay.append(modal);
  document.body.append(overlay);
}

function showNewWantModal(defaultCollectorId = "") {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "audit-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", "📖 Register Antiquarian Want"));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const colOptions = cachedCollectors
    .map(c => `<option value="${c.id}" ${c.id === defaultCollectorId ? "selected" : ""}>${c.name} (${c.phone || c.email || 'No contact'})</option>`)
    .join("");

  const form = node("form", undefined, "standard-form");
  form.innerHTML = `
    <div>
      <label for="want-col-select">Select Collector *</label>
      <select id="want-col-select" required style="width: 100%;">
        <option value="" disabled ${!defaultCollectorId ? "selected" : ""}>Select collector...</option>
        ${colOptions}
      </select>
    </div>
    <div style="display: flex; gap: 12px; margin-top: 12px;">
      <div style="flex: 2;">
        <label for="want-title">Book Title *</label>
        <input id="want-title" type="text" required placeholder="e.g. Two Solitudes" style="width: 100%;">
      </div>
      <div style="flex: 1;">
        <label for="want-author">Author</label>
        <input id="want-author" type="text" placeholder="e.g. Hugh MacLennan" style="width: 100%;">
      </div>
    </div>
    <div style="display: flex; gap: 12px; margin-top: 12px;">
      <div style="flex: 1;">
        <label for="want-max-price">Max Budget (CAD $)</label>
        <input id="want-max-price" type="number" step="0.50" placeholder="e.g. 175.00" style="width: 100%;">
      </div>
      <div style="flex: 1;">
        <label for="want-condition">Minimum Condition</label>
        <select id="want-condition" style="width: 100%;">
          <option value="">Any Condition</option>
          <option value="Fair">Fair or better</option>
          <option value="Good" selected>Good or better</option>
          <option value="Very Good">Very Good or better</option>
          <option value="Near Fine">Near Fine or better</option>
          <option value="Fine">Fine or As New</option>
        </select>
      </div>
      <div style="flex: 1;">
        <label for="want-binding">Binding</label>
        <select id="want-binding" style="width: 100%;">
          <option value="">Any Binding</option>
          <option value="Hardcover" selected>Hardcover</option>
          <option value="Paperback">Paperback</option>
          <option value="Leather">Leather / Deluxe</option>
        </select>
      </div>
    </div>
    <div style="margin-top: 12px;">
      <label for="want-keywords">Edition Details / Provenance Requirements</label>
      <textarea id="want-keywords" rows="2" placeholder="e.g. Collins 1945 first edition in original jacket preferred..." style="width: 100%;"></textarea>
    </div>
    <div style="margin-top: 18px; display: flex; justify-content: flex-end; gap: 8px;">
      <button type="button" class="ghost-sm" id="modal-want-cancel">Cancel</button>
      <button type="submit" class="primary">Record Active Want →</button>
    </div>
  `;

  form.querySelector("#modal-want-cancel").onclick = () => overlay.remove();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const collectorId = form.querySelector("#want-col-select").value;
    const title = form.querySelector("#want-title").value.trim();
    const author = form.querySelector("#want-author").value.trim();
    const maxPrice = form.querySelector("#want-max-price").value;
    const minCondition = form.querySelector("#want-condition").value;
    const binding = form.querySelector("#want-binding").value;
    const keywords = form.querySelector("#want-keywords").value.trim();

    try {
      await api(`/collectors/${collectorId}/wants`, {
        method: "POST",
        body: JSON.stringify({
          title,
          author,
          max_price_cad: maxPrice ? Number(maxPrice) : null,
          min_condition: minCondition || null,
          binding: binding || null,
          keywords,
        }),
      });
      message(`Want for '${title}' added to collector want list.`);
      overlay.remove();
      loadCollectorsView();
    } catch (err) {
      alert(`Error creating want: ${err.message}`);
    }
  };

  modal.append(form);
  overlay.append(modal);
  document.body.append(overlay);
}

if ($("btn-new-collector")) $("btn-new-collector").onclick = () => showNewCollectorModal();
if ($("btn-new-want")) $("btn-new-want").onclick = () => showNewWantModal();
if ($("btn-refresh-collectors")) $("btn-refresh-collectors").onclick = () => loadCollectorsView();
if ($("collector-search-input")) $("collector-search-input").oninput = () => renderCollectors(cachedCollectors);

