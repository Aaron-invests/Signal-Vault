"""
Signal Vault - Meme Coin Scanner with Auto Win Tracking

Features:
- Scans DexScreener for meme coin signals
- Posts signals to Discord
- Auto-tracks wins above 2x
- Auto-posts proof to Discord when 25% ATH drop or 2-hour timeout
- No external database, just Discord

Auto-installs requests if missing.
"""

import subprocess
import sys
import os
import time
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Auto-install requests if not present
try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "requests"])
    import requests

# ─────────────────────────────
# CONFIG
# ─────────────────────────────

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_MEME")
DISCORD_WEBHOOK_PROOF = os.getenv("DISCORD_WEBHOOK_PROOF")
PREMIUM_ROLE_ID = "1518420622282068028"

CHAINS = ["solana"]

MIN_LIQUIDITY_USD = 15000
MIN_VOLUME_24H_USD = 25000
MAX_AGE_HOURS = 24
MIN_PRICE_CHANGE_5M = 5

MAX_VOLUME_TO_LIQUIDITY_RATIO = 5
MIN_LIQUIDITY_FOR_VOLUME = 0.2

SCAN_INTERVAL_SECONDS = 300
SEEN_FILE = "seen_tokens.json"
TRACKED_WINS_FILE = "tracked_wins.json"

MIN_MULTIPLIER = 2.0
ATH_DROP_PERCENT = 25
WIN_TIMEOUT_HOURS = 2

# ─────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def load_tracked_wins():
    if os.path.exists(TRACKED_WINS_FILE):
        with open(TRACKED_WINS_FILE, "r") as f:
            data = json.load(f)
            for key in data:
                data[key]["signal_time"] = datetime.fromisoformat(data[key]["signal_time"])
                data[key]["ath_time"] = datetime.fromisoformat(data[key]["ath_time"])
            return data
    return {}

def save_tracked_wins(tracked):
    save_data = {}
    for key, val in tracked.items():
        save_data[key] = {
            "symbol": val["symbol"],
            "entry_price": val["entry_price"],
            "ath_price": val["ath_price"],
            "signal_time": val["signal_time"].isoformat(),
            "ath_time": val["ath_time"].isoformat(),
            "posted": val["posted"]
        }
    with open(TRACKED_WINS_FILE, "w") as f:
        json.dump(save_data, f)

# ─────────────────────────────
# DEXSCREENER
# ─────────────────────────────

def get_latest_token_profiles():
    try:
        r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[error] fetching token profiles: {e}")
        return []

def get_pair_data(chain_id, token_address):
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

    if liq > 0 and vol > 0:
        vol_to_liq_pct = vol / liq
        if vol_to_liq_pct < MIN_LIQUIDITY_FOR_VOLUME:
            print(f"[filtered] {symbol} - volume/liquidity too low ({vol_to_liq_pct:.1%}) - suspicious")
            return False
    return True

# ─────────────────────────────
# DISCORD POSTING
# ─────────────────────────────

def format_number(num):
    if num >= 1_000_000_000:
        return f"${num/1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"${num/1_000_000:.1f}M"
    elif num >= 1_000:
        return f"${num/1_000:.1f}K"
    else:
        return f"${num:.2f}"

