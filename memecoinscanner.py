"""
Signal Vault - Meme Coin Scanner
Single file. Just run: python memecoinscanner.py
Auto-installs requests if missing.
"""

import subprocess
import sys
import os
import time
import json
from datetime import datetime, timezone

# Auto-install requests if not present
try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "requests"])
    import requests

# ─────────────────────────────
# CONFIG - tweak these
# ─────────────────────────────
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1522712597282160681/BnXcGdCA0fyCgSNWDN0Z6lIzNi6sIryxpSv6eyVALUVbhfD6-qJi3ADK6Jd1HHypmB3j"
PREMIUM_ROLE_ID = "1518420622282068028"

CHAINS = ["solana"]              # add "base", "ethereum", etc. if you want other chains
MIN_LIQUIDITY_USD = 15000        # filters out illiquid rugpull bait
MIN_VOLUME_24H_USD = 25000
MAX_AGE_HOURS = 24                # only alert on tokens newer than this
MIN_PRICE_CHANGE_5M = 5           # % pump in last 5 min to count as "hot"

# RUG FILTERS
MAX_VOLUME_TO_LIQUIDITY_RATIO = 5  # if volume is 5x+ liquidity, likely a pump-and-dump setup
MIN_LIQUIDITY_FOR_VOLUME = 0.2    # volume must be at least 20% of liquidity (low = suspicious)

SCAN_INTERVAL_SECONDS = 300       # 5 min between scans
SEEN_FILE = "seen_tokens.json"

# ─────────────────────────────
# STATE (dedup so we don't spam the same token repeatedly)
# ─────────────────────────────
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ─────────────────────────────
# DEXSCREENER
# ─────────────────────────────
def get_latest_token_profiles():
    """Newest token profiles across chains (DexScreener public endpoint)."""
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[error] fetching token profiles: {e}")
        return []

def get_pair_data(chain_id, token_address):
    """Pull liquidity/volume/price data for a token address."""
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=10,
        )
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        pairs = [p for p in pairs if p.get("chainId") == chain_id]
        if not pairs:
            return None
        # pick the highest-liquidity pair for this token
        return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
    except Exception as e:
        print(f"[error] fetching pair data for {token_address}: {e}")
        return None

def passes_filters(pair):
    if not pair:
        return False
    liq = (pair.get("liquidity") or {}).get("usd", 0) or 0
    vol = (pair.get("volume") or {}).get("h24", 0) or 0
    created = pair.get("pairCreatedAt")
    change_5m = (pair.get("priceChange") or {}).get("m5", 0) or 0
    symbol = pair.get("baseToken", {}).get("symbol", "?")

    if liq < MIN_LIQUIDITY_USD:
        return False
    if vol < MIN_VOLUME_24H_USD:
        return False
    if created:
        age_hours = (time.time() * 1000 - created) / 1000 / 3600
        if age_hours > MAX_AGE_HOURS:
            return False
    if change_5m < MIN_PRICE_CHANGE_5M:
        return False
    
    # RUG CHECKS
    # Check 1: Volume way higher than liquidity = likely pump & dump or liquidity already pulled
    if liq > 0 and vol > 0:
        vol_to_liq_ratio = vol / liq
        if vol_to_liq_ratio > MAX_VOLUME_TO_LIQUIDITY_RATIO:
            print(f"[filtered] {symbol} - volume/liquidity ratio too high ({vol_to_liq_ratio:.1f}x) - likely rug")
            return False
    
    # Check 2: Volume suspiciously low relative to liquidity = low trade activity or artificial
    if liq > 0 and vol > 0:
        vol_to_liq_pct = vol / liq
        if vol_to_liq_pct < MIN_LIQUIDITY_FOR_VOLUME:
            print(f"[filtered] {symbol} - volume/liquidity too low ({vol_to_liq_pct:.1%}) - suspicious")
            return False
    
    return True

# ─────────────────────────────
# DISCORD
# ─────────────────────────────
def format_number(num):
    """Format number to K/M/B shorthand."""
    if num >= 1_000_000_000:
        return f"${num/1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"${num/1_000_000:.1f}M"
    elif num >= 1_000:
        return f"${num/1_000:.1f}K"
    else:
        return f"${num:.2f}"

