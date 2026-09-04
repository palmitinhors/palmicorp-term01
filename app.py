from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
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

for folder in (DATA_DIR, ART_BOOKS_DIR, ART_DRAWINGS_DIR):
    folder.mkdir(parents=True, exist_ok=True)


def default_personal_state():
    return {
        "art_days": 0,
        "study_days": 0,
        "art_log": [],
        "study_log": [],
        "art_last_day": None,
        "study_last_day": None,
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

    <div class="terminal-id">
        PALM-TERM-01
    </div>

</header>

<div class="module-nav">
    <button class="active" data-module="home" onclick="showModule('home', this)">HOME</button>
    <button data-module="art" onclick="showModule('art', this)">NYVIK ART</button>
    <button data-module="etec" onclick="showModule('etec', this)">ETEC STUDY</button>
    <button data-module="vault" onclick="showModule('vault', this)">VAULT</button>
    <button data-module="notes" onclick="showModule('notes', this)">NOTES</button>
    <button data-module="calendar" onclick="showModule('calendar', this)">CALENDAR</button>
    <button data-module="system" onclick="showModule('system', this)">SYSTEM</button>
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

<div id="module-vault" class="module-page">
    <div class="module-placeholder"><strong>VAULT CORE</strong><span>Password Vault + File Vault entram aqui na próxima etapa, com segurança de verdade.</span></div>
</div>

<div id="module-notes" class="module-page">
    <div class="module-placeholder"><strong>NOTES</strong><span>Notas pessoais, fixadas e privadas — módulo preparado para a próxima etapa.</span></div>
</div>

<div id="module-calendar" class="module-page">
    <div class="module-placeholder"><strong>CALENDAR / TASKS</strong><span>Calendário, tarefas, lembretes de desenho e estudo entram aqui.</span></div>
</div>

<div id="module-system" class="module-page">
    <div class="module-placeholder"><strong>PALMICORP SYSTEM</strong><span>Alert Center, Offline UI, PWA, ícones, versioning e controles do servidor.</span></div>
</div>

<div id="moduleToast" class="module-toast">PALMICORP</div>

<footer>

PALMICORP TERMINAL SYSTEM
<br>
VERSION 2.0.0-alpha // NYVIK ART

</footer>


</div>


<script>

let personalState = null;

function showModule(name, button) {
    document.querySelectorAll('.module-page').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.module-nav button').forEach(el => el.classList.remove('active'));
    const page = document.getElementById('module-' + name);
    if (page) page.classList.add('active');
    if (button) button.classList.add('active');
    if (name === 'art' || name === 'etec') refreshPersonal();
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

async function refreshPersonal() {
    try {
        const response = await fetch('/api/personal?t=' + Date.now());
        personalState = await response.json();
        document.getElementById('artDays').textContent = personalState.art_days || 0;
        document.getElementById('studyDays').textContent = personalState.study_days || 0;
        document.getElementById('artDrawingCount').textContent = (personalState.drawings || []).length;
        document.getElementById('artBookCount').textContent = (personalState.books || []).length;

        document.getElementById('artBooks').innerHTML = (personalState.books || []).map(file =>
            `<div class="library-item"><a target="_blank" href="/art-file?kind=book&name=${encodeURIComponent(file.name)}">${file.name}</a><small>${prettySize(file.size)}</small></div>`
        ).join('') || '<div class="small">Nenhum PDF ainda.</div>';

        document.getElementById('artGallery').innerHTML = (personalState.drawings || []).map(file =>
            `<a class="art-tile" target="_blank" href="/art-file?kind=drawing&name=${encodeURIComponent(file.name)}"><img loading="lazy" src="/art-file?kind=drawing&name=${encodeURIComponent(file.name)}"><span>${file.name}</span></a>`
        ).join('') || '<div class="small">Sua galeria ainda está vazia.</div>';

        document.getElementById('artLog').innerHTML = (personalState.art_log || []).slice().reverse().slice(0,8).map(item =>
            `<div class="log-entry"><strong>${item.date}</strong><br>${item.text}</div>`
        ).join('');

        document.getElementById('studyLog').innerHTML = (personalState.study_log || []).slice().reverse().slice(0,8).map(item =>
            `<div class="log-entry" style="border-color:#65e8ff"><strong>${item.date}</strong><br>${item.text}</div>`
        ).join('');
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

function uploadArtFile(kind, input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    toast('Enviando para o A02...');
    reader.onload = async () => {
        try {
            const response = await fetch('/api/art/upload', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({kind, name:file.name, data:reader.result})
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Upload falhou');
            input.value='';
            await refreshPersonal();
            toast(kind === 'book' ? 'PDF salvo na NYVIK LIBRARY' : 'Desenho salvo na NYVIK GALLERY');
        } catch (error) {
            toast(error.message || 'Falha no upload');
        }
    };
    reader.readAsDataURL(file);
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

    }

    catch(error) {

        console.log(
            "Palmicorp API error",
            error
        );

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

        "INITIALIZING TERMINAL...",

        "CONNECTING PALMICORP NETWORK...",

        "CHECKING DEVICE...",

        "SYSTEM ONLINE"

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

        }, 450);

}


updateBrasiliaClock();

setInterval(
    updateBrasiliaClock,
    1000
);

setupPalmicorpQr();


updateClock();

setInterval(
    updateClock,
    1000
);


refreshSystem();

setInterval(
    refreshSystem,
    5000
);

refreshPersonal();

bootSequence();


</script>


</body>

</html>
"""


# =========================================================
# HTTP SERVER
# =========================================================

class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):

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

        self.end_headers()

        self.wfile.write(encoded)


    def do_GET(self):

        if self.path.startswith(
            "/api/status"
        ):

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
            self.send_json(personal_payload())
            return

        if self.path.startswith("/art-file"):
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
                content = file.read_bytes()
                mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", len(content))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(content)
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

        self.end_headers()

        self.wfile.write(encoded)


    def do_POST(self):

        if self.path == "/api/personal":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                action = str(payload.get("action") or "")
                note = str(payload.get("text") or "").strip()[:1200]
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
                else:
                    self.send_json({"error": "Ação inválida."}, 400)
                    return

                save_personal_state(state)
                self.send_json({"ok": True})
            except Exception as error:
                self.send_json({"error": str(error)}, 400)
            return

        if self.path == "/api/art/upload":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > 48 * 1024 * 1024:
                    self.send_json({"error": "Arquivo muito grande."}, 413)
                    return
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                kind = str(payload.get("kind") or "")
                name = safe_filename(payload.get("name"))
                data_url = str(payload.get("data") or "")
                folder = ART_BOOKS_DIR if kind == "book" else ART_DRAWINGS_DIR if kind == "drawing" else None
                if folder is None or "," not in data_url:
                    self.send_json({"error": "Upload inválido."}, 400)
                    return
                header, encoded_data = data_url.split(",", 1)
                mime = header[5:].split(";", 1)[0] if header.startswith("data:") else ""
                allowed = {
                    "book": {"application/pdf"},
                    "drawing": {"image/jpeg", "image/png", "image/webp", "image/gif"},
                }
                if mime not in allowed[kind]:
                    self.send_json({"error": "Tipo de arquivo não permitido."}, 400)
                    return
                raw = base64.b64decode(encoded_data, validate=True)
                if len(raw) > 32 * 1024 * 1024:
                    self.send_json({"error": "Arquivo pode ter no máximo 32 MB."}, 413)
                    return
                target = folder / name
                if target.exists():
                    stem, suffix = target.stem, target.suffix
                    target = folder / f"{stem}-{int(time.time())}{suffix}"
                target.write_bytes(raw)
                self.send_json({"ok": True, "name": target.name})
            except Exception as error:
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
    "PALMICORP TERMINAL v1.0"
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
