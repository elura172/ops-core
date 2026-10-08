"use strict";
const $ = id => document.getElementById(id);
let checkedItems = new Set();
let token = "", session = null, items = [], selected = null, generation = 0, selection = 0, busy = false;
const labels = {intake_book:"Intake Book", create_product_draft:"Create product draft", update_inventory:"Update stock", update_prices:"Update prices", publish:"Publish content", send_customer_response:"Send customer response", sync_inventory:"Cross-Channel Sync", fulfill_order:"Fulfill Order", resolve_order_contention:"Resolve Contention", contention_hold_vs_marketplace:"Patron Hold Collision (Biblio)"};
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
    if (response.status === 401) disconnect();
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
  
  if (pendingCount > 0 && session && session.permissions.includes("review:decide")) {
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
  
  if (approvedCount > 0 && session && session.permissions.includes("review:execute")) {
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
      busy = false; message(`Batch execution completed: ${success} executed, ${fail} failed.`);
      refresh();
      if (typeof loadBookshelf === "function") {
        loadBookshelf();
      }
    };
    execBox.append(execBtn);
    detail.append(execBox);
  }

  if (session && session.permissions.includes("review:decide") && selectedItems.length > 0) {
    const delBox = node("div", undefined, "actions");
    delBox.style.marginTop = "16px";
    delBox.style.paddingTop = "12px";
    delBox.style.borderTop = "1px solid var(--line-subtle)";
    const label = `🗑️ Delete ${selectedItems.length} Selected ${selectedItems.length === 1 ? "Proposal" : "Proposals"}`;
    const delBatchBtn = node("button", label, "danger-sm");
    delBatchBtn.onclick = async () => {
      const count = selectedItems.length;
      if (!confirm(`Are you sure you want to delete ${count} ${count === 1 ? "proposal" : "proposals"}? This action cannot be reversed.`)) return;
      delBatchBtn.disabled = true;
      busy = true; $("refresh").disabled = true;
      detail.replaceChildren(node("p", `Deleting ${count} ${count === 1 ? "proposal" : "proposals"}...`));
      const idsToDelete = selectedItems.map(i => i.review_id);
      try {
        const res = await api("/reviews/batch-delete", {
          method: "POST",
          body: JSON.stringify({ review_ids: idsToDelete }),
        });
        idsToDelete.forEach(id => checkedItems.delete(id));
        message(`Batch delete completed: ${res.count ?? idsToDelete.length} proposals permanently removed.`);
      } catch (err) {
        let success = 0, fail = 0;
        for (const id of idsToDelete) {
          try {
            await api(`/reviews/${encodeURIComponent(id)}`, { method: "DELETE" });
            checkedItems.delete(id);
            success++;
          } catch (e) { fail++; }
        }
        message(`Batch delete completed: ${success} removed, ${fail} failed.`);
      } finally {
        busy = false;
        selected = null;
        refresh();
      }
    };
    delBox.append(delBatchBtn);
    detail.append(delBox);
  }
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
    } else if (item.operation === "contention_hold_vs_marketplace" && item.payload) {
      displayTitle = `⚠️ Hold Collision: ${item.payload.book_title || item.payload.sku}`;
      subDetail = `Patron: ${item.payload.collector_name || "Collector"} vs ${human(item.payload.source_channel || "Biblio")} #${item.payload.order_id}`;
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

    if (item.payload.issue_point_analysis) {
      const ip = item.payload.issue_point_analysis;
      const card = node("div", undefined, "issue-point-card");
      
      const headerRow = node("div", undefined, "issue-point-header");
      const titleSpan = node("h4", "🎯 Bibliographic Issue Point Audit", "eyebrow");
      
      let badgeClass = "badge-neutral";
      let badgeText = ip.classification;
      if (ip.is_first_edition && ip.is_first_impression) {
        badgeClass = "badge-first-edition";
        badgeText = "🎯 True 1st Edition (1st Printing)";
      } else if (ip.is_book_club) {
        badgeClass = "badge-bce";
        badgeText = "⚠️ Book Club Edition (BCE)";
      } else if (ip.impression_number && ip.impression_number > 1) {
        badgeClass = "badge-later-printing";
        badgeText = `📖 ${ip.impression_number}th Printing`;
      }
      
      const badge = node("span", badgeText, `issue-point-badge ${badgeClass}`);
      headerRow.append(titleSpan, badge);
      card.append(headerRow);

      const pubLine = node("div", undefined, "issue-point-pub-row");
      const confTag = node("span", (ip.confidence || "conclusive").toUpperCase(), "confidence-tag");
      pubLine.append(node("strong", "Publisher Rule: "), node("em", ip.publisher_rule_name || "Standard Antiquarian Inspection"), node("span", " · "), confTag);
      card.append(pubLine);

      if (ip.reasoning_trace) {
        const traceP = node("p", ip.reasoning_trace, "issue-point-trace");
        card.append(traceP);
      }

      if (ip.evidence_points && ip.evidence_points.length) {
        const evList = node("ul", undefined, "issue-point-evidence-list");
        ip.evidence_points.forEach(e => {
          const li = node("li", e);
          evList.append(li);
        });
        card.append(evList);
      }

      if (ip.disqualification_flags && ip.disqualification_flags.length) {
        const disList = node("ul", undefined, "issue-point-disqualify-list");
        ip.disqualification_flags.forEach(d => {
          const li = node("li", `⚠️ ${d}`);
          disList.append(li);
        });
        card.append(disList);
      }

      detail.append(card);
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
  } else if (item.operation === "contention_hold_vs_marketplace") {
    detail.append(node("h3", "⚠️ Patron Courtesy Hold vs Marketplace Collision"));
    const p = item.payload;
    const alertBox = node("div", undefined, "sanctuary-hold-alert");
    alertBox.style.padding = "12px";
    alertBox.style.marginBottom = "12px";
    alertBox.style.borderRadius = "var(--radius-sm)";
    alertBox.style.border = "1px solid var(--accent-gold)";
    alertBox.style.background = "rgba(212, 175, 55, 0.08)";
    alertBox.innerHTML = `<strong>Antiquarian Contention:</strong> An online order arrived on <strong>${human(p.source_channel || "Biblio")}</strong> (#${p.order_id}) for <em>${p.book_title || p.sku}</em>, but this volume is held under a 48-hour priority courtesy hold for <strong>${p.collector_name || "Collector"}</strong>.`;
    detail.append(alertBox);

    fields(detail, {
      "Target SKU": p.sku,
      "Book Title": p.book_title || p.sku,
      "Established Patron": p.collector_name || "VIP Collector",
      "Hold Expiry": p.hold_expires_at || "Active",
      "Marketplace Channel": human(p.source_channel || "Biblio"),
      "Marketplace Order": p.order_id,
      "Online Buyer": p.buyer_name || "Online Buyer",
      "Market Value": p.price_cad ? `$${Number(p.price_cad).toFixed(2)} CAD` : "N/A"
    });

    const guidance = node("div", undefined, "decision-guidance");
    guidance.style.marginTop = "10px";
    guidance.style.padding = "8px 12px";
    guidance.style.borderRadius = "var(--radius-sm)";
    guidance.style.background = "var(--surface-raised)";
    guidance.style.fontSize = "0.9rem";
    guidance.style.color = "var(--text-subtle)";
    guidance.innerHTML = `<strong>Operator Options:</strong><br>• <strong>Approve:</strong> Fulfill the ${human(p.source_channel || "Biblio")} order and release patron courtesy hold.<br>• <strong>Reject:</strong> Honor patron hold, keep book on shelf, and cancel ${human(p.source_channel || "Biblio")} order.`;
    detail.append(guidance);

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
    if (epoch === generation) {
      busy = false;
      await refresh();
      if (action === "execute" && typeof loadBookshelf === "function") {
        loadBookshelf();
      }
    }
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

let currentIntakeMode = "multi_stack";
const modePillStack = $("mode-pill-stack");
const modePillAngle = $("mode-pill-angle");

function setIntakeMode(mode) {
  currentIntakeMode = mode;
  if (modePillStack) {
    modePillStack.classList.toggle("active", mode === "multi_stack");
    const radio = modePillStack.querySelector("input");
    if (radio) radio.checked = mode === "multi_stack";
  }
  if (modePillAngle) {
    modePillAngle.classList.toggle("active", mode === "multi_angle_single_book");
    const radio = modePillAngle.querySelector("input");
    if (radio) radio.checked = mode === "multi_angle_single_book";
  }
}

if (modePillStack) modePillStack.onclick = () => setIntakeMode("multi_stack");
if (modePillAngle) modePillAngle.onclick = () => setIntakeMode("multi_angle_single_book");

let stagedPhotos = [];

function renderStagedTray() {
  const tray = $("staged-photos-tray");
  const grid = $("staged-thumbnails-grid");
  const countLabel = $("staged-tray-count");
  if (!tray || !grid) return;

  grid.replaceChildren();

  if (stagedPhotos.length === 0) {
    tray.hidden = true;
    return;
  }

  tray.hidden = false;
  if (countLabel) {
    countLabel.textContent = `${stagedPhotos.length} photo${stagedPhotos.length > 1 ? "s" : ""} staged for cataloging`;
  }

  stagedPhotos.forEach((file, idx) => {
    const item = node("div", undefined, "staged-thumb-item");
    const img = node("img", undefined, "staged-thumb-img");
    try {
      img.src = URL.createObjectURL(file);
    } catch (_) {}
    img.alt = file.name || `Photo ${idx + 1}`;

    const delBtn = node("button", "✕", "staged-thumb-del");
    delBtn.title = "Remove photo";
    delBtn.onclick = (e) => {
      e.stopPropagation();
      try { URL.revokeObjectURL(img.src); } catch (_) {}
      stagedPhotos.splice(idx, 1);
      renderStagedTray();
      if (stagedPhotos.length === 0) {
        $("camera-status").textContent = "Staging cleared. Ready for photo upload.";
      } else {
        $("camera-status").textContent = `${stagedPhotos.length} photo${stagedPhotos.length > 1 ? "s" : ""} staged.`;
      }
    };

    item.append(img, delBtn);
    grid.append(item);
  });
}

if ($("btn-clear-staged")) {
  $("btn-clear-staged").onclick = () => {
    stagedPhotos = [];
    renderStagedTray();
    $("camera-status").textContent = "Staging cleared. Ready for photo upload.";
  };
}

$("camera-file").onchange = event => {
  const files = event.target.files ? Array.from(event.target.files) : [];
  if (!files.length) return;

  stagedPhotos.push(...files);
  renderStagedTray();
  $("camera-status").textContent = `${stagedPhotos.length} photo${stagedPhotos.length > 1 ? "s" : ""} staged. Click 'Catalog Staged Photos' to begin.`;
  $("camera-file").value = "";
};

// Drag and drop onto intake dropzone
const intakeDropzone = $("intake-panel") ? $("intake-panel").querySelector(".intake-dropzone") : null;
if (intakeDropzone) {
  ["dragenter", "dragover"].forEach(evtName => {
    intakeDropzone.addEventListener(evtName, e => {
      e.preventDefault();
      e.stopPropagation();
      intakeDropzone.style.borderColor = "var(--copper)";
      intakeDropzone.style.background = "var(--surface-subtle)";
    });
  });
  ["dragleave", "drop"].forEach(evtName => {
    intakeDropzone.addEventListener(evtName, e => {
      e.preventDefault();
      e.stopPropagation();
      intakeDropzone.style.borderColor = "";
      intakeDropzone.style.background = "";
    });
  });
  intakeDropzone.addEventListener("drop", e => {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      const droppedFiles = Array.from(e.dataTransfer.files).filter(f =>
        f.type.startsWith("image/") || /\.(heic|heif|jpe?g|png|webp)$/i.test(f.name)
      );
      if (droppedFiles.length) {
        stagedPhotos.push(...droppedFiles);
        renderStagedTray();
        $("camera-status").textContent = `${stagedPhotos.length} photo${stagedPhotos.length > 1 ? "s" : ""} staged. Click 'Catalog Staged Photos' to begin.`;
      }
    }
  });
}

async function executeBatchIntake() {
  if (!stagedPhotos.length) {
    message("Please select or snap at least one photo first.");
    return;
  }

  const statusEl = $("camera-status");
  const snapBtn = $("camera-upload") ? $("camera-upload").querySelector("label.camera-snap-cta") : null;
  const processBtn = $("btn-process-staged");
  const summaryBox = $("batch-intake-summary");

  if (summaryBox) {
    summaryBox.hidden = true;
    summaryBox.replaceChildren();
  }

  if (snapBtn) {
    snapBtn.style.opacity = "0.5";
    snapBtn.style.pointerEvents = "none";
  }
  if (processBtn) {
    processBtn.disabled = true;
    processBtn.textContent = "Processing batch...";
  }

  try {
    statusEl.textContent = `Optimizing ${stagedPhotos.length} photo${stagedPhotos.length > 1 ? "s" : ""} for Gemini Vision...`;

    const preparedFiles = [];
    for (let i = 0; i < stagedPhotos.length; i++) {
      statusEl.textContent = `Optimizing photo ${i + 1} of ${stagedPhotos.length}...`;
      try {
        const prep = await prepareImageFile(stagedPhotos[i]);
        preparedFiles.push(prep);
      } catch (err) {
        console.warn("Client preparation failed, using raw file:", err);
        preparedFiles.push(stagedPhotos[i]);
      }
    }

    const formData = new FormData();
    preparedFiles.forEach(f => formData.append("files", f));
    formData.append("intake_mode", currentIntakeMode);
    if ($("intake-section")) formData.append("section", $("intake-section").value.trim() || "General");
    if ($("intake-shelf")) formData.append("shelf", $("intake-shelf").value.trim() || "");

    statusEl.textContent = currentIntakeMode === "multi_angle_single_book"
      ? `Synthesizing multi-angle views with Gemini Vision...`
      : `Analyzing ${preparedFiles.length} stack photos & deduplicating cross-image spines...`;

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

    // Reset staged photos
    stagedPhotos = [];
    renderStagedTray();

    const processedCount = body.processed || 0;
    const dupCount = (body.duplicates_suppressed && body.duplicates_suppressed.length) || 0;
    const failCount = (body.failed_images && body.failed_images.length) || 0;
    const successImgCount = body.successful_images != null ? body.successful_images : body.total_images;

    statusEl.textContent = `Batch complete: ${processedCount} books cataloged from ${successImgCount} photo${successImgCount === 1 ? "" : "s"}.`;

    if (summaryBox) {
      summaryBox.hidden = false;
      const header = node("div", undefined, "batch-summary-header");
      header.append(node("h4", currentIntakeMode === "multi_angle_single_book" ? "🔍 Multi-Angle Synthesized Volume" : "📚 Batch Intake Summary"));
      
      const stats = node("div", undefined, "batch-summary-stats");
      const stat1 = node("span", `✅ ${processedCount} Unique Books Cataloged`, "batch-stat-badge batch-stat-success");
      const stat2 = node("span", `📷 ${successImgCount}/${body.total_images} Photos Processed`, "batch-stat-badge batch-stat-success");
      stats.append(stat1, stat2);

      if (dupCount > 0) {
        const statDup = node("span", `✂️ ${dupCount} Overlapping Duplicate${dupCount > 1 ? "s" : ""} Suppressed`, "batch-stat-badge batch-stat-dup");
        stats.append(statDup);
      }
      if (failCount > 0) {
        const statFail = node("span", `⚠️ ${failCount} Photo${failCount > 1 ? "s" : ""} Failed`, "batch-stat-badge batch-stat-fail");
        stats.append(statFail);
      }
      summaryBox.append(header, stats);

      if (dupCount > 0) {
        const dupList = node("div", undefined, "batch-dup-list");
        dupList.append(node("strong", "Suppressed Cross-Image Duplicates:"));
        body.duplicates_suppressed.forEach(d => {
          const item = node("div", undefined, "batch-dup-item");
          item.textContent = `• "${d.title}" (${d.author || 'Unknown'}) — ${d.reason}`;
          dupList.append(item);
        });
        summaryBox.append(dupList);
      }

      if (failCount > 0) {
        const failList = node("div", undefined, "batch-dup-list");
        failList.append(node("strong", "Failed Photo Warnings:"));
        body.failed_images.forEach(f => {
          const item = node("div", undefined, "batch-dup-item");
          item.style.color = "#d32f2f";
          item.textContent = `• Photo ${f.image_index + 1} (${f.filename}): ${f.error}`;
          failList.append(item);
        });
        summaryBox.append(failList);
      }
    }

    message(currentIntakeMode === "multi_angle_single_book"
      ? `Multi-angle volume successfully synthesized and queued for review!`
      : `Batch cataloged ${processedCount} volumes (${dupCount} overlapping duplicates suppressed).`
    );
    refresh();

  } catch (error) {
    const msg = error && error.message ? error.message : String(error || "Upload failed");
    statusEl.textContent = `Batch upload failed: ${msg}`;
    message(`Upload failed: ${msg}`);
  } finally {
    if (snapBtn) {
      snapBtn.style.opacity = "1";
      snapBtn.style.pointerEvents = "auto";
    }
    if (processBtn) {
      processBtn.disabled = false;
      processBtn.textContent = "⚡ Catalog Staged Photos";
    }
  }
}

if ($("btn-process-staged")) {
  $("btn-process-staged").onclick = executeBatchIntake;
}


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
let selectedBookshelfSkus = new Set();
let currentFilteredSkus = [];

function playScanChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(1320, ctx.currentTime + 0.12);
    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.25);
  } catch(_) {}
}