def get_age_string(created_ms):
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
    """Send signal alert to main webhook."""
    base = pair.get("baseToken", {})
    name = base.get("name", "Unknown")
    symbol = base.get("symbol", "???")
    address = base.get("address", "?")
    price = pair.get("priceUsd", "?")
    liq = (pair.get("liquidity") or {}).get("usd", 0) or 0
    vol = (pair.get("volume") or {}).get("h24", 0) or 0
    change_5m = (pair.get("priceChange") or {}).get("m5", 0) or 0
    change_1h = (pair.get("priceChange") or {}).get("h1", 0) or 0
    change_6h = (pair.get("priceChange") or {}).get("h6", 0) or 0
    change_24h = (pair.get("priceChange") or {}).get("h24", 0) or 0
    url = pair.get("url", "")
    created = pair.get("pairCreatedAt")
    age = get_age_string(created)
    vol_to_liq = vol / liq if liq > 0 else 0

    if change_5m >= 20:
        color = 0xff0000
    elif change_5m >= 10:
        color = 0xff6600
    else:
        color = 0x9b59b6

    embed = {
        "title": f"🚨 {name} (${symbol})",
        "url": url,
        "color": color,
        "fields": [
            {"name": "📄 Contract Address", "value": f"`{address}`", "inline": False},
            {"name": "💰 Market Cap", "value": format_number(pair.get('marketCap', 0) or 0), "inline": True},
            {"name": "📊 5m Pump", "value": f"{change_5m:+.1f}% {'🔥' if change_5m >= 10 else ''}", "inline": True},
            {"name": "⏱ Age", "value": age, "inline": True},
            {"name": "💧 Liquidity", "value": format_number(liq), "inline": True},
            {"name": "📈 24h Volume", "value": format_number(vol), "inline": True},
            {"name": "💹 Vol/Liq", "value": f"{vol_to_liq:.2f}x", "inline": True},
            {"name": "1h Change", "value": f"{change_1h:+.1f}%", "inline": True},
            {"name": "6h Change", "value": f"{change_6h:+.1f}%", "inline": True},
            {"name": "24h Change", "value": f"{change_24h:+.1f}%", "inline": True},
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

def send_proof_post(symbol, entry_price, exit_price, multiplier):
    """Send proof to proof channel."""
    entry_mc = format_number(entry_price)
    exit_mc = format_number(exit_price)
    
    embed = {
        "title": f"✅ WIN: {symbol}",
        "color": 0x00ff00,
        "fields": [
            {"name": "💰 Multiplier", "value": f"{multiplier:.2f}X", "inline": True},
            {"name": "📥 Entry MC", "value": entry_mc, "inline": True},
            {"name": "📤 Exit MC", "value": exit_mc, "inline": True},
        ],
        "footer": {"text": "Signal Vault Proof of Results"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    payload = {
        "embeds": [embed],
    }

    try:
        r = requests.post(DISCORD_WEBHOOK_PROOF, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[proof posted] {symbol} - {multiplier:.2f}X")
    except Exception as e:
        print(f"[error] sending proof: {e}")

# ─────────────────────────────
# WIN TRACKING
# ─────────────────────────────

def check_and_update_wins(tracked_wins):
    """Check tracked wins and post proof when conditions are met."""
    to_remove = []
    
    for token_key, win_data in tracked_wins.items():
        if win_data["posted"]:
            continue
        
        chain_id, token_address = token_key.split(":")
        pair = get_pair_data(chain_id, token_address)
        
        if not pair:
            continue
        
        current_price = float(pair.get("priceUsd", 0))
        if current_price <= 0:
            continue
        
        entry_price = win_data["entry_price"]
        ath_price = win_data["ath_price"]
        signal_time = win_data["signal_time"]
        
        current_multiplier = current_price / entry_price
        
        # Update ATH if new high
        if current_price > ath_price:
            win_data["ath_price"] = current_price
            ath_price = current_price
            win_data["ath_time"] = datetime.now(timezone.utc)
        
        should_post = False
        
        # Check 25% drop from ATH
        drop_percent = ((ath_price - current_price) / ath_price) * 100
        if drop_percent >= ATH_DROP_PERCENT:
            should_post = True
        
        # Check 2-hour timeout
        time_elapsed = datetime.now(timezone.utc) - signal_time
        if time_elapsed >= timedelta(hours=WIN_TIMEOUT_HOURS):
            should_post = True
        
        if should_post:
            exit_price = win_data["ath_price"]  # Use ATH as the exit, not current price
            final_multiplier = exit_price / entry_price
            
            # Only post if it's actually a win (2x+)
            if final_multiplier >= MIN_MULTIPLIER:
                send_proof_post(
                    symbol=win_data["symbol"],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    multiplier=final_multiplier
                )
            
            win_data["posted"] = True
            to_remove.append(token_key)
        
        time.sleep(0.1)
    
    # Clean up posted wins
    for key in to_remove:
        del tracked_wins[key]
    
    return tracked_wins

# ─────────────────────────────
# MAIN LOOP
# ─────────────────────────────

def scan_once(seen, tracked_wins):
    """Scan for new signals."""
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
            
            # Get token info
            symbol = pair.get("baseToken", {}).get("symbol", "???")
            entry_price = float(pair.get("priceUsd", 0))
            
            # Add to tracked wins
            tracked_wins[key] = {
                "symbol": symbol,
                "entry_price": entry_price,
                "ath_price": entry_price,
                "signal_time": datetime.now(timezone.utc),
                "ath_time": datetime.now(timezone.utc),
                "posted": False
            }
            
            new_alerts += 1
            seen.add(key)

        time.sleep(0.5)

    # Check existing wins
    tracked_wins = check_and_update_wins(tracked_wins)

    return new_alerts, tracked_wins

def main():
    if not DISCORD_WEBHOOK_PROOF:
        print("[error] DISCORD_WEBHOOK_PROOF not set in .env")
    
    seen = load_seen()
    tracked_wins = load_tracked_wins()
    
    print(f"Meme scanner running — checking every {SCAN_INTERVAL_SECONDS}s")
    print(f"Win tracking: 2x multiplier, 25% ATH drop, 2h timeout")
    print(f"Auto-posts proofs to Discord\n")

    while True:
        try:
            count, tracked_wins = scan_once(seen, tracked_wins)
            save_seen(seen)
            save_tracked_wins(tracked_wins)
            
            status = f"({len(tracked_wins)} tracked)" if tracked_wins else ""
            print(f"[{datetime.now().strftime('%H:%M:%S')}] scan done, {count} alerts {status}")
        except Exception as e:
            print(f"[error] scan loop: {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
