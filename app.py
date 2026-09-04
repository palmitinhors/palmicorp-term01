from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import socket
import time
import urllib.parse
import urllib.request

PORT = 8080

# =========================================================
# PALMICORP CONFIG
# =========================================================

DEVICE_NAME = "PALM-TERM-01"
DEVICE_MODEL = "SAMSUNG GALAXY A02"

# Quando quiser conectar o MegaVault real,
# coloque o endereço dele aqui.
MEGAVAULT_URL = "http://127.0.0.1:5173"

# Quantos segundos sem heartbeat para considerar offline.
HEARTBEAT_TIMEOUT = 30

START_TIME = time.time()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PERSONAL_STATE_FILE = DATA_DIR / "personal_state.json"
ART_BOOKS_DIR = DATA_DIR / "art" / "books"
ART_DRAWINGS_DIR = DATA_DIR / "art" / "drawings"
AUTH_DIR = Path.home() / ".palmicorp"
AUTH_FILE = AUTH_DIR / "auth.json"
AUTH_DIR.mkdir(parents=True, exist_ok=True)
AUTH_ITERATIONS = 220_000
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSIONS = {}

for folder in (DATA_DIR, ART_BOOKS_DIR, ART_DRAWINGS_DIR):
    folder.mkdir(parents=True, exist_ok=True)


def load_auth_config():
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if not data.get("salt") or not data.get("hash"):
            return None
        return data
    except Exception:
        return None


def write_auth_config(data):
    temp = AUTH_FILE.with_name(AUTH_FILE.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except Exception:
        pass
    temp.replace(AUTH_FILE)
    try:
        os.chmod(AUTH_FILE, 0o600)
    except Exception:
        pass


def derive_password_hash(password, salt, iterations=AUTH_ITERATIONS):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        int(iterations),
    ).hex()


def configure_access_password(password):
    if load_auth_config() is not None:
        raise ValueError("A senha de acesso já foi configurada.")
    if len(password) < 8:
        raise ValueError("Use pelo menos 8 caracteres.")
    if len(password) > 200:
        raise ValueError("Senha longa demais.")
    salt = secrets.token_bytes(16)
    write_auth_config({
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": AUTH_ITERATIONS,
        "salt": salt.hex(),
        "hash": derive_password_hash(password, salt, AUTH_ITERATIONS),
        "created": datetime.now().isoformat(timespec="seconds"),
    })


def verify_access_password(password):
    config = load_auth_config()
    if not config:
        return False
    try:
        salt = bytes.fromhex(str(config["salt"]))
        iterations = int(config.get("iterations") or AUTH_ITERATIONS)
        actual = derive_password_hash(password, salt, iterations)
        return hmac.compare_digest(actual, str(config.get("hash") or ""))
    except Exception:
        return False


def purge_sessions():
    now = time.time()
    expired = [token for token, expires in SESSIONS.items() if expires <= now]
    for token in expired:
        SESSIONS.pop(token, None)


def create_session():
    purge_sessions()
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = time.time() + SESSION_TTL_SECONDS
    return token


def session_is_valid(token):
    if not token:
        return False
    purge_sessions()
    expires = SESSIONS.get(token)
    if not expires or expires <= time.time():
        SESSIONS.pop(token, None)
        return False
    return True


def read_cookie(header, name):
    if not header:
        return ""
    for part in header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value.strip()
    return ""


def default_personal_state():
    return {
        "art_days": 0,
        "study_days": 0,
        "art_log": [],
        "study_log": [],
        "art_last_day": None,
        "study_last_day": None,
        "ecommerce_days": 0,
        "ecommerce_last_day": None,
        "ecommerce_log": [],
        "notes": [],
        "tasks": [],
    }


def load_personal_state():
    try:
        data = json.loads(PERSONAL_STATE_FILE.read_text(encoding="utf-8"))
        state = default_personal_state()
        state.update(data if isinstance(data, dict) else {})
        return state
    except Exception:
        return default_personal_state()


def save_personal_state(state):
    PERSONAL_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def safe_filename(name):
    name = os.path.basename(str(name or "")).strip()
    name = re.sub(r"[^A-Za-z0-9._() -]+", "_", name)
    return name[:140] or "arquivo"


