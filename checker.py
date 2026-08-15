import os
import time
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector
from python_socks import ProxyType

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bsmxgksbiwpplkefkagl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "sb_publishable_TnP-AMERiSCQMGXo-5cHOQ_ECVyDWut")

# একাধিক দ্রুতগতির টেস্ট সার্ভার (একটি ফেইল করলে অন্যটি দিয়ে টেস্ট করবে)
TEST_TARGETS = [
    "http://api.ipify.org?format=json",
    "http://ip-api.com/json",
    "http://icanhazip.com"
]

TIMEOUT_SECONDS = 10
CONCURRENT_LIMIT = 30

async def test_proxy(proxy_item, semaphore, session_updater):
    proxy_id = proxy_item.get("id")
    host = str(proxy_item.get("ip") or proxy_item.get("host")).strip()
    port = int(str(proxy_item.get("port")).strip())
    user = str(proxy_item.get("username") or proxy_item.get("user") or "").strip()
    pwd = str(proxy_item.get("password") or proxy_item.get("pass") or "").strip()
    proto = str(proxy_item.get("protocol") or "socks5").lower().strip()

    # প্রোটোকল নির্ধারণ
    if "http" in proto:
        ptype = ProxyType.HTTP
    elif "socks4" in proto:
        ptype = ProxyType.SOCKS4
    else:
        ptype = ProxyType.SOCKS5

    async with semaphore:
        is_alive = False
        latency_ms = 0
        start_time = time.time()
        
        try:
            # ডাইরেক্ট সকেট কানেক্টর (URL পার্সিং এরর ছাড়া যেকোনো ক্যারেক্টার কাজ করবে)
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
                for target in TEST_TARGETS:
                    try:
                        async with client.get(target) as res:
                            if res.status == 200:
                                latency_ms = int((time.time() - start_time) * 1000)
                                is_alive = True
                                break
                    except Exception:
                        continue
        except Exception:
            is_alive = False

        status = "Active" if is_alive else "Dead"

        # Supabase-এ স্ট্যাটাস ও স্পিড আপডেট
        update_url = f"{SUPABASE_URL}/rest/v1/proxies?id=eq.{proxy_id}"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        update_data = {
            "status": status,
            "speed_ms": latency_ms
        }

        try:
            async with session_updater.patch(update_url, headers=headers, json=update_data) as resp:
                if resp.status in (200, 204):
                    print(f"[{status}] ID {proxy_id} ({host}:{port}) -> {latency_ms} ms")
        except Exception as e:
            print(f"[X] Exception updating ID {proxy_id}: {e}")

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
                print(f"[X] Failed to fetch proxies: {resp.status}")
                return
            proxies = await resp.json()

        print(f"[*] Found {len(proxies)} proxies. Starting Health Check...")
        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        
        tasks = [test_proxy(p, semaphore, session) for p in proxies]
        await asyncio.gather(*tasks)
        print("[✓] All proxies updated successfully in Supabase.")

if __name__ == "__main__":
    asyncio.run(main())
