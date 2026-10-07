with open("app/api/http.py", "r") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "inventory_store = open_inventory_store" in line:
        lines[i] = "        inventory_store = open_inventory_store(settings.review_db)\n"
        break

with open("app/api/http.py", "w") as f:
    f.writelines(lines)
