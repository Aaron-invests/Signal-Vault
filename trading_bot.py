"""
INDEX FUND SIGNAL BOT
Strategies: RSI, MACD, EMA Crossover, Bollinger Bands
Tickers: FXAIX, QQQM, VXUS, SCHD, VTI, VOO
Max 30 trades/month | 9:30 AM - 4:00 PM ET
"""

import sys, os, json, time, datetime, pytz, calendar
import numpy as np
import urllib.request
import pandas as pd
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

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

ET             = pytz.timezone("US/Eastern")
TICKERS        = ["FXAIX", "QQQM", "VXUS", "SCHD", "VTI", "VOO"]
MUTUAL_FUNDS   = ["FXAIX"]
MAX_TRADES     = 30
LOG_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_log.json")
STREAK_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "streak_log.json")
PERF_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perf_log.json")
FXAIX_CACHE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fxaix_cache.json")
BRIEF_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brief_log.json")
EOD_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eod_log.json")
WEEKLY_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weekly_log.json")
REFRESH        = 60
RSI_OVERSOLD   = 30
RSI_OVERBOUGHT = 70

# ── ENVIRONMENT VARIABLES ──────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID        = os.getenv("TELEGRAM_CHAT_ID")

# ── DISCORD WEBHOOK & ROLES ────────────────────────────────────
DISCORD_WEBHOOK_URL  = os.getenv("DISCORD_WEBHOOK_INDEX_FUNDS")
DISCORD_ROLE_INDEX   = "<@&1518793540434526218>"

TRADE_WARN     = 25

POSITIONS = {
    "VXUS":  {"shares": 3.179,  "avg_cost": 78.59},
    "FXAIX": {"shares": 2.447,  "avg_cost": 243.87},
    "QQQM":  {"shares": 0.82,   "avg_cost": 266.17},
    "SCHD":  {"shares": 5.634,  "avg_cost": 32.83},
}

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

ALL_SIGNALS = {
    "1": SIG_STRONG_BUY,
    "2": SIG_BUY,
    "3": SIG_REDUCE,
    "4": SIG_STRONG_SELL,
    "5": SIG_SELL,
}

# ── US Market Holidays ─────────────────────────────────────────

def get_market_holidays(year):
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
    today = datetime.datetime.now(ET).date()
    return today in get_market_holidays(today.year)

def get_holiday_name():
    today = datetime.datetime.now(ET).date()
    year  = today.year

    def nearest_weekday(dt):
        if dt.weekday() == 5: return dt - datetime.timedelta(days=1)
        if dt.weekday() == 6: return dt + datetime.timedelta(days=1)
        return dt

    def easter(y):
        a = y % 19; b = y // 100; c = y % 100
        d = b // 4; e = b % 4; f = (b + 8) // 25
        g = (b - f + 1) // 3; h = (19*a + b - d - g + 15) % 30
        i = c // 4; k = c % 4; l = (32 + 2*e + 2*i - h - k) % 7
        m = (a + 11*h + 22*l) // 451
        month = (h + l - 7*m + 114) // 31
        day   = ((h + l - 7*m + 114) % 31) + 1
        return datetime.date(y, month, day)

    named = {
        nearest_weekday(datetime.date(year, 1, 1)):   "New Year's Day",
        nearest_weekday(datetime.date(year, 6, 19)):  "Juneteenth",
        nearest_weekday(datetime.date(year, 7, 4)):   "Independence Day",
        nearest_weekday(datetime.date(year, 12, 25)): "Christmas Day",
    }
    jan = datetime.date(year, 1, 1)
    jan_mon = [jan + datetime.timedelta(days=i) for i in range(31) if (jan + datetime.timedelta(days=i)).weekday() == 0]
    named[jan_mon[2]] = "MLK Day"
    feb = datetime.date(year, 2, 1)
    feb_mon = [feb + datetime.timedelta(days=i) for i in range(28) if (feb + datetime.timedelta(days=i)).weekday() == 0]
    named[feb_mon[2]] = "Presidents Day"
    named[easter(year) - datetime.timedelta(days=2)] = "Good Friday"
    may = [datetime.date(year, 5, 1) + datetime.timedelta(days=i) for i in range(31)]
    named[[d for d in may if d.weekday() == 0][-1]] = "Memorial Day"
    sep = datetime.date(year, 9, 1)
    sep_mon = [sep + datetime.timedelta(days=i) for i in range(30) if (sep + datetime.timedelta(days=i)).weekday() == 0]
    named[sep_mon[0]] = "Labor Day"
    nov = datetime.date(year, 11, 1)
    nov_thu = [nov + datetime.timedelta(days=i) for i in range(30) if (nov + datetime.timedelta(days=i)).weekday() == 3]
    named[nov_thu[3]] = "Thanksgiving"
    return named.get(today)

# ── Daily send guards ──────────────────────────────────────────

def _read_date_file(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f).get("date")
    return None

def _write_date_file(path):
    today = datetime.datetime.now(ET).strftime("%Y-%m-%d")
    with open(path, "w") as f:
        json.dump({"date": today}, f)

def _is_today(path):
    today = datetime.datetime.now(ET).strftime("%Y-%m-%d")
    return _read_date_file(path) == today

def morning_already_sent():  return _is_today(BRIEF_FILE)
def mark_morning_sent():     _write_date_file(BRIEF_FILE)
def eod_already_sent():      return _is_today(EOD_FILE)
def mark_eod_sent():         _write_date_file(EOD_FILE)

def weekly_already_sent():
    if os.path.exists(WEEKLY_FILE):
        with open(WEEKLY_FILE) as f:
            data = json.load(f)
        now = datetime.datetime.now(ET)
        return data.get("week") == now.strftime("%Y-W%W")
    return False

def mark_weekly_sent():
    now = datetime.datetime.now(ET)
    with open(WEEKLY_FILE, "w") as f:
        json.dump({"week": now.strftime("%Y-W%W")}, f)

# ── FXAIX cache ────────────────────────────────────────────────

def load_fxaix_cache():
    if os.path.exists(FXAIX_CACHE):
        with open(FXAIX_CACHE) as f: return json.load(f)
    return {"price": None, "date": None}

def save_fxaix_cache(price):
    today = datetime.datetime.now(ET).strftime("%Y-%m-%d")
    with open(FXAIX_CACHE, "w") as f:
        json.dump({"price": price, "date": today}, f)

def after_4pm_et():
    now = datetime.datetime.now(ET)
    return now >= now.replace(hour=16, minute=0, second=0, microsecond=0) and now.weekday() < 5

def get_fxaix_price():
    today = datetime.datetime.now(ET).strftime("%Y-%m-%d")
    try:
        df = yf.download("FXAIX", period="5d", interval="1d", progress=False, auto_adjust=True)
        if not df.empty:
            price     = round(float(df["Close"].squeeze().iloc[-1]), 2)
            last_date = str(df.index[-1].date())
            save_fxaix_cache(price)
            if after_4pm_et() and last_date == today:
                return price, False
            return price, last_date != today
    except:
        pass
    cache = load_fxaix_cache()
    if cache["price"]:
        return cache["price"], True
    return None, True

# ── News ───────────────────────────────────────────────────────

def get_news(ticker, max_headlines=2):
    try:
        t         = yf.Ticker(ticker)
        news      = t.news
        headlines = []
        for item in news[:max_headlines]:
            title = item.get("content", {}).get("title") or item.get("title", "")
            if title: headlines.append(title)
        return headlines
    except:
        return []

def get_market_news(max_headlines=3):
    try:
        t         = yf.Ticker("SPY")
        news      = t.news
        headlines = []
        for item in news[:max_headlines]:
            title = item.get("content", {}).get("title") or item.get("title", "")
            if title: headlines.append(title)
        return headlines
    except:
        return []

# ── Streak log ─────────────────────────────────────────────────

def load_streaks():
    if os.path.exists(STREAK_FILE):
        with open(STREAK_FILE) as f: return json.load(f)
    return {}

def save_streaks(streaks):
    with open(STREAK_FILE, "w") as f: json.dump(streaks, f, indent=2)

def update_streak(ticker, signal):
    streaks = load_streaks()
    today   = datetime.datetime.now(ET).strftime("%Y-%m-%d")
    entry   = streaks.get(ticker, {"signal": None, "count": 0, "since": today})
    if entry["signal"] == signal:
        if entry.get("last_date") != today:
            entry["count"]    += 1
            entry["last_date"] = today
    else:
        entry = {"signal": signal, "count": 1, "since": today, "last_date": today}
    streaks[ticker] = entry
    save_streaks(streaks)
    return entry["count"]

def update_streak_fxaix(signal):
    """FXAIX streak only ticks once per day after 4 PM when NAV is confirmed."""
    streaks = load_streaks()
    today   = datetime.datetime.now(ET).strftime("%Y-%m-%d")
    entry   = streaks.get("FXAIX", {"signal": None, "count": 0, "since": today})
    if after_4pm_et():
        if entry["signal"] == signal:
            if entry.get("last_date") != today:
                entry["count"]    += 1
                entry["last_date"] = today
        else:
            entry = {"signal": signal, "count": 1, "since": today, "last_date": today}
        streaks["FXAIX"] = entry
        save_streaks(streaks)
    return entry.get("count", 1)

def get_streak(ticker):
    streaks = load_streaks()
    entry   = streaks.get(ticker)
    if not entry: return None, None
    return entry["signal"], entry["count"]

# ── Performance log ────────────────────────────────────────────

def load_perf():
    if os.path.exists(PERF_FILE):
        with open(PERF_FILE) as f: return json.load(f)
    return {"signals": []}

def save_perf(perf):
    with open(PERF_FILE, "w") as f: json.dump(perf, f, indent=2)

