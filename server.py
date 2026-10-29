import json, urllib.request, os
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMINS = [1817225234, 5538515530, 2025705698]
ADMIN = ADMINS[0]
SITE_RATE = {"upi": 100, "cdm": 99}

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
        else:
            self._send(404, "not found")
    def do_POST(self):
        if self.path == "/api/order":
            try:
                o = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                mode = o.get("mode")
                amt = float(o.get("amount", 0))
                inr = int(round(amt * SITE_RATE.get(mode, 100)))
                tag = "UPI" if mode == "upi" else "CDM"
                detail = f"UPI: {o.get('upi')}" if mode == "upi" else f"Account: {o.get('account')}\nPhone: {o.get('phone')}"
                ok = notify(f"🌐 WEBSITE ORDER ({tag})\n\nAmount: ${amt:g}\nPayout: ₹{inr:,}\n{detail}\n\nCollect the USDT, then message the customer on Telegram to complete.")
                self._send(200, json.dumps({"ok": ok}))
            except Exception as e:
                print("order err:", e, flush=True)
                self._send(400, json.dumps({"ok": False}))
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
                        caption = (f"📸 UPI QR FROM USER ({'CDM' if rate == 99 else 'USDT'})\n\n{name} (@{username}, id {chat})\n"
                                   f"Confirmed ${amt:g} order\nPayout: ₹{amt * rate:,.0f}\n\n"
                                   f"Scan the QR to pay, then tap a button to tell them:")
                        akb = [[
                            {"text": "✅ Payment done", "callback_data": f"adm_done:{chat}"},
                            {"text": "🚫 Order cancelled", "callback_data": f"adm_cancel:{chat}"},
                        ]]
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
                            else:
                                send(ADMIN, "No active order for that user.")
                        else:
                            entry = active_orders.pop(target, None)
                            if entry:
                                amt, rate = entry
                                send(target, "❌ Your order was cancelled by the team. If you have questions, message the admin.", KB)
                                send(ADMIN, f"🚫 Marked cancelled by {actor_name} (@{actor_un}, id {actor_id}): order {order_ids.get(target, '?')} (${amt:g}) from user {target}. They've been notified.")
                            else:
                                send(ADMIN, "No active order for that user.")
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
