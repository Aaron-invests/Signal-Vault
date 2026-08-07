"""
Signal Vault - Meme Coin Scanner with Auto Win Tracking (OPTIMIZED)

Fixes:
- Fetches TRENDING tokens instead of ALL tokens (~10x fewer API calls)
- Separates signal scanning (every 5m) from win tracking (every 30s)
- Caches pair data to avoid redundant requests
- Respects rate limits properly

This should cut your Railway usage by 80%+
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

# OPTIMIZED: Increased from 240s to 300s (5 minutes)
SIGNAL_SCAN_INTERVAL_SECONDS = 300
# Win tracking matches signal scan (both every 5 minutes)
# Once a proof posts, token is deleted immediately
WIN_CHECK_INTERVAL_SECONDS = 300

SEEN_FILE = "seen_tokens.json"
TRACKED_WINS_FILE = "tracked_wins.json"

MIN_MULTIPLIER = 2.0
ATH_DROP_PERCENT = 25
WIN_TIMEOUT_HOURS = 2

# RATE LIMITING: Max 8 signals per hour (prevent spam nights)
MAX_SIGNALS_PER_HOUR = 8
SIGNALS_THIS_HOUR = []

# ─────────────────────────────
# CACHING (reduce redundant API calls)
# ─────────────────────────────

_pair_data_cache = {}
_cache_timestamp = {}
CACHE_TTL_SECONDS = 60

def clear_old_cache():
    """Remove cached data older than TTL."""
    now = time.time()
    expired = [k for k, ts in _cache_timestamp.items() if now - ts > CACHE_TTL_SECONDS]
    for k in expired:
        del _pair_data_cache[k]
        del _cache_timestamp[k]

# ─────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
                # Safety: cap at 10,000 max tokens in memory
                if len(data) > 10000:
                    print(f"[warning] seen_tokens.json has {len(data)} entries, resetting to prevent memory bloat")
                    return set()
                return set(data)
        except:
            return set()
    return set()

def save_seen(seen):
    # Prevent memory bloat: if seen_tokens grows too large, clear it
    # (This prevents tracking 100k old tokens forever)
    if len(seen) > 15000:
        print(f"[maintenance] seen_tokens grew to {len(seen)}, clearing to free memory")
        seen = set()
    
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def load_tracked_wins():
    if os.path.exists(TRACKED_WINS_FILE):
        try:
            with open(TRACKED_WINS_FILE, "r") as f:
                data = json.load(f)
                now = datetime.now(timezone.utc)
                cleaned = {}
                
                for key, val in data.items():
                    val["signal_time"] = datetime.fromisoformat(val["signal_time"])
                    val["ath_time"] = datetime.fromisoformat(val["ath_time"])
                    if "entry_mc" not in val:
                        val["entry_mc"] = val.get("entry_price", 0)
                    if "ath_mc" not in val:
                        val["ath_mc"] = val.get("ath_price", 0)
                    
                    # CLEANUP: Remove tracked wins older than 48 hours
                    # (they should have posted proof or timed out by now)
                    age = now - val["signal_time"]
                    if age > timedelta(hours=48):
                        print(f"[cleanup] removing stale tracked win {val['symbol']} (age: {age})")
                        continue
                    
                    cleaned[key] = val
                
                if len(cleaned) != len(data):
                    print(f"[cleanup] removed {len(data) - len(cleaned)} old tracked wins from memory")
                
                return cleaned
        except:
            return {}
    return {}

def save_tracked_wins(tracked):
    save_data = {}
    for key, val in tracked.items():
        save_data[key] = {
            "symbol": val["symbol"],
            "entry_price": val["entry_price"],
            "entry_mc": val["entry_mc"],
            "ath_price": val["ath_price"],
            "ath_mc": val["ath_mc"],
            "signal_time": val["signal_time"].isoformat(),
            "ath_time": val["ath_time"].isoformat(),
            "posted": val["posted"]
        }
    with open(TRACKED_WINS_FILE, "w") as f:
        json.dump(save_data, f)

# ─────────────────────────────
# DEXSCREENER (OPTIMIZED)
# ─────────────────────────────

def get_trending_tokens():
    """
    OPTIMIZED: Fetch TRENDING tokens instead of ALL profiles.
    Returns ~20-50 tokens instead of 500+.
    """
    try:
        r = requests.get(
            "https://api.dexscreener.com/token-profiles/trending/v1",
            timeout=10
        )
        r.raise_for_status()
        tokens = r.json()
        # Cap at 50 max to further reduce API calls
        return tokens[:50] if tokens else []
    except Exception as e:
        print(f"[error] fetching trending tokens: {e}")
        return []

def get_pair_data(chain_id, token_address):
    """
    Fetch pair data with caching to avoid redundant API calls.
    If we've fetched this token in the last 60 seconds, return cached data.
    """
    key = f"{chain_id}:{token_address}"
    
    # Check cache first
    if key in _pair_data_cache:
        return _pair_data_cache[key]
    
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
        best_pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
        
        # Cache the result
        _pair_data_cache[key] = best_pair
        _cache_timestamp[key] = time.time()
        
        return best_pair
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
            print(f"[filtered] {symbol} - volume/liquidity too low ({vol_to_liq_pct:.1%})")
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
    elif num >= 1:
        return f"${num:.2f}"
    elif num >= 0.01:
        return f"${num:.4f}"
    elif num > 0:
        return f"${num:.8f}"
    else:
        return "$0.00"

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

def check_rate_limit():
    """
    Check if we've exceeded max signals per hour.
    Returns True if OK to send, False if rate limited.
    """
    global SIGNALS_THIS_HOUR
    now = time.time()
    
    # Remove signals older than 1 hour
    SIGNALS_THIS_HOUR = [ts for ts in SIGNALS_THIS_HOUR if now - ts < 3600]
    
    if len(SIGNALS_THIS_HOUR) >= MAX_SIGNALS_PER_HOUR:
        return False
    return True

def record_signal():
    """Record that we sent a signal (for rate limiting)."""
    global SIGNALS_THIS_HOUR
    SIGNALS_THIS_HOUR.append(time.time())

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

    info = pair.get("info") or {}
    socials = info.get("socials") or []
    social_links = []
    for s in socials:
        s_type = s.get("type", "").lower()
        s_url = s.get("url", "")
        if s_type == "telegram" and s_url:
            social_links.append(f"[Telegram]({s_url})")
        elif s_type in ("twitter", "x") and s_url:
            social_links.append(f"[Twitter/X]({s_url})")

    if social_links:
        embed["fields"].append({
            "name": "🔗 Links",
            "value": " • ".join(social_links),
            "inline": False
        })

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

def send_proof_post(symbol, entry_mc, exit_mc, multiplier):
    """Send proof to proof channel."""
    entry_mc_str = format_number(entry_mc)
    exit_mc_str = format_number(exit_mc)
    
    embed = {
        "title": f"✅ WIN: {symbol}",
        "color": 0x00ff00,
        "fields": [
            {"name": "Multiplier", "value": f"**{multiplier:.2f}X**", "inline": False},
            {"name": "─────────────", "value": "─────────────", "inline": False},
            {"name": "Entry MC", "value": f"**{entry_mc_str}**", "inline": False},
            {"name": "Exit MC", "value": f"**{exit_mc_str}**", "inline": False},
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
# WIN TRACKING (SEPARATED)
# ─────────────────────────────

def check_and_update_wins(tracked_wins):
    """
    Check tracked wins and post proof when conditions are met.
    DELETE from tracking immediately after posting.
    Runs every 30s but won't hammer API since we delete posted tokens.
    """
    tokens_to_delete = []
    
    for token_key, win_data in list(tracked_wins.items()):
        chain_id, token_address = token_key.split(":")
        pair = get_pair_data(chain_id, token_address)
        
        if not pair:
            continue
        
        current_price = float(pair.get("priceUsd", 0))
        if current_price <= 0:
            continue
        
        entry_price = win_data["entry_price"]
        entry_mc = win_data["entry_mc"]
        ath_price = win_data["ath_price"]
        ath_mc = win_data["ath_mc"]
        signal_time = win_data["signal_time"]
        current_mc = float(pair.get("marketCap", 0) or 0)
        
        # Update ATH if new high
        if current_price > ath_price:
            win_data["ath_price"] = current_price
            ath_price = current_price
            if current_mc > ath_mc:
                win_data["ath_mc"] = current_mc
                ath_mc = current_mc
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
            exit_mc = win_data["ath_mc"]
            final_multiplier = exit_mc / entry_mc if entry_mc > 0 else 0
            
            # Only post if it's actually a win (2x+)
            if final_multiplier >= MIN_MULTIPLIER:
                send_proof_post(
                    symbol=win_data["symbol"],
                    entry_mc=entry_mc,
                    exit_mc=exit_mc,
                    multiplier=final_multiplier
                )
            
            # DELETE immediately after posting - stop tracking this token
            tokens_to_delete.append(token_key)
    
    # Remove all posted tokens from tracking
    for token_key in tokens_to_delete:
        del tracked_wins[token_key]
    
    return tracked_wins

# ─────────────────────────────
# SIGNAL SCANNING (OPTIMIZED)
# ─────────────────────────────

def scan_for_signals(seen, tracked_wins):
    """
    Scan for new signals. Runs every 5 minutes.
    Fetches TRENDING tokens only (~20-50 instead of 500+).
    Rate limited to MAX_SIGNALS_PER_HOUR to prevent spam.
    """
    profiles = get_trending_tokens()
    new_alerts = 0
    skipped_rate_limit = 0

    for p in profiles:
        chain_id = p.get("chainId")
        address = p.get("tokenAddress")

        if chain_id not in CHAINS or not address:
            continue
        
        if address.startswith("http://") or address.startswith("https://"):
            continue

        key = f"{chain_id}:{address}"

        if key in seen:
            continue

        pair = get_pair_data(chain_id, address)

        if passes_filters(pair):
            # CHECK RATE LIMIT before sending
            if not check_rate_limit():
                print(f"[rate limited] {pair.get('baseToken', {}).get('symbol', '?')} - already sent {MAX_SIGNALS_PER_HOUR} this hour")
                skipped_rate_limit += 1
                continue
            
            send_alert(pair)
            record_signal()  # Record that we sent this signal
            
            symbol = pair.get("baseToken", {}).get("symbol", "???")
            
            # Add to tracked wins
            tracked_wins[key] = {
                "symbol": symbol,
                "entry_price": float(pair.get("priceUsd", 0)),
                "entry_mc": float(pair.get("marketCap", 0) or 0),
                "ath_price": float(pair.get("priceUsd", 0)),
                "ath_mc": float(pair.get("marketCap", 0) or 0),
                "signal_time": datetime.now(timezone.utc),
                "ath_time": datetime.now(timezone.utc),
                "posted": False
            }
            
            new_alerts += 1
            seen.add(key)

        time.sleep(0.2)

    if skipped_rate_limit > 0:
        print(f"[rate limit] blocked {skipped_rate_limit} signals this scan")

    return new_alerts, tracked_wins

# ─────────────────────────────
# MAIN LOOP (SEPARATED)
# ─────────────────────────────

def main():
    if not DISCORD_WEBHOOK_PROOF:
        print("[error] DISCORD_WEBHOOK_PROOF not set in .env")
    
    seen = load_seen()
    tracked_wins = load_tracked_wins()
    
    print(f"✓ Meme scanner OPTIMIZED")
    print(f"  • Signal scan: every {SIGNAL_SCAN_INTERVAL_SECONDS}s (trending tokens only)")
    print(f"  • Win check: every {WIN_CHECK_INTERVAL_SECONDS}s (separate loop)")
    print(f"  • Rate limit: {MAX_SIGNALS_PER_HOUR} signals/hour (prevent spam)")
    print(f"  • Memory: auto-cleanup seen_tokens + tracked_wins >48h old")
    print(f"  • Starting with {len(seen)} seen tokens, {len(tracked_wins)} tracked wins\n")

    last_signal_scan = 0
    last_win_check = 0

    while True:
        try:
            now = time.time()
            
            # SIGNAL SCAN (every 5 minutes)
            if now - last_signal_scan >= SIGNAL_SCAN_INTERVAL_SECONDS:
                count, tracked_wins = scan_for_signals(seen, tracked_wins)
                save_seen(seen)
                save_tracked_wins(tracked_wins)
                last_signal_scan = now
                print(f"[{datetime.now().strftime('%H:%M:%S')}] SIGNAL SCAN: +{count} new alerts, {len(tracked_wins)} tracking (seen: {len(seen)})")
            
            # WIN CHECK (every 5 minutes)
            if now - last_win_check >= WIN_CHECK_INTERVAL_SECONDS:
                tracked_wins = check_and_update_wins(tracked_wins)
                save_tracked_wins(tracked_wins)
                last_win_check = now
                print(f"[{datetime.now().strftime('%H:%M:%S')}] WIN CHECK: {len(tracked_wins)} still tracking")
            
            # Clear old cache entries
            clear_old_cache()
            
            # Sleep 5 seconds to avoid tight loop
            time.sleep(5)

        except Exception as e:
            print(f"[error] main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