function updateBookshelfBatchBar(filtered) {
  currentFilteredSkus = filtered.map(i => i.sku);
  const selectAllCb = $("bookshelf-select-all");
  const catalogBanner = $("bookshelf-catalog-banner");
  const stickyBar = $("bookshelf-sticky-bar");
  const stickyCount = $("sticky-selected-count");

  const selectedVisibleCount = currentFilteredSkus.filter(sku => selectedBookshelfSkus.has(sku)).length;

  if (selectAllCb) {
    if (currentFilteredSkus.length > 0 && selectedVisibleCount === currentFilteredSkus.length) {
      selectAllCb.checked = true;
      selectAllCb.indeterminate = false;
    } else if (selectedVisibleCount > 0) {
      selectAllCb.checked = false;
      selectAllCb.indeterminate = true;
    } else {
      selectAllCb.checked = false;
      selectAllCb.indeterminate = false;
    }
  }

  // Catalog expansion banner
  if (catalogBanner) {
    if (currentFilteredSkus.length > 0 && selectedVisibleCount === currentFilteredSkus.length && inventoryItems.length > currentFilteredSkus.length && selectedBookshelfSkus.size < inventoryItems.length) {
      catalogBanner.hidden = false;
      if ($("visible-selected-count")) $("visible-selected-count").textContent = currentFilteredSkus.length;
      if ($("total-catalog-count")) $("total-catalog-count").textContent = inventoryItems.length;
    } else {
      catalogBanner.hidden = true;
    }
  }

  // Sticky bottom action bar
  if (stickyBar) {
    if (selectedBookshelfSkus.size > 0) {
      stickyBar.hidden = false;
      if (stickyCount) stickyCount.textContent = selectedBookshelfSkus.size;
    } else {
      stickyBar.hidden = true;
    }
  }
}

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
    updateBookshelfBatchBar([]);
    return;
  }

  for (const item of filtered) {
    const card = node("div", undefined, "bookshelf-card");
    card.id = `card-${item.sku}`;
    if (selectedBookshelfSkus.has(item.sku)) card.classList.add("selected");
    
    const cardHeader = node("div", undefined, "bookshelf-card-header");
    const cb = node("input", undefined, "bookshelf-card-select-cb");
    cb.type = "checkbox";
    cb.checked = selectedBookshelfSkus.has(item.sku);
    cb.onclick = e => e.stopPropagation();
    cb.onchange = () => {
      if (cb.checked) {
        selectedBookshelfSkus.add(item.sku);
        card.classList.add("selected");
      } else {
        selectedBookshelfSkus.delete(item.sku);
        card.classList.remove("selected");
      }
      updateBookshelfBatchBar(filtered);
    };
    const title = node("h3", item.title);
    cardHeader.append(cb, title);
    card.append(cardHeader);

    const meta = node("div", undefined, "bookshelf-meta");
    const authorsStr = Array.isArray(item.authors) ? item.authors.join(", ") : (item.authors || "Unknown");
    meta.innerHTML = `<strong>${authorsStr}</strong><br>SKU: <code>${item.sku}</code> · Format: ${human(item.format)} · Grade: ${item.condition} · In-Store: $${Number(item.in_store_price_cad).toFixed(2)} CAD`;
    
    // Physical location & audit badges
    const locRow = node("div", undefined, "bookshelf-badge-row");
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
    
    const actions = node("div", undefined, "bookshelf-actions");
    if (item.status === "active") {
      const priceRow = node("div", undefined, "bookshelf-price-row");
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
          await api(`/inventory/${encodeURIComponent(item.sku)}`, { method: "DELETE" });
          message(`Book delisted and "Update stock" action queued for ${item.sku}.`);
          loadBookshelf();
        } catch(e) { message(e.message); delistBtn.disabled = false; }
      };

      priceRow.append(priceLabel, priceInput, saveBtn, delistBtn);

      const channelsRow = node("div", undefined, "bookshelf-channels-row");
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

      const deleteBookBtn = node("button", "🗑️ Delete", "danger-sm delete-btn");
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

      channelsRow.append(checkoutBtn, pushSquareBtn, pushEbayBtn, deleteBookBtn);
      actions.append(priceRow, channelsRow);
    } else {
      const inactiveRow = node("div", undefined, "bookshelf-channels-row");
      inactiveRow.append(node("span", `Status: ${human(item.status)}`, "muted"));

      if (item.status === "delisted") {
        const relistBtn = node("button", "🔄 Relist", "ghost-sm");
        relistBtn.title = "Restore book to active status on floor and channels";
        relistBtn.onclick = async () => {
          relistBtn.disabled = true;
          try {
            await api(`/inventory/${encodeURIComponent(item.sku)}/relist`, { method: "POST" });
            message(`Book "${item.title}" (${item.sku}) re-activated on floor.`);
            loadBookshelf();
          } catch(e) { message(e.message); relistBtn.disabled = false; }
        };
        inactiveRow.append(relistBtn);
      }

      const deleteBookBtn = node("button", "🗑️ Delete", "danger-sm delete-btn");
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
      inactiveRow.append(deleteBookBtn);
      actions.append(inactiveRow);
    }

    card.append(actions);
    
    list.append(card);
  }
  updateBookshelfBatchBar(filtered);
}

