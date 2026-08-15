import os
import time
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector
from python_socks import ProxyType

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bsmxgksbiwpplkefkagl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "sb_publishable_TnP-AMERiSCQMGXo-5cHOQ_ECVyDWut")

# সরাসরি প্রক্সির আসল GeoIP ও আইএসপি বের করার টার্গেট
GEO_TARGET_URL = "http://ip-api.com/json/?fields=status,country,countryCode,regionName,city,isp,query"

TIMEOUT_SECONDS = 10
CONCURRENT_LIMIT = 30

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
        country = "United States"
        country_code = "US"
        city = "Unknown"
        isp = "Residential ISP"
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
                            country = geo_data.get("country", "United States")
                            country_code = geo_data.get("countryCode", "US")
                            city = geo_data.get("city") or geo_data.get("regionName", "City")
                            isp = geo_data.get("isp", "Residential Provider")
                            is_alive = True
        except Exception:
            is_alive = False

        status = "Active" if is_alive else "Dead"

        # ডাটাবেজে স্ট্যাটাস, স্পিড, দেশ, শহর ও আইএসপি একসাথে আপডেট
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
            "country": country if is_alive else (proxy_item.get("country") or "Unknown"),
            "country_code": country_code if is_alive else (proxy_item.get("country_code") or "US"),
            "city": city if is_alive else (proxy_item.get("city") or "Unknown"),
            "isp": isp if is_alive else (proxy_item.get("isp") or "Unknown")
        }

        try:
            async with session_updater.patch(update_url, headers=headers, json=update_data) as resp:
                if resp.status in (200, 204):
                    print(f"[{status}] ID {proxy_id} ({host}:{port}) -> {country} ({country_code}) | {isp} | {latency_ms} ms")
        except Exception as e:
            print(f"[X] Error ID {proxy_id}: {e}")

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

        print(f"[*] Checking {len(proxies)} proxies & resolving GeoIP...")
        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        
        tasks = [test_and_enrich_proxy(p, semaphore, session) for p in proxies]
        await asyncio.gather(*tasks)
        print("[✓] Supabase updated with Live Country, City, ISP & Status.")

if __name__ == "__main__":
    asyncio.run(main())
