from __future__ import annotations

import json
from pathlib import Path


def test_capacitor_config():
    root = Path(__file__).parent.parent
    config_file = root / "capacitor.config.json"
    assert config_file.exists(), "capacitor.config.json must exist"

    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data["appId"] == "com.corvus.antiquarian"
    assert data["appName"] == "Corvus Antiquarian"
    assert data["webDir"] == "app/web"
    assert "Camera" in data.get("plugins", {})


def test_android_scaffolding_and_permissions():
    root = Path(__file__).parent.parent
    manifest = root / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    assert manifest.exists(), "Android manifest must exist"

    content = manifest.read_text(encoding="utf-8")
    assert "android.permission.CAMERA" in content
    assert "android.permission.INTERNET" in content

    # Assets copied to android
    dashboard_html = root / "android" / "app" / "src" / "main" / "assets" / "public" / "dashboard.html"
    assert dashboard_html.exists(), "Synced web assets must exist in Android public directory"


def test_ios_scaffolding_and_plist():
    root = Path(__file__).parent.parent
    plist = root / "ios" / "App" / "App" / "Info.plist"
    assert plist.exists(), "iOS Info.plist must exist"

    content = plist.read_text(encoding="utf-8")
    assert "NSCameraUsageDescription" in content
    assert "NSPhotoLibraryUsageDescription" in content

    # Assets copied to iOS
    dashboard_html = root / "ios" / "App" / "App" / "public" / "dashboard.html"
    assert dashboard_html.exists(), "Synced web assets must exist in iOS public directory"


def test_mobile_dossier_and_server_settings_in_cockpit():
    root = Path(__file__).parent.parent
    html_file = root / "app" / "web" / "dashboard.html"
    js_file = root / "app" / "web" / "dashboard.js"
    css_file = root / "app" / "web" / "dashboard.css"

    html = html_file.read_text(encoding="utf-8")
    js = js_file.read_text(encoding="utf-8")
    css = css_file.read_text(encoding="utf-8")

    # Server settings modal & pill
    assert 'id="btn-server-settings"' in html
    assert 'id="modal-server-settings"' in html
    assert 'id="form-server-settings"' in html
    assert "initServerSettings" in js
    assert "resolveApiUrl" in js

    # OriginUI Command Palette & Quick-Jumper
    assert 'id="btn-command-palette-trigger"' in html
    assert 'id="modal-command-palette"' in html
    assert 'id="cmd-palette-input"' in html
    assert 'id="cmd-palette-list"' in html
    assert "initCommandPalette" in js
    assert ".cmd-palette-dialog" in css
    assert ".command-trigger-btn" in css

    # 5-slot Bibliographic Dossier
    assert 'data-slot="cover"' in html
    assert 'data-slot="spine"' in html
    assert 'data-slot="title"' in html
    assert 'data-slot="copyright"' in html
    assert 'data-slot="defect"' in html
    assert ".vslots-grid-5" in css

    # 3D Antiquarian Book Tilt & Sheen (ibelick/tilt + Emil Kowalski)
    assert "attachAntiquarian3DTilt" in js
    assert ".antiquarian-3d-book" in css
    assert ".book-spine-crease" in css
    assert ".book-glare-sheen" in css
    assert ".book-page-block" in css

    # Floating Mobile Glass Dock (ibelick/dock)
    assert 'id="floating-mobile-dock"' in html
    assert 'id="dock-tab-desk"' in html
    assert 'id="dock-tab-bookshelf"' in html
    assert 'id="dock-tab-snap"' in html
    assert 'id="dock-tab-holds"' in html
    assert 'id="dock-tab-cmd"' in html
    assert "initMobileGlassDock" in js
    assert ".mobile-glass-dock" in css
    assert ".dock-shutter-hero" in css

    # Capacitor camera & haptics checks in JS
    assert "Capacitor.Plugins.Camera" in js
    assert "triggerHaptic" in js
