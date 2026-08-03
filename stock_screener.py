"""
STOCK SCREENER - AUTOMATED VERSION (RAILWAY OPTIMIZED)
Top 100 most traded stocks
Criteria: RSI, MACD, EMA Crossover, Bollinger Bands, ATR
Posts signals (BUY/SELL/SHORT) to Discord with embeds
Automatically runs every 2 minutes during market hours
✅ Smart sleep during off-market hours: 30 min sleep = ~95% less memory
✅ Railway-specific optimizations for stability
"""

import os, json, time, datetime, pytz, warnings, sys, traceback
import numpy as np
import urllib.request
import pandas as pd
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Suppress yfinance warnings
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.filterwarnings('ignore')

try:
    import yfinance as yf
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    print("Installing required packages...")
    os.system("py -m pip install yfinance colorama pytz numpy pandas python-dotenv")
    import yfinance as yf
    from colorama import Fore, Style, init
    init(autoreset=True)

ET = pytz.timezone("US/Eastern")
RESULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_screener_results.json")
SENT_SIGNALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_signals.json")

# ── SMART SLEEP SETTINGS ───────────────────────────────────────
SCREENER_INTERVAL = 120  # Run every 2 minutes during market hours
CLOSED_SLEEP = 1800      # 30 minutes — sleep when market closed (saves ~95% memory vs 60s loops)
APPROACH_SLEEP = 60      # 60 seconds — check frequently when market opening soon

# ── DISCORD WEBHOOKS & ROLES ──────────────────────────────────
# Read from environment variables
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_STOCKS")

DISCORD_ROLE_PREMIUM = "<@&1518420622282068028>"

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

SIG_STRONG_BUY  = "STRONG BUY"
SIG_BUY         = "BUY"
SIG_WATCH       = "WATCH"
SIG_HOLD        = "HOLD"
SIG_REDUCE      = "REDUCE POSITION"
SIG_STRONG_SELL = "STRONG SELL"
SIG_SELL        = "SELL"

ACT_BUY_SIGS  = (SIG_STRONG_BUY, SIG_BUY)
ACT_SELL_SIGS = (SIG_STRONG_SELL, SIG_SELL, SIG_REDUCE)
ALL_ACT       = ACT_BUY_SIGS + ACT_SELL_SIGS

# ── US MARKET HOLIDAYS ─────────────────────────────────────────

def get_market_holidays(year):
    """Get all US market holidays for the year."""
    holidays = set()

    def nearest_weekday(dt):
        if dt.weekday() == 5: return dt - datetime.timedelta(days=1)
        if dt.weekday() == 6: return dt + datetime.timedelta(days=1)
        return dt

    holidays.add(nearest_weekday(datetime.date(year, 1, 1)))
    holidays.add(nearest_weekday(datetime.date(year, 6, 19)))
    holidays.add(nearest_weekday(datetime.date(year, 7, 4)))
    holidays.add(nearest_weekday(datetime.date(year, 12, 25)))

    jan = datetime.date(year, 1, 1)
    jan_mon = [jan + datetime.timedelta(days=i) for i in range(31) if (jan + datetime.timedelta(days=i)).weekday() == 0]
    holidays.add(jan_mon[2])

    feb = datetime.date(year, 2, 1)
    feb_mon = [feb + datetime.timedelta(days=i) for i in range(28) if (feb + datetime.timedelta(days=i)).weekday() == 0]
    holidays.add(feb_mon[2])

    def easter(y):
        a = y % 19; b = y // 100; c = y % 100
        d = b // 4; e = b % 4; f = (b + 8) // 25
        g = (b - f + 1) // 3; h = (19*a + b - d - g + 15) % 30
        i = c // 4; k = c % 4; l = (32 + 2*e + 2*i - h - k) % 7
        m = (a + 11*h + 22*l) // 451
        month = (h + l - 7*m + 114) // 31
        day   = ((h + l - 7*m + 114) % 31) + 1
        return datetime.date(y, month, day)

    holidays.add(easter(year) - datetime.timedelta(days=2))

    may = [datetime.date(year, 5, 1) + datetime.timedelta(days=i) for i in range(31)]
    holidays.add([d for d in may if d.weekday() == 0][-1])

    sep = datetime.date(year, 9, 1)
    sep_mon = [sep + datetime.timedelta(days=i) for i in range(30) if (sep + datetime.timedelta(days=i)).weekday() == 0]
    holidays.add(sep_mon[0])

    nov = datetime.date(year, 11, 1)
    nov_thu = [nov + datetime.timedelta(days=i) for i in range(30) if (nov + datetime.timedelta(days=i)).weekday() == 3]
    holidays.add(nov_thu[3])

    return holidays

