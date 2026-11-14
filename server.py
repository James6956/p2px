import json, urllib.request, os
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMINS = [1817225234, 5538515530, 2025705698, 2105831616]
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
    ok = True
    for cid in ADMINS:
        data = json.dumps({"chat_id": cid, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            print("notify failed:", e, flush=True)
            ok = False
    return ok

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
                min_usd = 100 if mode == "cdm" else 0.1
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
                    if mode not in SITE_RATE or amt < (100 if mode == "cdm" else 0.1):
                        raise ValueError("bad order rebuild")
                    inr = int(round(amt * SITE_RATE[mode]))
                    order = {"amt": amt, "inr": inr, "mode": mode, "upi": None,
                             "acct": d.get("account"), "phone": d.get("phone"),
                             "ref": (d.get("ref") or "")[:20], "status": "awaiting", "ts": int(time.time())}
                    if not oid:
                        oid = new_oid()
                    web_orders[oid] = order
                    _web_save()
                if not order:
                    self._send(200, json.dumps({"ok": False, "err": "Order expired after a server restart. Please start the order again."}))
                    return
                upi_uri = decode_upi_bytes(data)
                if not upi_uri:
                    self._send(200, json.dumps({"ok": False, "err": "Could not read that QR. Make sure the whole QR is in the photo, or type your UPI ID instead."}))
                    return
                from urllib.parse import urlparse as _up, parse_qs as _pq
                qp = _pq(_up(upi_uri).query)
                pay_upi = qp.get("pa", [""])[0]
                order["upi"] = pay_upi
                if order["status"] == "awaiting_qr":
                    order["status"] = "awaiting"
                _web_save()
                inr = order["inr"]
                tkb = [[
                    {"text": "✅ USDT received", "callback_data": f"webr:{oid}"},
                    {"text": "🚫 Cancel", "callback_data": f"webc:{oid}"},
                ]]
                try:
                    call("sendMessage", chat_id=ADMIN, text=(f"🌐 WEBSITE ORDER #{oid} — QR received\nUPI: <code>{pay_upi}</code>\nPayout: ₹{inr:,}\n\nPay the rupees using the uploaded QR, then tap ✅ when you see the USDT."), reply_markup={"inline_keyboard": tkb}, parse_mode="HTML")
                except Exception as e:
                    print("qr admin notify failed:", e, flush=True)
                self._send(200, json.dumps({"ok": True, "id": oid}))
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
REF_FLAT = 20.0        # rs per referred friend first completed order
REF_PCT = 0.003        # 0.30 pct of every referred order (inr)
referrals = {}         # user_id -> referrer_id
seen_users = set()
ref_paid_first = set()
ref_bal = {}           # referrer_id -> pending rupees
ref_withdraw = {}      # referrer_id -> upi id pending request
waiting_refw = set()
# Use local JSON storage by default, including on Render Free.
# Render Free storage is ephemeral, so referral data can be lost on restart/redeploy.
_REF_DEFAULT_DIR = os.path.dirname(os.path.abspath(__file__))
_P2PX_DATA_DIR = os.environ.get("P2PX_DATA_DIR", "").strip()
REF_DATA_DIR = os.path.abspath(_P2PX_DATA_DIR or _REF_DEFAULT_DIR)
os.makedirs(REF_DATA_DIR, exist_ok=True)
REF_FILE = os.path.join(REF_DATA_DIR, "referrals.json")
_LEGACY_REF_FILE = os.path.join(_REF_DEFAULT_DIR, "referrals.json")
def _ref_save():
    tmp_file = REF_FILE + ".tmp"
    try:
        data = {"referrals": {str(k): v for k, v in referrals.items()}, "seen": list(seen_users), "first": list(ref_paid_first),
                "bal": {str(k): v for k, v in ref_bal.items()},
                "wd": {str(k): v for k, v in ref_withdraw.items()}}
        with open(tmp_file, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, REF_FILE)
    except Exception as e:
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except OSError:
            pass
        print("ref save failed:", e, flush=True)
        raise


def _ref_load():
    global referrals, seen_users, ref_paid_first, ref_bal, ref_withdraw
    # Prefer the configured durable data path. If it is empty, migrate the
    # existing beside-code JSON file once so deployments retain current rewards.
    source_file = REF_FILE
    if not os.path.exists(source_file) and _LEGACY_REF_FILE != REF_FILE and os.path.exists(_LEGACY_REF_FILE):
        source_file = _LEGACY_REF_FILE
    try:
        with open(source_file) as f:
            d = json.load(f)
        referrals = {int(k): int(v) for k, v in d.get("referrals", {}).items()}
        seen_users = set(d.get("seen", []))
        ref_paid_first = set(d.get("first", []))
        ref_bal = {int(k): float(v) for k, v in d.get("bal", {}).items()}
        ref_withdraw = {int(k): v for k, v in d.get("wd", {}).items()}
        if source_file != REF_FILE:
            _ref_save()
    except FileNotFoundError:
        pass
    except Exception as e:
        print("ref load failed:", e, flush=True)
        raise RuntimeError(f"Could not load referral data from {source_file}; refusing to start with empty referral balances") from e

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

# ===== on-chain payment verification and incoming USDT notices =====
import threading as _th
WALLET_L = WALLET.lower()
ES_KEY = os.environ.get("ETHERSCAN_KEY", "").strip()
ES_V2 = "https://api.etherscan.io/v2/api"
CHAINS = {"bep20": 56, "arb": 42161, "pol": 137, "base": 8453}
# Routescan's Etherscan-compatible free API covers the configured chains without an API key.
ROUTESCAN = "https://api.routescan.io/v2/network/mainnet/evm/{chainid}/etherscan/api"
EXPLORERS = {"bep20": "https://bscscan.com/tx/", "arb": "https://arbiscan.io/tx/",
             "pol": "https://polygonscan.com/tx/", "base": "https://basescan.org/tx/"}
USDT = {
    "bep20": "0x55d398326f99059ff775485246999027b3197955",
    "arb":   "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
    "pol":   "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
    # This is the bridged USDT token the existing project checked on Base.
    "base":  "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",
}
USDC = {
    # Binance-Peg USD Coin on BNB Smart Chain, verified on BscScan (18 decimals).
    "bep20": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    # Circle-native USDC addresses, as documented by Circle (6 decimals).
    "arb":   "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
    "pol":   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
    "base":  "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
}
TOKEN_DECIMALS = {
    "USDT": {"bep20": 18, "arb": 6, "pol": 6, "base": 6},
    "USDC": {"bep20": 18, "arb": 6, "pol": 6, "base": 6},
}
VALID_TOKENS={}
for _n, _c in USDT.items():
    VALID_TOKENS.setdefault(_c.lower(), []).append(_n + " USDT")
for _n, _c in USDC.items():
    VALID_TOKENS.setdefault(_c.lower(), []).append(_n + " USDC")
watch_payments = {}     # chat -> {"amt": float, "started": unix ts, "notified": bool}
verified_payments = {}  # chat -> result dict

# Persistent high-water marks prevent rescanning and duplicate notices after restarts.
TX_SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notified_usdt_txs.json")
_tx_seen = set()
_tx_cursors = {}
_tx_delivered = {}
_tx_pending = {}
_tx_lock = _th.Lock()
def _tx_state_load():
    global _tx_seen, _tx_cursors, _tx_delivered, _tx_pending
    try:
        with open(TX_SEEN_FILE) as f:
            state = json.load(f)
        if isinstance(state, dict):
            _tx_seen = set(state.get("seen", []))
            _tx_cursors = {str(k): int(v) for k, v in state.get("cursors", {}).items()}
            _tx_delivered = {str(k): set(v) for k, v in state.get("delivered", {}).items()}
            _tx_pending = {str(k): v for k, v in state.get("pending", {}).items()}
        elif isinstance(state, list):  # migrate the initial list format
            _tx_seen = set(state)
    except FileNotFoundError:
        pass
    except Exception as e:
        print("tx state load failed:", e, flush=True)

def _tx_state_save():
    tmp = TX_SEEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"seen": sorted(_tx_seen), "cursors": _tx_cursors,
                  "delivered": {k: sorted(v) for k, v in _tx_delivered.items()},
                  "pending": _tx_pending}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, TX_SEEN_FILE)