def list_art_files(folder):
    result = []
    try:
        for file in sorted(folder.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if file.is_file():
                info = file.stat()
                result.append({
                    "name": file.name,
                    "size": info.st_size,
                    "updated": int(info.st_mtime),
                })
    except Exception:
        pass
    return result


def personal_payload():
    state = load_personal_state()
    state["books"] = list_art_files(ART_BOOKS_DIR)
    state["drawings"] = list_art_files(ART_DRAWINGS_DIR)
    return state


devices = {
    "PALM-TERM-01": {
        "status": "ONLINE",
        "last_seen": time.time()
    },
    "PALM-TERM-02": {
        "status": "STANDBY",
        "last_seen": 0
    },
    "PALM-PC-01": {
        "status": "NOT CONNECTED",
        "last_seen": 0
    }
}


# =========================================================
# SYSTEM INFO
# =========================================================

def get_battery():
    try:
        output = os.popen("termux-battery-status").read()

        data = json.loads(output)

        return data.get("percentage")

    except:
        return None


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Não precisa realmente conectar à internet.
        s.connect(("8.8.8.8", 80))

        ip = s.getsockname()[0]

        s.close()

        return ip

    except:
        return "UNKNOWN"


def get_uptime():
    seconds = int(time.time() - START_TIME)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return f"{hours:02}:{minutes:02}:{secs:02}"


def megavault_status():
    if not MEGAVAULT_URL:
        return "NOT CONFIGURED"

    try:
        request = urllib.request.Request(
            MEGAVAULT_URL,
            headers={
                "User-Agent": "Palmicorp-Terminal/1.0"
            }
        )

        with urllib.request.urlopen(request, timeout=4) as response:
            if 200 <= response.status < 400:
                return "ONLINE"

    except:
        pass

    return "OFFLINE"


def update_device_states():
    now = time.time()

    devices["PALM-TERM-01"]["status"] = "ONLINE"
    devices["PALM-TERM-01"]["last_seen"] = now

    for name in ["PALM-TERM-02", "PALM-PC-01"]:

        last_seen = devices[name]["last_seen"]

        if last_seen == 0:
            continue

        if now - last_seen > HEARTBEAT_TIMEOUT:
            devices[name]["status"] = "OFFLINE"


# =========================================================
# WEB UI
# =========================================================

PAGE = r"""
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1, maximum-scale=1"
>

<title>PALMICORP // PALM-TERM-01</title>


<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background:
        radial-gradient(
            circle at top,
            #121923,
            #05070a 55%
        );

    color: #f1f5f9;

    font-family:
        Consolas,
        Monaco,
        monospace;

    min-height: 100vh;

}


#boot {

    position: fixed;

    inset: 0;

    background: #020304;

    display: flex;

    align-items: center;

    justify-content: center;

    flex-direction: column;

    z-index: 9999;

    transition: opacity 0.6s ease;

}


.boot-logo {

    font-size: 34px;

    letter-spacing: 8px;

    margin-bottom: 25px;

}


.boot-status {

    color: #7dff9b;

    font-size: 12px;

    letter-spacing: 2px;

}


.container {

    width: min(920px, 94%);

    margin: auto;

    padding: 25px 0 60px;

}


header {

    display: flex;

    align-items: center;

    justify-content: space-between;

    border-bottom: 1px solid #29313c;

    padding-bottom: 18px;

    margin-bottom: 25px;

}


.brand {

    font-size: 28px;

    letter-spacing: 6px;

    font-weight: bold;

}


.terminal-id {

    color: #8b98a8;

    font-size: 12px;

}


.grid {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(220px, 1fr)
        );

    gap: 14px;

}


.card {

    background: rgba(9, 13, 18, 0.88);

    border: 1px solid #303945;

    border-radius: 13px;

    padding: 18px;

}


.card-title {

    font-size: 11px;

    letter-spacing: 2px;

    color: #8794a5;

    margin-bottom: 14px;

}


.big {

    font-size: 25px;

    margin-bottom: 5px;

}


.small {

    color: #94a0ad;

    font-size: 12px;

}


.online {
    color: #52ff83;
}


.offline {
    color: #ff6868;
}


.standby {
    color: #ffd166;
}


.device {

    display: flex;

    justify-content: space-between;

    align-items: center;

    border-top: 1px solid #202832;

    padding: 12px 0;

}


.device:first-of-type {
    border-top: 0;
}


.device-name {
    font-size: 13px;
}


.status {

    font-size: 11px;

    letter-spacing: 1px;

}


.status::before {

    content: "●";

    margin-right: 7px;

}


.section {

    margin-top: 20px;

}


.buttons {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px, 1fr)
        );

    gap: 10px;

}


button {

    width: 100%;

    background: #0c1118;

    border: 1px solid #303945;

    color: white;

    padding: 14px;

    border-radius: 9px;

    font-family: inherit;

    cursor: pointer;

}


button:hover {

    background: #151d27;

}


footer {

    text-align: center;

    color: #5d6875;

    font-size: 11px;

    margin-top: 35px;

}


#clock {

    font-size: 28px;

}


#date {

    color: #84909d;

    margin-top: 3px;

    font-size: 12px;

}


.mega-online {
    color: #52ff83;
}

.mega-offline {
    color: #ff6868;
}

.mega-config {
    color: #ffd166;
}



/* =========================================================
   PALMICORP v2 // MODULE NAV + NYVIK ART
   ========================================================= */

.module-nav {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding: 8px 0 4px;
    margin-bottom: 18px;
    scrollbar-width: thin;
}

.module-nav button {
    width: auto;
    min-width: max-content;
    padding: 10px 14px;
    border-radius: 999px;
    font-size: 10px;
    letter-spacing: 1.4px;
}

.module-nav button.active {
    border-color: rgba(101,232,255,.7);
    background: rgba(101,232,255,.10);
    color: #9ff2ff;
    box-shadow: 0 0 18px rgba(101,232,255,.08);
}

.module-page { display: none; }
.module-page.active { display: block; }

.nyvik-hero {
    position: relative;
    overflow: hidden;
    min-height: 220px;
    display: flex;
    align-items: flex-end;
    padding: 28px;
    border: 1px solid rgba(213,164,255,.30);
    border-radius: 18px;
    background:
        radial-gradient(circle at 78% 15%, rgba(200,130,255,.20), transparent 34%),
        radial-gradient(circle at 18% 88%, rgba(90,210,255,.12), transparent 38%),
        linear-gradient(135deg, rgba(17,13,25,.97), rgba(7,10,15,.97));
    box-shadow: inset 0 0 70px rgba(192,117,255,.045);
}

.nyvik-hero::after {
    content: "NYVIK";
    position: absolute;
    right: -10px;
    top: -20px;
    font-size: clamp(70px, 16vw, 165px);
    font-weight: 900;
    letter-spacing: -8px;
    color: rgba(255,255,255,.025);
    pointer-events: none;
}

.nyvik-kicker {
    color: #d9a8ff;
    font-size: 10px;
    letter-spacing: 3px;
    margin-bottom: 8px;
}

.nyvik-title {
    font-size: clamp(38px, 8vw, 78px);
    line-height: .95;
    letter-spacing: 3px;
    font-weight: 800;
}

.nyvik-title span { color: #cf9cff; }

.nyvik-sub {
    color: #83909f;
    margin-top: 12px;
    font-size: 11px;
    letter-spacing: 1.6px;
}

.stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px,1fr));
    gap: 10px;
    margin-top: 14px;
}

.stat-box {
    background: rgba(8,12,17,.9);
    border: 1px solid #2c3540;
    border-radius: 12px;
    padding: 15px;
}

.stat-value {
    font-size: 28px;
    margin-top: 5px;
}

.art-action {
    border-color: rgba(207,156,255,.48);
    background: linear-gradient(135deg, rgba(207,156,255,.12), rgba(101,232,255,.06));
}

.art-action:hover { background: rgba(207,156,255,.18); }

.studio-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px,1fr));
    gap: 14px;
    margin-top: 14px;
}

.studio-card {
    background: rgba(8,12,17,.92);
    border: 1px solid #2c3540;
    border-radius: 15px;
    padding: 18px;
}

.studio-card h3 {
    margin: 0 0 6px;
    font-size: 14px;
    letter-spacing: 1.6px;
}

.studio-card p {
    color: #7f8c9a;
    font-size: 11px;
    line-height: 1.55;
}

.file-picker {
    display: block;
    border: 1px dashed #3a4653;
    border-radius: 12px;
    padding: 18px;
    text-align: center;
    cursor: pointer;
    color: #9aa6b4;
    margin-top: 12px;
}

.file-picker:hover { border-color: #cf9cff; color: #d9a8ff; }
.file-picker input { display: none; }

.library-list {
    display: grid;
    gap: 8px;
    margin-top: 12px;
    max-height: 300px;
    overflow: auto;
}

.library-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 11px;
    border: 1px solid #222b35;
    border-radius: 10px;
    background: #090d12;
}

.library-item a {
    color: #c7d0da;
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.art-gallery {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(130px,1fr));
    gap: 10px;
    margin-top: 12px;
}

.art-tile {
    position: relative;
    aspect-ratio: 4/5;
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #29333e;
    background: #080b0f;
}

.art-tile img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}

.art-tile span {
    position: absolute;
    left: 0; right: 0; bottom: 0;
    padding: 8px;
    font-size: 9px;
    background: linear-gradient(transparent, rgba(0,0,0,.9));
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.studio-textarea {
    width: 100%;
    min-height: 92px;
    resize: vertical;
    background: #070a0e;
    color: white;
    border: 1px solid #303945;
    border-radius: 10px;
    padding: 12px;
    font-family: inherit;
}

.log-list {
    display: grid;
    gap: 8px;
    margin-top: 12px;
}

.log-entry {
    border-left: 2px solid #cf9cff;
    background: rgba(207,156,255,.04);
    padding: 9px 11px;
    font-size: 11px;
    color: #aab4bf;
}

.etec-hero {
    border-color: rgba(101,232,255,.28);
    background:
        radial-gradient(circle at 90% 0%, rgba(101,232,255,.16), transparent 35%),
        rgba(7,11,16,.95);
}

.module-placeholder {
    min-height: 280px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    border: 1px dashed #33404d;
    border-radius: 16px;
    color: #71808f;
}

.module-placeholder strong {
    color: #c5d0da;
    font-size: 22px;
    letter-spacing: 2px;
    margin-bottom: 8px;
}

.module-toast {
    position: fixed;
    right: 18px;
    bottom: 18px;
    max-width: 320px;
    background: #0b1118;
    border: 1px solid #3a4652;
    border-radius: 12px;
    padding: 12px 14px;
    z-index: 10000;
    opacity: 0;
    transform: translateY(10px);
    pointer-events: none;
    transition: .2s ease;
}
.module-toast.show { opacity: 1; transform: translateY(0); }


/* =========================================================
   NYVIK VIEWER + E-COMMERCE LAB
   ========================================================= */

.library-item button {
    width: auto;
    padding: 7px 10px;
    font-size: 10px;
    border-color: rgba(207,156,255,.32);
}

.library-file-main {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
}

.library-file-name {
    color: #d7dee7;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.viewer-modal {
    position: fixed;
    inset: 0;
    z-index: 12000;
    display: none;
    background: rgba(1,3,6,.92);
    backdrop-filter: blur(14px);
    padding: 18px;
}

.viewer-modal.open { display: flex; }

.viewer-shell {
    width: min(1180px, 100%);
    height: min(92vh, 920px);
    margin: auto;
    display: flex;
    flex-direction: column;
    border: 1px solid rgba(207,156,255,.32);
    border-radius: 18px;
    overflow: hidden;
    background: #06090d;
    box-shadow: 0 30px 100px rgba(0,0,0,.55);
}

.viewer-head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 13px;
    border-bottom: 1px solid #222c36;
    background: rgba(9,13,19,.96);
}

.viewer-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: #d9e2ec;
    font-size: 11px;
    letter-spacing: 1px;
}

.viewer-head button,
.viewer-head a {
    width: auto;
    padding: 8px 11px;
    color: #c9d3dd;
    text-decoration: none;
    border: 1px solid #303945;
    border-radius: 8px;
    background: #0c1118;
    font: inherit;
    font-size: 10px;
    cursor: pointer;
}

.viewer-body {
    flex: 1;
    min-height: 0;
    display: grid;
    place-items: center;
    background:
        radial-gradient(circle at center, rgba(207,156,255,.06), transparent 45%),
        #030507;
}

.viewer-body img {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
}

.viewer-body iframe {
    width: 100%;
    height: 100%;
    border: 0;
    background: #fff;
}

.art-tile {
    cursor: zoom-in;
}

.ecom-hero {
    position: relative;
    overflow: hidden;
    min-height: 230px;
    display: flex;
    align-items: flex-end;
    padding: 28px;
    border: 1px solid rgba(255,182,72,.30);
    border-radius: 18px;
    background:
        radial-gradient(circle at 82% 18%, rgba(255,178,62,.20), transparent 32%),
        radial-gradient(circle at 15% 90%, rgba(82,255,131,.08), transparent 34%),
        linear-gradient(135deg, rgba(20,14,7,.98), rgba(6,10,12,.98));
    box-shadow: inset 0 0 80px rgba(255,177,65,.035);
}

.ecom-hero::after {
    content: "COMMERCE";
    position: absolute;
    right: -20px;
    top: -16px;
    font-size: clamp(54px, 12vw, 128px);
    font-weight: 900;
    letter-spacing: -7px;
    color: rgba(255,255,255,.022);
    pointer-events: none;
}

.ecom-kicker {
    color: #ffbd59;
    font-size: 10px;
    letter-spacing: 3px;
    margin-bottom: 8px;
}

.ecom-title {
    font-size: clamp(34px, 7vw, 68px);
    line-height: .95;
    letter-spacing: 2px;
    font-weight: 800;
}

.ecom-title span { color: #ffbd59; }

.ecom-sub {
    color: #8f887d;
    margin-top: 12px;
    font-size: 11px;
    letter-spacing: 1.5px;
}

.ecom-card {
    border-color: rgba(255,189,89,.20);
    background:
        linear-gradient(135deg, rgba(255,189,89,.035), transparent 45%),
        rgba(8,12,17,.94);
}

.ecom-select {
    width: 100%;
    margin: 8px 0 10px;
    padding: 11px;
    border-radius: 10px;
    border: 1px solid #39414a;
    background: #070a0e;
    color: #e7edf4;
    font-family: inherit;
}

.ecom-save {
    border-color: rgba(255,189,89,.45);
    background: linear-gradient(135deg, rgba(255,189,89,.12), rgba(82,255,131,.04));
}

.ecom-log .log-entry {
    border-left-color: #ffbd59;
    background: rgba(255,189,89,.035);
}



/* =========================================================
   PALMICORP v2.2 // NOTES + TASKS + ALERTS + WORLD CLOCK
   ========================================================= */

.form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 10px;
    margin: 10px 0;
}

.palm-input, .palm-select {
    width: 100%;
    padding: 11px 12px;
    border-radius: 10px;
    border: 1px solid #303945;
    background: #070a0e;
    color: #e8eef5;
    font-family: inherit;
}

.palm-label {
    display: grid;
    gap: 6px;
    color: #7f8b98;
    font-size: 10px;
    letter-spacing: 1px;
}

.notes-hero, .planner-hero {
    position: relative;
    overflow: hidden;
    min-height: 190px;
    display: flex;
    align-items: flex-end;
    padding: 26px;
    border-radius: 18px;
    border: 1px solid rgba(101,232,255,.24);
    background: radial-gradient(circle at 80% 20%, rgba(101,232,255,.14), transparent 32%), #071016;
}

.notes-hero::after { content: "NOTES"; }
.planner-hero::after { content: "PLAN"; }
.notes-hero::after, .planner-hero::after {
    position:absolute; right:-10px; top:-15px; font-size:clamp(70px,15vw,145px); font-weight:900;
    color:rgba(255,255,255,.025); letter-spacing:-7px; pointer-events:none;
}

.note-grid {
    display:grid;
    grid-template-columns: repeat(auto-fill, minmax(240px,1fr));
    gap:12px;
    margin-top:14px;
}
.note-card {
    border:1px solid #29333e; border-radius:13px; padding:14px; background:#090d12; min-height:135px;
}
.note-card.pinned { border-color:rgba(255,189,89,.46); box-shadow: inset 0 0 25px rgba(255,189,89,.03); }
.note-meta { color:#6f7d8b; font-size:9px; letter-spacing:1px; margin-bottom:8px; }
.note-title { color:#edf3f8; font-size:14px; font-weight:700; margin-bottom:8px; }
.note-body { color:#aeb8c3; font-size:11px; line-height:1.55; white-space:pre-wrap; }
.note-actions { display:flex; gap:7px; margin-top:12px; }
.note-actions button { width:auto; padding:7px 9px; font-size:9px; }

.task-list { display:grid; gap:9px; margin-top:12px; }
.task-item {
    display:grid; grid-template-columns:auto 1fr auto; gap:11px; align-items:center; padding:12px;
    border:1px solid #26313c; border-radius:11px; background:#090d12;
}
.task-item.done { opacity:.58; }
.task-item.done .task-title { text-decoration:line-through; }
.task-check { width:20px; height:20px; accent-color:#65e8ff; cursor:pointer; }
.task-title { color:#e9eff5; font-size:12px; }
.task-meta { color:#73808e; font-size:9px; margin-top:4px; }
.task-item.overdue { border-color:rgba(255,104,104,.42); }
.task-item.today { border-color:rgba(255,189,89,.42); }
.task-delete { width:auto; padding:7px 9px; font-size:9px; }

.calendar-shell {
    border:1px solid #27323d; border-radius:14px; padding:14px; background:#080c11; margin-top:14px;
}
.calendar-head { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px; }
.calendar-head button { width:auto; padding:7px 10px; }
.calendar-title { font-size:13px; letter-spacing:1.4px; color:#dce5ed; }
.calendar-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:5px; }
.calendar-cell {
    min-height:64px; border:1px solid #1f2933; border-radius:8px; padding:6px; color:#8090a0; font-size:10px; background:#070a0e;
}
.calendar-cell.head { min-height:auto; border:0; background:transparent; text-align:center; color:#657382; }
.calendar-cell.today { border-color:#65e8ff; color:#d9f8ff; }
.calendar-cell.has-task::after { content:"•"; display:block; color:#ffbd59; font-size:20px; line-height:12px; }
.calendar-cell.muted { opacity:.25; }

.alert-list { display:grid; gap:9px; margin-top:12px; }
.alert-item { padding:12px 13px; border-radius:10px; border:1px solid #28333e; background:#090d12; }
.alert-item.warn { border-color:rgba(255,189,89,.42); }
.alert-item.danger { border-color:rgba(255,104,104,.48); }
.alert-item.ok { border-color:rgba(82,255,131,.28); }
.alert-title { font-size:11px; color:#e8eef5; }
.alert-text { margin-top:4px; color:#7d8996; font-size:10px; line-height:1.45; }

.world-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:12px; }
.world-clock { border:1px solid #27323c; border-radius:11px; padding:13px; background:#080c11; }
.world-city { color:#7f8c99; font-size:9px; letter-spacing:1.5px; }
.world-time { color:#f0f5f9; font-size:22px; margin-top:6px; }
.world-date { color:#61707f; font-size:9px; margin-top:3px; }

.ecom-fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:9px; margin:10px 0; }
.ecom-mini { min-height:72px; }
.ecom-filterbar { display:grid; grid-template-columns:1fr minmax(160px,220px); gap:9px; margin-bottom:10px; }
.ecom-entry-title { color:#ffd18a; font-size:11px; margin-bottom:4px; }
.ecom-entry-meta { color:#7d756a; font-size:9px; margin-bottom:7px; }
.ecom-entry-section { color:#aeb8c3; font-size:10px; line-height:1.5; margin-top:5px; white-space:pre-wrap; }

@media(max-width:600px) {
    .ecom-filterbar { grid-template-columns:1fr; }
    .task-item { grid-template-columns:auto 1fr; }
    .task-delete { grid-column:2; justify-self:start; }
}

@media(max-width: 600px) {
    .viewer-modal { padding: 6px; }
    .viewer-shell { height: 96vh; border-radius: 12px; }
    .viewer-head { flex-wrap: wrap; }
}


/* =========================================================
   PALMICORP v2.3 // ACCESS CORE + OFFLINE UI + CUSTOM GLYPHS
   ========================================================= */

.lock-screen {
    position: fixed;
    inset: 0;
    z-index: 20000;
    display: none;
    place-items: center;
    padding: 18px;
    background:
        radial-gradient(circle at 50% 32%, rgba(101,232,255,.11), transparent 24%),
        radial-gradient(circle at 50% 58%, rgba(119,255,157,.06), transparent 32%),
        linear-gradient(180deg, #020407, #05080c 70%, #020304);
    overflow: hidden;
}
.lock-screen.open { display: grid; }
.lock-screen::before {
    content: "";
    position: absolute;
    inset: -20%;
    background:
        linear-gradient(rgba(101,232,255,.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(101,232,255,.025) 1px, transparent 1px);
    background-size: 34px 34px;
    transform: perspective(520px) rotateX(58deg) translateY(20%);
    transform-origin: center 70%;
    mask-image: linear-gradient(to bottom, transparent 2%, #000 35%, transparent 82%);
    animation: lockGrid 12s linear infinite;
}
@keyframes lockGrid { to { background-position: 0 68px, 68px 0; } }

.lock-shell {
    position: relative;
    width: min(500px, 95vw);
    border: 1px solid rgba(101,232,255,.28);
    border-radius: 22px;
    padding: 28px;
    background: rgba(4,8,12,.92);
    box-shadow: 0 35px 120px rgba(0,0,0,.62), inset 0 0 70px rgba(101,232,255,.035);
    backdrop-filter: blur(16px);
}
.lock-topline {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    color: #617180;
    font-size: 9px;
    letter-spacing: 1.8px;
}
.lock-core {
    width: 112px;
    height: 112px;
    border: 1px solid rgba(101,232,255,.48);
    border-radius: 50%;
    margin: 26px auto 20px;
    display: grid;
    place-items: center;
    position: relative;
    color: #dffaff;
    font-size: 38px;
    font-weight: 800;
    letter-spacing: -2px;
    box-shadow: 0 0 35px rgba(101,232,255,.10), inset 0 0 28px rgba(101,232,255,.05);
}
.lock-core::before, .lock-core::after {
    content: "";
    position: absolute;
    inset: -9px;
    border-radius: 50%;
    border: 1px dashed rgba(119,255,157,.34);
    animation: lockSpin 12s linear infinite;
}
.lock-core::after {
    inset: 12px;
    border-color: rgba(207,156,255,.24);
    animation-duration: 8s;
    animation-direction: reverse;
}
@keyframes lockSpin { to { transform: rotate(360deg); } }

.lock-title { text-align:center; font-size:clamp(28px,7vw,44px); letter-spacing:5px; font-weight:800; }
.lock-sub { text-align:center; margin-top:7px; color:#697786; font-size:9px; letter-spacing:2.2px; }
.lock-status {
    margin: 18px 0 12px;
    min-height: 18px;
    text-align: center;
    color: #77ff9d;
    font-size: 10px;
    letter-spacing: 1.4px;
}
.lock-field { position: relative; }
.lock-field input {
    width: 100%;
    padding: 15px 14px;
    border-radius: 11px;
    border: 1px solid #32404c;
    background: #03070a;
    color: #f0f7fb;
    font-family: inherit;
    letter-spacing: 1px;
    outline: none;
}
.lock-field input:focus { border-color:#65e8ff; box-shadow:0 0 0 3px rgba(101,232,255,.06); }
.lock-confirm { margin-top:9px; display:none; }
.lock-confirm.show { display:block; }
.lock-action {
    margin-top: 12px;
    border-color: rgba(101,232,255,.46);
    background: linear-gradient(135deg, rgba(101,232,255,.12), rgba(119,255,157,.05));
    letter-spacing: 1.4px;
}
.lock-hint { margin-top:13px; text-align:center; color:#586675; font-size:9px; line-height:1.6; }
.lock-screen.granted .lock-core { border-color:#77ff9d; color:#77ff9d; box-shadow:0 0 45px rgba(119,255,157,.18); }
.lock-screen.denied .lock-core { border-color:#ff6868; color:#ff8b8b; }

.header-right { display:flex; align-items:center; gap:10px; }
.lock-mini { width:auto; padding:7px 9px; font-size:9px; letter-spacing:1px; border-color:#26333e; }
.nav-glyph { color:#65e8ff; margin-right:5px; opacity:.86; }

.connection-badge {
    position: fixed;
    left: 14px;
    bottom: 14px;
    z-index: 15000;
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 8px 10px;
    border: 1px solid rgba(82,255,131,.25);
    border-radius: 999px;
    background: rgba(5,9,13,.90);
    color: #7fffa2;
    font-size: 9px;
    letter-spacing: 1px;
    backdrop-filter: blur(10px);
}
.connection-badge::before { content:""; width:6px; height:6px; border-radius:50%; background:#52ff83; box-shadow:0 0 8px rgba(82,255,131,.7); }
.connection-badge.offline { border-color:rgba(255,104,104,.34); color:#ff8989; }
.connection-badge.offline::before { background:#ff6868; box-shadow:0 0 8px rgba(255,104,104,.6); }

.security-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; margin-top:12px; }
.security-card { border:1px solid #293540; border-radius:12px; padding:14px; background:#080c11; }
.security-label { color:#6f7d89; font-size:9px; letter-spacing:1.4px; }
.security-value { margin-top:7px; color:#e7eef5; font-size:13px; }
.security-value.good { color:#77ff9d; }
.security-value.warn { color:#ffbd59; }

@media(max-width:600px) {
    .lock-shell { padding:22px 16px; }
    .lock-core { width:96px; height:96px; }
    .connection-badge { left:8px; bottom:8px; }
}

/* =========================================================
   PALMICORP // ADVANCED TERMINAL THEME
   ========================================================= */

:root {
    --terminal-green: #77ff9d;
    --terminal-cyan: #65e8ff;
    --terminal-dim: #7f8b98;
    --terminal-line: rgba(119, 255, 157, 0.16);
}

body::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    background:
        linear-gradient(rgba(255,255,255,0.018) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.012) 1px, transparent 1px);
    background-size: 32px 32px;
    mask-image: linear-gradient(to bottom, rgba(0,0,0,.85), transparent 90%);
    z-index: -1;
}

body::after {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    background: repeating-linear-gradient(
        to bottom,
        rgba(255,255,255,0.018) 0px,
        rgba(255,255,255,0.018) 1px,
        transparent 2px,
        transparent 4px
    );
    opacity: .22;
    z-index: 9998;
}

.hero-terminal {
    position: relative;
    margin: 20px 0;
    padding: 28px 20px 24px;
    border: 1px solid rgba(101, 232, 255, .28);
    border-radius: 16px;
    background:
        radial-gradient(circle at 50% 15%, rgba(101,232,255,.10), transparent 45%),
        rgba(6, 10, 14, .92);
    overflow: hidden;
    box-shadow:
        0 0 40px rgba(101,232,255,.05),
        inset 0 0 60px rgba(119,255,157,.025);
}

.hero-terminal::before {
    content: "PALMICORP // BRASILIA TIME CORE";
    position: absolute;
    top: 10px;
    left: 14px;
    font-size: 9px;
    letter-spacing: 2px;
    color: #64717f;
}

.hero-clock {
    text-align: center;
    font-size: clamp(54px, 10vw, 104px);
    line-height: .95;
    letter-spacing: 4px;
    font-weight: 700;
    color: #f8fbff;
    text-shadow:
        0 0 10px rgba(101,232,255,.28),
        0 0 35px rgba(119,255,157,.12);
    margin-top: 15px;
}

.hero-date {
    margin-top: 14px;
    text-align: center;
    color: var(--terminal-green);
    font-size: 13px;
    letter-spacing: 2px;
    text-transform: uppercase;
}

.hero-zone {
    margin-top: 7px;
    text-align: center;
    color: #697786;
    font-size: 10px;
    letter-spacing: 2px;
}

.terminal-strip {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin-top: 22px;
}

.terminal-strip > div {
    border-top: 1px solid rgba(119,255,157,.15);
    padding-top: 9px;
    text-align: center;
    font-size: 9px;
    letter-spacing: 1.5px;
    color: #667381;
}

.terminal-strip strong {
    display: block;
    color: #b9c4cf;
    font-size: 11px;
    margin-top: 4px;
    font-weight: normal;
}

.qr-card {
    display: grid;
    grid-template-columns: 150px 1fr;
    gap: 20px;
    align-items: center;
}

.qr-wrap {
    width: 150px;
    height: 150px;
    padding: 8px;
    background: #fff;
    border-radius: 10px;
}

.qr-wrap img {
    width: 100%;
    height: 100%;
    display: block;
}

.qr-url {
    word-break: break-all;
    color: var(--terminal-cyan);
    font-size: 12px;
    margin-top: 9px;
}

.qr-help {
    color: #8794a5;
    font-size: 12px;
    line-height: 1.6;
}

.service-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--terminal-green);
    box-shadow: 0 0 10px rgba(119,255,157,.7);
    margin-right: 7px;
}

@media(max-width: 600px) {
    .qr-card {
        grid-template-columns: 1fr;
        justify-items: center;
        text-align: center;
    }

    .terminal-strip {
        grid-template-columns: 1fr;
    }

    .hero-terminal {
        padding-left: 12px;
        padding-right: 12px;
    }
}

@media(max-width: 600px) {

    .brand {

        font-size: 22px;

        letter-spacing: 4px;

    }

    header {

        align-items: flex-start;

        flex-direction: column;

        gap: 7px;

    }

}

</style>

</head>


<body>


<div id="boot">

    <div class="boot-logo">
        PALMICORP
    </div>

    <div
        class="boot-status"
        id="bootText"
    >
        INITIALIZING TERMINAL...
    </div>

</div>

<div id="lockScreen" class="lock-screen" aria-hidden="true">
    <div class="lock-shell">
        <div class="lock-topline"><span>PALMICORP // ACCESS CORE</span><span>PALM-TERM-01</span></div>
        <div class="lock-core">P</div>
        <div class="lock-title">PALMICORP</div>
        <div class="lock-sub" id="lockModeText">AUTHENTICATION REQUIRED</div>
        <div class="lock-status" id="lockStatus">WAITING FOR CREDENTIALS...</div>
        <div class="lock-field"><input id="lockPassword" type="password" autocomplete="current-password" placeholder="ACCESS PASSWORD"></div>
        <div id="lockConfirmWrap" class="lock-field lock-confirm"><input id="lockConfirm" type="password" autocomplete="new-password" placeholder="CONFIRM PASSWORD"></div>
        <button id="lockAction" class="lock-action" onclick="submitAccess()">UNLOCK TERMINAL</button>
        <div class="lock-hint" id="lockHint">Sessão local protegida. Auto-lock após 30 minutos sem atividade.</div>
    </div>
</div>

<div id="connectionBadge" class="connection-badge">LOCAL LINK ONLINE</div>

<div class="container">


<header>

    <div>

        <div class="brand">
            PALMICORP
        </div>

        <div class="small">
            NETWORK OPERATIONS TERMINAL
        </div>

    </div>

    <div class="header-right">
        <div class="terminal-id">PALM-TERM-01</div>
        <button class="lock-mini" onclick="lockTerminal()">LOCK</button>
    </div>

</header>

<div class="module-nav">
    <button class="active" data-module="home" onclick="showModule('home', this)"><span class="nav-glyph">⌂</span>HOME</button>
    <button data-module="art" onclick="showModule('art', this)"><span class="nav-glyph">✦</span>NYVIK ART</button>
    <button data-module="etec" onclick="showModule('etec', this)"><span class="nav-glyph">▦</span>ETEC STUDY</button>
    <button data-module="ecommerce" onclick="showModule('ecommerce', this)"><span class="nav-glyph">◇</span>E-COMMERCE</button>
    <button data-module="vault" onclick="showModule('vault', this)"><span class="nav-glyph">⬡</span>VAULT</button>
    <button data-module="notes" onclick="showModule('notes', this)"><span class="nav-glyph">✎</span>NOTES</button>
    <button data-module="calendar" onclick="showModule('calendar', this)"><span class="nav-glyph">◫</span>CALENDAR</button>
    <button data-module="system" onclick="showModule('system', this)"><span class="nav-glyph">⚙</span>SYSTEM</button>
</div>

<div id="module-home" class="module-page active">

<div class="hero-terminal">
    <div class="hero-clock" id="brasiliaClock">--:--:--</div>
    <div class="hero-date" id="brasiliaDate">CARREGANDO DATA...</div>
    <div class="hero-zone">AMERICA/SAO_PAULO // UTC-03</div>

    <div class="terminal-strip">
        <div>NODE<strong>PALM-TERM-01</strong></div>
        <div>CORE STATUS<strong><span class="service-dot"></span>ONLINE</strong></div>
        <div>NETWORK MODE<strong>LOCAL SERVER</strong></div>
    </div>
</div>


<div class="grid">


<div class="card">

    <div class="card-title">
        LOCAL TIME
    </div>

    <div id="clock">
        --:--:--
    </div>

    <div id="date">
        loading
    </div>

</div>


<div class="card">

    <div class="card-title">
        DEVICE
    </div>

    <div class="big">
        PALM-TERM-01
    </div>

    <div class="small">
        SAMSUNG GALAXY A02
    </div>

    <br>

    <div class="online">
        ● SYSTEM ONLINE
    </div>

</div>


<div class="card">

    <div class="card-title">
        BATTERY
    </div>

    <div class="big" id="battery">
        --
    </div>

    <div class="small">
        TERM-01 POWER
    </div>

</div>


<div class="card">

    <div class="card-title">
        NETWORK
    </div>

    <div class="big" id="ip">
        --
    </div>

    <div class="small">
        LOCAL IP
    </div>

</div>


<div class="card">

    <div class="card-title">
        TERMINAL UPTIME
    </div>

    <div class="big" id="uptime">
        00:00:00
    </div>

    <div class="small">
        CURRENT SESSION
    </div>

</div>


<div class="card">

    <div class="card-title">
        MEGAVAULT
    </div>

    <div
        class="big"
        id="megavault"
    >
        CHECKING
    </div>

    <div class="small">
        PALMICORP SERVICE
    </div>

</div>


</div>


<div class="section card">

    <div class="card-title">
        PALMICORP NETWORK
    </div>


    <div class="device">

        <span class="device-name">
            PALM-TERM-01
        </span>

        <span
            id="term01"
            class="status online"
        >
            ONLINE
        </span>

    </div>


    <div class="device">

        <span class="device-name">
            PALM-TERM-02
        </span>

        <span
            id="term02"
            class="status standby"
        >
            STANDBY
        </span>

    </div>


    <div class="device">

        <span class="device-name">
            PALM-PC-01
        </span>

        <span
            id="pc01"
            class="status standby"
        >
            NOT CONNECTED
        </span>

    </div>


</div>


<div class="section card">

    <div class="card-title">
        QUICK ACCESS QR
    </div>

    <div class="qr-card">

        <div class="qr-wrap">
            <img id="palmicorpQr" alt="QR Code PALMICORP">
        </div>

        <div>
            <div class="big">
                SCAN TO OPEN
            </div>

            <div class="qr-help">
                Escaneie com outro aparelho conectado à mesma rede Wi-Fi
                para abrir este terminal PALMICORP.
            </div>

            <div class="qr-url" id="palmicorpQrUrl">
                --
            </div>
        </div>

    </div>

</div>


<div class="section">

    <div class="buttons">

        <button onclick="refreshSystem()">
            REFRESH SYSTEM
        </button>

        <button onclick="location.reload()">
            RELOAD TERMINAL
        </button>

        <button onclick="openMegaVault()">
            OPEN MEGAVAULT
        </button>

    </div>

</div>


</div>

<div id="module-art" class="module-page">

    <div class="nyvik-hero">
        <div>
            <div class="nyvik-kicker">PERSONAL ART WORKSPACE // PALMICORP</div>
            <div class="nyvik-title">NYVIK <span>ART</span></div>
            <div class="nyvik-sub">CREATE // STUDY // EVOLVE // BUILD YOUR OWN VISUAL LANGUAGE</div>
        </div>
    </div>

    <div class="stat-grid">
        <div class="stat-box"><div class="card-title">DAYS DRAWING</div><div class="stat-value" id="artDays">0</div></div>
        <div class="stat-box"><div class="card-title">DRAWINGS ARCHIVED</div><div class="stat-value" id="artDrawingCount">0</div></div>
        <div class="stat-box"><div class="card-title">BOOKS / PDF</div><div class="stat-value" id="artBookCount">0</div></div>
    </div>

    <div class="section">
        <div class="buttons">
            <button class="art-action" onclick="markArtDay()">+ DESENHEI HOJE</button>
            <button onclick="document.getElementById('drawingUpload').click()">+ NOVO DESENHO</button>
            <button onclick="document.getElementById('bookUpload').click()">+ NOVO LIVRO / PDF</button>
        </div>
    </div>

    <div class="studio-grid">
        <div class="studio-card">
            <h3>ART JOURNAL</h3>
            <p>Registre o que você praticou hoje: anatomia, cabelo, rosto, pose, perspectiva, luz e sombra...</p>
            <textarea id="artNote" class="studio-textarea" placeholder="Ex.: pratiquei construção do tronco e perspectiva... "></textarea>
            <button class="art-action" onclick="saveArtNote()">SALVAR NO DIÁRIO</button>
            <div class="log-list" id="artLog"></div>
        </div>

        <div class="studio-card">
            <h3>NYVIK LIBRARY</h3>
            <p>Sua biblioteca de estudo. PDFs ficam guardados no A02 e podem ser abertos pela PALMICORP.</p>
            <label class="file-picker">
                IMPORTAR PDF / LIVRO
                <input id="bookUpload" type="file" accept="application/pdf" onchange="uploadArtFile('book', this)">
            </label>
            <div class="library-list" id="artBooks"></div>
        </div>
    </div>

    <div class="section studio-card">
        <h3>NYVIK GALLERY</h3>
        <p>Um arquivo visual do seu progresso. Joga aqui fotos ou exports dos seus desenhos.</p>
        <label class="file-picker">
            ADICIONAR DESENHO
            <input id="drawingUpload" type="file" accept="image/jpeg,image/png,image/webp,image/gif" onchange="uploadArtFile('drawing', this)">
        </label>
        <div class="art-gallery" id="artGallery"></div>
    </div>

</div>

<div id="module-etec" class="module-page">

    <div class="nyvik-hero etec-hero">
        <div>
            <div class="nyvik-kicker" style="color:#65e8ff">PALMICORP // STUDY PROTOCOL</div>
            <div class="nyvik-title">ETEC <span style="color:#65e8ff">STUDY</span></div>
            <div class="nyvik-sub">ONE MORE DAY // ONE MORE STEP</div>
        </div>
    </div>

    <div class="stat-grid">
        <div class="stat-box"><div class="card-title">DAYS STUDIED</div><div class="stat-value" id="studyDays">0</div></div>
        <div class="stat-box"><div class="card-title">TARGET</div><div class="stat-value">ETEC</div></div>
    </div>

    <div class="section buttons">
        <button class="art-action" onclick="markStudyDay()">+1 DIA ESTUDADO</button>
    </div>

    <div class="studio-card">
        <h3>STUDY LOG</h3>
        <p>Anota rapidinho o que estudou hoje e mantém um histórico dentro da PALMICORP.</p>
        <textarea id="studyNote" class="studio-textarea" placeholder="Ex.: matemática — razão e proporção; português — interpretação..."></textarea>
        <button onclick="saveStudyNote()">SALVAR ESTUDO</button>
        <div class="log-list" id="studyLog"></div>
    </div>

</div>

<div id="module-ecommerce" class="module-page">

    <div class="ecom-hero">
        <div>
            <div class="ecom-kicker">PALMICORP // COMMERCE LEARNING LAB</div>
            <div class="ecom-title">E-COMMERCE <span>LAB</span></div>
            <div class="ecom-sub">LEARN // DOCUMENT // APPLY // TEST // SCALE</div>
        </div>
    </div>

    <div class="stat-grid">
        <div class="stat-box ecom-card"><div class="card-title">DAYS LEARNING</div><div class="stat-value" id="ecommerceDays">0</div></div>
        <div class="stat-box ecom-card"><div class="card-title">LESSONS ARCHIVED</div><div class="stat-value" id="ecommerceLessonCount">0</div></div>
        <div class="stat-box ecom-card"><div class="card-title">TOPICS TOUCHED</div><div class="stat-value" id="ecommerceTopicCount">0</div></div>
        <div class="stat-box ecom-card"><div class="card-title">MODE</div><div class="stat-value" style="font-size:18px;color:#ffbd59">BUILD & LEARN</div></div>
    </div>

    <div class="section studio-grid">
        <div class="studio-card ecom-card">
            <h3>COURSE LEARNING LOG</h3>
            <p>Transforma cada aula em um registro útil: tema, conceito, aplicação, dúvida e próximo teste.</p>

            <div class="ecom-fields">
                <label class="palm-label">ÁREA
                    <select id="ecommerceCategory" class="palm-select">
                        <option>Fundamentos</option>
                        <option>Nicho / Público</option>
                        <option>Pesquisa de Produto</option>
                        <option>Produto</option>
                        <option>Oferta</option>
                        <option>Precificação / Margem</option>
                        <option>Branding</option>
                        <option>Loja / UX</option>
                        <option>Página de Produto</option>
                        <option>Copywriting</option>
                        <option>Criativos</option>
                        <option>Conteúdo Orgânico</option>
                        <option>Tráfego Pago</option>
                        <option>Meta Ads</option>
                        <option>Google Ads</option>
                        <option>TikTok Ads</option>
                        <option>SEO</option>
                        <option>E-mail Marketing</option>
                        <option>WhatsApp / CRM</option>
                        <option>Funil / CRO</option>
                        <option>Checkout</option>
                        <option>Upsell / Cross-sell</option>
                        <option>Métricas / Analytics</option>
                        <option>Financeiro / Fluxo de Caixa</option>
                        <option>Operação / Logística</option>
                        <option>Fornecedores / Estoque</option>
                        <option>Atendimento / Pós-venda</option>
                        <option>Marketplace</option>
                        <option>Automação / Ferramentas</option>
                        <option>Jurídico / Fiscal</option>
                        <option>Testes / Experimentos</option>
                        <option>Ideias / Insights</option>
                        <option>Outro</option>
                    </select>
                </label>
                <label class="palm-label">MÓDULO / AULA
                    <input id="ecommerceLesson" class="palm-input" maxlength="120" placeholder="Ex.: Módulo 3 — Aula 5">
                </label>
                <label class="palm-label">NÍVEL
                    <select id="ecommerceLevel" class="palm-select">
                        <option>Aprendendo</option>
                        <option>Entendi</option>
                        <option>Preciso revisar</option>
                        <option>Quero testar</option>
                        <option>Dominei</option>
                    </select>
                </label>
            </div>

            <label class="palm-label">O QUE EU APRENDI
                <textarea id="ecommerceNote" class="studio-textarea" placeholder="Escreve o conceito principal da aula..."></textarea>
            </label>
            <label class="palm-label">COMO POSSO APLICAR
                <textarea id="ecommerceApply" class="studio-textarea ecom-mini" placeholder="Ex.: aplicar isso na página de produto, oferta, criativo..."></textarea>
            </label>
            <label class="palm-label">DÚVIDA / PONTO PRA REVISAR
                <textarea id="ecommerceQuestion" class="studio-textarea ecom-mini" placeholder="Alguma coisa ficou confusa? Anota aqui."></textarea>
            </label>
            <label class="palm-label">PRÓXIMA AÇÃO / TESTE
                <textarea id="ecommerceAction" class="studio-textarea ecom-mini" placeholder="Ex.: montar uma oferta exemplo e comparar 3 preços."></textarea>
            </label>
            <button class="ecom-save" onclick="saveEcommerceNote()">ARQUIVAR APRENDIZADO</button>
        </div>

        <div class="studio-card ecom-card">
            <h3>LEARNING ARCHIVE</h3>
            <p>Pesquise suas próprias anotações do curso por palavra ou área.</p>
            <div class="ecom-filterbar">
                <input id="ecommerceSearch" class="palm-input" placeholder="Buscar no arquivo..." oninput="renderEcommerceLog()">
                <select id="ecommerceFilter" class="palm-select" onchange="renderEcommerceLog()">
                    <option value="">Todas as áreas</option>
                </select>
            </div>
            <div class="log-list ecom-log" id="ecommerceLog"></div>
        </div>
    </div>

</div>

<div id="module-vault" class="module-page">
    <div class="nyvik-hero" style="border-color:rgba(119,255,157,.26);background:radial-gradient(circle at 82% 18%,rgba(119,255,157,.12),transparent 35%),#07100c">
        <div>
            <div class="nyvik-kicker" style="color:#77ff9d">PALMICORP // SECURE STORAGE FOUNDATION</div>
            <div class="nyvik-title">VAULT <span style="color:#77ff9d">CORE</span></div>
            <div class="nyvik-sub">ACCESS CONTROL ONLINE // ENCRYPTED STORAGE COMES NEXT</div>
        </div>
    </div>
    <div class="security-grid">
        <div class="security-card"><div class="security-label">ACCESS SESSION</div><div class="security-value good">AUTHENTICATED</div></div>
        <div class="security-card"><div class="security-label">PASSWORD VAULT</div><div class="security-value warn">NOT STORING SECRETS YET</div></div>
        <div class="security-card"><div class="security-label">FILE VAULT</div><div class="security-value warn">ENCRYPTION PENDING</div></div>
    </div>
    <div class="section studio-card">
        <h3>SECURITY FOUNDATION</h3>
        <p>A tela de bloqueio agora protege as APIs pessoais, PDFs, desenhos, notas, tarefas e uploads com uma sessão autenticada. Senhas e arquivos sensíveis ainda não são armazenados aqui até a camada de criptografia ficar pronta.</p>
        <button onclick="lockTerminal()">LOCK PALMICORP NOW</button>
    </div>
</div>

<div id="module-notes" class="module-page">
    <div class="notes-hero">
        <div>
            <div class="nyvik-kicker" style="color:#65e8ff">PALMICORP // PERSONAL KNOWLEDGE</div>
            <div class="nyvik-title">QUICK <span style="color:#65e8ff">NOTES</span></div>
            <div class="nyvik-sub">CAPTURE // PIN // FIND LATER</div>
        </div>
    </div>
    <div class="section studio-grid">
        <div class="studio-card">
            <h3>NOVA NOTA</h3>
            <div class="form-grid">
                <input id="noteTitle" class="palm-input" maxlength="100" placeholder="Título">
                <select id="noteCategory" class="palm-select">
                    <option>Geral</option><option>Ideia</option><option>Projeto</option><option>Desenho</option><option>ETEC</option><option>E-commerce</option><option>Servidor</option><option>Importante</option>
                </select>
            </div>
            <textarea id="noteBody" class="studio-textarea" placeholder="Escreve aqui..."></textarea>
            <button onclick="saveNote()">SALVAR NOTA</button>
        </div>
        <div class="studio-card">
            <h3>ARQUIVO</h3>
            <input id="noteSearch" class="palm-input" placeholder="Buscar notas..." oninput="renderNotes()">
            <div id="notesGrid" class="note-grid"></div>
        </div>
    </div>
</div>

<div id="module-calendar" class="module-page">
    <div class="planner-hero">
        <div>
            <div class="nyvik-kicker" style="color:#ffbd59">PALMICORP // PERSONAL PLANNER</div>
            <div class="nyvik-title">CALENDAR <span style="color:#ffbd59">/ TASKS</span></div>
            <div class="nyvik-sub">PLAN // EXECUTE // CHECK OFF</div>
        </div>
    </div>
    <div class="section studio-grid">
        <div class="studio-card">
            <h3>NOVA TAREFA</h3>
            <div class="form-grid">
                <input id="taskTitle" class="palm-input" maxlength="120" placeholder="O que precisa fazer?">
                <input id="taskDue" class="palm-input" type="date">
                <select id="taskCategory" class="palm-select">
                    <option>Geral</option><option>NYVIK ART</option><option>ETEC</option><option>E-commerce</option><option>PALMICORP</option><option>Pessoal</option>
                </select>
                <select id="taskPriority" class="palm-select">
                    <option>Normal</option><option>Alta</option><option>Baixa</option>
                </select>
            </div>
            <button onclick="saveTask()">ADICIONAR TAREFA</button>
            <div id="taskList" class="task-list"></div>
        </div>
        <div class="studio-card">
            <h3>CALENDÁRIO</h3>
            <div class="calendar-shell">
                <div class="calendar-head">
                    <button onclick="moveCalendar(-1)">◀</button>
                    <div id="calendarTitle" class="calendar-title">--</div>
                    <button onclick="moveCalendar(1)">▶</button>
                </div>
                <div id="calendarGrid" class="calendar-grid"></div>
            </div>
        </div>
    </div>
</div>

<div id="module-system" class="module-page">
    <div class="studio-grid">
        <div class="studio-card">
            <h3>ALERT CENTER</h3>
            <p>Alertas locais da PALMICORP, servidor e seus compromissos.</p>
            <div id="alertList" class="alert-list"></div>
        </div>
        <div class="studio-card">
            <h3>WORLD CLOCK</h3>
            <p>Horários úteis reunidos no terminal.</p>
            <div class="world-grid">
                <div class="world-clock"><div class="world-city">BRASÍLIA</div><div class="world-time" id="worldBrasilia">--:--</div><div class="world-date" id="worldBrasiliaDate">--</div></div>
                <div class="world-clock"><div class="world-city">LISBOA</div><div class="world-time" id="worldLisbon">--:--</div><div class="world-date" id="worldLisbonDate">--</div></div>
                <div class="world-clock"><div class="world-city">BERLIM</div><div class="world-time" id="worldBerlin">--:--</div><div class="world-date" id="worldBerlinDate">--</div></div>
                <div class="world-clock"><div class="world-city">TÓQUIO</div><div class="world-time" id="worldTokyo">--:--</div><div class="world-date" id="worldTokyoDate">--</div></div>
            </div>
        </div>
    </div>
    <div class="section studio-card">
        <h3>ACCESS / OFFLINE CORE</h3>
        <div class="security-grid">
            <div class="security-card"><div class="security-label">AUTH STATUS</div><div id="securityAuthState" class="security-value good">AUTHENTICATED</div></div>
            <div class="security-card"><div class="security-label">AUTO LOCK</div><div class="security-value">30 MIN IDLE</div></div>
            <div class="security-card"><div class="security-label">SERVER SESSION</div><div class="security-value">8 HOURS MAX</div></div>
            <div class="security-card"><div class="security-label">TRANSPORT</div><div class="security-value warn">LOCAL HTTP</div></div>
        </div>
        <p style="margin-top:12px">A autenticação protege os dados do painel, mas a rede ainda usa HTTP. Não exponha a porta 8080 diretamente à internet; HTTPS entra antes do Password Vault.</p>
    </div>
    <div class="section studio-card">
        <h3>PALMICORP VERSIONING</h3>
        <div class="big" style="color:#65e8ff">v2.3.0-alpha</div>
        <div class="small">ACCESS CORE // CINEMATIC LOCK // OFFLINE UI // CUSTOM GLYPHS // PERSONAL OS</div>
    </div>
</div>

<div id="artViewer" class="viewer-modal" onclick="viewerBackdrop(event)">
    <div class="viewer-shell">
        <div class="viewer-head">
            <div id="viewerTitle" class="viewer-title">NYVIK ART VIEWER</div>
            <a id="viewerOpenExternal" target="_blank" rel="noopener">ABRIR EM NOVA ABA</a>
            <button onclick="closeArtViewer()">FECHAR</button>
        </div>
        <div id="viewerBody" class="viewer-body"></div>
    </div>
</div>

<div id="moduleToast" class="module-toast">PALMICORP</div>

<footer>

PALMICORP TERMINAL SYSTEM
<br>
VERSION 2.3.0-alpha // ACCESS CORE

</footer>


</div>


<script>

let personalState = null;
let palmAuthenticated = false;
let palmAuthConfigured = false;
let idleTimer = null;
const PALM_IDLE_MS = 30 * 60 * 1000;
const nativeFetch = window.fetch.bind(window);

window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    const target = String(args[0] || '');
    if (response.status === 401 && !target.startsWith('/api/auth')) {
        palmAuthenticated = false;
        showLockScreen('login', 'SESSION EXPIRED // AUTHENTICATE AGAIN');
    }
    return response;
};

function setConnectionState(online, text='') {
    const badge = document.getElementById('connectionBadge');
    if (!badge) return;
    badge.classList.toggle('offline', !online);
    badge.textContent = text || (online ? 'LOCAL LINK ONLINE' : 'LOCAL LINK OFFLINE');
}

window.addEventListener('online', () => setConnectionState(true));
window.addEventListener('offline', () => setConnectionState(false, 'BROWSER NETWORK OFFLINE'));

function showLockScreen(mode='login', message='') {
    const screen = document.getElementById('lockScreen');
    const confirmWrap = document.getElementById('lockConfirmWrap');
    const modeText = document.getElementById('lockModeText');
    const action = document.getElementById('lockAction');
    const hint = document.getElementById('lockHint');
    const status = document.getElementById('lockStatus');
    screen.classList.remove('granted','denied');
    screen.classList.add('open');
    screen.setAttribute('aria-hidden','false');
    confirmWrap.classList.toggle('show', mode === 'setup');
    modeText.textContent = mode === 'setup' ? 'FIRST ACCESS // CREATE LOCAL PASSWORD' : 'AUTHENTICATION REQUIRED';
    action.textContent = mode === 'setup' ? 'CREATE ACCESS KEY' : 'UNLOCK TERMINAL';
    hint.textContent = mode === 'setup'
        ? 'Crie uma senha única para a PALMICORP. Não reutilize senha de e-mail, rede social ou banco.'
        : 'Sessão local protegida. Auto-lock após 30 minutos sem atividade.';
    status.textContent = message || (mode === 'setup' ? 'ACCESS CORE NOT CONFIGURED' : 'WAITING FOR CREDENTIALS...');
    screen.dataset.mode = mode;
    document.getElementById('lockPassword').value = '';
    document.getElementById('lockConfirm').value = '';
    setTimeout(() => document.getElementById('lockPassword').focus(), 80);
}

function hideLockScreen() {
    const screen = document.getElementById('lockScreen');
    screen.classList.add('granted');
    document.getElementById('lockStatus').textContent = 'ACCESS GRANTED // WELCOME BACK';
    setTimeout(() => {
        screen.classList.remove('open','granted','denied');
        screen.setAttribute('aria-hidden','true');
    }, 620);
}

function lockDenied(message) {
    const screen = document.getElementById('lockScreen');
    screen.classList.remove('granted');
    screen.classList.add('denied');
    document.getElementById('lockStatus').textContent = message || 'ACCESS DENIED';
    setTimeout(() => screen.classList.remove('denied'), 900);
}

async function checkAccessCore() {
    try {
        const response = await nativeFetch('/api/auth/status?t=' + Date.now(), {cache:'no-store'});
        const data = await response.json();
        palmAuthConfigured = !!data.configured;
        palmAuthenticated = !!data.authenticated;
        setConnectionState(true);
        if (!palmAuthConfigured) {
            showLockScreen('setup');
            return;
        }
        if (!palmAuthenticated) {
            showLockScreen('login');
            return;
        }
        hideLockScreen();
        startAuthenticatedSession();
    } catch (error) {
        setConnectionState(false, 'PALMICORP SERVER UNREACHABLE');
        showLockScreen('login', 'SERVER UNREACHABLE // CHECK LOCAL LINK');
    }
}

async function submitAccess() {
    const screen = document.getElementById('lockScreen');
    const mode = screen.dataset.mode || 'login';
    const password = document.getElementById('lockPassword').value;
    const confirm = document.getElementById('lockConfirm').value;
    const status = document.getElementById('lockStatus');
    if (!password) return lockDenied('ENTER ACCESS PASSWORD');
    if (mode === 'setup') {
        if (password.length < 8) return lockDenied('USE AT LEAST 8 CHARACTERS');
        if (password !== confirm) return lockDenied('PASSWORDS DO NOT MATCH');
    }
    status.textContent = mode === 'setup' ? 'GENERATING ACCESS CORE...' : 'VERIFYING CREDENTIALS...';
    try {
        const response = await nativeFetch(mode === 'setup' ? '/api/auth/setup' : '/api/auth/login', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({password})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'ACCESS DENIED');
        palmAuthConfigured = true;
        palmAuthenticated = true;
        hideLockScreen();
        startAuthenticatedSession();
    } catch (error) {
        lockDenied(error.message || 'ACCESS DENIED');
    }
}

async function lockTerminal() {
    try { await nativeFetch('/api/auth/logout', {method:'POST'}); } catch (_) {}
    palmAuthenticated = false;
    clearTimeout(idleTimer);
    showLockScreen('login', 'TERMINAL LOCKED');
}

function resetIdleTimer() {
    if (!palmAuthenticated) return;
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => lockTerminal(), PALM_IDLE_MS);
}

['pointerdown','keydown','touchstart'].forEach(eventName => {
    window.addEventListener(eventName, resetIdleTimer, {passive:true});
});

document.addEventListener('keydown', event => {
    if (event.key === 'Enter' && document.getElementById('lockScreen').classList.contains('open')) {
        submitAccess();
    }
});

function startAuthenticatedSession() {
    resetIdleTimer();
    refreshSystem();
    refreshPersonal();
    renderAlerts();
    const security = document.getElementById('securityAuthState');
    if (security) security.textContent = 'AUTHENTICATED';
}

function showModule(name, button) {
    document.querySelectorAll('.module-page').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.module-nav button').forEach(el => el.classList.remove('active'));
    const page = document.getElementById('module-' + name);
    if (page) page.classList.add('active');
    if (button) button.classList.add('active');
    if (['art','etec','ecommerce','vault','notes','calendar','system'].includes(name)) refreshPersonal();
    if (name === 'system') renderAlerts();
}

function toast(message) {
    const el = document.getElementById('moduleToast');
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(window.__palmToast);
    window.__palmToast = setTimeout(() => el.classList.remove('show'), 2200);
}

function prettySize(bytes) {
    if (!bytes) return '0 KB';
    const units = ['B','KB','MB','GB'];
    let value = Number(bytes), i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return value.toFixed(i ? 1 : 0) + ' ' + units[i];
}


function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({
        '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'
    })[ch]);
}

async function refreshPersonal() {
    try {
        const response = await fetch('/api/personal?t=' + Date.now());
        personalState = await response.json();
        document.getElementById('artDays').textContent = personalState.art_days || 0;
        document.getElementById('studyDays').textContent = personalState.study_days || 0;
        document.getElementById('artDrawingCount').textContent = (personalState.drawings || []).length;
        document.getElementById('artBookCount').textContent = (personalState.books || []).length;

        document.getElementById('artBooks').innerHTML = (personalState.books || []).map(file =>
            `<div class="library-item"><div class="library-file-main"><span class="library-file-name">${escapeHtml(file.name)}</span><small>${prettySize(file.size)}</small></div><button type="button" onclick='openArtBook(${JSON.stringify(file.name)})'>LER PDF</button></div>`
        ).join('') || '<div class="small">Nenhum PDF ainda.</div>';

        document.getElementById('artGallery').innerHTML = (personalState.drawings || []).map(file =>
            `<div class="art-tile" onclick='openArtDrawing(${JSON.stringify(file.name)})'><img loading="lazy" src="/art-file?kind=drawing&name=${encodeURIComponent(file.name)}"><span>${escapeHtml(file.name)}</span></div>`
        ).join('') || '<div class="small">Sua galeria ainda está vazia.</div>';

        document.getElementById('artLog').innerHTML = (personalState.art_log || []).slice().reverse().slice(0,8).map(item =>
            `<div class="log-entry"><strong>${escapeHtml(item.date)}</strong><br>${escapeHtml(item.text)}</div>`
        ).join('');

        document.getElementById('studyLog').innerHTML = (personalState.study_log || []).slice().reverse().slice(0,8).map(item =>
            `<div class="log-entry" style="border-color:#65e8ff"><strong>${escapeHtml(item.date)}</strong><br>${escapeHtml(item.text)}</div>`
        ).join('');


        document.getElementById('ecommerceDays').textContent = personalState.ecommerce_days || 0;
        document.getElementById('ecommerceLessonCount').textContent = (personalState.ecommerce_log || []).length;
        document.getElementById('ecommerceTopicCount').textContent = new Set((personalState.ecommerce_log || []).map(x => x.category).filter(Boolean)).size;
        syncEcommerceFilters();
        renderEcommerceLog();
        renderNotes();
        renderTasks();
        renderCalendar();
        renderAlerts();
    } catch (error) {
        console.log('Personal API error', error);
    }
}

async function personalAction(action, text='') {
    const response = await fetch('/api/personal', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({action, text})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Falha');
    await refreshPersonal();
    return data;
}

async function markArtDay() {
    try { await personalAction('art_day'); toast('NYVIK ART // dia registrado'); }
    catch (e) { toast(e.message); }
}

async function markStudyDay() {
    try { await personalAction('study_day'); toast('ETEC // +1 dia estudado'); }
    catch (e) { toast(e.message); }
}

async function saveArtNote() {
    const input = document.getElementById('artNote');
    const value = input.value.trim();
    if (!value) return toast('Escreve o que você praticou primeiro.');
    try { await personalAction('art_note', value); input.value=''; toast('Art journal salvo'); }
    catch (e) { toast(e.message); }
}

async function saveStudyNote() {
    const input = document.getElementById('studyNote');
    const value = input.value.trim();
    if (!value) return toast('Escreve o que você estudou primeiro.');
    try { await personalAction('study_note', value); input.value=''; toast('Study log salvo'); }
    catch (e) { toast(e.message); }
}


async function saveEcommerceNote() {
    const input = document.getElementById('ecommerceNote');
    const payload = {
        action:'ecommerce_note',
        text: input.value.trim(),
        category: document.getElementById('ecommerceCategory').value || 'Geral',
        lesson: document.getElementById('ecommerceLesson').value.trim(),
        level: document.getElementById('ecommerceLevel').value || 'Aprendendo',
        apply: document.getElementById('ecommerceApply').value.trim(),
        question: document.getElementById('ecommerceQuestion').value.trim(),
        next_action: document.getElementById('ecommerceAction').value.trim()
    };
    if (!payload.text) return toast('Escreve o que você aprendeu primeiro.');
    try {
        const response = await fetch('/api/personal', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Falha');
        ['ecommerceNote','ecommerceLesson','ecommerceApply','ecommerceQuestion','ecommerceAction'].forEach(id => document.getElementById(id).value='');
        await refreshPersonal();
        toast('E-COMMERCE LAB // aprendizado arquivado');
    } catch (e) { toast(e.message || 'Falha ao salvar'); }
}

function syncEcommerceFilters() {
    const filter = document.getElementById('ecommerceFilter');
    if (!filter) return;
    const current = filter.value;
    const cats = [...new Set((personalState?.ecommerce_log || []).map(x => x.category).filter(Boolean))].sort();
    filter.innerHTML = '<option value="">Todas as áreas</option>' + cats.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join('');
    filter.value = cats.includes(current) ? current : '';
}

function renderEcommerceLog() {
    const target = document.getElementById('ecommerceLog');
    if (!target) return;
    const search = (document.getElementById('ecommerceSearch')?.value || '').trim().toLowerCase();
    const category = document.getElementById('ecommerceFilter')?.value || '';
    const items = (personalState?.ecommerce_log || []).slice().reverse().filter(item => {
        if (category && item.category !== category) return false;
        const blob = [item.category,item.lesson,item.level,item.text,item.apply,item.question,item.next_action].join(' ').toLowerCase();
        return !search || blob.includes(search);
    }).slice(0,80);
    target.innerHTML = items.map(item => `
        <div class="log-entry">
            <div class="ecom-entry-title">${escapeHtml(item.category || 'Geral')} // ${escapeHtml(item.level || 'Aprendendo')}</div>
            <div class="ecom-entry-meta">${escapeHtml(item.date || '')}${item.lesson ? ' // ' + escapeHtml(item.lesson) : ''}</div>
            <div class="ecom-entry-section"><strong>APRENDI:</strong> ${escapeHtml(item.text || '')}</div>
            ${item.apply ? `<div class="ecom-entry-section"><strong>APLICAR:</strong> ${escapeHtml(item.apply)}</div>` : ''}
            ${item.question ? `<div class="ecom-entry-section"><strong>REVISAR:</strong> ${escapeHtml(item.question)}</div>` : ''}
            ${item.next_action ? `<div class="ecom-entry-section"><strong>PRÓXIMA AÇÃO:</strong> ${escapeHtml(item.next_action)}</div>` : ''}
        </div>`).join('') || '<div class="small">Nenhum aprendizado encontrado.</div>';
}

async function saveNote() {
    const title = document.getElementById('noteTitle').value.trim();
    const text = document.getElementById('noteBody').value.trim();
    const category = document.getElementById('noteCategory').value || 'Geral';
    if (!title && !text) return toast('Escreve a nota primeiro.');
    try {
        await apiPersonal({action:'note_add', title, text, category});
        document.getElementById('noteTitle').value=''; document.getElementById('noteBody').value='';
        toast('NOTES // nota salva');
    } catch(e) { toast(e.message); }
}

function renderNotes() {
    const target = document.getElementById('notesGrid');
    if (!target) return;
    const search = (document.getElementById('noteSearch')?.value || '').trim().toLowerCase();
    const items = (personalState?.notes || []).slice().sort((a,b) => Number(b.pinned)-Number(a.pinned) || Number(b.id)-Number(a.id)).filter(n =>
        !search || [n.title,n.text,n.category].join(' ').toLowerCase().includes(search)
    );
    target.innerHTML = items.map(n => `<div class="note-card ${n.pinned?'pinned':''}">
        <div class="note-meta">${escapeHtml(n.category || 'Geral')} // ${escapeHtml(n.date || '')}</div>
        <div class="note-title">${escapeHtml(n.title || 'SEM TÍTULO')}</div>
        <div class="note-body">${escapeHtml(n.text || '')}</div>
        <div class="note-actions"><button onclick="noteAction('note_pin', ${Number(n.id)})">${n.pinned?'DESAFIXAR':'FIXAR'}</button><button onclick="noteAction('note_delete', ${Number(n.id)})">EXCLUIR</button></div>
    </div>`).join('') || '<div class="small">Nenhuma nota ainda.</div>';
}

async function noteAction(action, id) {
    try { await apiPersonal({action, id}); toast('NOTES // atualizado'); } catch(e) { toast(e.message); }
}

async function saveTask() {
    const title = document.getElementById('taskTitle').value.trim();
    if (!title) return toast('Digite a tarefa primeiro.');
    try {
        await apiPersonal({action:'task_add', title, due:document.getElementById('taskDue').value, category:document.getElementById('taskCategory').value, priority:document.getElementById('taskPriority').value});
        document.getElementById('taskTitle').value='';
        toast('CALENDAR // tarefa adicionada');
    } catch(e) { toast(e.message); }
}

function taskClass(task) {
    if (task.done) return 'done';
    const today = new Date().toISOString().slice(0,10);
    if (task.due && task.due < today) return 'overdue';
    if (task.due === today) return 'today';
    return '';
}

function renderTasks() {
    const target = document.getElementById('taskList');
    if (!target) return;
    const items = (personalState?.tasks || []).slice().sort((a,b) => Number(a.done)-Number(b.done) || String(a.due||'9999').localeCompare(String(b.due||'9999')) || Number(b.id)-Number(a.id));
    target.innerHTML = items.map(t => `<div class="task-item ${taskClass(t)}">
        <input class="task-check" type="checkbox" ${t.done?'checked':''} onchange="taskAction('task_toggle', ${Number(t.id)})">
        <div><div class="task-title">${escapeHtml(t.title)}</div><div class="task-meta">${escapeHtml(t.category || 'Geral')} // ${escapeHtml(t.priority || 'Normal')}${t.due ? ' // ' + escapeHtml(t.due.split('-').reverse().join('/')) : ''}</div></div>
        <button class="task-delete" onclick="taskAction('task_delete', ${Number(t.id)})">EXCLUIR</button>
    </div>`).join('') || '<div class="small">Nenhuma tarefa pendente.</div>';
}

async function taskAction(action, id) {
    try { await apiPersonal({action, id}); } catch(e) { toast(e.message); }
}

async function apiPersonal(payload) {
    const response = await fetch('/api/personal', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Falha');
    await refreshPersonal();
    return data;
}

let calendarCursor = new Date();
calendarCursor.setDate(1);
function moveCalendar(delta) { calendarCursor.setMonth(calendarCursor.getMonth()+delta); renderCalendar(); }
function renderCalendar() {
    const grid = document.getElementById('calendarGrid');
    if (!grid) return;
    const year=calendarCursor.getFullYear(), month=calendarCursor.getMonth();
    const first=new Date(year,month,1), start=(first.getDay()+6)%7, days=new Date(year,month+1,0).getDate();
    document.getElementById('calendarTitle').textContent = new Intl.DateTimeFormat('pt-BR',{month:'long',year:'numeric'}).format(first).toUpperCase();
    const heads=['SEG','TER','QUA','QUI','SEX','SÁB','DOM'].map(x=>`<div class="calendar-cell head">${x}</div>`);
    const cells=[]; const today=new Date();
    for(let i=0;i<start;i++) cells.push('<div class="calendar-cell muted"></div>');
    for(let d=1;d<=days;d++){
        const iso=`${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
        const has=(personalState?.tasks||[]).some(t=>t.due===iso && !t.done);
        const isToday=today.getFullYear()===year&&today.getMonth()===month&&today.getDate()===d;
        cells.push(`<div class="calendar-cell ${has?'has-task':''} ${isToday?'today':''}">${d}</div>`);
    }
    grid.innerHTML=heads.join('')+cells.join('');
}

let lastSystemStatus = null;
function renderAlerts() {
    const target=document.getElementById('alertList'); if(!target) return;
    const alerts=[]; const today=new Date().toISOString().slice(0,10);
    if(lastSystemStatus?.megavault==='OFFLINE') alerts.push(['danger','MEGAVAULT OFFLINE','O serviço não respondeu à PALMICORP.']);
    else if(lastSystemStatus?.megavault==='ONLINE') alerts.push(['ok','MEGAVAULT ONLINE','Serviço respondendo normalmente.']);
    if(Number(lastSystemStatus?.battery) <= 20) alerts.push(['warn','BATERIA BAIXA',`PALM-TERM-01 está em ${lastSystemStatus.battery}%.`]);
    const overdue=(personalState?.tasks||[]).filter(t=>!t.done&&t.due&&t.due<today).length;
    const dueToday=(personalState?.tasks||[]).filter(t=>!t.done&&t.due===today).length;
    if(overdue) alerts.push(['danger','TAREFAS ATRASADAS',`${overdue} tarefa(s) passaram da data.`]);
    if(dueToday) alerts.push(['warn','TAREFAS DE HOJE',`${dueToday} tarefa(s) marcadas para hoje.`]);
    if(personalState?.art_last_day!==today) alerts.push(['warn','NYVIK ART','Desenho ainda não foi marcado hoje.']);
    if(personalState?.study_last_day!==today) alerts.push(['warn','ETEC STUDY','Estudo ainda não foi marcado hoje.']);
    if(!alerts.length) alerts.push(['ok','ALL SYSTEMS NOMINAL','Nenhum alerta local agora.']);
    target.innerHTML=alerts.map(a=>`<div class="alert-item ${a[0]}"><div class="alert-title">${a[1]}</div><div class="alert-text">${a[2]}</div></div>`).join('');
}

function updateWorldClocks() {
    const zones=[['worldBrasilia','worldBrasiliaDate','America/Sao_Paulo'],['worldLisbon','worldLisbonDate','Europe/Lisbon'],['worldBerlin','worldBerlinDate','Europe/Berlin'],['worldTokyo','worldTokyoDate','Asia/Tokyo']];
    const now=new Date();
    zones.forEach(([tid,did,zone])=>{
        const t=document.getElementById(tid), d=document.getElementById(did); if(!t||!d)return;
        t.textContent=new Intl.DateTimeFormat('pt-BR',{timeZone:zone,hour:'2-digit',minute:'2-digit',hour12:false}).format(now);
        d.textContent=new Intl.DateTimeFormat('pt-BR',{timeZone:zone,day:'2-digit',month:'2-digit'}).format(now);
    });
}

function openArtBook(name) {
    const url = '/art-file?kind=book&name=' + encodeURIComponent(name);
    openArtViewer('pdf', name, url);
}

function openArtDrawing(name) {
    const url = '/art-file?kind=drawing&name=' + encodeURIComponent(name);
    openArtViewer('image', name, url);
}

function openArtViewer(type, name, url) {
    const modal = document.getElementById('artViewer');
    const body = document.getElementById('viewerBody');
    document.getElementById('viewerTitle').textContent = name;
    document.getElementById('viewerOpenExternal').href = url;
    body.innerHTML = type === 'pdf'
        ? `<iframe title="${escapeHtml(name)}" src="${url}"></iframe>`
        : `<img alt="${escapeHtml(name)}" src="${url}">`;
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
}

function closeArtViewer() {
    const modal = document.getElementById('artViewer');
    modal.classList.remove('open');
    document.getElementById('viewerBody').innerHTML = '';
    document.body.style.overflow = '';
}

function viewerBackdrop(event) {
    if (event.target.id === 'artViewer') closeArtViewer();
}

window.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeArtViewer();
});

async function uploadArtFile(kind, input) {
    const file = input.files && input.files[0];
    if (!file) return;

    toast(`Enviando ${prettySize(file.size)} para o A02...`);

    try {
        const url = '/api/art/upload?kind=' + encodeURIComponent(kind) + '&name=' + encodeURIComponent(file.name);
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': file.type || 'application/octet-stream'
            },
            body: file
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Upload falhou');
        input.value = '';
        await refreshPersonal();
        toast(kind === 'book' ? 'PDF salvo na NYVIK LIBRARY' : 'Desenho salvo na NYVIK GALLERY');
    } catch (error) {
        toast(error.message || 'Falha no upload');
    }
}



function openMegaVault() {

    const url =
        window.location.protocol +
        "//" +
        window.location.hostname +
        ":5173";

    window.open(
        url,
        "_blank",
        "noopener"
    );

}


function updateBrasiliaClock() {

    const now = new Date();

    document.getElementById("brasiliaClock").textContent =
        new Intl.DateTimeFormat(
            "pt-BR",
            {
                timeZone: "America/Sao_Paulo",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false
            }
        ).format(now);

    document.getElementById("brasiliaDate").textContent =
        new Intl.DateTimeFormat(
            "pt-BR",
            {
                timeZone: "America/Sao_Paulo",
                weekday: "long",
                day: "2-digit",
                month: "long",
                year: "numeric"
            }
        ).format(now);

}


function setupPalmicorpQr() {

    const url =
        window.location.protocol +
        "//" +
        window.location.hostname +
        ":8080";

    document.getElementById("palmicorpQrUrl").textContent = url;

    document.getElementById("palmicorpQr").src =
        "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=" +
        encodeURIComponent(url);

}


function updateClock() {

    const now = new Date();

    document.getElementById("clock").textContent =
        now.toLocaleTimeString("pt-BR");

    document.getElementById("date").textContent =
        now.toLocaleDateString(
            "pt-BR",
            {
                weekday: "long",
                day: "2-digit",
                month: "long",
                year: "numeric"
            }
        );

}


function setStatus(element, status) {

    element.textContent = status;

    element.classList.remove(
        "online",
        "offline",
        "standby"
    );


    if(status === "ONLINE") {

        element.classList.add("online");

    }

    else if(status === "OFFLINE") {

        element.classList.add("offline");

    }

    else {

        element.classList.add("standby");

    }

}


async function refreshSystem() {

    try {

        const response =
            await fetch(
                "/api/status?t=" +
                Date.now()
            );

        const data =
            await response.json();
        lastSystemStatus = data;
        setConnectionState(true);


        document.getElementById(
            "battery"
        ).textContent =
            data.battery === null
            ? "UNKNOWN"
            : data.battery + "%";


        document.getElementById(
            "ip"
        ).textContent =
            data.ip;


        document.getElementById(
            "uptime"
        ).textContent =
            data.uptime;


        setStatus(
            document.getElementById(
                "term01"
            ),
            data.devices["PALM-TERM-01"]
        );


        setStatus(
            document.getElementById(
                "term02"
            ),
            data.devices["PALM-TERM-02"]
        );


        setStatus(
            document.getElementById(
                "pc01"
            ),
            data.devices["PALM-PC-01"]
        );


        const mega =
            document.getElementById(
                "megavault"
            );


        mega.textContent =
            data.megavault;


        mega.className = "big";


        if(data.megavault === "ONLINE") {

            mega.classList.add(
                "mega-online"
            );

        }

        else if(
            data.megavault === "OFFLINE"
        ) {

            mega.classList.add(
                "mega-offline"
            );

        }

        else {

            mega.classList.add(
                "mega-config"
            );

        }
        renderAlerts();

    }

    catch(error) {

        console.log(
            "Palmicorp API error",
            error
        );
        setConnectionState(false, "PALMICORP SERVER UNREACHABLE");

    }

}


function bootSequence() {

    const text =
        document.getElementById(
            "bootText"
        );

    const boot =
        document.getElementById(
            "boot"
        );


    const messages = [

        "INITIALIZING PALMICORP CORE...",

        "VERIFYING LOCAL NODE...",

        "MOUNTING PERSONAL WORKSPACE...",

        "STARTING ACCESS CONTROL...",

        "SYNCING TERMINAL SERVICES...",

        "CORE READY // AUTH GATE ACTIVE"

    ];


    let index = 0;


    const timer =
        setInterval(() => {

            text.textContent =
                messages[index];

            index++;


            if(index >= messages.length) {

                clearInterval(timer);


                setTimeout(() => {

                    boot.style.opacity = "0";


                    setTimeout(() => {

                        boot.style.display =
                            "none";

                    }, 600);

                }, 500);

            }

        }, 330);

}


updateBrasiliaClock();
updateWorldClocks();

setInterval(
    updateBrasiliaClock,
    1000
);
setInterval(updateWorldClocks, 1000);

setupPalmicorpQr();


updateClock();

setInterval(
    updateClock,
    1000
);


setInterval(() => { if (palmAuthenticated) refreshSystem(); }, 5000);

bootSequence();
setTimeout(checkAccessCore, 2550);


</script>


</body>

</html>
"""


# =========================================================
# HTTP SERVER
# =========================================================

class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200, extra_headers=None):

        encoded = json.dumps(
            data
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            len(encoded)
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)

        self.end_headers()

        self.wfile.write(encoded)


    def session_token(self):
        return read_cookie(self.headers.get("Cookie") or "", "palm_session")

    def authenticated(self):
        return session_is_valid(self.session_token())

    def require_auth(self):
        if self.authenticated():
            return True
        self.send_json({"error": "authentication required", "auth_required": True}, 401)
        return False

    def session_cookie(self, token):
        return (
            f"palm_session={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; "
            "HttpOnly; SameSite=Strict"
        )

    def clear_session_cookie(self):
        return "palm_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"

    def read_json_body(self, max_bytes=64 * 1024):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > max_bytes:
            raise ValueError("Invalid request size.")
        return json.loads(self.rfile.read(length).decode("utf-8"))


    def serve_local_file(self, file):
        try:
            total = file.stat().st_size
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            start = 0
            end = total - 1
            status = 200
            range_header = self.headers.get("Range") or ""

            if range_header.startswith("bytes="):
                spec = range_header[6:].split(",", 1)[0].strip()
                left, _, right = spec.partition("-")
                if left:
                    start = max(0, int(left))
                    end = min(total - 1, int(right)) if right else total - 1
                elif right:
                    length = min(total, int(right))
                    start = total - length
                    end = total - 1
                if start > end or start >= total:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                status = 206

            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", f'inline; filename="{file.name.replace(chr(34), "")}"')
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.end_headers()

            remaining = end - start + 1
            with file.open("rb") as handle:
                handle.seek(start)
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_error(404)


    def do_GET(self):

        if self.path.startswith("/api/auth/status"):
            self.send_json({
                "configured": load_auth_config() is not None,
                "authenticated": self.authenticated(),
                "session_ttl_seconds": SESSION_TTL_SECONDS,
            })
            return

        if self.path.startswith(
            "/api/status"
        ):
            if not self.require_auth():
                return

            update_device_states()

            battery = get_battery()

            data = {

                "device": DEVICE_NAME,

                "model": DEVICE_MODEL,

                "battery": battery,

                "ip": get_local_ip(),

                "uptime": get_uptime(),

                "megavault":
                    megavault_status(),

                "devices": {

                    name:
                        info["status"]

                    for name, info
                    in devices.items()

                }

            }

            self.send_json(data)

            return


        if self.path.startswith("/api/personal"):
            if not self.require_auth():
                return
            self.send_json(personal_payload())
            return

        if self.path.startswith("/art-file"):
            if not self.require_auth():
                return
            try:
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                kind = (query.get("kind") or [""])[0]
                name = safe_filename((query.get("name") or [""])[0])
                folder = ART_BOOKS_DIR if kind == "book" else ART_DRAWINGS_DIR if kind == "drawing" else None
                if folder is None:
                    self.send_error(404)
                    return
                file = folder / name
                if not file.is_file():
                    self.send_error(404)
                    return
                self.serve_local_file(file)
            except Exception:
                self.send_error(404)
            return


        encoded = PAGE.encode("utf-8")

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            len(encoded)
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

        self.end_headers()

        self.wfile.write(encoded)


    def do_POST(self):

        if self.path == "/api/auth/setup":
            try:
                if load_auth_config() is not None:
                    self.send_json({"error": "Access core already configured."}, 409)
                    return
                payload = self.read_json_body()
                password = str(payload.get("password") or "")
                configure_access_password(password)
                token = create_session()
                self.send_json(
                    {"ok": True, "configured": True, "authenticated": True},
                    200,
                    {"Set-Cookie": self.session_cookie(token)},
                )
            except Exception as error:
                self.send_json({"error": str(error)}, 400)
            return

        if self.path == "/api/auth/login":
            try:
                if load_auth_config() is None:
                    self.send_json({"error": "Access core is not configured."}, 409)
                    return
                payload = self.read_json_body()
                password = str(payload.get("password") or "")
                if not verify_access_password(password):
                    time.sleep(0.35)
                    self.send_json({"error": "ACCESS DENIED"}, 401)
                    return
                token = create_session()
                self.send_json(
                    {"ok": True, "authenticated": True},
                    200,
                    {"Set-Cookie": self.session_cookie(token)},
                )
            except Exception as error:
                self.send_json({"error": str(error)}, 400)
            return

        if self.path == "/api/auth/logout":
            token = self.session_token()
            if token:
                SESSIONS.pop(token, None)
            self.send_json(
                {"ok": True},
                200,
                {"Set-Cookie": self.clear_session_cookie()},
            )
            return

        if self.path == "/api/personal":
            if not self.require_auth():
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                action = str(payload.get("action") or "")
                note = str(payload.get("text") or "").strip()[:4000]
                category = str(payload.get("category") or "Geral").strip()[:60]
                title = str(payload.get("title") or "").strip()[:120]
                lesson = str(payload.get("lesson") or "").strip()[:120]
                level = str(payload.get("level") or "Aprendendo").strip()[:40]
                apply_text = str(payload.get("apply") or "").strip()[:3000]
                question = str(payload.get("question") or "").strip()[:3000]
                next_action = str(payload.get("next_action") or "").strip()[:3000]
                due = str(payload.get("due") or "").strip()[:10]
                priority = str(payload.get("priority") or "Normal").strip()[:20]
                item_id = int(payload.get("id") or 0)
                state = load_personal_state()
                today = datetime.now().strftime("%d/%m/%Y")
                iso_today = datetime.now().strftime("%Y-%m-%d")

                if action == "art_day":
                    if state.get("art_last_day") != iso_today:
                        state["art_days"] = int(state.get("art_days") or 0) + 1
                        state["art_last_day"] = iso_today
                elif action == "study_day":
                    if state.get("study_last_day") != iso_today:
                        state["study_days"] = int(state.get("study_days") or 0) + 1
                        state["study_last_day"] = iso_today
                elif action == "art_note" and note:
                    state.setdefault("art_log", []).append({"date": today, "text": note})
                    state["art_log"] = state["art_log"][-100:]
                elif action == "study_note" and note:
                    state.setdefault("study_log", []).append({"date": today, "text": note})
                    state["study_log"] = state["study_log"][-100:]
                elif action == "ecommerce_note" and note:
                    if state.get("ecommerce_last_day") != iso_today:
                        state["ecommerce_days"] = int(state.get("ecommerce_days") or 0) + 1
                        state["ecommerce_last_day"] = iso_today
                    state.setdefault("ecommerce_log", []).append({
                        "date": today, "category": category, "lesson": lesson, "level": level,
                        "text": note, "apply": apply_text, "question": question, "next_action": next_action,
                    })
                    state["ecommerce_log"] = state["ecommerce_log"][-500:]
                elif action == "note_add" and (title or note):
                    state.setdefault("notes", []).append({
                        "id": int(time.time() * 1000), "date": today, "title": title or "Sem título",
                        "category": category, "text": note, "pinned": False,
                    })
                    state["notes"] = state["notes"][-500:]
                elif action in {"note_pin", "note_delete"} and item_id:
                    notes = state.setdefault("notes", [])
                    found = next((x for x in notes if int(x.get("id") or 0) == item_id), None)
                    if not found:
                        self.send_json({"error": "Nota não encontrada."}, 404); return
                    if action == "note_pin": found["pinned"] = not bool(found.get("pinned"))
                    else: state["notes"] = [x for x in notes if int(x.get("id") or 0) != item_id]
                elif action == "task_add" and title:
                    if due:
                        try: datetime.strptime(due, "%Y-%m-%d")
                        except ValueError:
                            self.send_json({"error": "Data inválida."}, 400); return
                    state.setdefault("tasks", []).append({
                        "id": int(time.time() * 1000), "title": title, "due": due,
                        "category": category, "priority": priority, "done": False, "created": today,
                    })
                    state["tasks"] = state["tasks"][-500:]
                elif action in {"task_toggle", "task_delete"} and item_id:
                    tasks = state.setdefault("tasks", [])
                    found = next((x for x in tasks if int(x.get("id") or 0) == item_id), None)
                    if not found:
                        self.send_json({"error": "Tarefa não encontrada."}, 404); return
                    if action == "task_toggle": found["done"] = not bool(found.get("done"))
                    else: state["tasks"] = [x for x in tasks if int(x.get("id") or 0) != item_id]
                else:
                    self.send_json({"error": "Ação inválida."}, 400)
                    return

                save_personal_state(state)
                self.send_json({"ok": True})
            except Exception as error:
                self.send_json({"error": str(error)}, 400)
            return

        if self.path.startswith("/api/art/upload"):
            if not self.require_auth():
                return
            temp = None
            try:
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                kind = (query.get("kind") or [""])[0]
                name = safe_filename((query.get("name") or [""])[0])
                folder = ART_BOOKS_DIR if kind == "book" else ART_DRAWINGS_DIR if kind == "drawing" else None
                if folder is None:
                    self.send_json({"error": "Tipo de upload inválido."}, 400)
                    return

                length = int(self.headers.get("Content-Length", 0))
                max_bytes = 256 * 1024 * 1024
                if length <= 0:
                    self.send_json({"error": "Arquivo vazio ou tamanho desconhecido."}, 400)
                    return
                if length > max_bytes:
                    self.send_json({"error": "Arquivo pode ter no máximo 256 MB."}, 413)
                    return

                mime = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                suffix = Path(name).suffix.lower()
                allowed_mime = {
                    "book": {"application/pdf", "application/octet-stream"},
                    "drawing": {"image/jpeg", "image/png", "image/webp", "image/gif", "application/octet-stream"},
                }
                allowed_ext = {
                    "book": {".pdf"},
                    "drawing": {".jpg", ".jpeg", ".png", ".webp", ".gif"},
                }
                if suffix not in allowed_ext[kind] or mime not in allowed_mime[kind]:
                    self.send_json({"error": "Tipo de arquivo não permitido."}, 400)
                    return

                target = folder / name
                if target.exists():
                    target = folder / f"{target.stem}-{int(time.time())}{target.suffix}"
                temp = target.with_name(target.name + ".uploading")

                remaining = length
                with temp.open("wb") as handle:
                    while remaining > 0:
                        chunk = self.rfile.read(min(256 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Upload interrompido antes do fim.")
                        handle.write(chunk)
                        remaining -= len(chunk)
                temp.replace(target)
                temp = None
                self.send_json({"ok": True, "name": target.name, "size": target.stat().st_size})
            except Exception as error:
                try:
                    if temp and temp.exists():
                        temp.unlink()
                except Exception:
                    pass
                self.send_json({"error": str(error)}, 400)
            return

        if self.path != "/api/heartbeat":
            self.send_json({"error": "unknown endpoint"}, 404)
            return

        try:

            length = int(
                    self.headers.get(
                        "Content-Length",
                        0
                    )
                )

            body = self.rfile.read(length)

            payload = json.loads(
                    body.decode("utf-8")
                )

            name = payload.get(
                    "device"
                )


            if name not in devices:

                self.send_json(
                    {
                        "error":
                        "unknown device"
                    },
                    400
                )

                return


            devices[name]["last_seen"] = time.time()

            devices[name]["status"] = "ONLINE"


            self.send_json(
                {
                    "ok": True,

                    "device": name
                }
            )


        except Exception as error:

            self.send_json(
                {
                    "error":
                    str(error)
                },
                400
            )


    def log_message(
        self,
        format,
        *args
    ):

        print(
            "[PALMICORP]",
            self.address_string(),
            "-",
            format % args
        )


# =========================================================
# START
# =========================================================

server = ThreadingHTTPServer(
    (
        "0.0.0.0",
        PORT
    ),
    Handler
)


print()
print("=" * 42)
print("           PALMICORP SYSTEM")
print("=" * 42)

print()

print(
    "DEVICE  :",
    DEVICE_NAME
)

print(
    "MODEL   :",
    DEVICE_MODEL
)

print(
    "STATUS  : ONLINE"
)

print(
    "IP      :",
    get_local_ip()
)

print(
    "PORT    :",
    PORT
)

print()

print(
    "PALMICORP TERMINAL v2.3.0-alpha"
)

print("=" * 42)
print()


try:

    server.serve_forever()

except KeyboardInterrupt:

    print()
    print(
        "PALMICORP TERMINAL OFFLINE"
    )

finally:

    server.server_close()