def log_signal_perf(ticker, action, price):
    perf = load_perf()
    perf["signals"].append({
        "timestamp":     datetime.datetime.now(ET).isoformat(),
        "ticker":        ticker,
        "action":        action,
        "price":         round(price, 2),
        "outcome":       None,
        "outcome_price": None,
    })
    save_perf(perf)

def resolve_perf(eod_prices):
    """Resolve non-FXAIX signals that are 3+ days old using current price."""
    perf   = load_perf()
    now_et = datetime.datetime.now(ET)
    changed = False
    for sig in perf["signals"]:
        if sig["outcome"] is not None: continue
        if sig["ticker"] == "FXAIX": continue
        sig_dt   = datetime.datetime.fromisoformat(sig["timestamp"])
        days_old = (now_et - sig_dt).days
        if days_old < 3: continue
        price_now = eod_prices.get(sig["ticker"])
        if not price_now: continue
        result = (price_now - sig["price"]) if sig["action"] in ACT_BUY_SIGS else (sig["price"] - price_now)
        sig["outcome"]       = "WIN" if result >= 0 else "LOSS"
        sig["outcome_price"] = round(price_now, 2)
        sig["result_dollar"] = round(result, 2)
        sig["result_pct"]    = round((result / sig["price"]) * 100, 2)
        changed = True
    if changed: save_perf(perf)

def resolve_fxaix_perf():
    """
    Resolve FXAIX signals 3+ days old using live NAV after 4 PM.
    Re-resolves previously bad resolves with fresh price every time.
    Sends Telegram notification when outcome changes or first resolved.
    """
    if not after_4pm_et(): return
    price, stale = get_fxaix_price()
    if not price or stale: return

    perf    = load_perf()
    now_et  = datetime.datetime.now(ET)
    changed = False
    resolved_results = []

    for sig in perf["signals"]:
        if sig["ticker"] != "FXAIX": continue
        sig_dt   = datetime.datetime.fromisoformat(sig["timestamp"])
        days_old = (now_et - sig_dt).days
        if days_old < 3: continue

        result       = (price - sig["price"]) if sig["action"] in ACT_BUY_SIGS else (sig["price"] - price)
        outcome      = "WIN" if result >= 0 else "LOSS"
        prev_outcome = sig.get("outcome")

        sig["outcome"]       = outcome
        sig["outcome_price"] = round(price, 2)
        sig["result_dollar"] = round(result, 2)
        sig["result_pct"]    = round((result / sig["price"]) * 100, 2)
        changed = True

        if prev_outcome != outcome or prev_outcome is None:
            resolved_results.append({
                "action":      sig["action"],
                "price":       sig["price"],
                "nav_now":     price,
                "outcome":     outcome,
                "result":      round(result, 2),
                "result_pct":  round((result / sig["price"]) * 100, 2),
                "date":        sig["timestamp"][:10],
                "days_old":    days_old,
                "was_updated": prev_outcome is not None,
            })

    if changed:
        save_perf(perf)
        for r in resolved_results:
            emoji        = "✅" if r["outcome"] == "WIN" else "❌"
            arrow        = "📈" if r["action"] in ACT_BUY_SIGS else "📉"
            shares       = POSITIONS.get("FXAIX", {}).get("shares", 0)
            total_dollar = round(r["result"] * shares, 2)
            updated_str  = "\n🔄 *Re-resolved with fresh NAV (previous resolve was incorrect)*" if r["was_updated"] else ""
            msg = (
                f"{emoji} **FXAIX Signal Resolved**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"{arrow} Signal:    **{r['action']}**\n"
                f"📅 Date:      {r['date']} ({r['days_old']}d ago)\n"
                f"💰 Entry NAV: **${r['price']:.2f}**\n"
                f"📊 Now NAV:   **${r['nav_now']:.2f}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"{'🟢' if r['outcome']=='WIN' else '🔴'} Outcome:  **{r['outcome']}**\n"
                f"{'🟢' if r['result']>=0 else '🔴'} Return:   **{r['result']:+.2f} ({r['result_pct']:+.2f}%)**\n"
                f"💼 Your P&L:  **${total_dollar:+.2f}** on {shares} shares"
                f"{updated_str}"
            )
            send_telegram(msg)
            print(Fore.GREEN + f"  ✓ FXAIX resolved: {r['outcome']} @ ${r['nav_now']:.2f} ({r['result_pct']:+.2f}%)")

# ── Telegram ───────────────────────────────────────────────────

def send_telegram(msg):
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(Fore.RED + f"  Telegram error: {e}")

# ── Discord embed sender — Signals only ────────────────────────