_tx_state_load()

# Public JSON-RPC endpoints. These are read-only and need no explorer API key.
RPCS = {"bep20": ["https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org"],
        "arb": ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"],
        "pol": ["https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org"],
        "base": ["https://base-rpc.publicnode.com", "https://mainnet.base.org"]}
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

def _rpc(net, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for url in RPCS[net]:
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "p2px/1.0"})
            result = json.loads(urllib.request.urlopen(req, timeout=25).read())
            if "error" in result:
                raise RuntimeError(result["error"].get("message", "RPC error"))
            return result["result"]
        except Exception as e:
            last = e
    raise last

def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "p2px/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())

LOOKBACK = {"bep20": 10000, "arb": 10000, "pol": 10000, "base": 10000}

def _rpc_token_rows(net, contract):
    """Keyless fallback: recent incoming Transfer logs from public RPC, shaped like Etherscan tokentx rows."""
    latest = _latest_block(net)
    to_topic = "0x" + WALLET_L[2:].rjust(64, "0")
    start = max(0, latest - LOOKBACK[net])
    rows = []
    ts_cache = {}
    while start <= latest:
        end = min(start + SCAN_RANGE_BLOCKS, latest)
        logs = _rpc(net, "eth_getLogs", [{"fromBlock": hex(start), "toBlock": hex(end),
                     "address": contract, "topics": [TRANSFER_TOPIC, None, to_topic]}])
        for lg in logs:
            bn = lg.get("blockNumber", "0x0")
            if bn not in ts_cache:
                blk = _rpc(net, "eth_getBlockByNumber", [bn, False]) or {}
                ts_cache[bn] = int(blk.get("timestamp", "0x0"), 16)
            rows.append({"to": WALLET_L, "timeStamp": ts_cache[bn], "hash": lg.get("transactionHash", ""),
                         "value": int(lg.get("data", "0x0"), 16),
                         "tokenDecimal": TOKEN_DECIMALS["USDT" if contract.lower() == USDT[net].lower() else "USDC"][net]})
        start = end + 1
    return rows

