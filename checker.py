import os
import time
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bsmxgksbiwpplkefkagl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "sb_publishable_TnP-AMERiSCQMGXo-5cHOQ_ECVyDWut")

TEST_TARGET_URL = "http://httpbin.org/ip"
CONCURRENT_LIMIT = 50
TIMEOUT_SECONDS = 5

async def test_proxy(proxy_item, semaphore, session_updater):
    proxy_id = proxy_item.get("id")
    host = proxy_item.get("ip") or proxy_item.get("host")
    port = proxy_item.get("port")
    user = proxy_item.get("username") or proxy_item.get("user") or ""
    pwd = proxy_item.get("password") or proxy_item.get("pass") or ""
    proto = (proxy_item.get("protocol") or "socks5").lower()

    if user and pwd:
        proxy_url = f"{proto}://{user}:{pwd}@{host}:{port}"
    else:
        proxy_url = f"{proto}://{host}:{port}"

    async with semaphore:
        is_alive = False
        latency_ms = 0
        start_time = time.time()
        
        try:
            connector = ProxyConnector.from_url(proxy_url)
            timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as client:
                async with client.get(TEST_TARGET_URL) as res:
                    if res.status == 200:
                        latency_ms = int((time.time() - start_time) * 1000)
                        is_alive = True
        except Exception:
            is_alive = False

        status = "Active" if is_alive else "Dead"

        # Update status & speed in Supabase
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
                    print(f"[✓] ID {proxy_id} ({host}:{port}) -> {status} ({latency_ms} ms)")
                else:
                    err_msg = await resp.text()
                    print(f"[X] Failed to update ID {proxy_id}: Status {resp.status} - {err_msg}")
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