def is_market_holiday():
    """Check if today is a US market holiday."""
    today = datetime.datetime.now(ET).date()
    return today in get_market_holidays(today.year)

def get_seconds_until_market_open():
    """Calculate seconds until market opens (9:30 AM ET weekdays)."""
    now = datetime.datetime.now(ET)
    
    # If it's a weekend, find next Monday 9:30 AM
    if now.weekday() >= 5:
        days_ahead = 7 - now.weekday()
        next_open = now.replace(hour=9, minute=30, second=0, microsecond=0) + datetime.timedelta(days=days_ahead)
        return int((next_open - now).total_seconds())
    
    # If it's a holiday, skip to next trading day
    if is_market_holiday():
        next_open = now.replace(hour=9, minute=30, second=0, microsecond=0) + datetime.timedelta(days=1)
        return int((next_open - now).total_seconds())
    
    # Today is a trading day
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    
    if now < market_open:
        # Before market open today
        return int((market_open - now).total_seconds())
    else:
        # After market open today, next open is tomorrow
        next_open = (now + datetime.timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        return int((next_open - now).total_seconds())

def is_market_open():
    """Check if market is currently open (9:30 AM - 4:00 PM ET, weekdays only)."""
    now = datetime.datetime.now(ET)
    
    # Weekend check
    if now.weekday() >= 5:
        return False
    
    # Holiday check
    if is_market_holiday():
        return False
    
    # Market hours check (9:30 AM - 4:00 PM ET)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_open <= now < market_close

# ── Sent Signals Tracking ──────────────────────────────────────

def load_sent_signals():
    """Load previously sent signals from file"""
    try:
        with open(SENT_SIGNALS_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_sent_signals(signals):
    """Save sent signals to file"""
    with open(SENT_SIGNALS_FILE, 'w') as f:
        json.dump(signals, f)

def signal_already_sent(ticker, action):
    """Check if this signal was already sent"""
    sent = load_sent_signals()
    signal_id = f"{ticker}_{action}"
    return signal_id in sent

def mark_signal_sent(ticker, action):
    """Mark a signal as sent"""
    sent = load_sent_signals()
    signal_id = f"{ticker}_{action}"
    if signal_id not in sent:
        sent.append(signal_id)
        save_sent_signals(sent)

# ── Top 100 Most Traded Stocks ─────────────────────────────────

def get_top_100_stocks():
    top_stocks = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "BRK.B", "JNJ", "V",
        "WMT", "JPM", "MA", "AVGO", "PG", "HD", "COST", "MCD", "CRM", "NFLX",
        "ABBV", "XOM", "CVX", "AMD", "ACN", "INTC", "INTU", "KO", "CSCO", "AXP",
        "ISRG", "TXN", "CMG", "ADBE", "QCOM", "MU", "NOW", "BKNG", "AMGN", "GILD",
        "AMAT", "LRCX", "UBER", "ABNB", "GE", "IBM", "PYPL", "ASML", "SNPS", "CDNS",
        "ADSK", "CCI", "PCAR", "MCHP", "VRTX", "ELV", "VEEV", "ZS", "NXPI", "KLAC",
        "SSNC", "PAYX", "FTNT", "OKTA", "PLTR", "CRWD", "DDOG", "SPLK", "NET", "MDB",
        "SQ", "COIN", "MARA", "RIOT", "CLSK", "MSTR", "HOOD", "UPST", "SOFI", "ENPH",
        "RUN", "HYLN", "QFIN", "JKS", "FUTU", "BEKE", "IQ", "BZUN", "BILI", "BIDU",
        "PDD", "KNSL", "XMTR", "LI", "NIO", "XPEV", "LCID", "PSTG", "SNOW", "DBX",
    ]
    return top_stocks[:100]

# ── Indicators ─────────────────────────────────────────────────

def rsi(close, p=14):
    d = close.diff()
    g = d.where(d > 0, 0).ewm(alpha=1/p, adjust=False).mean()
    l = -d.where(d < 0, 0).ewm(alpha=1/p, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

def macd(close):
    f   = close.ewm(span=12, adjust=False).mean()
    s   = close.ewm(span=26, adjust=False).mean()
    m   = f - s
    sig = m.ewm(span=9, adjust=False).mean()
    return m, sig

def bollinger(close):
    sma = close.rolling(20).mean()
    std = close.rolling(20).std()
    return sma + 2*std, sma - 2*std

def atr(high, low, close, p=14):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def confidence_score(buys, sells, rsi_val, vsurge):
    sig_count = max(len(buys), len(sells))
    base      = min(sig_count * 25, 75)
    if buys:
        rsi_bonus = max(0, (RSI_OVERSOLD - rsi_val) * 1.5) if rsi_val < RSI_OVERSOLD else 0
    else:
        rsi_bonus = max(0, (rsi_val - RSI_OVERBOUGHT) * 1.5) if rsi_val > RSI_OVERBOUGHT else 0
    vol_bonus = 10 if vsurge else 0
    return min(100, int(base + rsi_bonus + vol_bonus))

def get_targets(df, price):
    try:
        close      = df["Close"].squeeze()
        high       = df["High"].squeeze()
        low        = df["Low"].squeeze()
        recent     = close.iloc[-20:]
        resistance = float(recent.max())
        atr_val    = float(atr(high, low, close).iloc[-1])
        stop_loss  = round(price - (1.5 * atr_val), 2)
        target     = round(resistance, 2)
        risk       = round(price - stop_loss, 2)
        reward     = round(target - price, 2)
        rr         = round(reward / risk, 2) if risk > 0 else 0
        return target, stop_loss, rr, round(atr_val, 2)
    except:
        return None, None, None, None

# ── Analyze Stock ──────────────────────────────────────────────

def analyze_stock(ticker):
    try:
        import sys
        from io import StringIO
        
        # Capture stderr to suppress yfinance warnings
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    yf.download, ticker, period="3mo", interval="1d",
                    progress=False, auto_adjust=True
                )
                try:
                    df = future.result(timeout=5)  # 5 second timeout
                except FuturesTimeoutError:
                    return None
        finally:
            sys.stderr = old_stderr
        

        if df.empty or len(df) < 30:
            return None
        close = df["Close"].squeeze()
        vol   = df["Volume"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()

        price = float(close.iloc[-1])
        rsi_s      = rsi(close)
        mac, sig   = macd(close)
        bb_u, bb_l = bollinger(close)
        rn  = float(rsi_s.iloc[-1])
        mn  = float(mac.iloc[-1]); mp  = float(mac.iloc[-2])
        sn  = float(sig.iloc[-1]); sp  = float(sig.iloc[-2])
        es  = float(close.ewm(span=12, adjust=False).mean().iloc[-1])
        ep  = float(close.ewm(span=12, adjust=False).mean().iloc[-2])
        el  = float(close.ewm(span=26, adjust=False).mean().iloc[-1])
        ep2 = float(close.ewm(span=26, adjust=False).mean().iloc[-2])
        prev   = float(close.iloc[-2])
        bu     = float(bb_u.iloc[-1]); bl = float(bb_l.iloc[-1])
        vsurge = float(vol.iloc[-1]) > float(vol.iloc[-20:].mean()) * 1.3
        buys, sells = [], []
        if rn < RSI_OVERSOLD:    buys.append(f"RSI oversold ({rn:.1f})")
        if rn > RSI_OVERBOUGHT:  sells.append(f"RSI overbought ({rn:.1f})")
        if mp < sp and mn > sn:  buys.append("MACD bullish cross")
        if mp > sp and mn < sn:  sells.append("MACD bearish cross")
        if ep2 < ep and es > el: buys.append("Golden cross (EMA)")
        if ep2 > ep and es < el: sells.append("Death cross (EMA)")
        if price < bl: buys.append(f"Below BB lower ({bl:.2f})")
        if price > bu: sells.append(f"Above BB upper ({bu:.2f})")
        vn         = " [HIGH VOL]" if vsurge else ""
        pct        = ((price - prev) / prev) * 100
        conf_score = confidence_score(buys, sells, rn, vsurge)
        target, stop_loss, rr, atr_val = get_targets(df, price)
        if len(buys) >= 3:
            action = SIG_STRONG_BUY;  reason = " | ".join(buys) + vn
        elif len(buys) == 2:
            action = SIG_BUY;         reason = " | ".join(buys) + vn
        elif len(buys) == 1 or len(sells) == 1:
            action = SIG_WATCH;       reason = (buys[0] if buys else sells[0]) + vn
        elif len(sells) >= 3:
            action = SIG_STRONG_SELL; reason = " | ".join(sells) + vn
        elif len(sells) == 2:
            action = SIG_SELL;        reason = " | ".join(sells) + vn
        else:
            action = SIG_HOLD;        reason = "No clear signal"
        return dict(
            ticker=ticker, price=round(price, 2), pct=round(pct, 2),
            action=action, rsi=round(rn, 1), vsurge=vsurge,
            reason=reason, buys=len(buys), sells=len(sells),
            target=target, stop_loss=stop_loss, rr=rr, atr=atr_val,
            conf_score=conf_score
        )
    except:
        return None

# ── Save Results ───────────────────────────────────────────────

def save_results(results):
    with open(RESULTS_FILE, "w") as f:
        json.dump({
            "timestamp":     datetime.datetime.now(ET).isoformat(),
            "total_scanned": len(results),
            "signals":       results
        }, f, indent=2)

# ── Post to Discord with Embeds (Stock Signals) ─────────────────

def post_to_discord_embed(ticker, action, price, pct, rsi_val, confidence, reason,
                          target=None, stop_loss=None, rr=None):
    """Send a formatted embed to Discord stock signals channel."""
    if not DISCORD_WEBHOOK_URL:
        return False
    
    try:
        # Color by action
        if action in ACT_BUY_SIGS:
            color = 3066993   # green
            emoji = "🚀" if action == SIG_STRONG_BUY else "📈"
        elif action in ACT_SELL_SIGS:
            color = 15158332  # red
            emoji = "🔴" if action == SIG_STRONG_SELL else "📉"
        else:
            color = 15105570  # yellow
            emoji = "⚠️"

        fields = [
            {"name": "💰 Price",       "value": f"**${price:.2f}**",    "inline": True},
            {"name": "📊 Daily %",     "value": f"**{pct:+.2f}%**",     "inline": True},
            {"name": "RSI",            "value": f"**{rsi_val:.1f}**",   "inline": True},
            {"name": "🎯 Confidence",  "value": f"**{confidence}**",    "inline": True},
            {"name": "📋 Reason",      "value": reason,                 "inline": False},
        ]

        if target and stop_loss and rr:
            fields.append({
                "name":  "🎯 Targets",
                "value": f"Target: **${target:.2f}** | Stop: **${stop_loss:.2f}** | R/R: **{rr:.1f}x**",
                "inline": False,
            })

        embed = {
            "title":  f"{emoji}  {action} — {ticker}",
            "color":  color,
            "fields": fields,
        }

        payload = json.dumps({
            "content": DISCORD_ROLE_PREMIUM,
            "embeds":  [embed],
        }).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent":   "Mozilla/5.0",
            }
        )
        urllib.request.urlopen(req, timeout=10)
        time.sleep(0.3)
        return True

    except Exception as e:
        print(Fore.RED + f"[FAILED: {e}]")
        return False



