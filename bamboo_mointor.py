# monitor.py
from pathlib import Path
from dotenv import load_dotenv
from twilio.rest import Client
from requests.auth import HTTPBasicAuth
from datetime import datetime
import requests, os, time

# Load the .env that sits next to this script and override OS envs
load_dotenv(dotenv_path=Path(__file__).with_name('.env'), override=True)

# ==== Bamboo target ====
BAMBOO_BASE_URL = (os.getenv("BAMBOO_BASE_URL") or "").rstrip("/")
PROJECT_KEY = os.getenv("PROJECT_KEY", "")

# ==== Auth for Bamboo (optional) ====
BASIC_AUTH_USER = os.getenv("BASIC_AUTH_USER") or ""
BASIC_AUTH_PASS = os.getenv("BASIC_AUTH_PASS") or ""
BEARER_TOKEN = os.getenv("BEARER_TOKEN") or ""

# ==== Timing & stability ====
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "900"))  # default: 15 min
TIMEOUT = int(os.getenv("TIMEOUT_SECONDS", "10"))
CONSECUTIVE_UPS_REQUIRED = int(os.getenv("CONSECUTIVE_UPS_REQUIRED", "2"))
CONSECUTIVE_DOWNS_REQUIRED = int(os.getenv("CONSECUTIVE_DOWNS_REQUIRED", "1"))  # debounce DOWN if desired

# ==== Alert (WhatsApp or SMS via Twilio) ====
ALERT_CHANNEL = (os.getenv("ALERT_CHANNEL", "whatsapp")).lower()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM")  # e.g., whatsapp:+14155238886
TWILIO_TO = os.getenv("TWILIO_TO")      # e.g., whatsapp:+919177615696

# ---- Sanity checks ----
if not (BAMBOO_BASE_URL and PROJECT_KEY):
    raise SystemExit("Please set BAMBOO_BASE_URL and PROJECT_KEY in .env")

if ALERT_CHANNEL == "whatsapp":
    if not (str(TWILIO_FROM).startswith("whatsapp:") and str(TWILIO_TO).startswith("whatsapp:")):
        raise SystemExit("For WhatsApp, TWILIO_FROM and TWILIO_TO must start with 'whatsapp:'.")

# Build the target endpoint (IMPORTANT: use '&' not '&amp;')
TARGET_URL = (
    f"{BAMBOO_BASE_URL}/rest/api/latest/project/{PROJECT_KEY}"
    f"?expand=plans&max-result=500"
)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_auth_and_headers():
    headers = {"User-Agent": "bamboo-updown-monitor/1.0"}
    auth = None
    if BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {BEARER_TOKEN}"
    elif BASIC_AUTH_USER and BASIC_AUTH_PASS:
        auth = HTTPBasicAuth(BASIC_AUTH_USER, BASIC_AUTH_PASS)
    return auth, headers

def bamboo_is_up() -> bool:
    """Returns True if Bamboo REST responds with HTTP 2xx/3xx."""
    auth, headers = get_auth_and_headers()
    try:
        r = requests.get(TARGET_URL, headers=headers, auth=auth, timeout=TIMEOUT, allow_redirects=True)
        return 200 <= r.status_code < 400
    except requests.RequestException:
        return False

def send_alert(message: str):
    """Send alert via Twilio (WhatsApp or SMS) and log basic delivery status."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM and TWILIO_TO):
        print(f"[{now()}] (WARN) Missing Twilio variables; alert not sent: {message}")
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(body=message, from_=TWILIO_FROM, to=TWILIO_TO)
        print(f"[{now()}] Alert sent via {ALERT_CHANNEL}. SID={msg.sid}")

        # Optional: short status check (best-effort)
        # Comment this block if you don't need delivery diagnostics
        import time as _t
        for _ in range(6):  # ~60 seconds
            _t.sleep(10)
            m = client.messages(msg.sid).fetch()
            print(f"[{now()}] Delivery status: {m.status}  error_code={m.error_code}  error_message={m.error_message}")
            if m.status in ("delivered", "undelivered", "failed"):
                break
    except Exception as e:
        print(f"[{now()}] (ERROR) Failed to send alert via Twilio: {e}")

def main():
    print(f"[{now()}] Monitoring Bamboo endpoint:")
    print(f"  {TARGET_URL}")
    print(f"  Check interval: {CHECK_INTERVAL}s (≈ {CHECK_INTERVAL/60:.1f} min)")
    print(f"  Consecutive UPs required: {CONSECUTIVE_UPS_REQUIRED}")
    print(f"  Consecutive DOWNs required: {CONSECUTIVE_DOWNS_REQUIRED}\n")

    state = "UNKNOWN"   # UNKNOWN → DOWN → UP
    sent_down = False
    consec_up = 0
    consec_down = 0

    while True:
        is_up = bamboo_is_up()

        if is_up:
            consec_up += 1
            consec_down = 0
            if consec_up >= CONSECUTIVE_UPS_REQUIRED:
                if state != "UP":
                    state = "UP"
                    sent_down = False
                    consec_up = 0
                    msg = f"✅ Bamboo is UP\nURL: {TARGET_URL}\nTime: {now()}"
                    print(f"[{now()}] {msg}")
                    send_alert(msg)
                else:
                    print(f"[{now()}] Still UP (stable).")
            else:
                print(f"[{now()}] Looks UP ({consec_up}/{CONSECUTIVE_UPS_REQUIRED})… confirming…")
        else:
            consec_up = 0
            consec_down += 1
            if consec_down >= CONSECUTIVE_DOWNS_REQUIRED:
                if state != "DOWN":
                    state = "DOWN"
                    print(f"[{now()}] Transitioned to DOWN.")
                if not sent_down:
                    msg = f"❌ Bamboo appears DOWN\nURL: {TARGET_URL}\nTime: {now()}"
                    print(f"[{now()}] {msg}")
                    send_alert(msg)
                    sent_down = True
                else:
                    print(f"[{now()}] Still DOWN… next check in {CHECK_INTERVAL}s")
            else:
                print(f"[{now()}] Looks DOWN ({consec_down}/{CONSECUTIVE_DOWNS_REQUIRED})… confirming…")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()