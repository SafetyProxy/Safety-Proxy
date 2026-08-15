import os
import time
import asyncio
import aiohttp
import requests
from aiohttp_socks import ProxyConnector
from python_socks import ProxyType

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bsmxgksbiwpplkefkagl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "sb_publishable_TnP-AMERiSCQMGXo-5cHOQ_ECVyDWut")

# ============================================================================
# 🤖 TELEGRAM BOT ALERT CONFIGURATION
# ============================================================================
TG_BOT_TOKEN = "8061946758:AAEjNcllL6ctgZoH5coI8Z64ypW_WOAt_Mc"  # 👈 Enter your Telegram Bot Token here
TG_CHAT_ID = "7544280143"                # 👈 Enter your Telegram Chat ID here

GEO_TARGET_URL = "http://ip-api.com/json/?fields=status,country,countryCode,regionName,city,isp,query"
TIMEOUT_SECONDS = 10
CONCURRENT_LIMIT = 30
LOW_PROXY_THRESHOLD = 3  # Alerts when active proxies <= 3

def send_telegram_alert(message):
    """Sends enterprise formatted alert to owner via Telegram"""
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

async def test_and_enrich_proxy(proxy_item, semaphore, session_updater):
    proxy_id = proxy_item.get("id")
    host = str(proxy_item.get("ip") or proxy_item.get("host")).strip()
    port = int(str(proxy_item.get("port")).strip())
    user = str(proxy_item.get("username") or proxy_item.get("user") or "").strip()
    pwd = str(proxy_item.get("password") or proxy_item.get("pass") or "").strip()
    proto = str(proxy_item.get("protocol") or "socks5").lower().strip()

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

        # Supabase update
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
        # 📊 Country Wise Aggregation & Telegram Alert Dispatcher
        # ====================================================================
        country_stats = {}
        for r in results:
            c_name = r.get("country", "Unknown")
            c_code = r.get("country_code", "N/A")
            if c_name in ("N/A", "Unknown"):
                continue
            key = f"{c_name} ({c_code})"
            if key not in country_stats:
                country_stats[key] = {"active": 0, "dead": 0, "total": 0}
            
            country_stats[key]["total"] += 1
            if r.get("status") == "Active":
                country_stats[key]["active"] += 1
            else:
                country_stats[key]["dead"] += 1

        print("\n📊 Country Wise Statistics:")
        for c_key, stats in country_stats.items():
            print(f" -> {c_key}: Active={stats['active']}, Dead={stats['dead']}, Total={stats['total']}")
            
            # 1. Critical All-Dead Alert
            if stats["active"] == 0 and stats["total"] > 0:
                alert_msg = (
                    f"🚨 <b>[CRITICAL: ALL PROXIES DEAD]</b>\n\n"
                    f"🌍 <b>Region / Country:</b> {c_key}\n"
                    f"❌ <b>Active Proxies:</b> 0 / {stats['total']} Online\n"
                    f"⚠️ <b>Status:</b> All residential nodes for this country have failed!\n"
                    f"🛑 <b>Action Required:</b> Immediate proxy replenishment needed."
                )
                send_telegram_alert(alert_msg)

            # 2. Low Pool Stock Warning
            elif 1 <= stats["active"] <= LOW_PROXY_THRESHOLD:
                alert_msg = (
                    f"⚠️ <b>[LOW PROXY POOL WARNING]</b>\n\n"
                    f"🌍 <b>Region / Country:</b> {c_key}\n"
                    f"⚡ <b>Active Proxies:</b> {stats['active']} Online\n"
                    f"❌ <b>Dead Proxies:</b> {stats['dead']} Offline\n"
                    f"🔔 <b>Notice:</b> Live pool inventory is critically low (≤ {LOW_PROXY_THRESHOLD}).\n"
                    f"👉 <b>Action Required:</b> Please add fresh proxies to database."
                )
                send_telegram_alert(alert_msg)

        print("[✓] Supabase updated & Telegram notification check completed.")

if __name__ == "__main__":
    asyncio.run(main())