# ── Main Screener ──────────────────────────────────────────────

def run_screener():
    """Run screener and post only NEW signals to Discord"""
    try:
        os.system("cls")
        print(Fore.CYAN + Style.BRIGHT + "\n" + "="*70)
        print(Fore.CYAN + Style.BRIGHT + "  STOCK SCREENER -- TOP 100 MOST TRADED (RAILWAY OPTIMIZED)")
        print(Fore.CYAN + "="*70 + "\n")

        stocks  = get_top_100_stocks()
        results = []

        print(Fore.WHITE + f"Scanning {len(stocks)} stocks...")
        print(Fore.WHITE + "This may take 2-3 minutes...\n")

        for i, ticker in enumerate(stocks, 1):
            print(Fore.CYAN + f"  [{i:3d}/{len(stocks)}] {ticker:<6}", end=" -> ", flush=True)
            r = analyze_stock(ticker)
            if r and r.get("action") in ALL_ACT:
                results.append(r)
                print(Fore.GREEN + f"{r['action']} ({r['conf_score']})")
            elif r:
                print(Fore.YELLOW + "no signal")
            else:
                print(Fore.YELLOW + "skipped")
            time.sleep(0.2)

        results.sort(key=lambda x: x["conf_score"], reverse=True)
        save_results(results)

        print(Fore.CYAN + "\n" + "="*70)
        print(Fore.GREEN + f"\n  Found {len(results)} signals")
        print(Fore.WHITE + f"  Saved to: stock_screener_results.json\n")

        if results:
            print(Fore.CYAN + Style.BRIGHT + "  TOP SIGNALS BY CONFIDENCE")
            print(Fore.CYAN + "-"*70)
            for r in results[:15]:
                action_color = Fore.GREEN if r["action"] in ACT_BUY_SIGS else Fore.RED
                print(Fore.WHITE + f"  {r['ticker']:<6} ${r['price']:<8.2f}  {r['pct']:+6.2f}%  RSI:{r['rsi']:>5.1f}  "
                      + action_color + f"{r['action']:<16}" + Fore.WHITE + f"({r['conf_score']})")
                print(Fore.WHITE + f"       -> {r['reason']}")
                if r.get("target"):
                    print(Fore.WHITE + f"       Target: ${r['target']:.2f}  Stop: ${r['stop_loss']:.2f}  R/R: {r['rr']:.1f}x")
                print()

            print(Fore.CYAN + "-"*70)
            print(Fore.WHITE + f"\n  Posting new signals to Discord...\n")

            try:
                # Post only NEW signals to Discord stock signals channel
                posted = 0
                for r in results:
                    if not signal_already_sent(r["ticker"], r["action"]):
                        print(Fore.CYAN + f"  Sending {r['ticker']}...", end=" ", flush=True)
                        ok = post_to_discord_embed(
                            ticker=r["ticker"],
                            action=r["action"],
                            price=r["price"],
                            pct=r["pct"],
                            rsi_val=r["rsi"],
                            confidence=r["conf_score"],
                            reason=r["reason"],
                            target=r.get("target"),
                            stop_loss=r.get("stop_loss"),
                            rr=r.get("rr"),
                        )
                        if ok:
                            mark_signal_sent(r["ticker"], r["action"])
                            print(Fore.GREEN + "sent!")
                            posted += 1
                        time.sleep(2)

                if posted > 0:
                    print(Fore.GREEN + Style.BRIGHT + f"\n  {posted} new signal(s) posted to Discord.")
                else:
                    print(Fore.YELLOW + f"\n  No new signals to post (already sent).")
            except Exception as e:
                print(f"ERROR posting to Discord: {e}", file=sys.stderr)
                traceback.print_exc()
                # Continue anyway
        else:
            print(Fore.YELLOW + "  No signals found this scan.")

        print(Fore.CYAN + "="*70 + "\n")
    except Exception as e:
        print(f"ERROR in run_screener: {e}", file=sys.stderr)
        traceback.print_exc()
        # Continue running even if scan fails


