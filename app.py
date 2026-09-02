from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime

PORT = 8080

PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PALM-TERM-01</title>

    <style>
        body {
            background: #080b10;
            color: #eeeeee;
            font-family: monospace;
            text-align: center;
            margin: 0;
            padding: 30px 15px;
        }

        h1 {
            font-size: 30px;
            letter-spacing: 4px;
        }

        .terminal {
            max-width: 500px;
            margin: auto;
        }

        .status {
            margin: 30px 0;
            padding: 20px;
            border: 1px solid #444;
            border-radius: 12px;
        }

        .online {
            color: #4cff7a;
        }

        .device {
            font-size: 20px;
        }

        .version {
            margin-top: 40px;
            color: #777;
        }
    </style>
</head>

<body>

<div class="terminal">

    <h1>PALMICORP</h1>

    <p>TERMINAL SYSTEM</p>

    <div class="status">

        <div class="device">
            PALM-TERM-01
        </div>

        <p>SAMSUNG GALAXY A02</p>

        <p class="online">
            ● SYSTEM ONLINE
        </p>

    </div>

    <p>PALMICORP NETWORK</p>

    <p>
        TERM-01 &nbsp; <span class="online">ONLINE</span>
    </p>

    <p>
        TERM-02 &nbsp; STANDBY
    </p>

    <p>
        PALM-PC-01 &nbsp; NOT CONNECTED
    </p>

    <div class="version">
        PALMICORP TERMINAL v0.1
    </div>

</div>

</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        self.wfile.write(PAGE.encode("utf-8"))


server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)

print("--------------------------------")
print("       PALMICORP SYSTEM")
print("--------------------------------")
print()
print("DEVICE : PALM-TERM-01")
print("STATUS : ONLINE")
print("PORT   :", PORT)
print()
print("PALMICORP TERMINAL v0.1")
print("--------------------------------")

server.serve_forever()
