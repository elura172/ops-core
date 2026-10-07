import re

with open("app/web/dashboard.js", "r") as f:
    content = f.read()

old_intake = """  if (item.operation === "intake_book" && item.payload.valuation) {
    const val = item.payload.valuation;
    const ext = item.payload.extraction;
    const dis = item.payload.disambiguation;
    
    detail.append(node("h3", "Book Intake Details"));
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
    
    const raw = node("details"); raw.append(node("summary", "View complete action data"), node("pre", JSON.stringify(item.payload, null, 2))); detail.append(raw);
  }"""

new_intake = """  if (item.operation === "intake_book" && item.payload.valuation) {
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
    
    const raw = node("details"); raw.append(node("summary", "View complete action data"), node("pre", JSON.stringify(item.payload, null, 2))); detail.append(raw);
  }"""

content = content.replace(old_intake, new_intake)

old_mutate = """    for (const approve of [true,false]) {
      const button = node("button", approve ? "Approve action" : "Reject action", approve ? "primary" : "");
      button.onclick = ()=>mutate(item, "decision", {approve, reason:reason.value});
      actions.append(button);
    }"""

new_mutate = """    for (const approve of [true,false]) {
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
    }"""

content = content.replace(old_mutate, new_mutate)

with open("app/web/dashboard.js", "w") as f:
    f.write(content)
