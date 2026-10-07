"use strict";
const $ = id => document.getElementById(id);
let checkedItems = new Set();
let token = "", session = null, items = [], selected = null, generation = 0, selection = 0, busy = false;
const labels = {intake_book:"Intake Book", create_product_draft:"Create product draft", update_inventory:"Update stock", update_prices:"Update prices", publish:"Publish content", send_customer_response:"Send customer response", sync_inventory:"Cross-Channel Sync", fulfill_order:"Fulfill Order", resolve_order_contention:"Resolve Contention"};
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
  $("items").replaceChildren(); $("detail").replaceChildren();
  $("identity").textContent = ""; $("workspace").hidden = true; $("logout").hidden = true;
  $("login-section").hidden = false; 
  if ($("billing-section")) $("billing-section").hidden = true;
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
}

function renderQueue() {
  $("count").textContent = `${items.length} actions · ${items.filter(i=>i.status === "PENDING").length} awaiting review`;
  const list = $("items"); list.replaceChildren();
  const shown = items.filter(i=>$("filter").value === "ALL" || i.status === $("filter").value);
  
  if (shown.length) {
    const selAll = node("button", "Toggle Select All");
    selAll.style.marginBottom = "10px";
    selAll.onclick = () => {
      const allChecked = shown.every(i => checkedItems.has(i.review_id));
      if (allChecked) checkedItems.clear();
      else shown.forEach(i => checkedItems.add(i.review_id));
      renderQueue();
      renderBatchDetail();
    };
    list.append(selAll);
  }
  
  if (!shown.length) list.append(node("p", items.length ? "No actions match this filter." : "Your queue is clear. New proposals will appear here when a connected workflow submits them.", "empty"));
  for (const item of shown) {
    const row = node("div");
    row.style.display = "flex";
    row.style.alignItems = "center";
    
    const cb = node("input");
    cb.type = "checkbox";
    cb.checked = checkedItems.has(item.review_id);
    cb.style.marginRight = "8px";
    cb.onclick = (e) => {
      e.stopPropagation();
      if (cb.checked) checkedItems.add(item.review_id);
      else checkedItems.delete(item.review_id);
      renderBatchDetail();
    };
    
    const button = node("button", undefined, "item");
    button.style.flex = "1";
    button.setAttribute("aria-pressed", String(item.review_id === selected));
    button.append(node("span", human(item.status), `badge ${item.status.toLowerCase()}`), node("strong", labels[item.operation] || human(item.operation)), node("small", item.review_id));
    button.disabled = busy;
    button.onclick = ()=> {
      if (checkedItems.size > 0) {
        cb.checked = !cb.checked;
        cb.onclick({stopPropagation: ()=>{}});
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
      detail.append(node("h4", "Pricing Evidence", "eyebrow"));
      const evData = {};
      if (ev.amazon_lowest_fba) evData["Amazon Lowest FBA"] = `$${Number(ev.amazon_lowest_fba).toFixed(2)}`;
      if (ev.ebay_recent_average) evData["eBay Recent Avg"] = `$${Number(ev.ebay_recent_average).toFixed(2)}`;
      evData["Comps Found"] = ev.comps_found;
      evData["Confidence Score"] = `${Math.round(ev.confidence_score * 100)}%`;
      fields(detail, evData);
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

$("show-signup").onclick = (e) => { e.preventDefault(); $("login-panel").hidden = true; $("signup-panel").hidden = false; };
$("show-login").onclick = (e) => { e.preventDefault(); $("signup-panel").hidden = true; $("login-panel").hidden = false; };

async function handleAuth(path, body, button) {
  const epoch = ++generation;
  button.disabled = true; message("");
  try {
    const res = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Authentication failed");
    
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
        $("workspace").hidden = false;
        await refresh();
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


$("camera-file").onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  
  $("camera-status").textContent = `Processing image...`;
  const button = $("camera-upload").querySelector("label");
  button.style.opacity = "0.5";
  button.style.pointerEvents = "none";
  
  try {
    // Resize image to max 1200px to prevent Vercel 4.5MB limits and Gemini 503s on massive base64 payloads
    const compressedFile = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        let width = img.width, height = img.height;
        const max = 1500;
        if (width > height && width > max) { height *= max / width; width = max; }
        else if (height > max) { width *= max / height; height = max; }
        
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, width, height);
        
        canvas.toBlob(blob => {
          if (!blob) return reject(new Error("Canvas to Blob failed"));
          resolve(new File([blob], file.name, { type: "image/jpeg" }));
        }, "image/jpeg", 0.8);
      };
      img.onerror = reject;
      img.src = URL.createObjectURL(file);
    });

    $("camera-status").textContent = `Uploading ${compressedFile.name}...`;
    const formData = new FormData();
    formData.append("file", compressedFile);
    
    const epoch = generation;
    const response = await fetch("/intake/camera", {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` },
      body: formData
    });
    
    if (epoch !== generation) return;
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Upload failed");
    
    $("camera-status").textContent = `Extracted ${body.processed} books (${body.orientation}).`;
    message(`Vision AI extracted ${body.processed} books from the stack and queued them for review.`);
    refresh();
  } catch (error) {
    $("camera-status").textContent = "Upload failed.";
    message(error.message);
  } finally {
    $("camera-file").value = "";
    button.style.opacity = "1";
    button.style.pointerEvents = "auto";
  }
};

let inventoryItems = [];
async function renderBookshelf() {
  const list = $("bookshelf-items"); list.replaceChildren();
  if (!inventoryItems.length) {
    list.append(node("p", "Your bookshelf is empty.", "empty"));
    return;
  }
  for (const item of inventoryItems) {
    const card = node("div", undefined, "item");
    card.style.display = "block";
    card.style.marginBottom = "10px";
    
    card.append(node("h3", item.title));
    card.append(node("p", `SKU: ${item.sku} | Author: ${item.authors.join(", ")} | Format: ${item.format} | Condition: ${item.condition} | Status: ${item.status}`, "muted"));
    
    if (item.status === "active") {
      const priceLabel = node("label", "Online Price (CAD): ");
      const priceInput = node("input");
      priceInput.type = "number";
      priceInput.value = item.online_price_cad;
      priceInput.style.marginRight = "10px";
      
      const saveBtn = node("button", "Save Price");
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
          message(`Price updated and "Update prices" action queued for ${item.sku}.`);
        } catch(e) { message(e.message); }
        finally { saveBtn.disabled = false; }
      };
      
      const delistBtn = node("button", "Delist");
      delistBtn.onclick = async () => {
        if (!confirm(`Are you sure you want to delist ${item.sku}? This will set stock to 0 on external channels.`)) return;
        delistBtn.disabled = true;
        try {
          await api(`/inventory/${item.sku}`, { method: "DELETE" });
          message(`Book delisted and "Update stock" action queued for ${item.sku}.`);
          loadBookshelf();
        } catch(e) { message(e.message); }
      };
      
      card.append(priceLabel, priceInput, saveBtn, delistBtn);
    }
    
    list.append(card);
  }
}

async function loadBookshelf() {
  try {
    inventoryItems = await api("/inventory");
    renderBookshelf();
  } catch(e) { message(e.message); }
}

if ($("tab-queue") && $("tab-bookshelf") && $("tab-settings")) {
  $("tab-queue").onclick = () => {
    $("tab-queue").className = "primary";
    $("tab-bookshelf").className = "";
    $("tab-settings").className = "";
    $("bookshelf-panel").hidden = true;
    $("settings-panel").hidden = true;
    $("queue-detail-panel").hidden = false;
  };
  $("tab-bookshelf").onclick = () => {
    $("tab-queue").className = "";
    $("tab-bookshelf").className = "primary";
    $("tab-settings").className = "";
    $("bookshelf-panel").hidden = false;
    $("settings-panel").hidden = true;
    $("queue-detail-panel").hidden = true;
    loadBookshelf();
  };
  $("tab-settings").onclick = () => {
    $("tab-queue").className = "";
    $("tab-bookshelf").className = "";
    $("tab-settings").className = "primary";
    $("bookshelf-panel").hidden = true;
    $("settings-panel").hidden = false;
    $("queue-detail-panel").hidden = true;
    loadSettings();
  };
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
