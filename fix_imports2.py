with open("app/db/inventory.py", "r") as f:
    content = f.read()

content = content.replace("import json", "import json\nfrom contextlib import contextmanager")

with open("app/db/inventory.py", "w") as f:
    f.write(content)