def send_discord_signal_embed(ticker, action, price, rsi_val, confidence, reason,
                               role_ping="", target=None, stop_loss=None, rr=None,
                               streak=None, market_condition="neutral", news=None,
                               fxaix_stale=False):
    """Send a formatted signal embed. Signals only — no position data."""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        if action in (SIG_STRONG_BUY, SIG_BUY):
            color = 3066993   # green
            emoji = "🚀" if action == SIG_STRONG_BUY else "📈"
        elif action in (SIG_STRONG_SELL, SIG_SELL, SIG_REDUCE):
            color = 15158332  # red
            emoji = "🔴" if action == SIG_STRONG_SELL else "📉"
        else:
            color = 15105570  # yellow
            emoji = "⚠️"

        fields = [
            {"name": "💰 Price",       "value": f"**${price:.2f}**",    "inline": True},
            {"name": "📊 RSI",         "value": f"**{rsi_val}**",       "inline": True},
            {"name": "🎯 Confidence",  "value": f"**{confidence}**",    "inline": True},
            {"name": "📋 Reason",      "value": reason,                 "inline": False},
            {"name": "📈 Market",      "value": market_condition.upper(), "inline": True},
        ]

        if target and stop_loss and rr:
            fields.append({
                "name":  "🎯 Targets",
                "value": f"Target: **${target:.2f}** | Stop: **${stop_loss:.2f}** | R/R: **{rr:.1f}x**",
                "inline": False,
            })

        if streak and streak >= 2:
            fields.append({
                "name":  "🔥 Streak",
                "value": f"**{streak} days** in a row",
                "inline": True,
            })

        if news:
            fields.append({
                "name":  "📰 News",
                "value": "\n".join(f"• {h[:80]}" for h in news),
                "inline": False,
            })

        if fxaix_stale:
            fields.append({
                "name":  "⚠️ FXAIX Note",
                "value": "Using previous NAV — today's update posts after 4 PM ET. Resolves in 3 days.",
                "inline": False,
            })

        embed = {
            "title":  f"{emoji}  {action} — {ticker}",
            "color":  color,
            "fields": fields,
        }

        payload = json.dumps({
            "content": role_ping,
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
        urllib.request.urlopen(req, timeout=5)
        time.sleep(0.3)  # Slight delay for readability between signals

    except Exception as e:
        print(Fore.RED + f"  Discord embed error: {e}")

# ── Morning Brief — Telegram only ──────────────────────────────

def send_morning_brief():
    try:
        now_str = datetime.datetime.now(ET).strftime("%A, %b %d %Y")
        lines   = [
            f"🌅 <b>Morning Brief</b>",
            f"📅 {now_str}",
            f"━━━━━━━━━━━━━━━━━━━━━",
        ]

        lines.append("\n💼 <b>YOUR PORTFOLIO</b>")
        total_value = 0.0; total_cost = 0.0
        for ticker, pos in POSITIONS.items():
            try:
                if ticker in MUTUAL_FUNDS:
                    price, stale = get_fxaix_price()
                    tag = " <i>(prev NAV)</i>" if stale else " <i>(NAV confirmed)</i>"
                else:
                    df    = yf.download(ticker, period="2d", interval="1d", progress=False, auto_adjust=True)
                    price = round(float(df["Close"].squeeze().iloc[-1]), 2)
                    tag   = ""
                if not price: continue
                value = price * pos["shares"]
                cost  = pos["avg_cost"] * pos["shares"]
                gl    = value - cost; gl_pct = (gl / cost) * 100
                total_value += value; total_cost += cost
                arrow = "🟢" if gl >= 0 else "🔴"
                sig, cnt = get_streak(ticker)
                streak_str = f"  🔥 {sig} x{cnt}d" if sig and cnt and cnt >= 2 else ""
                lines.append(f"{arrow} <b>{ticker}</b>  ${price:.2f}{tag}\n"
                             f"   {pos['shares']} shares · avg ${pos['avg_cost']:.2f} · P&L: <b>{gl:+.2f} ({gl_pct:+.1f}%)</b>{streak_str}")
            except:
                lines.append(f"⚪ {ticker}: unavailable")

        total_gl     = total_value - total_cost
        total_gl_pct = (total_gl / total_cost * 100) if total_cost else 0
        lines.append(f"\n{'🟢' if total_gl>=0 else '🔴'} <b>Total Value: ${total_value:.2f}</b>")
        lines.append(f"{'🟢' if total_gl>=0 else '🔴'} <b>Total P&L: {total_gl:+.2f} ({total_gl_pct:+.1f}%)</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n📊 <b>MARKET OVERVIEW</b>")
        for watch in ["SPY", "QQQ", "VTI"]:
            try:
                df    = yf.download(watch, period="2d", interval="1d", progress=False, auto_adjust=True)
                close = df["Close"].squeeze()
                pct   = ((float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2])) * 100
                arrow = "🟢" if pct >= 0 else "🔴"
                lines.append(f"{arrow} <b>{watch}</b>  ${float(close.iloc[-1]):.2f}  ({pct:+.2f}% vs prev close)")
            except:
                lines.append(f"⚪ {watch}: unavailable")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n📰 <b>MARKET NEWS</b>")
        headlines = get_market_news(3)
        if headlines:
            for h in headlines: lines.append(f"• {h}")
        else:
            lines.append("No headlines available.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        used = trades_this_month(); left = MAX_TRADES - used
        cap_emoji = "🔴" if left <= 5 else "🟡" if left <= 10 else "🟢"
        lines.append(f"\n{cap_emoji} <b>Trades:</b> {used}/{MAX_TRADES} used · {left} remaining")
        lines.append("⚠️ FXAIX mutual fund NAV updates after 4 PM ET")
        lines.append("\n<b>Market opens 9:30 AM ET — good luck today 📈</b>")

        msg = "\n".join(lines)
        send_telegram(msg)
    except Exception as e:
        print(Fore.RED + f"  Morning brief error: {e}")

# ── EOD Summary — Telegram only ────────────────────────────────

def send_eod_summary(daily_signals, daily_trades, eod_prices):
    try:
        now_str = datetime.datetime.now(ET).strftime("%A, %b %d %Y")
        lines   = [
            f"📊 <b>End of Day Summary</b>",
            f"📅 {now_str}",
            f"━━━━━━━━━━━━━━━━━━━━━",
        ]

        lines.append("\n💼 <b>PORTFOLIO PERFORMANCE</b>")
        total_gl = 0.0
        for ticker, pos in POSITIONS.items():
            price = eod_prices.get(ticker)
            if price:
                gl     = (price - pos["avg_cost"]) * pos["shares"]
                gl_pct = ((price - pos["avg_cost"]) / pos["avg_cost"]) * 100
                total_gl += gl
                arrow    = "🟢" if gl >= 0 else "🔴"
                tag      = " <i>(NAV ✓)</i>" if ticker in MUTUAL_FUNDS else ""
                lines.append(f"{arrow} <b>{ticker}</b>  ${price:.2f}{tag}\n"
                             f"   {pos['shares']} shares · P&L: <b>{gl:+.2f} ({gl_pct:+.1f}%)</b>")
        lines.append(f"\n{'🟢' if total_gl>=0 else '🔴'} <b>Total Unrealized P&L: {total_gl:+.2f}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        spy_price = eod_prices.get("SPY"); spy_open = eod_prices.get("SPY_OPEN")
        if spy_price and spy_open:
            spy_chg = ((spy_price - spy_open) / spy_open) * 100
            if spy_chg <= -1.5:
                cond = f"🔴 Bearish ({spy_chg:+.2f}%) — signals suppressed"
            elif spy_chg >= 1.5:
                cond = f"🟢 Bullish ({spy_chg:+.2f}%)"
            else:
                cond = f"🟡 Neutral ({spy_chg:+.2f}%)"
            lines.append(f"\n📈 <b>MARKET CONDITION</b>")
            lines.append(f"SPY: {cond}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n📰 <b>MARKET NEWS</b>")
        headlines = get_market_news(3)
        if headlines:
            for h in headlines: lines.append(f"• {h}")
        else:
            lines.append("No headlines available.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n🔔 <b>SIGNALS TODAY</b>")
        if daily_signals:
            for ticker, actions in daily_signals.items():
                lines.append(f"• <b>{ticker}</b>: {' → '.join(actions)}")
        else:
            lines.append("No signals fired today.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n💸 <b>TRADES LOGGED TODAY</b>")
        if daily_trades:
            for t in daily_trades:
                price_now = eod_prices.get(t["ticker"])
                if price_now:
                    result     = (price_now - t["price"]) if t["action"] in ACT_BUY_SIGS else (t["price"] - price_now)
                    result_pct = (result / t["price"]) * 100
                    arrow      = "🟢" if result >= 0 else "🔴"
                    lines.append(f"{arrow} <b>{t['action']}</b> {t['ticker']}  ${t['price']:.2f} → ${price_now:.2f}\n"
                                 f"   Result: <b>{result:+.2f} ({result_pct:+.1f}%)</b>")
        else:
            lines.append("No trades logged today.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n🔥 <b>ACTIVE STREAKS</b>")
        streaks = load_streaks()
        active  = [(t, e) for t, e in streaks.items() if e["count"] >= 2]
        if active:
            for ticker, entry in active:
                lines.append(f"• <b>{ticker}</b>: {entry['signal']} × {entry['count']} days")
        else:
            lines.append("No multi-day streaks.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        used = trades_this_month(); left = MAX_TRADES - used
        cap_emoji = "🔴" if left <= 5 else "🟡" if left <= 10 else "🟢"
        lines.append(f"\n{cap_emoji} <b>Monthly Trades:</b> {used}/{MAX_TRADES} · {left} remaining")
        lines.append("⚠️ All signals resolve 3 days after firing")

        msg = "\n".join(lines)
        send_telegram(msg)
    except Exception as e:
        print(Fore.RED + f"  EOD summary error: {e}")

# ── Weekly Summary — Telegram only ─────────────────────────────

def send_weekly_summary():
    try:
        now        = datetime.datetime.now(ET)
        week_start = now - datetime.timedelta(days=7)
        now_str    = now.strftime("%A, %b %d %Y")
        lines      = [
            f"📅 <b>Weekly Summary</b>",
            f"Week of {week_start.strftime('%b %d')} – {now.strftime('%b %d')}",
            f"━━━━━━━━━━━━━━━━━━━━━",
        ]

        lines.append("\n💼 <b>PORTFOLIO THIS WEEK</b>")
        total_value = 0.0; total_cost = 0.0
        for ticker, pos in POSITIONS.items():
            try:
                if ticker in MUTUAL_FUNDS:
                    price, stale = get_fxaix_price()
                    tag = " <i>(prev NAV)</i>" if stale else ""
                    week_pct = None
                else:
                    df       = yf.download(ticker, period="10d", interval="1d", progress=False, auto_adjust=True)
                    close    = df["Close"].squeeze()
                    price    = float(close.iloc[-1])
                    w_ago    = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
                    week_pct = ((price - w_ago) / w_ago) * 100
                    tag      = ""
                if not price: continue
                value = price * pos["shares"]
                cost  = pos["avg_cost"] * pos["shares"]
                gl    = value - cost; gl_pct = (gl / cost) * 100
                total_value += value; total_cost += cost
                arrow = "🟢" if gl >= 0 else "🔴"
                week_str = f" · week: {week_pct:+.2f}%" if week_pct is not None else ""
                lines.append(f"{arrow} <b>{ticker}</b>  ${price:.2f}{tag}\n"
                             f"   overall: <b>{gl_pct:+.1f}%</b>{week_str}")
            except:
                lines.append(f"⚪ {ticker}: unavailable")

        total_gl     = total_value - total_cost
        total_gl_pct = (total_gl / total_cost * 100) if total_cost else 0
        lines.append(f"\n{'🟢' if total_gl>=0 else '🔴'} <b>Total: ${total_value:.2f}  P&L: {total_gl:+.2f} ({total_gl_pct:+.1f}%)</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n📊 <b>MARKET THIS WEEK</b>")
        for watch in ["SPY", "QQQ", "VTI"]:
            try:
                df       = yf.download(watch, period="10d", interval="1d", progress=False, auto_adjust=True)
                close    = df["Close"].squeeze()
                price    = float(close.iloc[-1])
                w_ago    = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
                week_pct = ((price - w_ago) / w_ago) * 100
                arrow    = "🟢" if week_pct >= 0 else "🔴"
                lines.append(f"{arrow} <b>{watch}</b>  ${price:.2f}  ({week_pct:+.2f}% this week)")
            except:
                lines.append(f"⚪ {watch}: unavailable")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n📰 <b>MARKET NEWS</b>")
        headlines = get_market_news(3)
        if headlines:
            for h in headlines: lines.append(f"• {h}")
        else:
            lines.append("No headlines available.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n💸 <b>TRADES THIS WEEK</b>")
        log      = load_log()
        w_trades = [t for t in log.get("trades", [])
                    if datetime.datetime.fromisoformat(t["timestamp"]) >= week_start]
        if w_trades:
            for t in w_trades:
                dt    = datetime.datetime.fromisoformat(t["timestamp"]).strftime("%a %b %d")
                arrow = "📈" if t["action"] in ACT_BUY_SIGS else "📉"
                lines.append(f"{arrow} {dt}: <b>{t['action']}</b> {t['ticker']} @ ${t['price']:.2f}")
        else:
            lines.append("No trades logged this week.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n🔥 <b>ACTIVE STREAKS</b>")
        streaks = load_streaks()
        active  = [(t, e) for t, e in streaks.items() if e["count"] >= 2]
        if active:
            for ticker, entry in active:
                lines.append(f"• <b>{ticker}</b>: {entry['signal']} × {entry['count']} days (since {entry['since']})")
        else:
            lines.append("No active streaks.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        used = trades_this_month(); left = MAX_TRADES - used
        cap_emoji = "🔴" if left <= 5 else "🟡" if left <= 10 else "🟢"
        lines.append(f"\n{cap_emoji} <b>Monthly Trades:</b> {used}/{MAX_TRADES} · {left} remaining")
        lines.append("\n<b>See you next week 📈</b>")

        msg = "\n".join(lines)
        send_telegram(msg)
    except Exception as e:
        print(Fore.RED + f"  Weekly summary error: {e}")

# ── Monthly Report — Telegram only ─────────────────────────────

def send_monthly_report():
    try:
        now         = datetime.datetime.now(ET)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_name  = now.strftime("%B %Y")
        perf        = load_perf()
        sigs        = [s for s in perf["signals"]
                       if datetime.datetime.fromisoformat(s["timestamp"]) >= month_start
                       and s["outcome"] is not None]

        lines = [
            f"📈 <b>Monthly Performance Report</b>",
            f"📅 {month_name}",
            f"━━━━━━━━━━━━━━━━━━━━━",
        ]

        lines.append("\n🎯 <b>SIGNAL ACCURACY</b>")
        if not sigs:
            lines.append("No resolved signals this month.")
        else:
            wins     = [s for s in sigs if s["outcome"] == "WIN"]
            losses   = [s for s in sigs if s["outcome"] == "LOSS"]
            win_rate = (len(wins) / len(sigs)) * 100
            avg_win  = np.mean([s["result_pct"] for s in wins])  if wins   else 0
            avg_loss = np.mean([s["result_pct"] for s in losses]) if losses else 0
            lines.append(f"Total signals: <b>{len(sigs)}</b>")
            lines.append(f"{'🟢' if win_rate>=50 else '🔴'} Win rate: <b>{win_rate:.1f}%</b>  ({len(wins)}W / {len(losses)}L)")
            lines.append(f"Avg win:  <b>{avg_win:+.2f}%</b>")
            lines.append(f"Avg loss: <b>{avg_loss:+.2f}%</b>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")

            lines.append("\n📊 <b>BY TICKER</b>")
            by_ticker = {}
            for s in sigs: by_ticker.setdefault(s["ticker"], []).append(s)
            for ticker, tsigs in by_ticker.items():
                tw = sum(1 for s in tsigs if s["outcome"] == "WIN")
                pct = (tw / len(tsigs)) * 100
                lines.append(f"{'🟢' if pct>=50 else '🔴'} <b>{ticker}</b>: {tw}/{len(tsigs)} correct ({pct:.0f}%)")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")

            sorted_sigs = sorted(sigs, key=lambda x: x.get("result_pct", 0), reverse=True)
            best  = sorted_sigs[0]
            worst = sorted_sigs[-1]
            lines.append("\n🏆 <b>BEST / WORST</b>")
            lines.append(f"🟢 Best:  {best['action']} <b>{best['ticker']}</b> @ ${best['price']:.2f} → <b>{best['result_pct']:+.2f}%</b>")
            lines.append(f"🔴 Worst: {worst['action']} <b>{worst['ticker']}</b> @ ${worst['price']:.2f} → <b>{worst['result_pct']:+.2f}%</b>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")

        lines.append("\n💼 <b>PORTFOLIO THIS MONTH</b>")
        total_value = 0.0; total_cost = 0.0
        for ticker, pos in POSITIONS.items():
            try:
                if ticker in MUTUAL_FUNDS:
                    price, _ = get_fxaix_price()
                else:
                    df    = yf.download(ticker, period="35d", interval="1d", progress=False, auto_adjust=True)
                    price = round(float(df["Close"].squeeze().iloc[-1]), 2)
                if not price: continue
                value  = price * pos["shares"]
                cost   = pos["avg_cost"] * pos["shares"]
                gl     = value - cost; gl_pct = (gl / cost) * 100
                total_value += value; total_cost += cost
                lines.append(f"{'🟢' if gl>=0 else '🔴'} <b>{ticker}</b>  ${price:.2f}  overall: <b>{gl_pct:+.1f}%</b>")
            except:
                lines.append(f"⚪ {ticker}: unavailable")
        total_gl     = total_value - total_cost
        total_gl_pct = (total_gl / total_cost * 100) if total_cost else 0
        lines.append(f"\n{'🟢' if total_gl>=0 else '🔴'} <b>Total: ${total_value:.2f}  P&L: {total_gl:+.2f} ({total_gl_pct:+.1f}%)</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        used = trades_this_month()
        cap_emoji = "🔴" if (MAX_TRADES-used) <= 5 else "🟡" if (MAX_TRADES-used) <= 10 else "🟢"
        lines.append(f"\n{cap_emoji} <b>Trades used:</b> {used}/{MAX_TRADES}")

        msg = "\n".join(lines)
        send_telegram(msg)
    except Exception as e:
        print(Fore.RED + f"  Monthly report error: {e}")

# ── Market condition ───────────────────────────────────────────

def get_market_condition():
    try:
        df = yf.download("SPY", period="5d", interval="1m", progress=False, auto_adjust=True)
        if df.empty: return "neutral", None, None
        today    = datetime.datetime.now(ET).date()
        df.index = df.index.tz_convert(ET)
        today_df = df[df.index.date == today]
        if today_df.empty: return "neutral", None, None
        open_price  = float(today_df["Open"].iloc[0])
        close_price = float(today_df["Close"].iloc[-1])
        chg_pct     = ((close_price - open_price) / open_price) * 100
        if chg_pct <= -1.5: return "bearish", open_price, close_price
        if chg_pct >=  1.5: return "bullish", open_price, close_price
        return "neutral", open_price, close_price
    except:
        return "neutral", None, None

# ── Trade log ──────────────────────────────────────────────────

def load_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f: return json.load(f)
    return {"trades": []}

def save_log(log):
    with open(LOG_FILE, "w") as f: json.dump(log, f, indent=2)

def trades_this_month():
    now   = datetime.datetime.now(ET)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return sum(1 for t in load_log()["trades"]
               if datetime.datetime.fromisoformat(t["timestamp"]) >= start)

def trades_today():
    now   = datetime.datetime.now(ET)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return [t for t in load_log()["trades"]
            if datetime.datetime.fromisoformat(t["timestamp"]) >= start]

def log_trade(ticker, action, price, reason="Manual entry"):
    log = load_log()
    log["trades"].append({
        "timestamp": datetime.datetime.now(ET).isoformat(),
        "ticker":    ticker,
        "action":    action,
        "price":     round(price, 2),
        "reason":    reason
    })
    save_log(log)

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

def is_open():
    now = datetime.datetime.now(ET)
    if now.weekday() >= 5: return False, "Weekend"
    if is_market_holiday():
        name = get_holiday_name() or "Holiday"
        return False, f"Market Holiday ({name})"
    o = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    c = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    if now < o:
        s = (o - now).seconds; m, sec = divmod(s, 60)
        return False, f"Pre-market (opens in {m}m {sec}s)"
    if now >= c: return False, "Closed (after 4 PM)"
    return True, "OPEN"

# ── Price target and ATR stop loss ─────────────────────────────

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

def get_intraday_change(ticker):
    if ticker in MUTUAL_FUNDS: return None, None
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df.empty: return None, None
        open_price  = float(df["Open"].iloc[0])
        close_price = float(df["Close"].iloc[-1])
        dollar      = close_price - open_price
        pct         = (dollar / open_price) * 100
        return round(pct, 2), round(dollar, 2)
    except:
        return None, None

def confidence_score(buys, sells, rsi_val, vsurge):
    sig_count = max(len(buys), len(sells))
    base      = min(sig_count * 25, 75)
    if buys:
        rsi_bonus = max(0, (RSI_OVERSOLD - rsi_val) * 1.5) if rsi_val < RSI_OVERSOLD else 0
    else:
        rsi_bonus = max(0, (rsi_val - RSI_OVERBOUGHT) * 1.5) if rsi_val > RSI_OVERBOUGHT else 0
    vol_bonus = 10 if vsurge else 0
    return min(100, int(base + rsi_bonus + vol_bonus))

# ── Analyze ────────────────────────────────────────────────────

def analyze(ticker, market_condition="neutral"):
    try:
        is_mutual   = ticker in MUTUAL_FUNDS
        fxaix_stale = False

        df = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 30: return None
        close = df["Close"].squeeze()
        vol   = df["Volume"].squeeze()

        if is_mutual:
            live_price, fxaix_stale = get_fxaix_price()
            price = live_price if live_price else float(close.iloc[-1])
        else:
            price = float(close.iloc[-1])

        rsi_s      = rsi(close)
        mac, sig   = macd(close)
        bb_u, bb_l = bollinger(close)

        rn   = float(rsi_s.iloc[-1])
        mn   = float(mac.iloc[-1]); mp  = float(mac.iloc[-2])
        sn   = float(sig.iloc[-1]); sp  = float(sig.iloc[-2])
        es   = float(close.ewm(span=12, adjust=False).mean().iloc[-1])
        ep   = float(close.ewm(span=12, adjust=False).mean().iloc[-2])
        el   = float(close.ewm(span=26, adjust=False).mean().iloc[-1])
        ep2  = float(close.ewm(span=26, adjust=False).mean().iloc[-2])
        prev = float(close.iloc[-2])
        bu   = float(bb_u.iloc[-1]); bl = float(bb_l.iloc[-1])
        vsurge = float(vol.iloc[-1]) > float(vol.iloc[-20:].mean()) * 1.3

        buys, sells = [], []
        if rn < RSI_OVERSOLD:       buys.append(f"RSI oversold ({rn:.1f})")
        if rn > RSI_OVERBOUGHT:     sells.append(f"RSI overbought ({rn:.1f})")
        if mp < sp and mn > sn:     buys.append("MACD bullish cross")
        if mp > sp and mn < sn:     sells.append("MACD bearish cross")
        if ep2 < ep and es > el:    buys.append("Golden cross (EMA)")
        if ep2 > ep and es < el:    sells.append("Death cross (EMA)")
        if price < bl:              buys.append(f"Below BB lower ({bl:.2f})")
        if price > bu:              sells.append(f"Above BB upper ({bu:.2f})")

        vn          = " [HIGH VOL]" if vsurge else ""
        pct         = ((price - prev) / prev) * 100
        in_position = ticker in POSITIONS
        conf_score  = confidence_score(buys, sells, rn, vsurge)
        target, stop_loss, rr, atr_val = get_targets(df, price)

        if len(buys) >= 3:
            if market_condition == "bearish":
                action = SIG_HOLD; conf = "—"; reason = "Strong Buy suppressed — bearish | " + " | ".join(buys)
            else:
                action = SIG_STRONG_BUY; conf = f"{conf_score}"; reason = " | ".join(buys) + vn
        elif len(buys) == 2:
            if market_condition == "bearish":
                action = SIG_HOLD; conf = "—"; reason = "Buy suppressed — bearish | " + " | ".join(buys)
            else:
                action = SIG_BUY; conf = f"{conf_score}"; reason = " | ".join(buys) + vn
        elif len(buys) == 1 or len(sells) == 1:
            action = SIG_WATCH; conf = f"{conf_score}"; reason = (buys[0] if buys else sells[0]) + vn
        elif len(sells) >= 3 and in_position:
            action = SIG_STRONG_SELL; conf = f"{conf_score}"; reason = " | ".join(sells) + vn
        elif len(sells) >= 3 and not in_position:
            action = SIG_SELL; conf = f"{conf_score}"; reason = " | ".join(sells) + vn
        elif len(sells) == 2 and in_position:
            action = SIG_REDUCE; conf = f"{conf_score}"; reason = " | ".join(sells) + vn
        elif len(sells) == 2 and not in_position:
            action = SIG_SELL; conf = f"{conf_score}"; reason = " | ".join(sells) + vn
        else:
            action = SIG_HOLD; conf = "—"; reason = "No clear signal"

        if is_mutual and fxaix_stale:
            reason += " [FXAIX: prev NAV — awaiting today's update]"

        streak_count = update_streak_fxaix(action) if is_mutual else update_streak(ticker, action)
        intraday_pct, intraday_dollar = get_intraday_change(ticker)
        news = get_news(ticker, 2)

        return dict(
            ticker=ticker, price=round(price,2), pct=round(pct,2),
            action=action, conf=conf, rsi=round(rn,1),
            bu=round(bu,2), bl=round(bl,2), vsurge=vsurge,
            reason=reason, buys=len(buys), sells=len(sells),
            in_position=in_position,
            avg_cost=POSITIONS[ticker]["avg_cost"] if in_position else None,
            shares=POSITIONS[ticker]["shares"]     if in_position else None,
            intraday_pct=intraday_pct, intraday_dollar=intraday_dollar,
            target=target, stop_loss=stop_loss, rr=rr, atr=atr_val,
            streak=streak_count, conf_score=conf_score, news=news,
            is_mutual=is_mutual, fxaix_stale=fxaix_stale,
        )
    except Exception as e:
        return dict(ticker=ticker, error=str(e))

# ── Display helpers ────────────────────────────────────────────

def ca(a):
    if a == SIG_STRONG_BUY:  return Fore.GREEN  + Style.BRIGHT + a
    if a == SIG_BUY:         return Fore.GREEN  + a
    if a == SIG_WATCH:       return Fore.YELLOW + a
    if a == SIG_REDUCE:      return Fore.MAGENTA + a
    if a == SIG_STRONG_SELL: return Fore.RED    + Style.BRIGHT + a
    if a == SIG_SELL:        return Fore.RED    + a
    return Fore.CYAN + a

def cp(p):
    return (Fore.GREEN if p > 0 else Fore.RED if p < 0 else Fore.WHITE) + f"{p:+.2f}%"

def cr(r):
    return (Fore.GREEN if r < 30 else Fore.RED if r > 70 else Fore.WHITE) + f"{r:.1f}"

def header(used, market_condition="neutral", spy_chg=None):
    os.system("cls")
    now   = datetime.datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    left  = MAX_TRADES - used
    bar   = Fore.GREEN + "█"*used + Fore.WHITE + "░"*left
    mkt_color = Fore.GREEN if market_condition=="bullish" else Fore.RED if market_condition=="bearish" else Fore.YELLOW
    spy_str   = f" (SPY {spy_chg:+.2f}%)" if spy_chg is not None else ""
    warn_str  = ""
    if left <= 5:            warn_str = Fore.RED    + Style.BRIGHT + "  ⚠ TRADE CAP ALMOST REACHED"
    elif used >= TRADE_WARN: warn_str = Fore.YELLOW + f"  ⚠ {left} trades left"
    print(Fore.CYAN + Style.BRIGHT + "═"*80)
    print(Fore.CYAN + Style.BRIGHT +
          f"  SIGNAL BOT  │  {Fore.WHITE}{now}  │  "
          + (Fore.RED if left<=5 else Fore.YELLOW)
          + f"Trades: {used}/{MAX_TRADES}  {left} left{warn_str}")
    print(Fore.CYAN + f"  Market: {mkt_color}{market_condition.upper()}{spy_str}" +
          Fore.CYAN + ("  │  ⚠ Strong Buy/Buy suppressed" if market_condition=="bearish" else ""))
    print(Fore.CYAN + "  " + bar)
    print(Fore.CYAN + Style.BRIGHT + "═"*80 + "\n")

def print_positions(results):
    print(Fore.CYAN + Style.BRIGHT + "  YOUR POSITIONS")
    print(Fore.CYAN + "─"*80)
    total_value = 0.0; total_cost = 0.0
    for ticker, pos in POSITIONS.items():
        r     = next((x for x in results if x.get("ticker") == ticker), None)
        price = r["price"] if r and "price" in r else None
        if price:
            value    = price * pos["shares"]
            cost     = pos["avg_cost"] * pos["shares"]
            gl       = value - cost; gl_pct = (gl / cost) * 100
            total_value += value; total_cost += cost
            gl_color = Fore.GREEN if gl >= 0 else Fore.RED
            intraday_str = ""
            if r and r.get("intraday_pct") is not None:
                ip       = r["intraday_pct"]
                id_total = r["intraday_dollar"] * pos["shares"]
                intraday_str = "  " + (Fore.GREEN if ip >= 0 else Fore.RED) + f"today: {ip:+.2f}% (${id_total:+.2f})"
            streak_str = ""
            if r and r.get("streak") and r["streak"] >= 2:
                streak_str = Fore.WHITE + f"  [{r['action']} x{r['streak']}d]"
            stale_str = Fore.YELLOW + " [prev NAV]" if r and r.get("fxaix_stale") else ""
            print(Fore.WHITE + f"  {ticker:<6}  {pos['shares']} shares  avg ${pos['avg_cost']:.2f}  now ${price:.2f}  "
                  + gl_color + f"overall: {gl:+.2f} ({gl_pct:+.1f}%)"
                  + intraday_str + streak_str + stale_str)
    total_gl     = total_value - total_cost
    total_gl_pct = (total_gl / total_cost * 100) if total_cost else 0
    total_color  = Fore.GREEN if total_gl >= 0 else Fore.RED
    print(Fore.CYAN + "─"*80)
    print(Fore.WHITE + f"  Total value: ${total_value:.2f}  " + total_color + f"P&L: {total_gl:+.2f} ({total_gl_pct:+.1f}%)")
    print(Fore.CYAN + "─"*80 + "\n")

# ── Option 4 — Manual trade logger ────────────────────────────

def option4_log_trade():
    os.system("cls")
    print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*55)
    print(Fore.CYAN + Style.BRIGHT + "  LOG A TRADE MANUALLY")
    print(Fore.CYAN + "═"*55)

    used = trades_this_month()
    left = MAX_TRADES - used
    cap_color = Fore.RED if left <= 5 else Fore.YELLOW
    print(Fore.WHITE + f"\n  Trades this month: " + cap_color + f"{used}/{MAX_TRADES}  ({left} remaining)\n")

    print(Fore.WHITE + "  Available tickers: " + ", ".join(TICKERS))
    try:
        ticker = input(Fore.WHITE + "\n  Ticker: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return
    if ticker not in TICKERS:
        print(Fore.RED + f"  '{ticker}' not in ticker list. Aborting.")
        time.sleep(2); return

    print(Fore.WHITE + "\n  Signal type:")
    print(Fore.GREEN  + Style.BRIGHT + "  1  →  Strong Buy")
    print(Fore.GREEN  +                "  2  →  Buy")
    print(Fore.MAGENTA+                "  3  →  Reduce Position")
    print(Fore.RED    + Style.BRIGHT + "  4  →  Strong Sell")
    print(Fore.RED    +                "  5  →  Sell")
    try:
        sig_choice = input(Fore.WHITE + "\n  Choice: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if sig_choice not in ALL_SIGNALS:
        print(Fore.RED + "  Invalid choice. Aborting.")
        time.sleep(2); return
    action = ALL_SIGNALS[sig_choice]

    try:
        raw_price = input(Fore.WHITE + f"\n  Price you paid/sold at (ENTER for current price): ").strip()
        if raw_price == "":
            if ticker in MUTUAL_FUNDS:
                price, stale = get_fxaix_price()
                if stale:
                    print(Fore.YELLOW + f"  Using last known FXAIX NAV: ${price:.2f} (today's NAV not yet available)")
                else:
                    print(Fore.CYAN + f"  Using current FXAIX NAV: ${price:.2f}")
            else:
                df    = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
                price = round(float(df["Close"].iloc[-1]), 2)
                print(Fore.CYAN + f"  Using current price: ${price:.2f}")
        else:
            price = round(float(raw_price), 2)
    except (EOFError, KeyboardInterrupt):
        return
    except ValueError:
        print(Fore.RED + "  Invalid price. Aborting.")
        time.sleep(2); return

    try:
        notes  = input(Fore.WHITE + f"\n  Notes (optional, ENTER to skip): ").strip()
        reason = notes if notes else f"Manual entry — {action}"
    except (EOFError, KeyboardInterrupt):
        reason = f"Manual entry — {action}"

    print()
    print(Fore.CYAN + "─"*55)
    color = Fore.GREEN if action in ACT_BUY_SIGS else Fore.RED
    print(color + Style.BRIGHT + f"  {action}  {ticker}  @ ${price:.2f}")
    print(Fore.WHITE + f"  Notes: {reason}")
    print(Fore.CYAN + "─"*55)
    try:
        confirm = input(Fore.WHITE + "\n  Confirm? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if confirm != "y":
        print(Fore.YELLOW + "  Cancelled.")
        time.sleep(1); return

    log_trade(ticker, action, price, reason)
    used += 1
    print(Fore.GREEN + Style.BRIGHT + f"\n  ✓ Trade logged. {used}/{MAX_TRADES} trades this month.")

    emoji  = ("🚀" if action == SIG_STRONG_BUY else "📈" if action == SIG_BUY else
              "⚠️" if action == SIG_REDUCE else "🔴" if action == SIG_STRONG_SELL else "📉")
    fxaix_note = "\n⚠️ FXAIX fills at EOD NAV — confirm execution in Fidelity" if ticker in MUTUAL_FUNDS else ""
    msg = (
        f"{emoji} <b>Trade Logged</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Action: <b>{action}</b>\n"
        f"📊 Ticker: <b>{ticker}</b>\n"
        f"💰 Price:  <b>${price:.2f}</b>\n"
        f"📝 Notes:  {reason}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗂 {used}/{MAX_TRADES} trades used this month{fxaix_note}"
    )
    send_telegram(msg)
    print(Fore.GREEN + "  ✓ Sent to Telegram.")
    time.sleep(2)

# ── Option 5 — Manage / Edit / Delete trades ──────────────────

def option5_manage_trades():
    while True:
        os.system("cls")
        log    = load_log()
        trades = log.get("trades", [])

        print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*72)
        print(Fore.CYAN + Style.BRIGHT + "  MANAGE TRADES")
        print(Fore.CYAN + "═"*72)

        if not trades:
            print(Fore.YELLOW + "\n  No trades logged yet.\n")
            input(Fore.WHITE + "  Press ENTER to return to menu...")
            return

        sorted_trades = sorted(enumerate(trades), key=lambda x: x[1]["timestamp"], reverse=True)
        print(Fore.WHITE + Style.BRIGHT +
              f"\n  {'#':<5} {'DATE':<22} {'TICKER':<8} {'ACTION':<22} {'PRICE':<10} NOTES")
        print("  " + "─"*72)
        for display_i, (orig_i, t) in enumerate(sorted_trades, 1):
            dt = datetime.datetime.fromisoformat(t["timestamp"]).strftime("%Y-%m-%d %H:%M ET")
            ac = Fore.GREEN if t["action"] in ACT_BUY_SIGS else Fore.RED
            print(f"  {Fore.WHITE}{display_i:<5} {dt:<22} {t['ticker']:<8} {ac}{t['action']:<22}"
                  f" {Fore.WHITE}${t['price']:<9.2f} {t['reason'][:28]}")

        print(Fore.CYAN + "\n" + "─"*72)
        print(Fore.WHITE + "  Enter trade # to edit/delete, or 0 to go back.")
        try:
            choice = input(Fore.WHITE + "\n  Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if choice == "0": return

        try:
            display_num = int(choice)
            if display_num < 1 or display_num > len(sorted_trades):
                print(Fore.RED + "  Invalid number."); time.sleep(1); continue
        except ValueError:
            print(Fore.RED + "  Invalid input."); time.sleep(1); continue

        orig_i, trade = sorted_trades[display_num - 1]

        os.system("cls")
        print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*55)
        print(Fore.CYAN + Style.BRIGHT + "  EDIT / DELETE TRADE")
        print(Fore.CYAN + "═"*55)
        dt = datetime.datetime.fromisoformat(trade["timestamp"]).strftime("%Y-%m-%d %H:%M ET")
        ac = Fore.GREEN if trade["action"] in ACT_BUY_SIGS else Fore.RED
        print(Fore.WHITE + f"\n  Date:    {dt}")
        print(Fore.WHITE + f"  Ticker:  {trade['ticker']}")
        print(ac + Style.BRIGHT + f"  Action:  {trade['action']}")
        print(Fore.WHITE + f"  Price:   ${trade['price']:.2f}")
        print(Fore.WHITE + f"  Notes:   {trade['reason']}")
        print(Fore.CYAN + "\n" + "─"*55)
        print(Fore.WHITE + "  1  →  Edit this trade")
        print(Fore.WHITE + "  2  →  Delete this trade")
        print(Fore.WHITE + "  0  →  Back")
        print(Fore.CYAN + "─"*55)

        try:
            action_choice = input(Fore.WHITE + "\n  Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            continue

        if action_choice == "0":
            continue
        elif action_choice == "2":
            try:
                confirm = input(Fore.RED + "\n  Delete this trade? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                continue
            if confirm == "y":
                log["trades"].pop(orig_i)
                save_log(log)
                print(Fore.GREEN + Style.BRIGHT + "\n  ✓ Trade deleted.")
                time.sleep(1.5)
            else:
                print(Fore.YELLOW + "  Cancelled."); time.sleep(1)
        elif action_choice == "1":
            os.system("cls")
            print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*55)
            print(Fore.CYAN + Style.BRIGHT + "  EDIT TRADE  (ENTER to keep current value)")
            print(Fore.CYAN + "═"*55 + "\n")

            print(Fore.WHITE + "  Available tickers: " + ", ".join(TICKERS))
            try:
                new_ticker = input(Fore.WHITE + f"  Ticker [{trade['ticker']}]: ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                continue
            if new_ticker == "":
                new_ticker = trade["ticker"]
            elif new_ticker not in TICKERS:
                print(Fore.RED + f"  '{new_ticker}' not in ticker list. Keeping original.")
                time.sleep(1.5); new_ticker = trade["ticker"]

            print(Fore.WHITE + "\n  Signal type:")
            print(Fore.GREEN  + Style.BRIGHT + "  1  →  Strong Buy")
            print(Fore.GREEN  +                "  2  →  Buy")
            print(Fore.MAGENTA+                "  3  →  Reduce Position")
            print(Fore.RED    + Style.BRIGHT + "  4  →  Strong Sell")
            print(Fore.RED    +                "  5  →  Sell")
            try:
                sig_choice = input(Fore.WHITE + f"\n  Signal [{trade['action']}] (ENTER to keep): ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if sig_choice == "":
                new_action = trade["action"]
            elif sig_choice in ALL_SIGNALS:
                new_action = ALL_SIGNALS[sig_choice]
            else:
                print(Fore.RED + "  Invalid choice. Keeping original."); time.sleep(1.5)
                new_action = trade["action"]

            try:
                raw_price = input(Fore.WHITE + f"\n  Price [${trade['price']:.2f}] (ENTER to keep): ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if raw_price == "":
                new_price = trade["price"]
            else:
                try:
                    new_price = round(float(raw_price), 2)
                except ValueError:
                    print(Fore.RED + "  Invalid price. Keeping original."); time.sleep(1.5)
                    new_price = trade["price"]

            try:
                new_notes = input(Fore.WHITE + f"\n  Notes [{trade['reason']}] (ENTER to keep): ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if new_notes == "": new_notes = trade["reason"]

            print()
            print(Fore.CYAN + "─"*55)
            ac2 = Fore.GREEN if new_action in ACT_BUY_SIGS else Fore.RED
            print(ac2 + Style.BRIGHT + f"  {new_action}  {new_ticker}  @ ${new_price:.2f}")
            print(Fore.WHITE + f"  Notes: {new_notes}")
            print(Fore.CYAN + "─"*55)
            try:
                confirm = input(Fore.WHITE + "\n  Save changes? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                continue
            if confirm == "y":
                log["trades"][orig_i]["ticker"] = new_ticker
                log["trades"][orig_i]["action"] = new_action
                log["trades"][orig_i]["price"]  = new_price
                log["trades"][orig_i]["reason"] = new_notes
                save_log(log)
                print(Fore.GREEN + Style.BRIGHT + "\n  ✓ Trade updated.")
                time.sleep(1.5)
            else:
                print(Fore.YELLOW + "  Cancelled."); time.sleep(1)
        else:
            print(Fore.RED + "  Invalid choice."); time.sleep(1)

# ── Option 6 — View all signals fired ─────────────────────────

def option6_view_signals():
    os.system("cls")

    if after_4pm_et():
        resolve_fxaix_perf()

    perf = load_perf()
    sigs = perf.get("signals", [])

    print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*88)
    print(Fore.CYAN + Style.BRIGHT + "  ALL SIGNALS FIRED")
    print(Fore.CYAN + "═"*88)

    if not sigs:
        print(Fore.YELLOW + "\n  No signals logged yet.\n")
        input(Fore.WHITE + "  Press ENTER to return to menu...")
        return

    wins     = sum(1 for s in sigs if s.get("outcome") == "WIN")
    losses   = sum(1 for s in sigs if s.get("outcome") == "LOSS")
    pending  = sum(1 for s in sigs if s.get("outcome") is None)
    resolved = wins + losses
    win_rate = (wins / resolved * 100) if resolved else 0

    print(Fore.WHITE + f"\n  Total: {len(sigs)}   "
          + Fore.GREEN + f"{wins}W " + Fore.RED + f"{losses}L  "
          + Fore.YELLOW + f"{pending} pending  "
          + Fore.WHITE + f"  Win rate: {win_rate:.1f}%")
    print(Fore.YELLOW + "  Note: All signals resolve 3 days after firing\n")

    print(Fore.WHITE + Style.BRIGHT +
          f"  {'DATE':<20} {'TICKER':<8} {'ACTION':<16} {'PRICE':<10} {'OUTCOME':<10} {'RESULT':<14}")
    print("  " + "─"*84)

    for s in sorted(sigs, key=lambda x: x["timestamp"], reverse=True):
        dt = datetime.datetime.fromisoformat(s["timestamp"]).strftime("%Y-%m-%d %H:%M")
        ac = Fore.GREEN if s["action"] in ACT_BUY_SIGS else Fore.RED if s["action"] in ACT_SELL_SIGS else Fore.YELLOW

        if s.get("outcome") == "WIN":
            outcome_str = Fore.GREEN + "WIN"
            result_str  = Fore.GREEN + f"{s.get('result_dollar',0):+.2f} ({s.get('result_pct',0):+.1f}%)"
        elif s.get("outcome") == "LOSS":
            outcome_str = Fore.RED + "LOSS"
            result_str  = Fore.RED + f"{s.get('result_dollar',0):+.2f} ({s.get('result_pct',0):+.1f}%)"
        else:
            if s["ticker"] == "FXAIX":
                sig_dt   = datetime.datetime.fromisoformat(s["timestamp"])
                days_old = (datetime.datetime.now(ET) - sig_dt).days
                days_left = max(0, 3 - days_old)
                outcome_str = Fore.YELLOW + f"~{days_left}d left"
            else:
                outcome_str = Fore.YELLOW + "pending"
            result_str = Fore.WHITE + "—"

        print(f"  {Fore.WHITE}{dt:<20} {s['ticker']:<8} {ac}{s['action']:<16}"
              f" {Fore.WHITE}${s['price']:<9.2f} {outcome_str:<19} {result_str}")

    print(Fore.CYAN + "\n" + "═"*88 + "\n")
    input(Fore.WHITE + "  Press ENTER to return to menu...")

# ── Option 1 — Run bot ─────────────────────────────────────────

def option1_run_bot():
    last_signals    = {}
    daily_signals   = {}
    trade_warn_sent = False
    eod_prices      = {}

    while True:
        now_et        = datetime.datetime.now(ET)
        open_, status = is_open()
        used          = trades_this_month()
        close_time    = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
        open_time     = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
        weekly_time   = now_et.replace(hour=17, minute=0,  second=0, microsecond=0)

        if now_et.weekday() == 6 and now_et >= weekly_time and not weekly_already_sent():
            send_weekly_summary(); mark_weekly_sent()

        last_day = calendar.monthrange(now_et.year, now_et.month)[1]
        if (now_et.day == last_day and not open_ and "Closed" in status and
                not _is_today(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monthly_log.json"))):
            send_monthly_report()
            _write_date_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monthly_log.json"))

        if open_ and now_et >= open_time and now_et.weekday() < 5 and not morning_already_sent():
            send_morning_brief(); mark_morning_sent()

        left = MAX_TRADES - used
        if left <= 5 and not trade_warn_sent:
            msg = (
                f"⚠️ <b>Trade Cap Warning</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Used <b>{used}/{MAX_TRADES}</b> trades this month\n"
                f"Only <b>{left} remaining</b> — use them carefully"
            )
            send_telegram(msg)
            trade_warn_sent = True

        if (not open_ and "Closed" in status and
                now_et.weekday() < 5 and now_et >= close_time and
                not eod_already_sent() and not is_market_holiday()):
            resolve_fxaix_perf()
            fxaix_price, fxaix_stale = get_fxaix_price()
            if fxaix_price and not fxaix_stale:
                eod_prices["FXAIX"] = fxaix_price
            resolve_perf(eod_prices)
            send_eod_summary(daily_signals, trades_today(), eod_prices)
            mark_eod_sent()

        if not open_:
            os.system("cls")
            print(Fore.CYAN + Style.BRIGHT + "═"*60)
            print(Fore.CYAN + f"  SIGNAL BOT  │  " + Fore.RED + status)
            print(Fore.CYAN + "═"*60)
            print(Fore.WHITE + f"  Trades this month: {used}/{MAX_TRADES}  ({left} left)")
            if eod_already_sent():     print(Fore.GREEN + "  ✓ EOD summary sent today.")
            if weekly_already_sent():  print(Fore.GREEN + "  ✓ Weekly summary sent.")
            if morning_already_sent(): print(Fore.GREEN + "  ✓ Morning brief sent today.")
            if after_4pm_et() and not is_market_holiday():
                fxaix_price, fxaix_stale = get_fxaix_price()
                if fxaix_price and not fxaix_stale:
                    print(Fore.GREEN + f"  ✓ FXAIX NAV updated: ${fxaix_price:.2f}")
                else:
                    print(Fore.YELLOW + "  ⏳ FXAIX NAV not yet posted — checking each cycle.")
            print(Fore.WHITE + "  Waiting for market open... (Ctrl+C to go back)\n")
            time.sleep(60)
            continue

        market_condition, spy_open, spy_now = get_market_condition()
        spy_chg = ((spy_now - spy_open) / spy_now * 100) if spy_open and spy_now else None

        header(used, market_condition, spy_chg)
        print(Fore.WHITE + "  Scanning: " + ", ".join(TICKERS) + " ...\n")
        results = [r for t in TICKERS for r in [analyze(t, market_condition)] if r]

        for r in results:
            if "price" in r: eod_prices[r["ticker"]] = r["price"]
        if spy_now:  eod_prices["SPY"]      = spy_now
        if spy_open: eod_prices["SPY_OPEN"] = spy_open

        header(used, market_condition, spy_chg)
        print_positions(results)

        for i, r in enumerate(results, 1):
            if "error" in r:
                print(Fore.RED + f"  {i}. [{r['ticker']}] ERROR: {r['error']}"); continue
            v          = Fore.YELLOW + " ▲" if r["vsurge"] else ""
            pos_tag    = Fore.CYAN + " [HOLDING]" if r.get("in_position") else ""
            streak_tag = Fore.WHITE + f" x{r['streak']}d" if r.get("streak") and r["streak"] >= 2 else ""
            conf_str   = f" ({r['conf']})" if r["conf"] != "—" else ""
            stale_tag  = Fore.YELLOW + " [prev NAV]" if r.get("fxaix_stale") else ""

            print(Fore.WHITE + Style.BRIGHT + f"  {i}. {r['ticker']:<6}" +
                  Style.RESET_ALL +
                  f"  ${r['price']:<8.2f}  {cp(r['pct']):<18}  RSI:{cr(r['rsi']):<14}  "
                  f"{ca(r['action']):<30}{conf_str}{v}{pos_tag}{streak_tag}{stale_tag}")
            print(Fore.WHITE + f"       → {r['reason']}")

            if r.get("target") and r["action"] in (*ACT_BUY_SIGS, SIG_REDUCE, SIG_STRONG_SELL):
                rr_str  = f"  R/R: {r['rr']:.1f}x" if r.get("rr") else ""
                atr_str = f"  ATR: {r['atr']}" if r.get("atr") else ""
                print(Fore.WHITE + f"       🎯 Target: ${r['target']:.2f}  🛑 Stop: ${r['stop_loss']:.2f}{rr_str}{atr_str}")

            if r.get("news"):
                for headline in r["news"]:
                    print(Fore.WHITE + f"       📰 {headline[:70]}")
            print()

            prev_action = last_signals.get(r["ticker"])
            if r["action"] in ALL_ACT and r["action"] != prev_action:
                emoji = ("🚀" if r["action"] == SIG_STRONG_BUY else "📈" if r["action"] == SIG_BUY else
                         "⚠️" if r["action"] == SIG_REDUCE else "🔴" if r["action"] == SIG_STRONG_SELL else "📉")
                pos_line = ""
                if r.get("in_position"):
                    gl = (r["price"] - r["avg_cost"]) * r["shares"]
                    id_str = ""
                    if r.get("intraday_dollar") is not None:
                        id_total = r["intraday_dollar"] * r["shares"]
                        id_str   = f"\n📉 Today: {r['intraday_pct']:+.2f}% (${id_total:+.2f})"
                    pos_line = (f"\n━━━━━━━━━━━━━━━━━━━━━"
                                f"\n💼 Holding {r['shares']} shares · avg ${r['avg_cost']:.2f}"
                                f"\n📊 Overall P&L: <b>{gl:+.2f}</b>{id_str}")
                target_line = ""
                if r.get("target"):
                    target_line = (f"\n━━━━━━━━━━━━━━━━━━━━━"
                                   f"\n🎯 Target: <b>${r['target']:.2f}</b>  🛑 Stop: <b>${r['stop_loss']:.2f}</b>"
                                   f"  R/R: <b>{r['rr']:.1f}x</b>  ATR: {r['atr']}")
                streak_line = f"\n🔥 Signal streak: <b>{r['streak']} days</b>" if r.get("streak") and r["streak"] >= 2 else ""
                news_line   = "\n📰 <b>News:</b>\n" + "\n".join(f"• {h}" for h in r["news"]) if r.get("news") else ""
                stale_line  = "\n⚠️ FXAIX prev NAV — today's updates after 4 PM ET\n⏳ Resolves in 3 days" if r.get("fxaix_stale") else ""
                mkt_line    = f"\n📈 Market: <b>{market_condition.upper()}</b>"

                # Telegram — full text format with positions
                msg = (
                    f"{emoji} <b>{r['action']} — {r['ticker']}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Price:      <b>${r['price']:.2f}</b>\n"
                    f"📊 RSI:        <b>{r['rsi']}</b>\n"
                    f"🎯 Confidence: <b>{r['conf']}</b>\n"
                    f"📋 Reason:     {r['reason']}"
                    f"{mkt_line}{pos_line}{target_line}{streak_line}{news_line}{stale_line}"
                )
                send_telegram(msg)

                # Discord — embed with signals only (no positions)
                send_discord_signal_embed(
                    ticker=r["ticker"],
                    action=r["action"],
                    price=r["price"],
                    rsi_val=r["rsi"],
                    confidence=r["conf"],
                    reason=r["reason"],
                    role_ping=DISCORD_ROLE_INDEX,
                    target=r.get("target"),
                    stop_loss=r.get("stop_loss"),
                    rr=r.get("rr"),
                    streak=r.get("streak"),
                    market_condition=market_condition,
                    news=r.get("news"),
                    fxaix_stale=r.get("fxaix_stale", False),
                )

                log_signal_perf(r["ticker"], r["action"], r["price"])
                last_signals[r["ticker"]] = r["action"]
                if r["ticker"] not in daily_signals: daily_signals[r["ticker"]] = []
                daily_signals[r["ticker"]].append(f"{r['action']} @ ${r['price']:.2f}")
            elif r["action"] == SIG_HOLD:
                last_signals[r["ticker"]] = None

        actionable = [r for r in results if r.get("action") in ALL_ACT]
        if actionable:
            print(Fore.CYAN + "─"*80)
            print(Fore.CYAN + Style.BRIGHT + "  ACTION REQUIRED — enter in Fidelity manually")
            print(Fore.CYAN + Style.BRIGHT + "  Only act on STRONG BUY / STRONG SELL for tax efficiency")
            print(Fore.CYAN + "─"*80)
            left_trades = MAX_TRADES - used
            for r in actionable:
                if left_trades <= 0: print(Fore.RED + "  ⚠ TRADE LIMIT REACHED"); break
                color    = Fore.GREEN if r["action"] in ACT_BUY_SIGS else Fore.RED
                priority = " ◄ ACT" if r["action"] in (SIG_STRONG_BUY, SIG_STRONG_SELL) else ""
                print(color + Style.BRIGHT + f"  ► {r['action']:<20} {r['ticker']}  @ ${r['price']:.2f}{priority}")
                left_trades -= 1
            print()
            print(Fore.WHITE + "  To log a trade go to menu option 4 anytime.")
            print(Fore.WHITE + f"  Refreshing in {REFRESH}s — Ctrl+C to return to menu")
        else:
            print(Fore.WHITE + f"  Refreshing in {REFRESH}s — Ctrl+C to return to menu")

        time.sleep(REFRESH)

# ── Option 2 — View trade history ─────────────────────────────

def option2_view_trades():
    os.system("cls")
    log    = load_log()
    trades = log.get("trades", [])
    now    = datetime.datetime.now(ET)
    mstart = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    mt     = [t for t in trades if datetime.datetime.fromisoformat(t["timestamp"]) >= mstart]

    print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*72)
    print(Fore.CYAN + Style.BRIGHT + "  TRADE HISTORY")
    print(Fore.CYAN + "═"*72)

    if not trades:
        print(Fore.YELLOW + "\n  No trades logged yet.\n")
    else:
        print(Fore.YELLOW + f"\n  This month: {len(mt)}/{MAX_TRADES} trades used\n")
        print(Fore.WHITE + Style.BRIGHT +
              f"  {'DATE':<22} {'TICKER':<8} {'ACTION':<22} {'PRICE':<10} NOTES")
        print("  " + "─"*72)
        for t in sorted(trades, key=lambda x: x["timestamp"], reverse=True):
            dt = datetime.datetime.fromisoformat(t["timestamp"]).strftime("%Y-%m-%d %H:%M ET")
            ac = Fore.GREEN if t["action"] in ACT_BUY_SIGS else Fore.RED
            print(f"  {Fore.WHITE}{dt:<22} {t['ticker']:<8} {ac}{t['action']:<22}"
                  f" {Fore.WHITE}${t['price']:<9.2f} {t['reason'][:28]}")

    print(Fore.CYAN + "\n" + "═"*72 + "\n")
    input(Fore.WHITE + "  Press ENTER to return to menu...")

# ── Option 3 — Strategy guide ──────────────────────────────────

def option3_about():
    os.system("cls")
    print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*72)
    print(Fore.CYAN + Style.BRIGHT + "  STRATEGY GUIDE")
    print(Fore.CYAN + "═"*72)
    lines = [
        ("Tickers",             "FXAIX, QQQM, VXUS, SCHD, VTI, VOO"),
        ("FXAIX (Mutual Fund)", "NAV updates after 4 PM ET. Bot uses last known price and marks it [prev NAV] until updated."),
        ("Signal Resolve",       "All signals resolve 3 days after firing — you get a Telegram notification with WIN/LOSS when resolved."),
        ("FXAIX Streak",        "Only updates after 4 PM ET when NAV is confirmed. Won't inflate from 60s scan cycles."),
        ("Signal Levels",       "Strong Buy (3+) → Buy (2) → Watch (1) → Hold (0) → Reduce (2 sells, holding) → Strong Sell (3+ sells, holding)"),
        ("Tax note",            "Only act on Strong Buy and Strong Sell to minimize taxable events."),
        ("Confidence",          "0-100 score based on signal count, RSI extremity, and volume."),
        ("RSI",                 "Below 30 = oversold → buy. Above 70 = overbought → sell."),
        ("MACD",                "Fast line crosses above slow = buy. Below = sell."),
        ("EMA Cross",           "EMA12 above EMA26 = golden cross. Below = death cross."),
        ("Bollinger",           "Price below lower band = bounce. Above upper = reversal."),
        ("ATR Stop Loss",       "Stop = price minus 1.5x ATR. Adjusts per ticker volatility."),
        ("Price Target",        "Resistance from last 20 trading days."),
        ("News",                "2 headlines per ticker on screen and in Telegram on signal."),
        ("Streak",              "Days in a row a ticker holds the same signal."),
        ("Market",              "SPY down 1.5%+ = bearish. Strong Buy and Buy suppressed."),
        ("Holidays",            "Bot detects all US market holidays and shows Market Holiday status. EOD skipped on holidays."),
        ("Morning Brief",       "Telegram only — once per day at market open."),
        ("EOD Summary",         "Telegram only — once per day at 4 PM, skipped on holidays."),
        ("Weekly",              "Telegram only — Sunday 5 PM, once per week."),
        ("Monthly",             "Telegram only — last trading day, once per month."),
        ("Discord Signals",     "Live signals fire to Discord with embeds, role ping, signal data only."),
        ("Log a Trade",         "Menu option 4 anytime — log any trade with full signal types."),
        ("Manage Trades",       "Menu option 5 — edit or delete any logged trade."),
        ("View Signals",        "Menu option 6 — see every signal fired. FXAIX shows days remaining until resolve."),
    ]
    for title, desc in lines:
        print(Fore.YELLOW + Style.BRIGHT + f"\n  {title}")
        print(Fore.WHITE  + f"  {desc}")
    print(Fore.CYAN + "\n\n" + "═"*72 + "\n")
    input(Fore.WHITE + "  Press ENTER to return to menu...")

# ── Main menu ──────────────────────────────────────────────────

def main():
    while True:
        os.system("cls")
        used          = trades_this_month()
        open_, status = is_open()
        status_color  = Fore.GREEN if open_ else Fore.RED
        left          = MAX_TRADES - used
        brief_status  = Fore.GREEN + "✓ sent today" if morning_already_sent() else Fore.YELLOW + "not yet sent"
        eod_status    = Fore.GREEN + "✓ sent today" if eod_already_sent()     else Fore.YELLOW + "not yet sent"

        fxaix_nav_str = ""
        if after_4pm_et() and not is_market_holiday():
            fxaix_price, fxaix_stale = get_fxaix_price()
            if fxaix_price and not fxaix_stale:
                fxaix_nav_str = Fore.GREEN + f"✓ NAV: ${fxaix_price:.2f}"
            else:
                fxaix_nav_str = Fore.YELLOW + "⏳ NAV pending..."

        print(Fore.CYAN + Style.BRIGHT + "\n" + "═"*50)
        print(Fore.CYAN + Style.BRIGHT + "  INDEX FUND SIGNAL BOT")
        print(Fore.CYAN + "═"*50)
        print(Fore.WHITE + f"  Market:  {status_color}{status}")
        print(Fore.WHITE + f"  Trades:  " +
              (Fore.RED if left<=5 else Fore.YELLOW) +
              f"{used}/{MAX_TRADES}  ({left} remaining this month)")
        print(Fore.WHITE + f"  Brief:   {brief_status}")
        print(Fore.WHITE + f"  EOD:     {eod_status}")
        if fxaix_nav_str:
            print(Fore.WHITE + f"  FXAIX:   {fxaix_nav_str}")
        print(Fore.CYAN + "─"*50)
        print(Fore.WHITE + "\n  1  →  Run live signal scanner")
        print(Fore.WHITE + "  2  →  View trade history")
        print(Fore.WHITE + "  3  →  Strategy guide / about")
        print(Fore.WHITE + "  4  →  Log a trade manually")
        print(Fore.WHITE + "  5  →  Manage / edit trades")
        print(Fore.WHITE + "  6  →  View all signals fired")
        print(Fore.WHITE + "  0  →  Exit\n")
        print(Fore.CYAN + "─"*50)

        try:
            choice = input(Fore.WHITE + "  Enter choice: ").strip()
        except (KeyboardInterrupt, EOFError):
            print(Fore.YELLOW + "\n  Goodbye.\n"); break

        if   choice == "1":
            try: option1_run_bot()
            except KeyboardInterrupt: pass
        elif choice == "2": option2_view_trades()
        elif choice == "3": option3_about()
        elif choice == "4": option4_log_trade()
        elif choice == "5": option5_manage_trades()
        elif choice == "6": option6_view_signals()
        elif choice == "0": print(Fore.YELLOW + "\n  Goodbye.\n"); break
        else: print(Fore.RED + "  Invalid. Try again."); time.sleep(1)

if __name__ == "__main__":
    try:
        option1_run_bot()
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n  Bot stopped.\n")