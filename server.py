import json, urllib.request, os
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMINS = [1817225234, 5538515530, 2025705698]
ADMIN = ADMINS[0]
SITE_RATE = {"upi": 100, "cdm": 99}

def upi_pay_link(upi, inr):
    from urllib.parse import quote
    return f"upi://pay?pa={quote(upi)}&am={inr}&cu=INR"

def _ensure_zxing():
    try:
        import zxingcpp
    except ImportError:
        import subprocess
        subprocess.run(["pip", "install", "--quiet", "zxing-cpp", "Pillow"], check=False)
        import zxingcpp
    return zxingcpp

def decode_upi_bytes(data):
    try:
        import io
        zx = _ensure_zxing()
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        for r in zx.read_barcodes(img):
            if r.text.startswith("upi://"):
                return r.text
        return None
    except Exception as e:
        print("qr decode failed:", e, flush=True)
        return None

def decode_upi_qr(file_id):
    try:
        gp = call("getFile", file_id=file_id)
        data = urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{gp['result']['file_path']}", timeout=30).read()
        return decode_upi_bytes(data)
    except Exception as e:
        print("qr fetch failed:", e, flush=True)
        return None

def notify(text):
    for cid in ADMINS:
        data = json.dumps({"chat_id": cid, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            print("notify failed:", e, flush=True)
            return False
    return True

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
        elif self.path.startswith("/pay?"):
            from urllib.parse import urlparse, parse_qs, quote
            q = parse_qs(urlparse(self.path).query)
            upi = q.get("u", [""])[0]
            amt = q.get("a", [""])[0]
            if upi:
                link = f"upi://pay?pa={quote(upi)}"
                if amt:
                    link += f"&am={amt}"
                link += "&cu=INR"
                self._send(200, f'<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="0;url={link}"><title>Opening payment app…</title></head><body style="font-family:sans-serif;text-align:center;padding-top:40vh"><p>Opening your payment app…</p><p><a href="{link}">Tap here if it does not open</a></p></body></html>')
            else:
                self._send(400, "missing upi")
        elif self.path == "/api/ping":
            self._send(200, json.dumps({"ok": True}))
        elif self.path.startswith("/api/status"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            oid = (q.get("id") or [""])[0]
            o = web_orders.get(oid)
            if o:
                self._send(200, json.dumps({"ok": True, "status": o["status"]}))
            else:
                self._send(404, json.dumps({"ok": False}))
        else:
            self._send(404, "not found")
    def do_POST(self):
        if self.path == "/api/order":
            try:
                o = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                mode = o.get("mode")
                amt = float(o.get("amount", 0))
                min_usd = 50.50 if mode == "cdm" else 0.1
                if mode not in SITE_RATE or amt < min_usd:
                    raise ValueError("bad order")
                inr = int(round(amt * SITE_RATE[mode]))
                oid = new_oid()
                qr_mode = bool(o.get("qr"))
                web_orders[oid] = {"amt": amt, "inr": inr, "mode": mode, "upi": o.get("upi"),
                                   "acct": o.get("account"), "phone": o.get("phone"),
                                   "ref": (o.get("ref") or "")[:20],
                                   "status": "awaiting_qr" if qr_mode else "awaiting", "ts": int(time.time())}
                _web_save()
                tag = "UPI" if mode == "upi" else "CDM"
                detail = (f"QR photo coming next..." if qr_mode else f"UPI: {o.get('upi')}") if mode == "upi" else f"Account: {o.get('account')}\nPhone: {o.get('phone')}"
                wkb = [[
                    {"text": "✅ USDT received", "callback_data": f"webr:{oid}"},
                    {"text": "🚫 Cancel", "callback_data": f"webc:{oid}"},
                ]]
                try:
                    call("sendMessage", chat_id=ADMIN, text=(f"🌐 WEBSITE ORDER #{oid} ({tag})\n\nAmount: ${amt:g}\nPayout: ₹{inr:,}\n{detail}\n\nOn-chain watch is running. Tap ✅ when you see the USDT, then pay the rupees and tap 💸."), reply_markup={"inline_keyboard": wkb})
                except Exception as e:
                    print("web admin notify failed:", e, flush=True)
                self._send(200, json.dumps({"ok": True, "id": oid, "status": "awaiting"}))
            except Exception as e:
                print("order err:", e, flush=True)
                self._send(400, json.dumps({"ok": False}))
        elif self.path == "/api/qr":
            try:
                o = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                oid = str(o.get("id", ""))
                import base64 as _b64
                data = _b64.b64decode(o.get("img", ""))
                if len(data) > 6_000_000:
                    raise ValueError("too big")
                order = web_orders.get(oid)
                if not order and o.get("order"):
                    d = o["order"]
                    mode = d.get("mode")
                    amt = float(d.get("amount", 0))
                    if mode not in SITE_RATE or amt < (50.50 if mode == "cdm" else 0.1):
                        raise ValueError("bad order rebuild")
                    inr = int(round(amt * SITE_RATE[mode]))
                    order = {"amt": amt, "inr": inr, "mode": mode, "upi": None,
                             "acct": d.get("account"), "phone": d.get("phone"),
                             "ref": (d.get("ref") or "")[:20], "status": "awaiting", "ts": int(time.time())}
                    web_orders[oid] = order
                    _web_save()
                if not order:
                    self._send(200, json.dumps({"ok": False, "err": "Order expired after a server restart. Please start the order again."}))
                    return
                upi_uri = decode_upi_bytes(data)
                if not upi_uri:
                    self._send(200, json.dumps({"ok": False, "err": "Could not read that QR. Make sure the whole QR is in the photo, or type your UPI ID instead."}))
                    return
                from urllib.parse import urlparse as _up, parse_qs as _pq, quote as _q
                qp = _pq(_up(upi_uri).query)
                pay_upi = qp.get("pa", [""])[0]
                order["upi"] = pay_upi
                if order["status"] == "awaiting_qr":
                    order["status"] = "awaiting"
                _web_save()
                inr = order["inr"]
                tkb = [[
                    {"text": f"📲 Pay ₹{inr:,} (opens payment app)", "url": f"https://p2px.onrender.com/pay?u={_q(pay_upi)}&a={_q(str(inr))}"},
                ], [
                    {"text": "✅ USDT received", "callback_data": f"webr:{oid}"},
                    {"text": "🚫 Cancel", "callback_data": f"webc:{oid}"},
                ]]
                try:
                    call("sendMessage", chat_id=ADMIN, text=(f"🌐 WEBSITE ORDER #{oid} — QR received\nUPI: <code>{pay_upi}</code>\nPayout: ₹{inr:,}\n\nTap the pay button to open your payment app with the amount filled."), reply_markup={"inline_keyboard": tkb}, parse_mode="HTML")
                except Exception as e:
                    print("qr admin notify failed:", e, flush=True)
                self._send(200, json.dumps({"ok": True}))
            except Exception as e:
                print("qr upload err:", e, flush=True)
                self._send(200, json.dumps({"ok": False, "err": "Upload failed, try again"}))
        else:
            self._send(404, "not found")

# ===== bot (runs in a thread so the same free render service hosts both) =====
import json, time, urllib.request, os

TOKEN=os.environ.get("BOT_TOKEN","").strip()
ADMIN = 1817225234
WALLET = "0xD606DC557199390D8B0c15937e136ea15D4C5FBb"
API = f"https://api.telegram.org/bot{TOKEN}"
MIN_USD = 0.1
import random, string as _string
def new_oid():
    return "".join(random.choices(_string.digits, k=6))
order_ids = {}  # chat -> order id

# ---- referral system ----
REF_FLAT = 10.0        # rs per referred friend first completed order
REF_PCT = 0.003        # 0.30 pct of every referred order (inr)
referrals = {}         # user_id -> referrer_id
seen_users = set()
ref_paid_first = set()
ref_bal = {}           # referrer_id -> pending rupees
ref_withdraw = {}      # referrer_id -> upi id pending request
waiting_refw = set()
REF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "referrals.json")

def _ref_save():
    try:
        with open(REF_FILE, "w") as f:
            json.dump({"referrals": {str(k): v for k, v in referrals.items()}, "seen": list(seen_users), "first": list(ref_paid_first),
                       "bal": {str(k): v for k, v in ref_bal.items()},
                       "wd": {str(k): v for k, v in ref_withdraw.items()}}, f)
    except Exception as e:
        print("ref save failed:", e, flush=True)

def _ref_load():
    global referrals, seen_users, ref_paid_first, ref_bal, ref_withdraw
    try:
        with open(REF_FILE) as f:
            d = json.load(f)
        referrals = {int(k): int(v) for k, v in d.get("referrals", {}).items()}
        seen_users = set(d.get("seen", []))
        ref_paid_first = set(d.get("first", []))
        ref_bal = {int(k): float(v) for k, v in d.get("bal", {}).items()}
        ref_withdraw = {int(k): v for k, v in d.get("wd", {}).items()}
    except Exception:
        pass

_ref_load()

def ref_credit(target, inr):
    ref = referrals.get(target)
    if not ref or ref == target:
        return
    earn = 0.0
    if target not in ref_paid_first:
        ref_paid_first.add(target)
        earn += REF_FLAT
    earn += round(inr * REF_PCT, 2)
    if earn <= 0:
        return
    ref_bal[ref] = round(ref_bal.get(ref, 0.0) + earn, 2)
    _ref_save()
    try:
        send(ref, f"Referral reward: Rs {earn:,.2f} added.\n\nBalance: Rs {ref_bal[ref]:,.2f}\n\nWithdraw anytime from the Refer button.")
    except Exception:
        pass

# ===== on-chain payment verification (Etherscan V2, one free key covers all chains) =====
import threading as _th
WALLET_L = WALLET.lower()
ES_KEY = os.environ.get("ETHERSCAN_KEY", "").strip()
ES_V2 = "https://api.etherscan.io/v2/api"
CHAINS = {"bep20": 56, "arb": 42161, "pol": 137, "base": 8453}
USDT = {
    "bep20": "0x55d398326f99059ff775485246999027b3197955",
    "arb":   "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
    "pol":   "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    "base":  "0xfde4c96ca85d4fe8d2136fc38cdbae4fbc2574cd",
}
USDC = {
    "bep20": "0x8ac76a51cc950d9822d68b83fe1c97db239d6c",
    "arb":   "0x912ce59144191c1204e64559fe8253a0e49e6548",
    "pol":   "0x3c499c542cef5e3811e1192ce54d1406e970125",
    "base":  "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
}
VALID_TOKENS = {}
for _n, _c in USDT.items():
    VALID_TOKENS.setdefault(_c.lower(), []).append(_n + " USDT")
for _n, _c in USDC.items():
    VALID_TOKENS.setdefault(_c.lower(), []).append(_n + " USDC")
watch_payments = {}     # chat -> {"amt": float, "started": unix ts, "notified": bool}
verified_payments = {}  # chat -> result dict

def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "p2px/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())

def check_incoming(amt, since_ts=None):
    """Find an incoming REAL USDT/USDC transfer to WALLET of >= amt. 1 call per chain."""
    for net, chainid in CHAINS.items():
        url = (f"{ES_V2}?chainid={chainid}&module=account&action=tokentx"
               f"&address={WALLET_L}&page=1&offset=100&sort=desc&apikey={ES_KEY}")
        try:
            res = _get_json(url)
        except Exception:
            continue
        rows = res.get("result")
        if not isinstance(rows, list):
            continue
        for tx in rows[:100]:
            if tx.get("to", "").lower() != WALLET_L:
                continue
            if tx.get("contractAddress", "").lower() not in VALID_TOKENS:
                continue
            if since_ts and int(tx.get("timeStamp") or 0) < since_ts:
                continue
            dec = int(tx.get("tokenDecimal") or 18)
            val = int(tx.get("value") or 0) / (10 ** dec)
            if val + 1e-6 >= amt:
                return {"net": net, "amount": val, "tx": tx.get("hash", ""), "token": tx.get("contractAddress", "")}
    return None

def payment_watcher():
    while True:
        try:
            hour = time.localtime().tm_hour
            if hour < 8 or hour >= 23:
                time.sleep(60)
                continue
            if ES_KEY:
                for chat in list(watch_payments.keys()):
                    w = watch_payments[chat]
                    if w.get("notified"):
                        continue
                    hit = check_incoming(w["amt"], since_ts=w.get("started"))
                    if hit:
                        hit["oid"] = order_ids.get(chat, "")
                        verified_payments[chat] = hit
                        w["notified"] = True
                        notify(f"✅ PAYMENT VERIFIED ON-CHAIN\n\n🧾 Order ID: {hit['oid']}\nUser: {chat}\nAmount: {hit['amount']:g}\nChain: {hit['net'].upper()}\nToken: {hit['token'][:12]}...\nTx: {hit['tx'][:20]}...")
                    elif time.time() - w["started"] > 3600 and not w.get("warned"):
                        w["warned"] = True
                        notify(f"⚠️ No on-chain payment yet\n\n🧾 Order ID: {order_ids.get(chat, '')}\nUser: {chat}\nNothing matching ${w['amt']:g} arrived within 1 hour of confirm. They may not have sent.")
        except Exception as e:
            print("watch err:", e, flush=True)
        time.sleep(5)

# ---- website orders (bot-like flow on p2px.onrender.com) ----
WEB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_orders.json")
web_orders = {}

def _web_save():
    try:
        with open(WEB_FILE, "w") as f:
            json.dump(web_orders, f)
    except Exception as e:
        print("web save failed:", e, flush=True)

def _web_load():
    global web_orders
    try:
        with open(WEB_FILE) as f:
            web_orders = json.load(f)
    except Exception:
        pass

_web_load()

def web_watch():
    while True:
        try:
            changed = False
            for oid, o in list(web_orders.items()):
                if o.get("status") == "awaiting" and not o.get("alerted"):
                    hit = check_incoming(o["amt"], o["ts"])
                    if hit:
                        o["alerted"] = True
                        changed = True
                        o["status"] = "payout"
                        _web_save()
                        try:
                            call("sendMessage", chat_id=ADMIN, text=(f"✅ USDT detected on-chain for WEBSITE order #{oid} (${o['amt']:g}). Pay the rupees, then tap ✅ Paid below."), reply_markup={"inline_keyboard": [[
                                {"text": "💸 Paid", "callback_data": f"webd:{oid}"},
                                {"text": "🚫 Cancel", "callback_data": f"webc:{oid}"},
                            ]]})
                        except Exception as e:
                            print("web watch notify failed:", e, flush=True)
        except Exception as e:
            print("web watch err:", e, flush=True)
        time.sleep(60)

_th.Thread(target=web_watch, daemon=True).start()


def _boot_check():
    if not ES_KEY:
        notify("⚠️ payment checker OFFLINE — ETHERSCAN_KEY not set on render. It will silently do nothing until the key is added.")
        return
    try:
        res = _get_json(f"{ES_V2}?chainid=137&module=account&action=tokentx&contractaddress={USDT['pol']}&address={WALLET_L}&page=1&offset=1&sort=desc&apikey={ES_KEY}")
        if isinstance(res.get("result"), list):
            notify("✅ payment checker online — key works, watching all 4 chains")
        else:
            notify(f"❌ payment checker key problem: {str(res.get('result') or res.get('message'))[:80]}")
    except Exception as e:
        notify(f"❌ payment checker error: {str(e)[:80]}")

_th.Thread(target=payment_watcher, daemon=True).start()
_th.Thread(target=_boot_check, daemon=True).start()

offset = 0
waiting_amount = set()
waiting_upi = {}
waiting_upi_choice = {}
active_orders = {}
user_pending = {}
chat_rate = {}
pending_approval = {}
waiting_acct = {}
waiting_phone = {}
pay_method = {}
proof_reply = {}

def call(method, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def send(chat_id, text, kb=None, pm="Markdown"):
    targets = ADMINS if chat_id in ADMINS else [chat_id]
    for cid in targets:
        params = {"chat_id": cid, "text": text, "parse_mode": pm}
        if kb:
            params["reply_markup"] = {"inline_keyboard": kb}
        try:
            call("sendMessage", **params)
        except Exception:
            params.pop("parse_mode", None)
            call("sendMessage", **params)

KB = [[
    {"text": "💵 Sell at 100rs/$ (UPI)", "callback_data": "sell"},
    {"text": "🏦 Sell at 99rs/$ (CDM)", "callback_data": "sellcdm"},
],[
    {"text": "🏪 Become merchant / 💰 Buy USDT", "callback_data": "merchant"},
],[
    {"text": "🎁 Refer and earn ₹10 + 0.30%", "callback_data": "ref"},
    {"text": "❓ Help", "callback_data": "help"},
]]

WELCOME = ("Welcome to the P2P exchange!\n\n"
           "• Sell at 100rs/$ (UPI)\n"
           "• Sell at 99rs/$ (CDM)\n"
           "• Become a merchant / buy USDT\n"
           "• Help")


def bot_loop():
    global offset
    print("bot debug: toklen=", len(TOKEN), "prefix=", TOKEN[:5], flush=True)
    while True:
        try:
            res = call("getUpdates", offset=offset, timeout=50)
            for up in res.get("result", []):
                offset = up["update_id"] + 1
                msg = up.get("message")
                cb = up.get("callback_query")
                if msg:
                    chat = msg["chat"]["id"]
                    name = msg["chat"].get("first_name", "")
                    username = msg["chat"].get("username", "no username")
                    text = msg.get("text", "").strip()
                    if text.startswith("/start"):
                        waiting_amount.discard(chat)
                        waiting_upi.pop(chat, None)
                        waiting_upi_choice.pop(chat, None)
                        waiting_acct.pop(chat, None)
                        waiting_phone.pop(chat, None)
                        parts = text.split()
                        if len(parts) > 1 and parts[1].upper().startswith("REF") and parts[1][3:].isdigit():
                            ref = int(parts[1][3:])
                            if ref != chat and chat not in seen_users:
                                referrals[chat] = ref
                                try:
                                    send(ref, "👥 New referral joined with your link! You get ₹10 + 0.30% after they complete their first order.")
                                except Exception:
                                    pass
                        if chat not in seen_users:
                            seen_users.add(chat)
                            _ref_save()
                        send(chat, WELCOME, KB)
                    elif chat in waiting_acct:
                        acct = text.strip().replace(" ", "")
                        if not acct.isdigit() or len(acct) < 9 or len(acct) > 18:
                            send(chat, "That doesn't look like a bank account number. It's usually 9-18 digits. Try again:")
                            continue
                        amt = waiting_acct.pop(chat)
                        waiting_phone[chat] = (amt, acct)
                        send(chat, "✅ Account number saved.\n\nNow enter your phone number (10 digits, linked to the account):")
                    elif chat in waiting_phone:
                        phone = text.strip().replace(" ", "").replace("+91", "").replace("-", "")
                        if not phone.isdigit() or len(phone) != 10:
                            send(chat, "That doesn't look right. Enter a 10-digit phone number:")
                            continue
                        amt, acct = waiting_phone.pop(chat)
                        rate = chat_rate.get(chat, 99)
                        active_orders[chat] = (amt, rate)
                        send(chat, f"✅ Phone number saved.\n\nYour ₹{amt * rate:,.0f} will be transferred via CDM shortly.", KB)
                        send(ADMIN, f"🏦 CDM PAYOUT\n\n🧾 Order ID: {order_ids.get(chat, '?')}\n\n{name} (@{username}, id {chat})\nConfirmed ${amt:g} order\nBank account: {acct}\nPhone: {phone}\nPayout: ₹{amt * rate:,.0f}\n\nDo the CDM deposit, then tap a button to tell them:", [[
                            {"text": "✅ Payment done", "callback_data": f"adm_done:{chat}"},
                            {"text": "🚫 Order cancelled", "callback_data": f"adm_cancel:{chat}"},
                        ]])
                    elif chat in ADMINS and msg.get("photo") and chat in proof_reply:
                        dest = proof_reply.pop(chat)
                        try:
                            call("sendPhoto", chat_id=dest, photo=msg["photo"][-1]["file_id"], caption="📸 Your payment screenshot proof.")
                            send(chat, "forwarded ✅")
                        except Exception as e:
                            print("proof forward failed:", e, flush=True)
                            send(chat, "forward failed, try again")
                        continue
                    elif chat in waiting_upi and msg.get("photo"):
                        amt = waiting_upi.get(chat)
                        rate = chat_rate.get(chat, 100)
                        file_id = msg["photo"][-1]["file_id"]
                        send(chat, "⏳ One sec, checking your QR…")
                        upi_uri = decode_upi_qr(file_id)
                        pay_note = "Scan the QR to pay, then tap a button to tell them:"
                        akb = [[
                            {"text": "✅ Payment done", "callback_data": f"adm_done:{chat}"},
                            {"text": "🚫 Order cancelled", "callback_data": f"adm_cancel:{chat}"},
                        ]]
                        if upi_uri:
                            from urllib.parse import urlparse, parse_qs, quote
                            qp = parse_qs(urlparse(upi_uri).query)
                            pay_upi = qp.get("pa", [""])[0]
                            pay_amt = qp.get("am", [str(int(amt * rate))])[0]
                            web_pay = f"https://p2px.onrender.com/pay?u={quote(pay_upi)}&a={quote(pay_amt)}"
                            pay_note = "Tap the pay button below to open your payment app with everything filled:"
                            akb = [[
                                {"text": f"📲 Pay ₹{amt * rate:,.0f} (opens payment app)", "url": web_pay},
                            ], [
                                {"text": "✅ Payment done", "callback_data": f"adm_done:{chat}"},
                                {"text": "🚫 Order cancelled", "callback_data": f"adm_cancel:{chat}"},
                            ]]
                        caption = (f"📸 UPI QR FROM USER ({'CDM' if rate == 99 else 'USDT'})\n\n{name} (@{username}, id {chat})\n"
                                   f"Confirmed ${amt:g} order\nPayout: ₹{amt * rate:,.0f}\n\n"
                                   f"{pay_note}")
                        ok = True
                        for cid in ADMINS:
                            try:
                                call("sendPhoto", chat_id=cid, photo=file_id, caption=caption, reply_markup={"inline_keyboard": akb})
                            except Exception as e:
                                print("qr forward failed:", e, flush=True)
                                ok = False
                        if ok:
                            waiting_upi.pop(chat, None)
                            active_orders[chat] = (amt, rate)
                            pay_method[chat] = "qr"
                            send(chat, f"✅ QR received! Your ₹{amt * rate:,.0f} is on the way. Our team will send it shortly.", KB)
                        continue
                    elif chat in ADMINS and text.lower().startswith("revoke "):
                        try:
                            target = int(text.split()[1])
                        except (ValueError, IndexError):
                            send(ADMIN, "Usage: revoke <user id>")
                            continue
                        old_bal = ref_bal.get(target, 0.0)
                        ref_bal[target] = 0.0
                        ref_paid_first.add(target)  # blocks future first-order bonus too
                        referrals.pop(target, None)
                        _ref_save()
                        send(ADMIN, f"🚫 Rewards revoked for user {target}: ₹{old_bal:,.2f} cleared, future referral rewards blocked.")
                        try:
                            send(target, "⚠️ Your referral rewards were revoked by the team (misuse or self-referrals). Contact the admin if you think this is a mistake.")
                        except Exception:
                            pass
                    elif chat in waiting_refw:
                        upi = text.strip()
                        if "@" not in upi or len(upi) < 6 or " " in upi:
                            send(chat, "That doesn't look like a UPI ID. It's usually name@bank, like rahul@okhdfc. Try again:")
                            continue
                        waiting_refw.discard(chat)
                        ref_withdraw[chat] = upi
                        _ref_save()
                        bal = ref_bal.get(chat, 0.0)
                        wkb = [[
                            {"text": "✅ Sent", "callback_data": f"refwpay:{chat}"},
                            {"text": "🚫 Cancel", "callback_data": f"refwcancel:{chat}"},
                        ]]
                        send(ADMIN, f"💸 REFERRAL WITHDRAWAL\n\n🧾 User: {name} (@{username}, id {chat})\nUPI: <code>{upi}</code>\nAmount: ₹{bal:,.2f}\n\nSend the rupees, then tap a button:", wkb, pm="HTML")
                        send(chat, "✅ Withdrawal requested! You'll get the rupees shortly.")
                    elif chat in waiting_upi:
                        upi = text.strip()
                        if "@" not in upi or len(upi) < 6 or " " in upi:
                            send(chat, "That doesn't look like a UPI ID. It's usually name@bank, like rahul@okhdfc. Try again:")
                            continue
                        amt = waiting_upi.pop(chat)
                        rate = chat_rate.get(chat, 100)
                        active_orders[chat] = (amt, rate)
                        akb = [[
                            {"text": "✅ Payment done", "callback_data": f"adm_done:{chat}"},
                            {"text": "🚫 Order cancelled", "callback_data": f"adm_cancel:{chat}"},
                        ]]
                        send(chat, f"✅ UPI ID saved: {upi}\n\nYour ₹{amt * rate:,.0f} is on the way. Our team will send it shortly.", KB)
                        send(ADMIN, f"💳 UPI FOR PAYOUT ({'CDM' if rate == 99 else 'USDT'})\n\n🧾 Order ID: {order_ids.get(chat, '?')}\n\n{name} (@{username}, id {chat})\nConfirmed ${amt:g} order\nUPI: <code>{upi}</code>\nPayout: ₹{amt * rate:,.0f}\n\nSend the rupees now, then tap a button to tell them:", akb, pm="HTML")
                    elif chat in waiting_amount:
                        try:
                            amt = float(text.replace("$", "").replace(",", ""))
                        except ValueError:
                            send(chat, "That's not a number. Enter an amount in USD, like 10")
                            continue
                        rate = chat_rate.get(chat, 100)
                        min_usd = 50.50 if rate == 99 else MIN_USD
                        if amt < min_usd:
                            if rate == 99:
                                send(chat, "Minimum for CDM is $50.50 (₹5,000). Enter a bigger amount:")
                            else:
                                send(chat, f"Minimum is ${MIN_USD:g}. Enter a bigger amount:")
                            continue
                        waiting_amount.discard(chat)
                        inr = amt * rate
                        pending_approval[chat] = (amt, rate)
                        order_ids[chat] = new_oid()
                        oid = order_ids[chat]
                        mode = "CDM" if rate == 99 else "USDT"
                        send(chat, f"✅ Got it: ${amt:g} = ₹{inr:,.0f} at {rate:g}rs/$\n\n🧾 Order ID: {oid}\n\n⏳ Your order is pending approval from the team. You'll get a message here as soon as it's accepted or rejected.", KB)
                        send(ADMIN, f"🔔 NEW SELL ORDER ({mode}) — APPROVAL NEEDED\n\n🧾 Order ID: {oid}\n\n{name} (@{username}, id {chat})\nAmount: ${amt:g}\nRate: {rate:g}rs/$\nPayout: ₹{inr:,.0f}\n\nAccept or reject their order:", [[
                            {"text": "✅ Accept order", "callback_data": f"apr:{chat}"},
                            {"text": "❌ Reject order", "callback_data": f"rej:{chat}"},
                        ]])
                    elif chat in ADMINS and text.startswith("/check"):
                        w = watch_payments.get(chat)
                        if not w:
                            # check against any active watch
                            hits = {c: v for c, v in watch_payments.items()}
                            if hits:
                                out = "\n".join(f"chat {c}: watching ${v['amt']:g} since accept" for c, v in hits.items())
                            else:
                                out = "no active watches right now"
                            send(chat, f"🔍 {out}", KB)
                        else:
                            send(chat, "🔍 checking on-chain now...", None)
                            hit = check_incoming(w["amt"], since_ts=w.get("started"))
                            if hit:
                                send(chat, f"✅ found: {hit['amount']:g} on {hit['net'].upper()} tx {hit['tx'][:20]}...", KB)
                            else:
                                send(chat, f"🔍 nothing found matching ${w['amt']:g} after accept time. Watching ${w['amt']:g}, started {int((time.time()-w['started'])//60)} min ago. Transfers sent BEFORE accept don't count.", KB)
                    elif text.startswith("/help"):
                        send(chat, "Tap 💵 Sell to start a trade. For anything else message the admin.", KB)
                    else:
                        send(chat, WELCOME, KB)
                elif cb:
                    chat = cb["message"]["chat"]["id"]
                    name = cb["message"]["chat"].get("first_name", "")
                    username = cb["message"]["chat"].get("username", "no username")
                    data = cb["data"]
                    actor = cb.get("from", {})
                    actor_id = actor.get("id", "?")
                    actor_name = actor.get("first_name", "")
                    actor_un = actor.get("username", "no username")
                    if data == "sell":
                        waiting_amount.add(chat)
                        chat_rate[chat] = 100
                        send(chat, "💵 Great! You'll get 100rs per $1.\n\nHow much USD do you want to sell? (minimum $0.1)")
                    elif data == "sellcdm":
                        waiting_amount.add(chat)
                        chat_rate[chat] = 99
                        send(chat, "🏦 CDM deposit! You'll get 99rs per $1.\n\nNote: only SBI accounts available.\n\nHow much USD do you want to sell? (minimum $50.50 / ₹5,000)")
                    elif data == "confirm":
                        amt = user_pending.pop(chat, None)
                        if amt:
                            rate = chat_rate.get(chat, 100)
                            watch_payments[chat] = {"amt": amt, "started": int(time.time()), "notified": False}
                            if rate == 99:
                                waiting_acct[chat] = amt
                                send(chat, "⏳ Merchant is checking your payment. Now enter your bank account number to get paid:")
                            else:
                                waiting_upi_choice[chat] = amt
                                ck = [[
                                    {"text": "🔤 UPI ID", "callback_data": "payupi"},
                                    {"text": "📷 QR", "callback_data": "payqr"},
                                ]]
                                send(chat, "⏳ Merchant is checking your payment. How do you want to receive the rupees?", ck)
                        else:
                            send(chat, "No active order. Tap 💵 Sell to start one.", KB)
                    elif data == "cancel":
                        amt = user_pending.pop(chat, None)
                        if not amt:
                            pa = pending_approval.pop(chat, None)
                            if pa:
                                amt = pa[0]
                                send(ADMIN, f"ℹ️ User {chat} cancelled their ${amt:g} order while awaiting approval.")
                        active_orders.pop(chat, None)
                        watch_payments.pop(chat, None)
                        if amt:
                            send(chat, "❌ Order cancelled. No payment was expected. Tap 💵 Sell anytime to start a new one.", KB)
                            send(ADMIN, f"🚫 {name} (@{username}, id {chat}) CANCELLED order {order_ids.get(chat, '?')} (${amt:g}).")
                        else:
                            send(chat, "No active order. Tap 💵 Sell to start one.", KB)
                    elif data.startswith("adm_"):
                        if chat not in ADMINS:
                            call("answerCallbackQuery", callback_query_id=cb["id"])
                            continue
                        target = int(data.split(":")[1])
                        if data.startswith("adm_done:"):
                            entry = active_orders.pop(target, None)
                            watch_payments.pop(target, None)
                            if entry:
                                amt, rate = entry
                                how = "transferred to your account via CDM" if rate == 99 else ("sent to your UPI QR" if pay_method.get(target) == "qr" else "sent to your UPI ID")
                                pay_method.pop(target, None)
                                pkb = [[
                                    {"text": "📸 Yes, send proof", "callback_data": "proofyes"},
                                    {"text": "No, thanks", "callback_data": "proofno"},
                                ]]
                                send(target, f"🎉 Done! Order {order_ids.get(target, '')}: ₹{amt * rate:,.0f} has been {how}. Thanks for trading with us!\n\nWant a screenshot proof of the payment?", pkb)
                                send(ADMIN, f"✅ Marked as paid by {actor_name} (@{actor_un}, id {actor_id}): order {order_ids.get(target, '?')}, ₹{amt * rate:,.0f} to user {target}. They've been notified.")
                                ref_credit(target, amt * rate)
                            else:
                                send(ADMIN, "No active order for that user.")
                        else:
                            entry = active_orders.pop(target, None)
                            watch_payments.pop(target, None)
                            if entry:
                                amt, rate = entry
                                send(target, "❌ Your order was cancelled by the team. If you have questions, message the admin.", KB)
                                send(ADMIN, f"🚫 Marked cancelled by {actor_name} (@{actor_un}, id {actor_id}): order {order_ids.get(target, '?')} (${amt:g}) from user {target}. They've been notified.")
                            else:
                                send(ADMIN, "No active order for that user.")
                    elif data.startswith("webr:") or data.startswith("webd:") or data.startswith("webc:"):
                        if chat not in ADMINS:
                            call("answerCallbackQuery", callback_query_id=cb["id"])
                            continue
                        oid = data.split(":")[1]
                        o = web_orders.get(oid)
                        if not o:
                            send(ADMIN, "No such web order.")
                        elif data.startswith("webr:"):
                            o["status"] = "payout"
                            _web_save()
                            send(ADMIN, f"✅ Web order #{oid} marked USDT received. Pay ₹{o['inr']:,} to the customer, then tap 💸 Paid.")
                        elif data.startswith("webd:"):
                            o["status"] = "done"
                            _web_save()
                            send(ADMIN, f"💸 Web order #{oid} marked PAID (₹{o['inr']:,}). Customer's page now shows done.")
                        else:
                            o["status"] = "cancelled"
                            _web_save()
                            send(ADMIN, f"🚫 Web order #{oid} cancelled.")
                    elif data.startswith("apr:") or data.startswith("rej:"):
                        if chat not in ADMINS:
                            call("answerCallbackQuery", callback_query_id=cb["id"])
                            continue
                        target = int(data.split(":")[1])
                        entry = pending_approval.pop(target, None)
                        if not entry:
                            send(ADMIN, "No pending order for that user (maybe already handled or they cancelled).")
                        elif data.startswith("apr:"):
                            amt, rate = entry
                            inr = amt * rate
                            watch_payments[target] = {"amt": amt, "started": int(time.time()), "notified": False}
                            kb2 = [[
                                {"text": "✅ Confirm payment", "callback_data": "confirm"},
                                {"text": "❌ Cancel order", "callback_data": "cancel"},
                            ]]
                            user_pending[target] = amt
                            send(target, f"🎉 Order ACCEPTED! 🧾 {order_ids.get(target, '')}\n\n${amt:g} = ₹{inr:,.0f} at {rate:g}rs/$\n\nNow send ${amt:g} (USDT/USDC) to this wallet (network: BEP20, ARB, POL or BASE):\n`{WALLET}`", kb2)
                            send(ADMIN, f"✅ {actor_name} (@{actor_un}, id {actor_id}) accepted order {order_ids.get(target, '?')} (${amt:g}) from user {target}. Wallet sent to them.")
                        else:
                            amt, rate = entry
                            send(target, "❌ Your order was rejected by the team. If you think this is a mistake, message the admin.", KB)
                            send(ADMIN, f"🚫 {actor_name} (@{actor_un}, id {actor_id}) rejected order {order_ids.get(target, '?')} (${amt:g}) from user {target}. They've been notified.")
                    elif data == "proofyes":
                        send(chat, "👍 Sure! Our team will send your screenshot proof here shortly.", KB)
                        sk = [[
                            {"text": f"📸 Send proof to {name or 'user'}", "callback_data": f"proofsend:{chat}"},
                        ]]
                        send(ADMIN, f"📸 User {name} (@{username}, id {chat}) requested a screenshot proof.\n\nTap the button, then send the photo — it goes straight to them.", sk)
                    elif data == "proofno":
                        send(chat, "No problem! Tap 💵 Sell anytime to trade again.", KB)
                    elif data.startswith("proofsend:"):
                        if chat not in ADMINS:
                            call("answerCallbackQuery", callback_query_id=cb["id"])
                            continue
                        proof_reply[chat] = int(data.split(":")[1])
                        send(chat, "Okay, send the proof photo now — I'll forward it to the user.", None)
                    elif data in ("payupi", "payqr"):
                        amt = waiting_upi_choice.pop(chat, None)
                        if amt is None:
                            send(chat, "No pending order. Tap 💵 Sell to start one.", KB)
                        elif data == "payupi":
                            waiting_upi[chat] = amt
                            send(chat, "⏳ Type your UPI ID to get paid (like name@okhdfc):", KB)
                        else:
                            waiting_upi[chat] = amt
                            send(chat, "⏳ Send your UPI QR as a photo:", KB)
                    elif data == "merchant":
                        send(chat, "🏪 Become a merchant or 💰 buy USDT?\n\nBoth are handled personally by our team. Great rates, steady volume and priority support.\n\nTap below to contact the admin directly.", [[
                            {"text": "👤 Contact admin", "url": "https://t.me/Official_p2pX"},
                        ]])
                    elif data == "ref":
                        try:
                            me = call("getMe")["result"]["username"]
                        except Exception:
                            me = "Official_P2pXbot"
                        link = f"https://t.me/{me}?start=REF{chat}"
                        bal = ref_bal.get(chat, 0.0)
                        n = sum(1 for v in referrals.values() if v == chat)
                        rkb = [[
                            {"text": f"💸 Withdraw ₹{bal:,.2f}", "callback_data": "refw"},
                        ]] if bal > 0 else []
                        send(chat, f"🎁 REFER AND EARN\n\nShare your link, and when a friend joins and completes their first order you get:\n• ₹10 bonus\n• 0.30% of every order they make, forever\n\n👥 Referrals: {n}\n💰 Balance: ₹{bal:,.2f}\n\nYour link:\n{link}\n\nTap the link, copy and share it.\n\n⚠️ The team can revoke rewards if misuse or self-referrals are found.", rkb)
                    elif data == "refw":
                        if ref_bal.get(chat, 0.0) <= 0:
                            call("answerCallbackQuery", callback_query_id=cb["id"], text="Nothing to withdraw yet", show_alert=False)
                            continue
                        if chat in ref_withdraw:
                            send(chat, "⏳ You already have a withdrawal request pending. Wait for the team to send it.")
                            continue
                        waiting_refw.add(chat)
                        send(chat, "💸 Type the UPI ID where you want your ₹" + f"{ref_bal.get(chat, 0):,.2f}" + " sent (like name@okhdfc):")
                    elif data.startswith("refwpay:") or data.startswith("refwcancel:"):
                        if chat not in ADMINS:
                            continue
                        target = int(data.split(":")[1])
                        upi = ref_withdraw.get(target)
                        bal = ref_bal.get(target, 0.0)
                        if data.startswith("refwcancel:"):
                            ref_withdraw.pop(target, None)
                            _ref_save()
                            send(ADMIN, "🚫 Withdrawal request cancelled.")
                            try:
                                send(target, "❌ Your withdrawal request was cancelled. Contact the admin if this is a mistake.")
                            except Exception:
                                pass
                        elif upi:
                            ref_withdraw.pop(target, None)
                            ref_bal[target] = 0.0
                            _ref_save()
                            send(ADMIN, f"✅ Marked withdrawal paid: ₹{bal:,.2f} to UPI {upi} (user {target}). Balance reset to ₹0.")
                            try:
                                send(target, f"💸 Your ₹{bal:,.2f} has been sent to {upi}! Keep referring 🎁", KB)
                            except Exception:
                                pass
                        else:
                            send(ADMIN, "No pending withdrawal for that user.")
                    elif data == "help":
                        hkb = [[
                            {"text": "📖 FAQ", "callback_data": "faq"},
                            {"text": "💸 Order confirmed, payment not received", "callback_data": "noreceive"},
                        ],[
                            {"text": "🛟 Contact customer support", "url": "https://t.me/P2p_dotX/5"},
                        ]]
                        send(chat, "❓ Help — pick a topic:", hkb)
                    elif data == "faq":
                        send(chat, "📖 FAQ\n\nQ: What's the rate?\nA: We buy USD at 100rs per $1.\n\nQ: What's the minimum?\nA: $0.1.\n\nQ: How do I sell?\nA: Tap 💵 Sell, enter the amount, send USDT to the wallet shown, then tap ✅ Confirm payment and enter your UPI ID.\n\nQ: When do I get my rupees?\nA: After we confirm your USDT on-chain, usually within minutes.\n\nQ: Is my data shared with the government?\nA: No. This is a fully decentralised platform.\n\nQ: Can I ask for screenshot proofs?\nA: Yes, definitely — just ask the admin.\n\nQ: Is my money safe? Any chance of account freeze or lien?\nA: Our merchants are third parties and we have no relation with them, so we can't guarantee anything. Please do your own research.\n\nQ: What if my account gets frozen or liened?\nA: For safety, always use a secondary account when doing P2P with us to protect your main account.\n\nQ: How long does a CDM take?\nA: 30-60 minutes.", KB)
                    elif data == "noreceive":
                        send(chat, "💸 Stuck payment? Sorry about that.\n\nIf you confirmed your payment but haven't received your rupees:\n\n1. Check your USDT transaction is complete on-chain\n2. Make sure your UPI ID was correct\n3. Most payouts land within minutes\n\nIf it's been over 30 minutes, contact support below with your transaction hash.", [[
                            {"text": "🛟 Contact customer support", "url": "https://t.me/P2p_dotX/5"},
                        ]])
                    call("answerCallbackQuery", callback_query_id=cb["id"])
        except Exception as e:
            print("err:", e, flush=True)
            time.sleep(3)

import threading
threading.Thread(target=bot_loop, daemon=True).start()

HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever()
