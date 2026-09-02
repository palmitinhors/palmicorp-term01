from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import json
import os
import socket
import time
import urllib.request

PORT = 8080

# =========================================================
# PALMICORP CONFIG
# =========================================================

DEVICE_NAME = "PALM-TERM-01"
DEVICE_MODEL = "SAMSUNG GALAXY A02"

# Quando quiser conectar o MegaVault real,
# coloque o endereço dele aqui.
MEGAVAULT_URL = "https://megavault-privado.bonny-eagle-2456.chatgpt.site"

# Quantos segundos sem heartbeat para considerar offline.
HEARTBEAT_TIMEOUT = 30

START_TIME = time.time()

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


<div class="section">

    <div class="buttons">

        <button onclick="refreshSystem()">
            REFRESH SYSTEM
        </button>

        <button onclick="location.reload()">
            RELOAD TERMINAL
        </button>

    </div>

</div>


<footer>

PALMICORP TERMINAL SYSTEM
<br>
VERSION 1.0

</footer>


</div>


<script>


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

        if self.path != "/api/heartbeat":

            self.send_json(
                {
                    "error":
                    "unknown endpoint"
                },
                404
            )

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
