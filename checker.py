import os
import time
import asyncio
import aiohttp
import requests
from aiohttp_socks import ProxyConnector
from python_socks import ProxyType

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bsmxgksbiwpplkefkagl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "sb_publishable_TnP-AMERiSCQMGXo-5cHOQ_ECVyDWut")
FIREBASE_DB_URL = "https://safety-proxy-update-default-rtdb.firebaseio.com"

# ============================================================================
# 🤖 TELEGRAM BOT ALERT CONFIGURATION
# ============================================================================
TG_BOT_TOKEN = "7290192834:AAEb2mZ7..."  # 👈 Enter your Telegram Bot Token
TG_CHAT_ID = "584920194"                # 👈 Enter your Telegram Chat ID

GEO_TARGET_URL = "http://ip-api.com/json/?fields=status,country,countryCode,regionName,city,isp,query"
TIMEOUT_SECONDS = 10
CONCURRENT_LIMIT = 30
LOW_PROXY_THRESHOLD = 3
RESTOCK_THRESHOLD = 10  # Pool must reach 10+ to reset alert cycle

def send_telegram_alert(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TG_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[X] Telegram Send Error: {e}")

# ============================================================================
# 🧠 FIREBASE ALERT STATE ENGINE (PREVENTS SPAM / STORES ALERT HISTORY)
# ============================================================================
def get_cloud_alert_states():
    try:
        url = f"{FIREBASE_DB_URL}/alert_states.json"
        res = requests.get(url, timeout=3).json()
        return res if res and isinstance(res, dict) else {}
    except Exception:
        return {}

def update_cloud_alert_state(country_code, state_data):
    try:
        url = f"{FIREBASE_DB_URL}/alert_states/{country_code}.json"
        requests.put(url, json=state_data, timeout=3)
    except Exception:
        pass

async def test_and_enrich_proxy(proxy_item, semaphore, session_updater):
    proxy_id = proxy_item.get("id")
    
    # Supports both Type/User/Password and protocol/username/password
    host = str(proxy_item.get("ip") or proxy_item.get("IP") or proxy_item.get("host") or "").strip()
    port = int(str(proxy_item.get("port") or proxy_item.get("Port")).strip())
    user = str(proxy_item.get("user") or proxy_item.get("User") or proxy_item.get("username") or "").strip()
    pwd = str(proxy_item.get("password") or proxy_item.get("Password") or proxy_item.get("pass") or "").strip()
    proto = str(proxy_item.get("type") or proxy_item.get("Type") or proxy_item.get("protocol") or "socks5").lower().strip()

    if "http" in proto:
        ptype = ProxyType.HTTP
    elif "socks4" in proto:
        ptype = ProxyType.SOCKS4
    else:
        ptype = ProxyType.SOCKS5

    async with semaphore:
        is_alive = False
        latency_ms = 0
        country = proxy_item.get("country") or "N/A"
        country_code = proxy_item.get("country_code") or "N/A"
        city = proxy_item.get("city") or "N/A"
        isp = proxy_item.get("isp") or "N/A"
        start_time = time.time()
        
        try:
            connector = ProxyConnector(
                proxy_type=ptype,
                host=host,
                port=port,
                username=user if user else None,
                password=pwd if pwd else None,
                rdns=True
            )
            timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS, connect=TIMEOUT_SECONDS)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as client:
                async with client.get(GEO_TARGET_URL) as res:
                    if res.status == 200:
                        geo_data = await res.json()
                        if geo_data.get("status") == "success":
                            latency_ms = int((time.time() - start_time) * 1000)
                            country = geo_data.get("country", "Unknown")
                            country_code = geo_data.get("countryCode", "Unknown")
                            city = geo_data.get("city") or geo_data.get("regionName", "Unknown")
                            isp = geo_data.get("isp", "Residential Provider")
                            is_alive = True
        except Exception:
            is_alive = False

        status = "Active" if is_alive else "Dead"

        update_url = f"{SUPABASE_URL}/rest/v1/proxies?id=eq.{proxy_id}"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        
        update_data = {
            "status": status,
            "speed_ms": latency_ms if is_alive else 0,
            "country": country if is_alive else (proxy_item.get("country") or "N/A"),
            "country_code": country_code if is_alive else (proxy_item.get("country_code") or "N/A"),
            "city": city if is_alive else (proxy_item.get("city") or "N/A"),
            "isp": isp if is_alive else (proxy_item.get("isp") or "N/A")
        }

        try:
            async with session_updater.patch(update_url, headers=headers, json=update_data) as resp:
                if resp.status in (200, 204):
                    print(f"[{status}] ID {proxy_id} ({host}:{port}) -> {country} ({country_code}) | {latency_ms} ms")
        except Exception as e:
            print(f"[X] Error ID {proxy_id}: {e}")

        return {
            "country": update_data["country"],
            "country_code": update_data["country_code"],
            "status": status
        }