def _token_rows(net, contract):
    """Order-checker lookup: Etherscan when the key works, otherwise public RPC logs (no key needed)."""
    global ES_KEY
    if ES_KEY:
        params = f"module=account&action=tokentx&contractaddress={contract}&address={WALLET_L}&page=1&offset=100&sort=desc"
        url = f"{ES_V2}?chainid={CHAINS[net]}&{params}&apikey={ES_KEY}"
        try:
            res = _get_json(url)
            if isinstance(res.get("result"), list):
                return res["result"]
            if "api key" in str(res.get("result", "")).lower():
                print("etherscan key rejected, using public RPC instead", flush=True)
                ES_KEY = ""
        except Exception as e:
            print("etherscan lookup failed:", net, e, flush=True)
    try:
        return _rpc_token_rows(net, contract)
    except Exception as e:
        print("rpc lookup failed:", net, e, flush=True)
        return []

def _latest_block(net):
    return int(_rpc(net, "eth_blockNumber", []), 16)

INITIAL_LOOKBACK_BLOCKS = 10000
SCAN_RANGE_BLOCKS = 1999


def scan_incoming_usdt():
    """Read incoming USDT and USDC Transfer logs with bounded scans and chain isolation."""
    hits = []
    latest_blocks = {}
    failures = {}
    to_topic = "0x" + WALLET_L[2:].rjust(64, "0")
    for net in CHAINS:
        try:
            latest = _latest_block(net)
            with _tx_lock:
                cursor = _tx_cursors.get(net)
            start = max(0, latest - INITIAL_LOOKBACK_BLOCKS + 1) if cursor is None else cursor + 1
            chain_hits = []
            while start <= latest:
                end = min(start + SCAN_RANGE_BLOCKS, latest)
                for asset, contracts in (("USDT", USDT), ("USDC", USDC)):
                    contract = contracts.get(net)
                    if not contract:
                        continue
                    logs = _rpc(net, "eth_getLogs", [{"fromBlock": hex(start), "toBlock": hex(end),
                                 "address": contract, "topics": [TRANSFER_TOPIC, None, to_topic]}])
                    for log in logs:
                        tx_hash = log.get("transactionHash", "")
                        topics = log.get("topics") or []
                        if not tx_hash or len(topics) < 3:
                            continue
                        try:
                            value = int(log.get("data", "0x0"), 16) / (10 ** TOKEN_DECIMALS[asset][net])
                            block_num = int(log.get("blockNumber", "0x0"), 16)
                            sender = "0x" + topics[1][-40:]
                            recipient = "0x" + topics[2][-40:]
                            if recipient.lower() != WALLET_L:
                                continue
                            block = _rpc(net, "eth_getBlockByNumber", [hex(block_num), False]) or {}
                            timestamp = int(block.get("timestamp", "0x0"), 16)
                            confirmations = max(0, latest - block_num + 1)
                        except (ValueError, TypeError):
                            continue
                        chain_hits.append({"net": net, "asset": asset, "amount": value, "tx": tx_hash,
                                           "from": sender, "to": recipient,
                                           "timestamp": timestamp, "confirmations": confirmations,
                                           "token": contract, "block": block_num,
                                           "log_index": log.get("logIndex", "0x0")})
                start = end + 1
            hits.extend(chain_hits)
            latest_blocks[net] = latest
        except Exception as e:
            failures[net] = e
            print("incoming token scan failed:", net, e, flush=True)
    return hits, latest_blocks, failures


