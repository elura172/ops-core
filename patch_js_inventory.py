with open("app/web/dashboard.js", "r") as f:
    content = f.read()

new_code = """
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

if ($("tab-queue") && $("tab-bookshelf")) {
  $("tab-queue").onclick = () => {
    $("tab-queue").className = "primary";
    $("tab-bookshelf").className = "";
    $("bookshelf-panel").hidden = true;
    $("queue-detail-panel").hidden = false;
  };
  $("tab-bookshelf").onclick = () => {
    $("tab-queue").className = "";
    $("tab-bookshelf").className = "primary";
    $("bookshelf-panel").hidden = false;
    $("queue-detail-panel").hidden = true;
    loadBookshelf();
  };
}
"""

content += new_code
with open("app/web/dashboard.js", "w") as f:
    f.write(content)