async def main():
    print("[*] Fetching proxies from Supabase...")
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    fetch_url = f"{SUPABASE_URL}/rest/v1/proxies?select=*"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(fetch_url, headers=headers) as resp:
            if resp.status != 200:
                print(f"[X] Failed to fetch: {resp.status}")
                return
            proxies = await resp.json()

        print(f"[*] Checking {len(proxies)} proxies...")
        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        
        tasks = [test_and_enrich_proxy(p, semaphore, session) for p in proxies]
        results = await asyncio.gather(*tasks)

        # ====================================================================
        # 📊 SMART ANTI-SPAM DISPATCHER (EXACTLY 1 ALERT PER LIFECYCLE)
        # ====================================================================
        country_stats = {}
        for r in results:
            c_name = r.get("country", "Unknown")
            c_code = r.get("country_code", "N/A")
            if c_name in ("N/A", "Unknown") or c_code in ("N/A", "UN"):
                continue
            key = c_code
            if key not in country_stats:
                country_stats[key] = {"name": c_name, "active": 0, "dead": 0, "total": 0}
            
            country_stats[key]["total"] += 1
            if r.get("status") == "Active":
                country_stats[key]["active"] += 1
            else:
                country_stats[key]["dead"] += 1

        alert_states = get_cloud_alert_states()

        for c_code, stats in country_stats.items():
            c_name = stats["name"]
            active_count = stats["active"]
            total_count = stats["total"]
            state = alert_states.get(c_code, {})
            
            low_alert_sent = state.get("low_alert_sent", False)
            dead_alert_sent = state.get("dead_alert_sent", False)

            # 1. Restock Condition: If pool replenished to >= 10, reset alert memory!
            if active_count >= RESTOCK_THRESHOLD:
                if low_alert_sent or dead_alert_sent:
                    update_cloud_alert_state(c_code, {"low_alert_sent": False, "dead_alert_sent": False})
                continue

            # 2. Critical All-Dead Alert (Sent ONLY ONCE)
            if active_count == 0 and total_count > 0:
                if not dead_alert_sent:
                    alert_msg = (
                        f"🚨 <b>[CRITICAL: ALL PROXIES DEAD]</b>\n\n"
                        f"🌍 <b>Region / Country:</b> {c_name} ({c_code})\n"
                        f"❌ <b>Active Proxies:</b> 0 / {total_count} Online\n"
                        f"⚠️ <b>Status:</b> All residential nodes for this country have failed!\n"
                        f"🛑 <b>Action Required:</b> Please upload fresh proxies to Supabase."
                    )
                    send_telegram_alert(alert_msg)
                    update_cloud_alert_state(c_code, {"low_alert_sent": True, "dead_alert_sent": True})

            # 3. Low Stock Warning (Sent ONLY ONCE)
            elif 1 <= active_count <= LOW_PROXY_THRESHOLD:
                if not low_alert_sent:
                    alert_msg = (
                        f"⚠️ <b>[LOW PROXY POOL WARNING]</b>\n\n"
                        f"🌍 <b>Region / Country:</b> {c_name} ({c_code})\n"
                        f"⚡ <b>Active Proxies:</b> {active_count} Online\n"
                        f"❌ <b>Dead Proxies:</b> {stats['dead']} Offline\n"
                        f"🔔 <b>Notice:</b> Live pool inventory is critically low (≤ {LOW_PROXY_THRESHOLD}).\n"
                        f"👉 <b>Action Required:</b> Please restock proxies soon."
                    )
                    send_telegram_alert(alert_msg)
                    update_cloud_alert_state(c_code, {"low_alert_sent": True, "dead_alert_sent": False})

        print("[✓] Health Check completed with Smart Anti-Spam state verification.")

if __name__ == "__main__":
    asyncio.run(main())