def get_age_string(created_ms):
    """Convert created timestamp to readable age."""
    if not created_ms:
        return "?"
    age_seconds = (time.time() * 1000 - created_ms) / 1000
    if age_seconds < 60:
        return f"{int(age_seconds)}s"
    elif age_seconds < 3600:
        return f"{int(age_seconds/60)}m"
    elif age_seconds < 86400:
        return f"{int(age_seconds/3600)}h"
    else:
        return f"{int(age_seconds/86400)}d"

def send_alert(pair):
    base = pair.get("baseToken", {})
    name = base.get("name", "Unknown")
    symbol = base.get("symbol", "???")
    price = pair.get("priceUsd", "?")
    liq = (pair.get("liquidity") or {}).get("usd", 0) or 0
    vol = (pair.get("volume") or {}).get("h24", 0) or 0
    change_5m = (pair.get("priceChange") or {}).get("m5", 0) or 0
    change_1h = (pair.get("priceChange") or {}).get("h1", 0) or 0
    change_6h = (pair.get("priceChange") or {}).get("h6", 0) or 0
    change_24h = (pair.get("priceChange") or {}).get("h24", 0) or 0
    url = pair.get("url", "")
    chain = pair.get("chainId", "")
    created = pair.get("pairCreatedAt")
    age = get_age_string(created)
    
    vol_to_liq = vol / liq if liq > 0 else 0

    # Color based on momentum
    if change_5m >= 20:
        color = 0xff0000  # red hot
    elif change_5m >= 10:
        color = 0xff6600  # orange
    else:
        color = 0x9b59b6  # purple

    embed = {
        "title": f"🚨 {name} (${symbol})",
        "url": url,
        "color": color,
        "fields": [
            # Price & Movement
            {
                "name": "💰 Price",
                "value": f"{format_number(float(price) if isinstance(price, (int, float)) else 0)}",
                "inline": True
            },
            {
                "name": "📊 5m Pump",
                "value": f"{change_5m:+.1f}% {'🔥' if change_5m >= 10 else ''}",
                "inline": True
            },
            {
                "name": "⏱ Age",
                "value": f"{age}",
                "inline": True
            },
            # Liquidity & Volume
            {
                "name": "💧 Liquidity",
                "value": f"{format_number(liq)}",
                "inline": True
            },
            {
                "name": "📈 24h Volume",
                "value": f"{format_number(vol)}",
                "inline": True
            },
            {
                "name": "💹 Vol/Liq",
                "value": f"{vol_to_liq:.2f}x",
                "inline": True
            },
            # Time-based changes
            {
                "name": "1h Change",
                "value": f"{change_1h:+.1f}%",
                "inline": True
            },
            {
                "name": "6h Change",
                "value": f"{change_6h:+.1f}%",
                "inline": True
            },
            {
                "name": "24h Change",
                "value": f"{change_24h:+.1f}%",
                "inline": True
            },
        ],
        "footer": {"text": "Signal Vault Meme Scanner • Extreme risk — not financial advice, DYOR"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    payload = {
        "content": f"<@&{PREMIUM_ROLE_ID}>",
        "embeds": [embed],
        "allowed_mentions": {"roles": [PREMIUM_ROLE_ID]},
    }

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[alert sent] {symbol}")
    except Exception as e:
        print(f"[error] sending webhook: {e}")

# ─────────────────────────────
# MAIN LOOP
# ─────────────────────────────
def scan_once(seen):
    profiles = get_latest_token_profiles()
    new_alerts = 0
    for p in profiles:
        chain_id = p.get("chainId")
        address = p.get("tokenAddress")
        if chain_id not in CHAINS or not address:
            continue
        key = f"{chain_id}:{address}"
        if key in seen:
            continue

        pair = get_pair_data(chain_id, address)
        if passes_filters(pair):
            send_alert(pair)
            new_alerts += 1
        seen.add(key)
        time.sleep(0.5)  # be polite to the free API

    return new_alerts

def main():
    seen = load_seen()
    print(f"Meme scanner running — checking every {SCAN_INTERVAL_SECONDS}s. Ctrl+C to stop.")
    while True:
        try:
            count = scan_once(seen)
            save_seen(seen)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] scan done, {count} alerts sent")
        except Exception as e:
            print(f"[error] scan loop: {e}")
        time.sleep(SCAN_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()