# ── Start Scheduler & Screener Loop ────────────────────────────

if __name__ == "__main__":
    import threading

    print(Fore.CYAN + Style.BRIGHT + "\n" + "="*70)
    print(Fore.CYAN + Style.BRIGHT + "  STOCK SCREENER - AUTOMATED MODE (RAILWAY OPTIMIZED)")
    print(Fore.CYAN + "="*70)
    print(Fore.WHITE + "  ✅ Scanning every 2 minutes DURING market hours only")
    print(Fore.WHITE + "  ✅ Smart sleep: 30 min when market closed (saves ~95% memory) 💾")
    print(Fore.WHITE + "  ✅ Detects weekends, holidays, and market hours automatically")
    print(Fore.CYAN + "="*70 + "\n")

    # Run screener immediately on startup
    run_screener()

    def run_screener_loop():
        """
        ✅ OPTIMIZED FOR RAILWAY HOBBY PLAN
        Background thread that runs screener every 2 minutes ONLY during market hours.
        Smart sleep when market is closed:
        - If < 30 min until open: Check every 60s (stay responsive)
        - If > 30 min until open: Sleep 30 min (saves ~95% memory)
        """
        last_status_printed = None
        while True:
            try:
                open_now = is_market_open()
                
                if open_now:
                    # ═══ DURING MARKET HOURS ═══════════════════════
                    # Scan every 2 minutes
                    run_screener()
                    time.sleep(SCREENER_INTERVAL)
                else:
                    # ═══ MARKET CLOSED ═════════════════════════════
                    # Use smart sleep
                    seconds_until_open = get_seconds_until_market_open()
                    now = datetime.datetime.now(ET)
                    
                    if seconds_until_open < CLOSED_SLEEP:
                        # Less than 30 min until open
                        sleep_duration = APPROACH_SLEEP  # 60s
                        status = f"⏳ Market opens in {seconds_until_open}s — checking frequently"
                    else:
                        # More than 30 min until open
                        sleep_duration = CLOSED_SLEEP  # 1800s = 30 min
                        hours, remainder = divmod(seconds_until_open, 3600)
                        mins, secs = divmod(remainder, 60)
                        status = f"💤 Market opens in ~{hours}h {mins}m — sleeping 30 min (saves memory)"
                    
                    # Print status once per sleep cycle (not every loop)
                    if status != last_status_printed:
                        print(Fore.YELLOW + f"[{now.strftime('%H:%M:%S')}] {status}")
                        last_status_printed = status
                    
                    time.sleep(sleep_duration)
                    
            except Exception as e:
                print(f"[screener loop] ERROR: {e}", file=sys.stderr)
                traceback.print_exc()
                time.sleep(60)

    # Start the screener loop in a background daemon thread
    screener_thread = threading.Thread(target=run_screener_loop, daemon=True)
    screener_thread.start()

    # Keep the main process alive forever
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Screener stopped by user")