async function loadBookshelf() {
  try {
    inventoryItems = await api("/inventory");
    renderBookshelf();
  } catch(e) { message(e.message); }
}

async function downloadSelectedExport(format) {
  if (selectedBookshelfSkus.size === 0) return;
  try {
    const res = await fetch("/inventory/export", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        skus: Array.from(selectedBookshelfSkus),
        format: format
      })
    });
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = format === "uiee" ? `selected_catalog_${Date.now()}.uiee` : `selected_inventory_${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message(`Exported ${selectedBookshelfSkus.size} books as ${format.toUpperCase()}.`);
  } catch(e) {
    alert(e.message);
  }
}

function showBulkPriceAdjustModal() {
  if (selectedBookshelfSkus.size === 0) return;
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "bulk-price-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", `🏷️ Adjust Prices (${selectedBookshelfSkus.size} books)`));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const selectedItems = inventoryItems.filter(i => selectedBookshelfSkus.has(i.sku));

  const modeRow = node("div");
  modeRow.style.display = "flex";
  modeRow.style.gap = "12px";
  modeRow.style.alignItems = "center";

  const modeSelect = node("select");
  modeSelect.innerHTML = `
    <option value="percent">Percentage Adjustment (%)</option>
    <option value="fixed_delta">Dollar Delta (+/- $ CAD)</option>
    <option value="flat_override">Flat Price Override ($ CAD)</option>
  `;
  modeSelect.style.flex = "1";

  const valInput = node("input");
  valInput.type = "number";
  valInput.step = "0.50";
  valInput.value = "-10";
  valInput.style.width = "100px";

  modeRow.append(node("label", "Mode:"), modeSelect, valInput);
  modal.append(modeRow);

  const targetRow = node("div");
  targetRow.style.display = "flex";
  targetRow.style.gap = "12px";
  targetRow.style.alignItems = "center";
  const targetSelect = node("select");
  targetSelect.innerHTML = `
    <option value="both" selected>Both Online & In-Store Prices</option>
    <option value="online">Online Price Only</option>
    <option value="in_store">In-Store Price Only</option>
  `;
  targetSelect.style.flex = "1";
  targetRow.append(node("label", "Target:"), targetSelect);
  modal.append(targetRow);

  const previewWrap = node("div", undefined, "price-preview-table-wrap");
  const table = node("table", undefined, "price-preview-table");
  const thead = node("thead");
  thead.innerHTML = `
    <tr>
      <th>SKU</th>
      <th>Title</th>
      <th>Cur Online</th>
      <th>New Online</th>
      <th>Cur Store</th>
      <th>New Store</th>
    </tr>
  `;
  const tbody = node("tbody");
  table.append(thead, tbody);
  previewWrap.append(table);
  modal.append(previewWrap);

  function updatePreview() {
    tbody.replaceChildren();
    const mode = modeSelect.value;
    const val = parseFloat(valInput.value) || 0;
    const target = targetSelect.value;

    selectedItems.slice(0, 50).forEach(item => {
      const curOn = Number(item.online_price_cad);
      const curIn = Number(item.in_store_price_cad);
      let newOn = curOn;
      let newIn = curIn;

      if (mode === "flat_override") {
        if (target === "online" || target === "both") newOn = Math.max(0, val);
        if (target === "in_store" || target === "both") newIn = Math.max(0, val);
      } else if (mode === "fixed_delta") {
        if (target === "online" || target === "both") newOn = Math.max(0, curOn + val);
        if (target === "in_store" || target === "both") newIn = Math.max(0, curIn + val);
      } else if (mode === "percent") {
        const factor = 1 + (val / 100);
        if (target === "online" || target === "both") newOn = Math.max(0, curOn * factor);
        if (target === "in_store" || target === "both") newIn = Math.max(0, curIn * factor);
      }

      const tr = node("tr");
      tr.innerHTML = `
        <td><code>${item.sku}</code></td>
        <td style="max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${item.title}</td>
        <td>$${curOn.toFixed(2)}</td>
        <td style="font-weight:600; color:${newOn !== curOn ? 'var(--accent, #6366f1)' : 'inherit'}">$${newOn.toFixed(2)}</td>
        <td>$${curIn.toFixed(2)}</td>
        <td style="font-weight:600; color:${newIn !== curIn ? 'var(--accent, #6366f1)' : 'inherit'}">$${newIn.toFixed(2)}</td>
      `;
      tbody.append(tr);
    });
  }

  modeSelect.onchange = updatePreview;
  valInput.oninput = updatePreview;
  targetSelect.onchange = updatePreview;
  updatePreview();

  const actionRow = node("div");
  actionRow.style.display = "flex";
  actionRow.style.justifyContent = "flex-end";
  actionRow.style.gap = "8px";
  actionRow.style.marginTop = "8px";

  const cancelBtn = node("button", "Cancel", "ghost-sm");
  cancelBtn.onclick = () => overlay.remove();

  const applyBtn = node("button", `Apply to ${selectedItems.length} Books`, "primary");
  applyBtn.onclick = async () => {
    applyBtn.disabled = true;
    try {
      const mode = modeSelect.value;
      const val = parseFloat(valInput.value) || 0;
      const target = targetSelect.value;
      const res = await api("/inventory/batch-action", {
        method: "POST",
        body: JSON.stringify({
          action: "price_adjust",
          skus: Array.from(selectedBookshelfSkus),
          params: { price_mode: mode, price_value: val, price_target: target }
        })
      });
      message(`Adjusted prices on ${res.affected_count} books. Multi-channel price update proposal queued.`);
      overlay.remove();
      selectedBookshelfSkus.clear();
      loadBookshelf();
    } catch(e) {
      alert(e.message);
      applyBtn.disabled = false;
    }
  };

  actionRow.append(cancelBtn, applyBtn);
  modal.append(actionRow);

  overlay.append(modal);
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  document.body.append(overlay);
}

function showBulkRelocateModal() {
  if (selectedBookshelfSkus.size === 0) return;
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "bulk-price-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", `📍 Relocate ${selectedBookshelfSkus.size} Books`));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const secRow = node("div");
  secRow.style.margin = "10px 0";
  const secLabel = node("label", "Target Section:");
  secLabel.style.display = "block";
  secLabel.style.marginBottom = "4px";
  const secInput = node("input");
  secInput.placeholder = "e.g. Rare & Antiquarian, Sci-Fi, Folio Society";
  secInput.value = "Rare & Antiquarian";
  secInput.style.width = "100%";
  secRow.append(secLabel, secInput);
  modal.append(secRow);

  const shelfRow = node("div");
  shelfRow.style.margin = "10px 0";
  const shelfLabel = node("label", "Target Shelf:");
  shelfLabel.style.display = "block";
  shelfLabel.style.marginBottom = "4px";
  const shelfInput = node("input");
  shelfInput.placeholder = "e.g. Vault-B, A-02, Display-Case";
  shelfInput.value = "Vault-A";
  shelfInput.style.width = "100%";
  shelfRow.append(shelfLabel, shelfInput);
  modal.append(shelfRow);

  const actionRow = node("div");
  actionRow.style.display = "flex";
  actionRow.style.justifyContent = "flex-end";
  actionRow.style.gap = "8px";
  actionRow.style.marginTop = "14px";

  const cancelBtn = node("button", "Cancel", "ghost-sm");
  cancelBtn.onclick = () => overlay.remove();

  const applyBtn = node("button", `Relocate ${selectedBookshelfSkus.size} Books`, "primary");
  applyBtn.onclick = async () => {
    applyBtn.disabled = true;
    try {
      const res = await api("/inventory/batch-action", {
        method: "POST",
        body: JSON.stringify({
          action: "relocate",
          skus: Array.from(selectedBookshelfSkus),
          params: { section: secInput.value.trim() || "General", shelf: shelfInput.value.trim() }
        })
      });
      message(`Relocated ${res.affected_count} books to ${secInput.value} / ${shelfInput.value}.`);
      overlay.remove();
      selectedBookshelfSkus.clear();
      loadBookshelf();
    } catch(e) {
      alert(e.message);
      applyBtn.disabled = false;
    }
  };

  actionRow.append(cancelBtn, applyBtn);
  modal.append(actionRow);

  overlay.append(modal);
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  document.body.append(overlay);
}

function handleScannedBarcode(rawCode) {
  if (!rawCode) return;
  const code = rawCode.trim();
  const lower = code.toLowerCase();
  const cleanSku = lower.replace(/^bk-/i, "");

  const match = inventoryItems.find(i => {
    const s = (i.sku || "").toLowerCase();
    const sClean = s.replace(/^bk-/i, "");
    if (s === lower || sClean === cleanSku) return true;
    if ((i.title || "").toLowerCase().includes(lower)) return true;
    return false;
  });

  if (match) {
    playScanChime();
    if (typeof switchTab === "function") switchTab("bookshelf");
    renderBookshelf();
    setTimeout(() => {
      const card = document.getElementById(`card-${match.sku}`);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.classList.add("scanner-pulse");
        setTimeout(() => card.classList.remove("scanner-pulse"), 2500);
      }
      showScannedBookDrawer(match);
    }, 120);
    message(`Scanned: "${match.title}" (${match.sku})`);
  } else {
    if ($("bookshelf-search")) {
      $("bookshelf-search").value = code;
      renderBookshelf();
    }
    message(`Barcode "${code}" scanned: no direct catalog match found.`);
  }
}

function showScannedBookDrawer(item) {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "scanned-book-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", `📖 Scanned: ${item.title}`));
  const closeBtn = node("button", "✕", "ghost-sm");
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);
  modal.append(header);

  const info = node("div");
  const authorsStr = (item.authors || []).join(", ") || "Unknown";
  info.innerHTML = `
    <p style="margin:4px 0;"><strong>Author:</strong> ${authorsStr}</p>
    <p style="margin:4px 0;"><strong>SKU:</strong> <code>${item.sku}</code> · <strong>Status:</strong> ${human(item.status)}</p>
    <p style="margin:4px 0;"><strong>Location:</strong> 📍 ${item.section || "General"} · ${item.shelf || "Unassigned"}</p>
    <p style="margin:4px 0;"><strong>In-Store:</strong> $${Number(item.in_store_price_cad).toFixed(2)} CAD · <strong>Online:</strong> $${Number(item.online_price_cad).toFixed(2)} CAD</p>
  `;
  modal.append(info);

  const btnRow = node("div", undefined, "bookshelf-channels-row");
  btnRow.style.marginTop = "14px";
  btnRow.style.gap = "8px";

  if (item.status === "active") {
    const delistBtn = node("button", "❌ Delist", "ghost-sm");
    delistBtn.onclick = async () => {
      delistBtn.disabled = true;
      try {
        await api(`/inventory/${encodeURIComponent(item.sku)}`, { method: "DELETE" });
        message(`Book ${item.sku} delisted.`);
        overlay.remove();
        loadBookshelf();
      } catch(e) { alert(e.message); delistBtn.disabled = false; }
    };
    btnRow.append(delistBtn);
  } else {
    const relistBtn = node("button", "🔄 Relist", "ghost-sm");
    relistBtn.onclick = async () => {
      relistBtn.disabled = true;
      try {
        await api(`/inventory/${encodeURIComponent(item.sku)}/relist`, { method: "POST" });
        message(`Book ${item.sku} re-activated on floor.`);
        overlay.remove();
        loadBookshelf();
      } catch(e) { alert(e.message); relistBtn.disabled = false; }
    };
    btnRow.append(relistBtn);
  }

  const editLocBtn = node("button", "📍 Move Location", "ghost-sm");
  editLocBtn.onclick = async () => {
    const newSec = prompt("Section:", item.section || "General");
    if (newSec === null) return;
    const newShelf = prompt("Shelf:", item.shelf || "");
    if (newShelf === null) return;
    try {
      await api(`/inventory/${encodeURIComponent(item.sku)}`, {
        method: "PUT",
        body: JSON.stringify({ section: newSec.trim() || "General", shelf: newShelf.trim() })
      });
      overlay.remove();
      loadBookshelf();
    } catch(e) { alert(e.message); }
  };
  btnRow.append(editLocBtn);

  const auditBtn = node("button", "✓ Mark Audited", "primary");
  auditBtn.style.padding = "4px 10px";
  auditBtn.style.fontSize = "12px";
  auditBtn.onclick = async () => {
    try {
      await api("/inventory/audit/direct", {
        method: "POST",
        body: JSON.stringify({ section: item.section || "General", shelf: item.shelf || "", verified_skus: [item.sku], misplaced_skus: [] })
      });
      message(`Verified ${item.sku} in ${item.section || "General"} / ${item.shelf || "Unassigned"}.`);
      overlay.remove();
      loadBookshelf();
    } catch(e) { alert(e.message); }
  };
  btnRow.append(auditBtn);

  modal.append(btnRow);
  overlay.append(modal);
  overlay.onclick = e => { if (e.target === overlay) overlay.remove(); };
  document.body.append(overlay);
}

async function showCameraScannerModal() {
  const overlay = node("div", undefined, "sanctuary-modal-overlay");
  const modal = node("div", undefined, "scanner-modal");

  const header = node("div", undefined, "sanctuary-header");
  header.append(node("h3", "📷 Optical Barcode & ISBN Scanner"));
  const closeBtn = node("button", "✕", "ghost-sm");
  header.append(closeBtn);
  modal.append(header);

  const sub = node("p", "Point your camera at a book's ISBN barcode or EAN code to automatically locate and manage it.", "muted text-sm");
  modal.append(sub);

  const previewBox = node("div", undefined, "camera-preview-container");
  const video = node("video", undefined, "camera-preview-video");
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  video.setAttribute("playsinline", "");

  const overlayBox = node("div", undefined, "camera-scan-overlay");
  const laser = node("div", undefined, "camera-scan-laser");
  overlayBox.append(laser);
  previewBox.append(video, overlayBox);
  modal.append(previewBox);

  const statusP = node("p", "Initializing camera stream...", "muted text-sm");
  statusP.style.textAlign = "center";
  modal.append(statusP);

  const manualRow = node("div");
  manualRow.style.display = "flex";
  manualRow.style.gap = "8px";
  manualRow.style.marginTop = "6px";
  const manualInput = node("input");
  manualInput.placeholder = "Or type barcode / ISBN manually...";
  manualInput.style.flex = "1";
  const manualBtn = node("button", "Lookup", "ghost-sm");
  manualBtn.onclick = () => {
    if (manualInput.value.trim()) {
      cleanup();
      overlay.remove();
      handleScannedBarcode(manualInput.value.trim());
    }
  };
  manualInput.onkeydown = e => { if (e.key === "Enter") manualBtn.click(); };
  manualRow.append(manualInput, manualBtn);
  modal.append(manualRow);

  overlay.append(modal);
  document.body.append(overlay);

  let stream = null;
  let detector = null;
  let animId = null;
  let active = true;

  function cleanup() {
    active = false;
    if (animId) cancelAnimationFrame(animId);
    if (stream) stream.getTracks().forEach(t => t.stop());
  }

  closeBtn.onclick = () => { cleanup(); overlay.remove(); };
  overlay.onclick = e => { if (e.target === overlay) { cleanup(); overlay.remove(); } };

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }
    });
    video.srcObject = stream;
    await video.play();
    statusP.textContent = "Scanning for ISBN / EAN barcode...";

    if ('BarcodeDetector' in window) {
      detector = new window.BarcodeDetector({
        formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'qr_code']
      });

      async function scanFrame() {
        if (!active) return;
        try {
          if (video.readyState === video.HAVE_ENOUGH_DATA) {
            const barcodes = await detector.detect(video);
            if (barcodes.length > 0) {
              const code = barcodes[0].rawValue;
              cleanup();
              overlay.remove();
              handleScannedBarcode(code);
              return;
            }
          }
        } catch(_) {}
        animId = requestAnimationFrame(scanFrame);
      }
      animId = requestAnimationFrame(scanFrame);
    } else {
      statusP.textContent = "Camera active. Native BarcodeDetector not available in this browser; enter code below or use hardware scanner.";
    }
  } catch(err) {
    statusP.textContent = `Camera unavailable: ${err.message}. Enter barcode manually below.`;
  }
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
if ($("btn-sync-biblio")) {
  $("btn-sync-biblio").onclick = async (e) => {
    const btn = $("btn-sync-biblio");
    btn.disabled = true;
    btn.textContent = "Checking Biblio...";
    try {
      const isShift = e && e.shiftKey;
      const res = await api(`/inventory/biblio/sync-orders${isShift ? "?demo=true" : ""}`, { method: "POST" });
      if (res.orders_checked === 0 && !isShift) {
        const wantDemo = confirm("Checked Biblio API: 0 new open orders found. Would you like to simulate an incoming Biblio order to test collision detection and dual delisting?");
        if (wantDemo) {
          btn.textContent = "Simulating Biblio Order...";
          const demoRes = await api("/inventory/biblio/sync-orders?demo=true", { method: "POST" });
          message(`Biblio demo order processed: checked 1 order, processed ${demoRes.orders_processed} action(s). Check Review Queue for arbitration!`);
          refresh();
          return;
        }
      }
      message(`Biblio sync complete: checked ${res.orders_checked} order(s), processed ${res.orders_processed} sale(s).`);
      btn.textContent = `✓ Checked (${res.orders_processed})`;
      if (res.orders_processed > 0) refresh();
    } catch(err) {
      alert(`Biblio sync error: ${err.message}`);
      btn.textContent = "📚 Sync Biblio";
    } finally {
    }
  };
}

async function triggerReconciliationPulse(demo = false) {
  const logEl = $("pulse-result-log");
  const triggerBtn = $("btn-trigger-pulse");
  const demoBtn = $("btn-trigger-demo-pulse");
  
  if (triggerBtn) triggerBtn.disabled = true;
  if (demoBtn) demoBtn.disabled = true;
  if (logEl) {
    logEl.style.display = "block";
    logEl.textContent = `Pulse initiated (demo=${demo})... Polling channels & consulting Corvus Warden.`;
    logEl.style.color = "var(--text-muted)";
  }

  try {
    const res = await api(`/api/cron/sync${demo ? "?demo=true" : ""}`, { method: "POST" });
    const polledTotal = (res.orders_polled?.biblio || 0) + (res.orders_polled?.abebooks || 0);
    const approved = res.corvus_actions?.approved || 0;
    const collisions = res.collisions_detected || 0;
    
    if (logEl) {
      logEl.textContent = `✓ Pulse completed: ${res.tenants_checked || 1} tenant(s) checked | Orders polled: ${polledTotal} | Processed: ${res.orders_processed} | Holds checked: ${collisions} collisions | Corvus approved: ${approved}`;
      logEl.style.color = collisions > 0 ? "var(--warning, #f59e0b)" : "var(--success, #10b981)";
    }
    message(`Reconciliation pulse completed: ${res.orders_processed} orders processed, ${approved} Corvus auto-delists executed.`);
    refresh();
  } catch (err) {
    if (logEl) {
      logEl.textContent = `✗ Pulse failed: ${err.message}`;
      logEl.style.color = "var(--danger, #ef4444)";
    }
    alert(`Reconciliation error: ${err.message}`);
  } finally {
    if (triggerBtn) triggerBtn.disabled = false;
    if (demoBtn) demoBtn.disabled = false;
  }
}

if ($("btn-trigger-pulse")) {
  $("btn-trigger-pulse").onclick = () => triggerReconciliationPulse(false);
}
if ($("btn-trigger-demo-pulse")) {
  $("btn-trigger-demo-pulse").onclick = () => triggerReconciliationPulse(true);
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
if ($("btn-scan-barcode")) $("btn-scan-barcode").onclick = () => showCameraScannerModal();

if ($("bookshelf-select-all")) {
  $("bookshelf-select-all").onchange = () => {
    const checked = $("bookshelf-select-all").checked;
    currentFilteredSkus.forEach(sku => {
      if (checked) selectedBookshelfSkus.add(sku);
      else selectedBookshelfSkus.delete(sku);
    });
    renderBookshelf();
  };
}

if ($("btn-select-catalog-all")) {
  $("btn-select-catalog-all").onclick = () => {
    inventoryItems.forEach(i => selectedBookshelfSkus.add(i.sku));
    renderBookshelf();
  };
}

if ($("btn-bulk-clear")) {
  $("btn-bulk-clear").onclick = () => {
    selectedBookshelfSkus.clear();
    renderBookshelf();
  };
}

if ($("btn-bulk-delist")) {
  $("btn-bulk-delist").onclick = async () => {
    if (selectedBookshelfSkus.size === 0) return;
    if (!confirm(`Are you sure you want to delist ${selectedBookshelfSkus.size} books? This will mark them delisted and propose stock=0 on all sales channels.`)) return;
    $("btn-bulk-delist").disabled = true;
    try {
      const res = await api("/inventory/batch-action", {
        method: "POST",
        body: JSON.stringify({ action: "delist", skus: Array.from(selectedBookshelfSkus) })
      });
      message(`Delisted ${res.affected_count} books and queued multi-channel stock sync.`);
      selectedBookshelfSkus.clear();
      loadBookshelf();
    } catch(e) {
      alert(e.message);
    } finally {
      $("btn-bulk-delist").disabled = false;
    }
  };
}

if ($("btn-bulk-relist")) {
  $("btn-bulk-relist").onclick = async () => {
    if (selectedBookshelfSkus.size === 0) return;
    if (!confirm(`Relist ${selectedBookshelfSkus.size} books as active on floor and external channels?`)) return;
    $("btn-bulk-relist").disabled = true;
    try {
      const res = await api("/inventory/batch-action", {
        method: "POST",
        body: JSON.stringify({ action: "relist", skus: Array.from(selectedBookshelfSkus) })
      });
      message(`Relisted ${res.affected_count} books and queued multi-channel restoration.`);
      selectedBookshelfSkus.clear();
      loadBookshelf();
    } catch(e) {
      alert(e.message);
    } finally {
      $("btn-bulk-relist").disabled = false;
    }
  };
}

if ($("btn-bulk-price")) {
  $("btn-bulk-price").onclick = () => showBulkPriceAdjustModal();
}

if ($("btn-bulk-relocate")) {
  $("btn-bulk-relocate").onclick = () => showBulkRelocateModal();
}

if ($("btn-bulk-export-csv")) {
  $("btn-bulk-export-csv").onclick = () => downloadSelectedExport("csv");
}

if ($("btn-bulk-export-uiee")) {
  $("btn-bulk-export-uiee").onclick = () => downloadSelectedExport("uiee");
}

if ($("btn-bulk-delete")) {
  $("btn-bulk-delete").onclick = async () => {
    if (selectedBookshelfSkus.size === 0) return;
    if (!confirm(`⚠️ PERMANENT ACTION: Are you sure you want to permanently delete ${selectedBookshelfSkus.size} books from the database?`)) return;
    $("btn-bulk-delete").disabled = true;
    try {
      const res = await api("/inventory/batch-action", {
        method: "POST",
        body: JSON.stringify({ action: "delete", skus: Array.from(selectedBookshelfSkus) })
      });
      message(`Permanently deleted ${res.affected_count} books from database.`);
      selectedBookshelfSkus.clear();
      loadBookshelf();
    } catch(e) {
      alert(e.message);
    } finally {
      $("btn-bulk-delete").disabled = false;
    }
  };
}

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

let barcodeScannerBuffer = "";
let lastBarcodeKeyTime = 0;

// Power operator keyboard shortcuts & ambient barcode scanner listener
window.addEventListener("keydown", event => {
  const now = Date.now();
  const diff = now - lastBarcodeKeyTime;
  lastBarcodeKeyTime = now;

  if (diff > 75) {
    barcodeScannerBuffer = "";
  }

  if (event.key === "Enter") {
    if (barcodeScannerBuffer.length >= 3) {
      event.preventDefault();
      const code = barcodeScannerBuffer.trim();
      barcodeScannerBuffer = "";
      handleScannedBarcode(code);
      return;
    }
    barcodeScannerBuffer = "";
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    barcodeScannerBuffer += event.key;
  }

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

// ============================================================================
// AI ANTIQUARIAN VISION INTAKE 2.0 (IN-BROWSER CAMERA & MULTI-ANGLE STUDIO)
// ============================================================================

function generateBarcodeSvg(code) {
  if (!code) return "";
  const clean = String(code).toUpperCase().replace(/[^0-9A-Z\-\. \$\/\+\%]/g, "");
  const textToEncode = `*${clean}*`;

  const CODE39 = {
    '0': '000110100', '1': '100100001', '2': '001100001', '3': '101100000',
    '4': '000110001', '5': '100110000', '6': '001110000', '7': '000100101',
    '8': '100100100', '9': '001100100', 'A': '100001001', 'B': '001001001',
    'C': '101001000', 'D': '000011001', 'E': '100011000', 'F': '001011000',
    'G': '000001101', 'H': '100001100', 'I': '001001100', 'J': '000011100',
    'K': '100000011', 'L': '001000011', 'M': '101000010', 'N': '000010011',
    'O': '100010010', 'P': '001010010', 'Q': '000000111', 'R': '100000110',
    'S': '001000110', 'T': '000010110', 'U': '110000001', 'V': '011000001',
    'W': '111000000', 'X': '010010001', 'Y': '110010000', 'Z': '011010000',
    '-': '010000101', '.': '110000100', ' ': '011000100', '*': '010010100',
    '$': '010101000', '/': '010100010', '+': '010001010', '%': '000101010'
  };

  const narrow = 2;
  const wide = 5;
  const gap = 2;
  const height = 44;

  let x = 10;
  const rects = [];

  for (let i = 0; i < textToEncode.length; i++) {
    const ch = textToEncode[i];
    const pattern = CODE39[ch] || CODE39['*'];
    for (let j = 0; j < 9; j++) {
      const isBar = (j % 2 === 0);
      const isWide = pattern[j] === '1';
      const w = isWide ? wide : narrow;
      if (isBar) {
        rects.push(`<rect x="${x}" y="0" width="${w}" height="${height}" fill="#000" />`);
      }
      x += w;
    }
    x += gap;
  }
  const totalWidth = x + 10;
  return `<svg viewBox="0 0 ${totalWidth} ${height + 18}" width="${Math.min(totalWidth, 260)}" height="${Math.round((height + 18) * 0.8)}" xmlns="http://www.w3.org/2000/svg" style="background:#fff; border-radius:4px; padding:4px;">
    ${rects.join("")}
    <text x="${totalWidth / 2}" y="${height + 14}" text-anchor="middle" font-family="monospace" font-size="11" font-weight="bold" fill="#000">${clean}</text>
  </svg>`;
}

function printBookLabel(item) {
  const w = window.open("", "_blank", "width=420,height=300");
  if (!w) return;
  const sku = item.sku || "UNKNOWN";
  const title = (item.title || "").substring(0, 38);
  const author = Array.isArray(item.authors) ? item.authors.join(", ") : (item.authors || "");
  const price = (item.online_price_cad || item.in_store_price_cad)
    ? `$${Number(item.online_price_cad || item.in_store_price_cad).toFixed(2)} CAD`
    : "";
  const loc = `${item.section || "General"} · ${item.shelf || "Floor"}`;
  const barcodeSvg = generateBarcodeSvg(sku);
  w.document.write(`
    <!DOCTYPE html>
    <html>
    <head>
      <title>Label ${sku}</title>
      <style>
        @page { size: 2.25in 1.25in; margin: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 6px; padding: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
        .title { font-size: 11px; font-weight: 700; max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .author { font-size: 9px; color: #444; max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px; }
        .price { font-size: 11px; font-weight: 700; color: #111; margin-bottom: 3px; }
        .loc { font-size: 9px; color: #666; margin-top: 2px; }
      </style>
    </head>
    <body>
      <div class="title">${title}</div>
      <div class="author">${author}</div>
      <div class="price">${price}</div>
      ${barcodeSvg}
      <div class="loc">📍 ${loc}</div>
      <script>window.onload = () => { window.print(); setTimeout(() => window.close(), 500); };<\/script>
    </body>
    </html>
  `);
  w.document.close();
}

function createSampleBookShot(type) {
  const canvas = document.createElement("canvas");
  canvas.width = 400;
  canvas.height = 550;
  const ctx = canvas.getContext("2d");

  if (type === "cover") {
    ctx.fillStyle = "#1b2838";
    ctx.fillRect(0, 0, 400, 550);
    ctx.strokeStyle = "#c5a059";
    ctx.lineWidth = 4;
    ctx.strokeRect(20, 20, 360, 510);
    ctx.fillStyle = "#e5c158";
    ctx.font = "bold 38px serif";
    ctx.textAlign = "center";
    ctx.fillText("DUNE", 200, 180);
    ctx.fillStyle = "#f3f4f6";
    ctx.font = "italic 22px serif";
    ctx.fillText("FRANK HERBERT", 200, 240);
    ctx.fillStyle = "#c5a059";
    ctx.font = "14px sans-serif";
    ctx.fillText("CHILTON BOOKS", 200, 480);
  } else if (type === "title") {
    ctx.fillStyle = "#fbf8ee";
    ctx.fillRect(0, 0, 400, 550);
    ctx.fillStyle = "#111827";
    ctx.font = "bold 34px serif";
    ctx.textAlign = "center";
    ctx.fillText("DUNE", 200, 160);
    ctx.font = "18px serif";
    ctx.fillText("by", 200, 210);
    ctx.font = "bold 22px serif";
    ctx.fillText("FRANK HERBERT", 200, 250);
    ctx.font = "13px serif";
    ctx.fillText("CHILTON COMPANY - BOOK DIVISION", 200, 440);
    ctx.fillText("Publishers", 200, 465);
    ctx.fillText("Philadelphia  ·  New York", 200, 490);
  } else {
    ctx.fillStyle = "#fbf8ee";
    ctx.fillRect(0, 0, 400, 550);
    ctx.fillStyle = "#374151";
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("FIRST EDITION", 200, 200);
    ctx.fillText("Copyright © 1965 by Frank Herbert", 200, 230);
    ctx.fillText("Library of Congress Catalog Card Number 65-22547", 200, 260);
    ctx.fillText("All Rights Reserved", 200, 290);
    ctx.fillText("Printed in the United States of America", 200, 320);
  }

  return new Promise((resolve) => {
    canvas.toBlob((blob) => {
      resolve(new File([blob], `dune_${type}.jpg`, { type: "image/jpeg" }));
    }, "image/jpeg", 0.9);
  });
}

let visionMode = "3angle"; // "3angle" | "stack"
const visionSlotFiles = { cover: null, title: null, copyright: null };
const visionSlotUrls = { cover: null, title: null, copyright: null };
let visionStackFiles = [];
let visionCameraStream = null;
let visionCameraTargetSlot = null;

const slotTitles = {
  cover: "Front Cover & Spine",
  title: "Title Page",
  copyright: "Copyright / Colophon",
};
const slotIcons = {
  cover: "🖼️",
  title: "📖",
  copyright: "⚖️",
};

function initVisionIntake() {
  const modal = $("modal-vision-intake");
  const openBtn = $("btn-open-vision-intake");
  const closeBtn = $("btn-close-vision-modal");
  const cancelBtn = $("btn-cancel-vision");
  const mode3AngleBtn = $("vmode-3angle");
  const modeStackBtn = $("vmode-stack");
  const runBtn = $("btn-run-vision-intake");
  const sampleBtn = $("btn-vision-sample-dune");
  const cameraDrawer = $("vision-camera-drawer");
  const stopCamBtn = $("btn-stop-camera");
  const snapBtn = $("btn-snap-viewfinder");

  if (!modal || !openBtn) return;

  function updateRunButtonState() {
    if (!runBtn) return;
    if (visionMode === "3angle") {
      const hasAny = Boolean(visionSlotFiles.cover || visionSlotFiles.title || visionSlotFiles.copyright);
      runBtn.disabled = !hasAny;
    } else {
      runBtn.disabled = visionStackFiles.length === 0;
    }
  }

  function setSlotFile(slot, file) {
    if (visionSlotUrls[slot]) {
      URL.revokeObjectURL(visionSlotUrls[slot]);
      visionSlotUrls[slot] = null;
    }
    visionSlotFiles[slot] = file || null;
    const preview = $(`vslot-preview-${slot}`);
    if (!preview) return;

    if (file) {
      const url = URL.createObjectURL(file);
      visionSlotUrls[slot] = url;
      preview.innerHTML = `
        <div class="vslot-preview-filled">
          <img src="${url}" alt="${slotTitles[slot]} preview">
          <button type="button" class="btn-slot-remove" data-slot="${slot}" title="Remove photo">✕</button>
        </div>
      `;
      const remBtn = preview.querySelector(".btn-slot-remove");
      if (remBtn) {
        remBtn.onclick = (e) => {
          e.stopPropagation();
          setSlotFile(slot, null);
        };
      }
    } else {
      preview.innerHTML = `
        <div class="vslot-empty-state">
          <span class="vslot-icon">${slotIcons[slot]}</span>
          <span>Drop photo or snap</span>
        </div>
      `;
    }
    updateRunButtonState();
  }

  function openModal() {
    modal.hidden = false;
    if ($("vision-progress-card")) $("vision-progress-card").hidden = true;
    if ($("vision-result-card")) {
      $("vision-result-card").hidden = true;
      $("vision-result-card").replaceChildren();
    }
    updateRunButtonState();
  }

  function closeModal() {
    stopVisionCamera();
    modal.hidden = true;
  }

  openBtn.onclick = openModal;
  if (closeBtn) closeBtn.onclick = closeModal;
  if (cancelBtn) cancelBtn.onclick = closeModal;
  modal.onclick = (e) => {
    if (e.target === modal) closeModal();
  };

  // Mode switching
  if (mode3AngleBtn && modeStackBtn) {
    mode3AngleBtn.onclick = () => {
      visionMode = "3angle";
      mode3AngleBtn.classList.add("active");
      modeStackBtn.classList.remove("active");
      if ($("vslots-3angle-container")) $("vslots-3angle-container").hidden = false;
      if ($("vslots-stack-container")) $("vslots-stack-container").hidden = true;
      updateRunButtonState();
    };

    modeStackBtn.onclick = () => {
      visionMode = "stack";
      modeStackBtn.classList.add("active");
      mode3AngleBtn.classList.remove("active");
      if ($("vslots-3angle-container")) $("vslots-3angle-container").hidden = true;
      if ($("vslots-stack-container")) $("vslots-stack-container").hidden = false;
      updateRunButtonState();
    };
  }

  // Camera handling
  async function startVisionCamera(slot) {
    visionCameraTargetSlot = slot;
    if ($("camera-slot-title")) {
      $("camera-slot-title").textContent = `📷 Live Viewfinder: ${slotTitles[slot] || "Angle"}`;
    }
    if (cameraDrawer) cameraDrawer.hidden = false;

    try {
      if (visionCameraStream) {
        visionCameraStream.getTracks().forEach((t) => t.stop());
      }
      visionCameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
      const video = $("vision-video");
      if (video) {
        video.srcObject = visionCameraStream;
        await video.play();
      }
    } catch (err) {
      alert(`Camera unavailable: ${err.message}. You can upload a photo from your files.`);
      stopVisionCamera();
    }
  }

  function stopVisionCamera() {
    if (visionCameraStream) {
      visionCameraStream.getTracks().forEach((t) => t.stop());
      visionCameraStream = null;
    }
    const video = $("vision-video");
    if (video) video.srcObject = null;
    if (cameraDrawer) cameraDrawer.hidden = true;
    visionCameraTargetSlot = null;
  }

  if (stopCamBtn) stopCamBtn.onclick = stopVisionCamera;

  if (snapBtn) {
    snapBtn.onclick = () => {
      const video = $("vision-video");
      const canvas = $("vision-canvas");
      if (!video || !canvas || !visionCameraTargetSlot) return;

      canvas.width = video.videoWidth || 1280;
      canvas.height = video.videoHeight || 720;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      canvas.toBlob(
        (blob) => {
          if (!blob) return;
          const file = new File([blob], `${visionCameraTargetSlot}_snap.jpg`, { type: "image/jpeg" });
          setSlotFile(visionCameraTargetSlot, file);
          stopVisionCamera();
        },
        "image/jpeg",
        0.92
      );
    };
  }

  // Wire slot buttons and file inputs
  document.querySelectorAll(".btn-slot-cam").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const target = btn.dataset.target;
      if (target) startVisionCamera(target);
    };
  });

  document.querySelectorAll(".slot-file-input").forEach((input) => {
    input.onchange = (e) => {
      const target = input.dataset.target;
      if (target && input.files && input.files[0]) {
        setSlotFile(target, input.files[0]);
      }
    };
  });

  // Drag and drop onto individual 3-angle slot cards
  document.querySelectorAll(".vslot-card").forEach((card) => {
    const slot = card.dataset.slot;
    if (!slot) return;

    ["dragenter", "dragover"].forEach((evt) => {
      card.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        card.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach((evt) => {
      card.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        card.classList.remove("drag-over");
      });
    });
    card.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        const file = Array.from(e.dataTransfer.files).find((f) =>
          f.type.startsWith("image/") || /\.(jpe?g|png|webp|heic|heif)$/i.test(f.name)
        );
        if (file) setSlotFile(slot, file);
      }
    });
  });

  // Stack mode drag-and-drop & file selection
  const stackDropzone = $("vslots-stack-container");
  const stackInput = $("vstack-files");
  const stackThumbs = $("vstack-thumbs");

  function renderStackThumbs() {
    if (!stackThumbs) return;
    stackThumbs.replaceChildren();
    visionStackFiles.forEach((f, idx) => {
      const wrap = node("div", undefined, "vstack-thumb-wrap");
      const img = node("img");
      img.src = URL.createObjectURL(f);
      img.onload = () => URL.revokeObjectURL(img.src);
      wrap.append(img);
      const rem = node("button", "✕", "btn-thumb-remove");
      rem.onclick = (e) => {
        e.stopPropagation();
        visionStackFiles.splice(idx, 1);
        renderStackThumbs();
        updateRunButtonState();
      };
      wrap.append(rem);
      stackThumbs.append(wrap);
    });
  }

  if (stackInput) {
    stackInput.onchange = () => {
      if (stackInput.files && stackInput.files.length) {
        visionStackFiles.push(...Array.from(stackInput.files));
        renderStackThumbs();
        updateRunButtonState();
      }
    };
  }

  if (stackDropzone) {
    ["dragenter", "dragover"].forEach((evt) => {
      stackDropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        stackDropzone.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach((evt) => {
      stackDropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        stackDropzone.classList.remove("drag-over");
      });
    });
    stackDropzone.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        const files = Array.from(e.dataTransfer.files).filter((f) =>
          f.type.startsWith("image/") || /\.(jpe?g|png|webp|heic|heif)$/i.test(f.name)
        );
        if (files.length) {
          visionStackFiles.push(...files);
          renderStackThumbs();
          updateRunButtonState();
        }
      }
    });
  }

  // Render Extraction Result Preview Card
  function renderVisionResultCard(data) {
    const card = $("vision-result-card");
    if (!card) return;
    card.hidden = false;
    card.replaceChildren();

    const bookData = (data.analyzed_payloads && data.analyzed_payloads[0]) ||
      (data.extracted_items && data.extracted_items[0]) || {};
    const proposal = (data.proposals && data.proposals[0]) || {};
    const issuePoints = bookData.issue_point_analysis || {};
    const wantMatch = bookData.want_match || (proposal.payload && proposal.payload.want_match);

    const title = bookData.title || "Untitled Book";
    const authors = Array.isArray(bookData.authors) ? bookData.authors.join(", ") : (bookData.authors || "Unknown");
    const fmt = human(bookData.format || "hardcover_with_jacket");
    const condition = bookData.condition || "Good";
    const inStorePrice = bookData.in_store_price_cad || "0.00";
    const onlinePrice = bookData.online_price_cad || "0.00";
    const rationale = bookData.pricing_rationale || "";
    const isAutoRegistered = Boolean(data.auto_register && proposal.sku);
    const sku = proposal.sku || (proposal.result && proposal.result.sku) || "";

    // 1. Issue-Point Banner
    const banner = node("div", undefined, "vresult-banner");
    if (issuePoints.is_first_edition && issuePoints.is_first_impression) {
      banner.className = "vresult-banner first-edition";
      banner.innerHTML = `<span>🎯</span> <strong>TRUE 1st EDITION (1st Impression) CONFIRMED</strong> &nbsp;·&nbsp; ${issuePoints.publisher_rule_name || "Antiquarian Rule"}`;
    } else if (issuePoints.is_book_club) {
      banner.className = "vresult-banner book-club";
      banner.innerHTML = `<span>⚠️</span> <strong>BOOK CLUB EDITION (BCE) DISQUALIFIED</strong> &nbsp;·&nbsp; Reading copy valuation applied`;
    } else {
      banner.className = "vresult-banner";
      banner.style.background = "rgba(255, 255, 255, 0.08)";
      banner.innerHTML = `<span>📘</span> <strong>STANDARD TRADE EDITION</strong> &nbsp;·&nbsp; Bibliographic record verified`;
    }
    card.append(banner);

    // 2. Want Match Alert (if matched)
    if (wantMatch && wantMatch.matched) {
      const wantBanner = node("div", undefined, "vresult-banner");
      wantBanner.style.background = "rgba(245, 158, 11, 0.2)";
      wantBanner.style.border = "1px solid rgba(245, 158, 11, 0.5)";
      wantBanner.style.color = "#fef08a";
      wantBanner.innerHTML = `<span>⭐</span> <strong>PRIORITY COURTESY HOLD:</strong> Matched active want for <strong>${wantMatch.collector_name || "Collector"}</strong>! 48h hold initiated.`;
      card.append(wantBanner);
    }

    // 3. Grid: Left (Metadata & Pricing), Right (Barcode & SKU)
    const grid = node("div", undefined, "vresult-grid");

    const leftCol = node("div", undefined, "vresult-field-group");
    const bookTitleHeading = node("h4", title);
    bookTitleHeading.style.margin = "0";
    bookTitleHeading.style.fontSize = "1.1rem";
    bookTitleHeading.style.color = "var(--ink-bright, #fff)";

    const authorLine = node("p", `By ${authors}`, "muted text-sm");
    authorLine.style.margin = "2px 0 8px 0";

    const metaTags = node("div");
    metaTags.style.display = "flex";
    metaTags.style.gap = "8px";
    metaTags.style.flexWrap = "wrap";
    metaTags.style.marginBottom = "8px";

    const fmtBadge = node("span", `Format: ${fmt}`, "status-badge");
    const condBadge = node("span", `Condition: ${condition}`, "status-badge");
    metaTags.append(fmtBadge, condBadge);

    const priceInfo = node("div");
    priceInfo.style.background = "rgba(0, 0, 0, 0.3)";
    priceInfo.style.padding = "8px 12px";
    priceInfo.style.borderRadius = "6px";
    priceInfo.style.fontSize = "0.85rem";
    priceInfo.innerHTML = `
      <div><strong>Online Valuation:</strong> <span style="color:var(--green, #22c55e); font-weight:700;">$${onlinePrice} CAD</span> &nbsp;·&nbsp; <strong>In-Store:</strong> $${inStorePrice} CAD</div>
      <div style="font-size:0.75rem; color:#9ca3af; margin-top:4px;">${rationale}</div>
    `;

    leftCol.append(bookTitleHeading, authorLine, metaTags, priceInfo);

    // Right Column: Barcode & Actions
    const rightCol = node("div", undefined, "vresult-field-group");
    rightCol.style.alignItems = "center";
    rightCol.style.justifyContent = "center";

    if (isAutoRegistered && sku) {
      const barcodeBox = node("div", undefined, "vresult-barcode-box");
      barcodeBox.innerHTML = `
        ${generateBarcodeSvg(sku)}
        <span class="vresult-sku-text">${sku}</span>
      `;
      const printBtn = node("button", "🖨️ Print Barcode Label", "ghost-sm");
      printBtn.style.marginTop = "8px";
      printBtn.style.width = "100%";
      printBtn.onclick = () => {
        printBookLabel({
          sku,
          title,
          authors: [authors],
          in_store_price_cad: inStorePrice,
          online_price_cad: onlinePrice,
          section: $("vopt-section") ? $("vopt-section").value : "Rare Books",
          shelf: $("vopt-shelf") ? $("vopt-shelf").value : "",
        });
      };

      const viewShelfBtn = node("button", "📚 View on Bookshelf", "primary");
      viewShelfBtn.style.marginTop = "4px";
      viewShelfBtn.style.width = "100%";
      viewShelfBtn.onclick = () => {
        closeModal();
        switchTab("bookshelf");
        loadBookshelf();
      };

      rightCol.append(barcodeBox, printBtn, viewShelfBtn);
    } else {
      const statusBox = node("div");
      statusBox.style.background = "rgba(0, 0, 0, 0.3)";
      statusBox.style.padding = "12px";
      statusBox.style.borderRadius = "8px";
      statusBox.style.textAlign = "center";
      statusBox.innerHTML = `
        <div style="font-size:1.2rem; margin-bottom:4px;">📋</div>
        <strong>Queued for Review</strong>
        <p class="muted text-sm" style="margin:4px 0 8px 0;">Review ID: <code>${proposal.review_id || "rev-staged"}</code></p>
      `;
      const viewDeskBtn = node("button", "Review on Desk →", "primary");
      viewDeskBtn.style.width = "100%";
      viewDeskBtn.onclick = () => {
        closeModal();
        switchTab("queue");
        refresh();
      };
      statusBox.append(viewDeskBtn);
      rightCol.append(statusBox);
    }

    grid.append(leftCol, rightCol);
    card.append(grid);
  }

  // Sample Dune Test Button
  if (sampleBtn) {
    sampleBtn.onclick = async () => {
      sampleBtn.disabled = true;
      const prevHtml = sampleBtn.innerHTML;
      sampleBtn.innerHTML = `<span>⏳ Simulating 3-Angle Gemini Extraction...</span>`;

      // Fill visual slot previews
      try {
        const [coverFile, titleFile, copyFile] = await Promise.all([
          createSampleBookShot("cover"),
          createSampleBookShot("title"),
          createSampleBookShot("copyright")
        ]);
        setSlotFile("cover", coverFile);
        setSlotFile("title", titleFile);
        setSlotFile("copyright", copyFile);
      } catch (_) {}

      const progressCard = $("vision-progress-card");
      if (progressCard) {
        progressCard.hidden = false;
        if ($("vision-progress-title")) $("vision-progress-title").textContent = "Decoding Chilton 1965 Issue Points...";
        if ($("vision-progress-step")) $("vision-progress-step").textContent = "Synthesizing cover cloth, Title page imprint & 4-line copyright page...";
      }
      if ($("vision-result-card")) $("vision-result-card").hidden = true;

      try {
        const formData = new FormData();
        formData.append("auto_register", $("vopt-auto-register") ? $("vopt-auto-register").checked : true);
        formData.append("section", $("vopt-section") ? $("vopt-section").value.trim() || "Rare Books" : "Rare Books");
        formData.append("shelf", $("vopt-shelf") ? $("vopt-shelf").value.trim() || "Vault-01" : "Vault-01");

        const response = await fetch("/intake/sample-dune", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        });

        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `Sample intake failed (${response.status})`);
        }

        const data = await response.json();
        if (progressCard) progressCard.hidden = true;
        renderVisionResultCard(data);

        message("✨ Sample Dune 1st Edition analyzed and cataloged!");
        loadBookshelf();
        refresh();
      } catch (err) {
        if (progressCard) progressCard.hidden = true;
        alert(`Sample intake failed: ${err.message}`);
      } finally {
        sampleBtn.disabled = false;
        sampleBtn.innerHTML = prevHtml;
      }
    };
  }

  // Run Gemini Vision & Catalog
  if (runBtn) {
    runBtn.onclick = async () => {
      const filesToUpload =
        visionMode === "3angle"
          ? [visionSlotFiles.cover, visionSlotFiles.title, visionSlotFiles.copyright].filter(Boolean)
          : visionStackFiles;

      if (!filesToUpload.length) {
        alert("Please snap or select at least one photo first.");
        return;
      }

      runBtn.disabled = true;
      const prevRunHtml = runBtn.innerHTML;
      runBtn.innerHTML = `<span>⏳ Processing with Gemini Vision...</span>`;

      const progressCard = $("vision-progress-card");
      if (progressCard) {
        progressCard.hidden = false;
        if ($("vision-progress-title")) {
          $("vision-progress-title").textContent =
            visionMode === "3angle" ? "Analyzing Bibliographic Angles..." : "Analyzing Stack Spines...";
        }
        if ($("vision-progress-step")) {
          $("vision-progress-step").textContent =
            visionMode === "3angle"
              ? "Gemini Vision synthesizing cover, title page & copyright numberline..."
              : `Gemini Vision detecting every visible spine across ${filesToUpload.length} photo(s)...`;
        }
      }
      if ($("vision-result-card")) $("vision-result-card").hidden = true;

      try {
        const formData = new FormData();
        for (const file of filesToUpload) {
          try {
            const prep = await prepareImageFile(file);
            formData.append("files", prep);
          } catch (_) {
            formData.append("files", file);
          }
        }
        formData.append("intake_mode", visionMode === "3angle" ? "multi_angle_single_book" : "multi_stack");
        formData.append("section", $("vopt-section") ? $("vopt-section").value.trim() || "General" : "General");
        formData.append("shelf", $("vopt-shelf") ? $("vopt-shelf").value.trim() || "" : "");
        formData.append("auto_register", $("vopt-auto-register") ? $("vopt-auto-register").checked : true);

        const response = await fetch("/intake/camera", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        });

        const text = await response.text();
        let data;
        try {
          data = JSON.parse(text);
        } catch (_) {
          data = { detail: text || `Upload failed (${response.status})` };
        }

        if (!response.ok) {
          throw new Error(formatError(data.detail) || `Intake failed (${response.status})`);
        }

        if (progressCard) progressCard.hidden = true;
        renderVisionResultCard(data);

        message(
          data.auto_register
            ? `✨ Successfully cataloged and registered to Bookshelf!`
            : `✨ Analyzed and queued for review on Desk.`
        );
        loadBookshelf();
        refresh();
      } catch (err) {
        if (progressCard) progressCard.hidden = true;
        alert(`Gemini Vision Intake failed: ${err.message}`);
      } finally {
        runBtn.disabled = false;
        runBtn.innerHTML = prevRunHtml;
        updateRunButtonState();
      }
    };
  }
}

// Initialize on script load
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initVisionIntake);
} else {
  initVisionIntake();
}


