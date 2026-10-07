with open("app/web/dashboard.html", "r") as f:
    content = f.read()

content = content.replace(
    '<h2>Review queue</h2>',
    '<h2>Review queue</h2><button type="button" id="tab-queue" class="primary" style="margin-right:10px;">Review Queue</button><button type="button" id="tab-bookshelf">Bookshelf (Inventory)</button>'
)

content = content.replace(
    '<section class="panel detail">',
    '<section id="bookshelf-panel" class="panel detail" hidden><h2>Inventory Bookshelf</h2><div id="bookshelf-items"></div></section>\n      <section id="queue-detail-panel" class="panel detail">'
)
content = content.replace(
    '</main>',
    '</section>\n    </main>'
)

with open("app/web/dashboard.html", "w") as f:
    f.write(content)