def _notify_incoming_transfer(hit):
    from datetime import datetime, timezone
    timestamp = datetime.fromtimestamp(hit["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if hit["timestamp"] else "unknown"
    tx_url = EXPLORERS[hit["net"]] + hit["tx"]
    asset = hit.get("asset", "USDT")
    text = (f"⚠️ UNVERIFIED INCOMING {asset} TRANSFER\n\n"
            f"Chain: {hit['net'].upper()}\nAmount: {hit['amount']:g} {asset}\n"
            f"Sender: {hit['from']}\nRecipient: {hit['to']}\n"
            f"Timestamp: {timestamp}\nConfirmations: {hit['confirmations']}\n"
            f"Tx hash: {hit['tx']}\nExplorer: {tx_url}\n\n"
            "This transfer is not linked to or verified for any order. Please check manually.")
    delivery_key = hit["net"] + ":" + hit["tx"].lower() + ":" + str(hit["log_index"])
    with _tx_lock:
        _tx_delivered.setdefault(delivery_key, set())
        _tx_pending.setdefault(delivery_key, hit)
        try:
            _tx_state_save()
        except Exception as e:
            _tx_pending.pop(delivery_key, None)
            print("incoming transfer state save failed:", e, flush=True)
            return False

    all_delivered = True
    for cid in ADMINS:
        with _tx_lock:
            if str(cid) in _tx_delivered.get(delivery_key, set()):
                continue
        data = json.dumps({"chat_id": cid, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read())
            if not result.get("ok", False):
                raise RuntimeError("Telegram API did not confirm delivery")
            with _tx_lock:
                _tx_delivered.setdefault(delivery_key, set()).add(str(cid))
                try:
                    _tx_state_save()
                except Exception as e:
                    _tx_delivered[delivery_key].discard(str(cid))
                    print("incoming transfer delivery state save failed:", e, flush=True)
                    all_delivered = False
        except Exception as e:
            all_delivered = False
            print("incoming transfer notify failed:", e, flush=True)

    with _tx_lock:
        delivered = _tx_delivered.get(delivery_key, set())
        if all(str(cid) in delivered for cid in ADMINS):
            _tx_seen.add(delivery_key)
            _tx_pending.pop(delivery_key, None)
            try:
                _tx_state_save()
            except Exception as e:
                _tx_seen.discard(delivery_key)
                print("incoming transfer completion save failed:", e, flush=True)
                return False
            return True
    return False

def check_incoming(amt, since_ts=None):
    """Find incoming REAL USDT/USDC transfer to WALLET of >= amt."""
    for net in CHAINS:
        for contract in (USDT[net], USDC[net]):
            for tx in _token_rows(net, contract):
                if tx.get("to", "").lower() != WALLET_L:
                    continue
                if since_ts and int(tx.get("timeStamp") or 0) < since_ts:
                    continue
                try:
                    dec = int(tx.get("tokenDecimal") or (18 if net == "bep20" else 6))
                    val = int(tx.get("value") or 0) / (10 ** dec)
                except (ValueError, TypeError):
                    continue
                if val + 1e-6 >= amt:
                    return {"net": net, "amount": val, "tx": tx.get("hash", ""), "token": contract}
    return None

def payment_watcher():
    while True:
        try:
            hits, latest_blocks, scan_failures = scan_incoming_usdt()
            with _tx_lock:
                pending_hits = list(_tx_pending.values())
            current_keys = {hit["net"] + ":" + hit["tx"].lower() + ":" + str(hit["log_index"]) for hit in hits}
            for pending in pending_hits:
                pending_key = pending["net"] + ":" + pending["tx"].lower() + ":" + str(pending["log_index"])
                if pending_key not in current_keys:
                    hits.append(pending)
                    current_keys.add(pending_key)
            retry_cursors = set()
            for hit in hits:
                key = hit["net"] + ":" + hit["tx"].lower() + ":" + str(hit["log_index"])
                with _tx_lock:
                    if key in _tx_seen:
                        continue
                delivered = _notify_incoming_transfer(hit)
                if not delivered:
                    with _tx_lock:
                        if key not in _tx_pending:
                            retry_cursors.add(hit["net"])
            # Advance only chains whose full scan succeeded and whose alerts
            # are safely persisted as delivered or pending.
            with _tx_lock:
                for net, latest in latest_blocks.items():
                    if net not in retry_cursors:
                        _tx_cursors[net] = max(_tx_cursors.get(net, latest), latest)
                _tx_state_save()
            for net, error in scan_failures.items():
                print("watcher will retry chain next cycle:", net, error, flush=True)
            hour = time.localtime().tm_hour
            if hour < 8 or hour >= 23:
                time.sleep(20)
                continue
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
        time.sleep(20)

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
    try:
        for net in CHAINS:
            _latest_block(net)
        notify("✅ payment checker online, watching all 4 chains via public RPC (no explorer key needed)")
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
merchant_processing = set()  # customer chats whose order is with the merchant
upi_payout_collection = set()  # customer chats choosing/submitting UPI payout details
main_menu_messages = {}  # customer chat -> inline main-menu message ids

def call(method, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def order_flow_active(chat_id):
    return chat_id in merchant_processing or chat_id in upi_payout_collection


def main_menu_keyboard(chat_id):
    """Return the main inline menu only when this chat is outside active flows."""
    return None if order_flow_active(chat_id) else KB


def send_payout_choice(chat_id, amount, inr, order_id):
    """Start the customer's UPI ID/QR selection after a UPI order is accepted."""
    upi_payout_collection.add(chat_id)
    hide_main_menu(chat_id)
    waiting_upi_choice[chat_id] = amount
    keyboard = [[
        {"text": "🔤 UPI ID", "callback_data": "payupi"},
        {"text": "📷 QR", "callback_data": "payqr"},
    ]]
    send(chat_id, f"🎉 Order ACCEPTED! 🧾 {order_id}\n\n⏳ Merchant is sending your ₹{inr:,.0f}. How do you want to receive the rupees?", keyboard)


def send(chat_id, text, kb=None, pm="Markdown"):
    targets = ADMINS if chat_id in ADMINS else [chat_id]
    for cid in targets:
        params = {"chat_id": cid, "text": text, "parse_mode": pm}
        menu_kb = None
        if kb is KB:
            menu_kb = main_menu_keyboard(cid)
        elif kb:
            menu_kb = kb

        in_flow = order_flow_active(cid)
        if in_flow and text == WELCOME:
            # Suppress the menu text too, even if a handler bypasses
            # send_welcome() and passes the welcome payload directly.
            return

        if in_flow and menu_kb:
            # ReplyKeyboardRemove is a reply keyboard, and Telegram does not
            # let editMessageReplyMarkup convert that message into an inline
            # keyboard. Clear any legacy persistent keyboard on a separate
            # message, then send the requested flow buttons with the real text.
            try:
                call("sendMessage", chat_id=cid,
                     text="Your order is active. Use the buttons below to continue.",
                     reply_markup={"remove_keyboard": True})
            except Exception as e:
                print("stale reply keyboard removal failed:", e, flush=True)
            params["reply_markup"] = {"inline_keyboard": menu_kb}
        elif in_flow:
            # An older bot version may have left a persistent reply keyboard
            # containing "▶️ Start" on the user's client. Clear it during the flow.
            params["reply_markup"] = {"remove_keyboard": True}
        elif menu_kb:
            params["reply_markup"] = {"inline_keyboard": menu_kb}

        try:
            result = call("sendMessage", **params)
        except Exception:
            params.pop("parse_mode", None)
            result = call("sendMessage", **params)

        if kb is KB and cid not in ADMINS and not in_flow and menu_kb:
            message_id = (result.get("result") or {}).get("message_id")
            if message_id is not None:
                track_main_menu_message(cid, message_id)


def send_welcome(chat_id):
    """Do not interrupt an active order with the generic welcome/menu message."""
    if order_flow_active(chat_id):
        # Clear a persistent ReplyKeyboard left by an older bot version. A tap
        # on its old Start button falls through here as ordinary text.
        send(chat_id, "⏳ Your current order is still in progress. Use the buttons on your order message.")
        return False
    send(chat_id, WELCOME, KB)
    return True


def track_main_menu_message(chat_id, message_id):
    if message_id is not None:
        main_menu_messages.setdefault(chat_id, set()).add(message_id)


def hide_main_menu(chat_id):
    """Remove only this customer's tracked inline main-menu buttons."""
    for message_id in main_menu_messages.pop(chat_id, set()):
        try:
            call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                 reply_markup={"inline_keyboard": []})
        except Exception as e:
            print("main menu hide failed:", e, flush=True)

KB = [[
    {"text": "💵 Sell at 100rs/$ (UPI)", "callback_data": "sell"},
    {"text": "🏦 Sell at 99rs/$ (CDM)", "callback_data": "sellcdm"},
],[
    {"text": "🏪 Become merchant / 💰 Buy USDT", "callback_data": "merchant"},
],[
    {"text": "🎁 Refer and earn ₹20 + 0.30%", "callback_data": "ref"},
    {"text": "❓ Help", "callback_data": "help"},
]]

# The main menu is intentionally suppressed in in-progress payment/payout flows.

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
                    if text == "▶️ Start":
                        # This was the label of the old persistent reply-keyboard
                        # button, not a bot command. Consume it during either
                        # active flow so it cannot trigger the generic welcome.
                        if order_flow_active(chat):
                            send(chat, "⏳ Your current order is still in progress. Use the buttons on your order message.")
                        else:
                            send_welcome(chat)
                    elif text.startswith("/start"):
                        # Older bot versions exposed a persistent ReplyKeyboard
                        # Start button. Do not let that stale button interrupt
                        # or reset an order that is already in progress.
                        if order_flow_active(chat):
                            send(chat, "⏳ Your current order is still in progress. Please finish or cancel it first.")
                            continue
                        merchant_processing.discard(chat)
                        upi_payout_collection.discard(chat)
                        hide_main_menu(chat)
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
                                    send(ref, "👥 New referral joined with your link! You get ₹20 + 0.30% after they complete their first order.")
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
                        merchant_processing.discard(chat)
                        upi_payout_collection.discard(chat)
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
                        from html import escape as _html_escape
                        from urllib.parse import urlparse, parse_qs
                        akb = [[
                            {"text": "✅ Payment done", "callback_data": f"adm_done:{chat}"},
                            {"text": "🚫 Order cancelled", "callback_data": f"adm_cancel:{chat}"},
                        ]]
                        detail_lines = []
                        if upi_uri:
                            qp = parse_qs(urlparse(upi_uri).query)
                            pay_upi = qp.get("pa", [""])[0]
                            pay_amt = qp.get("am", [str(int(amt * rate))])[0]
                            payee = qp.get("pn", [""])[0]
                            currency = qp.get("cu", ["INR"])[0]
                            note = qp.get("tn", [""])[0]
                            detail_lines = [f"UPI ID: <code>{_html_escape(pay_upi)}</code>"]
                            if payee:
                                detail_lines.append(f"Payee: {_html_escape(payee)}")
                            detail_lines.append(f"QR amount: {_html_escape(pay_amt)} {_html_escape(currency)}")
                            if note:
                                detail_lines.append(f"Note: {_html_escape(note)}")
                        caption = (f"📸 UPI QR FROM USER ({'CDM' if rate == 99 else 'USDT'})\n\n{name} (@{username}, id {chat})\n"
                                   f"Confirmed ${amt:g} order\nPayout: ₹{amt * rate:,.0f}\n\n"
                                   + "\n".join(detail_lines)
                                   + "\n\nUse the UPI ID/details in your own payment app. Original QR image is attached.")
                        ok = True
                        for cid in ADMINS:
                            try:
                                call("sendPhoto", chat_id=cid, photo=file_id, caption=caption, parse_mode="HTML", reply_markup={"inline_keyboard": akb})
                            except Exception as e:
                                print("qr forward failed:", e, flush=True)
                                ok = False
                        if ok:
                            waiting_upi.pop(chat, None)
                            active_orders[chat] = (amt, rate)
                            pay_method[chat] = "qr"
                            upi_payout_collection.discard(chat)
                            merchant_processing.discard(chat)
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
                        upi_payout_collection.discard(chat)
                        merchant_processing.discard(chat)
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
                        min_usd = 100 if rate == 99 else MIN_USD
                        if amt < min_usd:
                            if rate == 99:
                                send(chat, "Minimum for CDM is $100. Enter a bigger amount:")
                            else:
                                send(chat, f"Minimum is ${MIN_USD:g}. Enter a bigger amount:")
                            continue
                        waiting_amount.discard(chat)
                        inr = amt * rate
                        order_ids[chat] = new_oid()
                        oid = order_ids[chat]
                        mode = "CDM" if rate == 99 else "USDT"
                        if rate == 99:
                            pending_approval[chat] = (amt, rate)
                            send(chat, f"✅ Got it: ${amt:g} = ₹{inr:,.0f} at {rate:g}rs/$\n\n🧾 Order ID: {oid}\n\n⏳ Your order is pending approval from the team. You'll get a message here as soon as it's accepted or rejected.", KB)
                            send(ADMIN, f"🔔 NEW SELL ORDER ({mode}) — APPROVAL NEEDED\n\n🧾 Order ID: {oid}\n\n{name} (@{username}, id {chat})\nAmount: ${amt:g}\nRate: {rate:g}rs/$\nPayout: ₹{inr:,.0f}\n\nAccept or reject their order:", [[
                                {"text": "✅ Accept order", "callback_data": f"apr:{chat}"},
                                {"text": "❌ Reject order", "callback_data": f"rej:{chat}"},
                            ]])
                        else:
                            user_pending[chat] = amt
                            kb2 = [[
                                {"text": "✅ Confirm payment", "callback_data": "confirm"},
                                {"text": "❌ Cancel order", "callback_data": "cancel"},
                            ]]
                            send(chat, f"✅ Got it: ${amt:g} = ₹{inr:,.0f} at {rate:g}rs/$\n\n🧾 Order ID: {oid}\n\nNow send ${amt:g} (USDT/USDC) to this wallet (network: BEP20, ARB, POL or BASE):\n`{WALLET}`", kb2)
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
                        send_welcome(chat)
                elif cb:
                    chat = cb["message"]["chat"]["id"]
                    name = cb["message"]["chat"].get("first_name", "")
                    username = cb["message"]["chat"].get("username", "no username")
                    data = cb["data"]
                    actor = cb.get("from", {})
                    actor_id = actor.get("id", "?")
                    actor_name = actor.get("first_name", "")
                    actor_un = actor.get("username", "no username")
                    if data == "start":
                        # Ignore a stale legacy Start button during an active flow.
                        # The /start command remains the explicit way to reset it.
                        if order_flow_active(chat):
                            call("answerCallbackQuery", callback_query_id=cb["id"],
                                 text="Finish or cancel your current order first")
                            continue
                        merchant_processing.discard(chat)
                        upi_payout_collection.discard(chat)
                        hide_main_menu(chat)
                        waiting_amount.discard(chat)
                        waiting_upi.pop(chat, None)
                        waiting_upi_choice.pop(chat, None)
                        waiting_acct.pop(chat, None)
                        waiting_phone.pop(chat, None)
                        send(chat, WELCOME, KB)
                    elif data == "sell":
                        track_main_menu_message(chat, cb["message"].get("message_id"))
                        waiting_amount.add(chat)
                        chat_rate[chat] = 100
                        send(chat, "💵 Great! You'll get 100rs per $1.\n\nHow much USD do you want to sell? (minimum $0.1)")
                    elif data == "sellcdm":
                        track_main_menu_message(chat, cb["message"].get("message_id"))
                        waiting_amount.add(chat)
                        chat_rate[chat] = 99
                        send(chat, "🏦 CDM deposit! You'll get 99rs per $1.\n\nNote: only SBI accounts available.\n\nHow much USD do you want to sell? (minimum $100)")
                    elif data == "confirm":
                        amt = user_pending.pop(chat, None)
                        if amt:
                            rate = chat_rate.get(chat, 100)
                            watch_payments[chat] = {"amt": amt, "started": int(time.time()), "notified": False}
                            merchant_processing.add(chat)
                            hide_main_menu(chat)
                            if rate == 99:
                                waiting_acct[chat] = amt
                                send(chat, "⏳ Merchant is checking your payment. Now enter your bank account number to get paid:")
                            else:
                                pending_approval[chat] = (amt, rate)
                                send(chat, "⏳ Merchant is checking your payment. You'll get a message here as soon as it's accepted.", KB)
                                send(ADMIN, f"🔔 SELL ORDER (USDT) — APPROVAL NEEDED\n\n🧾 Order ID: {order_ids.get(chat, '?')}\n\n{name} (@{username}, id {chat}) paid ${amt:g}\nPayout: ₹{amt * rate:,.0f}\n\nAccept or reject their order:", [[
                                    {"text": "✅ Accept order", "callback_data": f"apr:{chat}"},
                                    {"text": "❌ Reject order", "callback_data": f"rej:{chat}"},
                                ]])
                        else:
                            send(chat, "No active order. Tap 💵 Sell to start one.", KB)
                    elif data == "cancel":
                        was_payout_flow = (chat in upi_payout_collection or chat in waiting_upi_choice or chat in waiting_upi)
                        amt = user_pending.pop(chat, None)
                        if not amt:
                            pa = pending_approval.pop(chat, None)
                            if pa:
                                amt = pa[0]
                                send(ADMIN, f"ℹ️ User {chat} cancelled their ${amt:g} order while awaiting approval.")
                        active_orders.pop(chat, None)
                        watch_payments.pop(chat, None)
                        merchant_processing.discard(chat)
                        upi_payout_collection.discard(chat)
                        waiting_upi_choice.pop(chat, None)
                        waiting_upi.pop(chat, None)
                        hide_main_menu(chat)
                        if amt or was_payout_flow:
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
                            merchant_processing.discard(target)
                            upi_payout_collection.discard(target)
                            waiting_upi_choice.pop(target, None)
                            waiting_upi.pop(target, None)
                            if entry:
                                amt, rate = entry
                                how = "transferred to your account via CDM" if rate == 99 else ("sent to your UPI QR" if pay_method.get(target) == "qr" else "sent to your UPI ID")
                                pay_method.pop(target, None)
                                pkb = [[
                                    {"text": "📸 Yes, send proof", "callback_data": "proofyes"},
                                    {"text": "No, thanks", "callback_data": "proofno"},
                                ]]
                                send(target, f"🎉 Done! Order {order_ids.get(target, '')}: ₹{amt * rate:,.0f} has been {how}. Thanks for trading with us!\n\nWant a screenshot proof of the payment?", pkb)
                                send(target, "You can start another order anytime:", KB)
                                send(ADMIN, f"✅ Marked as paid by {actor_name} (@{actor_un}, id {actor_id}): order {order_ids.get(target, '?')}, ₹{amt * rate:,.0f} to user {target}. They've been notified.")
                                ref_credit(target, amt * rate)
                            else:
                                send(ADMIN, "No active order for that user.")
                        else:
                            entry = active_orders.pop(target, None)
                            watch_payments.pop(target, None)
                            merchant_processing.discard(target)
                            upi_payout_collection.discard(target)
                            was_payout_flow = (target in waiting_upi_choice or target in waiting_upi)
                            waiting_upi_choice.pop(target, None)
                            waiting_upi.pop(target, None)
                            if entry:
                                amt, rate = entry
                                send(target, "❌ Your order was cancelled by the team. If you have questions, message the admin.", KB)
                                send(ADMIN, f"🚫 Marked cancelled by {actor_name} (@{actor_un}, id {actor_id}): order {order_ids.get(target, '?')} (${amt:g}) from user {target}. They've been notified.")
                            elif was_payout_flow:
                                send(target, "❌ Your order was cancelled by the team. If you have questions, message the admin.", KB)
                                send(ADMIN, f"🚫 Marked cancelled during payout setup by {actor_name} (@{actor_un}, id {actor_id}) for user {target}.")
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
                            merchant_processing.discard(target)
                            if rate == 99:
                                kb2 = [[
                                    {"text": "✅ Confirm payment", "callback_data": "confirm"},
                                    {"text": "❌ Cancel order", "callback_data": "cancel"},
                                ]]
                                user_pending[target] = amt
                                send(target, f"🎉 Order ACCEPTED! 🧾 {order_ids.get(target, '')}\n\n${amt:g} = ₹{inr:,.0f} at {rate:g}rs/$\n\nNow send ${amt:g} (USDT/USDC) to this wallet (network: BEP20, ARB, POL or BASE):\n`{WALLET}`", kb2)
                                send(ADMIN, f"✅ {actor_name} (@{actor_un}, id {actor_id}) accepted order {order_ids.get(target, '?')} (${amt:g}) from user {target}. Wallet sent to them.")
                            else:
                                send_payout_choice(target, amt, inr, order_ids.get(target, ''))
                                send(ADMIN, f"✅ {actor_name} (@{actor_un}, id {actor_id}) accepted order {order_ids.get(target, '?')} (${amt:g}) from user {target}. Payout method being collected.")
                        else:
                            amt, rate = entry
                            merchant_processing.discard(target)
                            upi_payout_collection.discard(target)
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
                            upi_payout_collection.add(chat)
                            hide_main_menu(chat)
                            waiting_upi[chat] = amt
                            send(chat, "⏳ Type your UPI ID to get paid (like name@okhdfc):", KB)
                        else:
                            upi_payout_collection.add(chat)
                            hide_main_menu(chat)
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
                        send(chat, f"🎁 REFER AND EARN\n\nShare your link, and when a friend joins and completes their first order you get:\n• ₹20 bonus\n• 0.30% of every order they make, forever\n\n👥 Referrals: {n}\n💰 Balance: ₹{bal:,.2f}\n\nYour link:\n{link}\n\nTap the link, copy and share it.\n\n⚠️ The team can revoke rewards if misuse or self-referrals are found.", rkb)
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
