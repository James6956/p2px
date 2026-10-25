import json, urllib.request, os
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN=os.env...EN", "").strip()
ADMIN = 1817225234
RATE = {"upi": 100, "cdm": 99}

def notify(text):
    data = json.dumps({"chat_id": ADMIN, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print("notify failed:", e, "toklen=", len(TOKEN), "prefix=", TOKEN[:4], flush=True)
        return False

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode() if isinstance(body, str) else body)
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")) as f:
                self._send(200, f.read())
        else:
            self._send(404, "not found")
    def do_POST(self):
        if self.path == "/api/order":
            try:
                o = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                mode = o.get("mode")
                amt = float(o.get("amount", 0))
                inr = int(round(amt * RATE.get(mode, 100)))
                tag = "UPI" if mode == "upi" else "CDM"
                detail = f"UPI: {o.get('upi')}" if mode == "upi" else f"Account: {o.get('account')}\nPhone: {o.get('phone')}"
                ok = notify(f"🌐 WEBSITE ORDER ({tag})\n\nAmount: ${amt:g}\nPayout: ₹{inr:,}\n{detail}\n\nCollect the USDT, then message the customer on Telegram to complete.")
                self._send(200, json.dumps({"ok": ok}))
            except Exception as e:
                print("order err:", e, flush=True)
                self._send(400, json.dumps({"ok": False}))
        else:
            self._send(404, "not found")

HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever()
