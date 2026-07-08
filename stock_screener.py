"""
STOCK SCREENER - AUTOMATED VERSION
Top 100 most traded stocks
Criteria: RSI, MACD, EMA Crossover, Bollinger Bands, ATR
Posts signals (BUY/SELL/SHORT) to Discord paid tier with embeds
Automatically runs every minute + scheduled strategy posts
"""

import os, json, time, datetime, pytz, warnings
import numpy as np
import urllib.request
import pandas as pd
import logging
from apscheduler.schedulers.background import BackgroundScheduler

# Suppress yfinance warnings
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.filterwarnings('ignore')

try:
    import yfinance as yf
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    print("Installing required packages...")
    os.system("py -m pip install yfinance colorama pytz numpy pandas apscheduler")
    import yfinance as yf
    from colorama import Fore, Style, init
    init(autoreset=True)

ET = pytz.timezone("US/Eastern")
RESULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_screener_results.json")
SENT_SIGNALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_signals.json")

# ── DISCORD WEBHOOKS & ROLES ──────────────────────────────────
# Old webhook: stock signals channel (ALL signals posted here)
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1518717001944662228/-1-X8RrxSqD0oa_jchwd2_TUUxTHHfdNmfnlnLdcL8odg_1M3xrR93HZMHIyqFKNpncW"

# New webhook: strategy-discussion channel (market context, educational, discussion)
DISCORD_WEBHOOK_STRATEGY = "https://discord.com/api/webhooks/1524476771217572041/4DENpk8Z2YqOuFAz88hbsCfVhhG0kqUsH_pxKKZND7AFXNwtK8hscJi0gTvUNcaqIqO"

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
        "ISRG", "TXN", "CMG", "ADBE", "QCOM", "MU", "NOW", "BKKING", "AMGN", "GILD",
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
        
        df = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=True)
        
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

# ── Post to Strategy Discussion Channel ────────────────────────

def post_to_strategy_channel(title, content, color=15105570):
    """Send an embed to the strategy-discussion channel"""
    if not DISCORD_WEBHOOK_STRATEGY:
        return False
    
    try:
        embed = {
            "title": title,
            "description": content,
            "color": color,
        }

        payload = json.dumps({
            "embeds": [embed],
        }).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_STRATEGY,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            }
        )
        urllib.request.urlopen(req, timeout=10)
        time.sleep(0.3)
        return True

    except Exception as e:
        print(Fore.RED + f"[STRATEGY POST FAILED: {e}]")
        return False

def post_market_context():
    """Post detailed pre-market context to strategy channel"""
    try:
        now = datetime.datetime.now(ET)
        day_name = now.strftime("%A")
        
        market_context = f"""
**🕐 Market Opens: 9:30 AM ET**

**📊 KEY LEVELS TO WATCH:**
• **SPY** - Watch 600 support / 610 resistance
• **QQQ** - Tech momentum plays / watch 500 level
• **IWM** - Small cap strength indicator

**📈 SECTOR FOCUS:**
• Tech (NVDA, MSFT, TSLA) - Earnings impact
• Financials (JPM, GS) - Rate sensitive
• Healthcare (JNJ, ABBV) - Defensive plays

**🗓️ ECONOMIC DATA:**
• CPI / Jobs reports this week?
• Fed speakers scheduled?
• Earnings season impact

**🎯 SIGNAL VAULT STRATEGY:**
Look for high-conviction setups with 1:2+ risk/reward. Volume surge is key. Don't chase - wait for pullbacks to defined support.

**PREPARE:**
✅ Set your watchlist tonight
✅ Review resistance/support levels
✅ Watch for gap direction at open

Let's make today profitable! 🚀
        """
        ok = post_to_strategy_channel("📈 Pre-Market Brief", market_context.strip(), color=3066993)
        if ok:
            print(Fore.GREEN + f"[{now.strftime('%H:%M')}] Market context posted!")
        return ok
    except Exception as e:
        print(Fore.RED + f"Failed to post market context: {e}")
        return False

def post_educational():
    """Post detailed educational breakdown - rotates through 5 core setups"""
    try:
        now = datetime.datetime.now(ET)
        
        # Rotate through 5 trading setups based on day of week
        day_of_week = now.weekday()
        setups = [
            {
                "title": "🎓 Trading Masterclass: RSI Divergence",
                "content": """
**The Setup That Catches Reversals**

Most traders see RSI < 30 and panic-buy. That's losing money right there.

**Real traders look for DIVERGENCE:**

**Step 1:** Price makes a **lower low** (new support break)
**Step 2:** RSI makes a **higher low** (momentum WEAKENING)
**Step 3:** Enter on the bounce with tight ATR stop

**Why it works:**
The divergence shows bulls stepping in even though price is lower. That's strength hidden in weakness.

**Real Example:**
If AAPL drops to $150 (lower low) but RSI only drops to 28 (higher than previous 22), that's your setup.

**Entry:** Tight stop at $148
**Target:** Previous resistance at $160
**Risk/Reward:** 1:2.5

This is ONE of 5 core setups we trade daily. Get 50+ premium members and you unlock ALL of them with live alerts. 🚀
                """
            },
            {
                "title": "🎓 Trading Masterclass: MACD Crossover",
                "content": """
**The Momentum Setup That Catches Trends**

MACD is the most overlooked indicator. Most people watch it wrong.

**The Real Setup:**

**MACD Bullish Cross:**
• MACD line (12-26) crosses ABOVE signal line (9 EMA)
• Momentum is accelerating UP
• Entry: When MACD crosses + price holds support
• Risk: Break below recent support

**MACD Bearish Cross:**
• MACD line crosses BELOW signal line
• Momentum is dying
• Exit longs / Consider shorts
• Stop: Break of recent resistance

**Why it works:**
MACD measures momentum CHANGE. When it crosses, the direction is shifting. Early birds catch the move.

**Real Setup:**
RSI 40-60 (neutral) + MACD bullish cross = 70+ confidence setup.

**Pro Tip:**
Volume surge on the cross = higher probability. That's why we combine indicators.

This is setup #2 of our 5-part system. Full access in premium tier. 🔥
                """
            },
            {
                "title": "🎓 Trading Masterclass: Bollinger Bands Bounce",
                "content": """
**The Mean Reversion Setup**

Bollinger Bands tell you when price is STRETCHED. That's when reversions happen.

**The Setup:**

**Upper Band Rejection:**
• Price touches or breaks above upper BB
• Look for rejection candle (close below)
• Strong sellers stepping in
• Risk: Break and hold above

**Lower Band Bounce:**
• Price touches lower BB
• Oversold conditions (RSI < 30)
• Entry: Bounce candle with higher low
• Target: 20-period SMA (middle band)

**Why it works:**
Bands measure 2 standard deviations. Price rarely stays there. Reversion is probabilistic.

**Real Example:**
QQQ drops to lower band at $480, RSI is 25, volume surge on bounce. Entry at $482 with stop at $478.

**Key Rule:**
Don't buy the touch. Wait for rejection / bounce confirmation. Entry on the reversal candle, not the extreme.

This is setup #3. Master this and you're beating 80% of retail traders. 💪
                """
            },
            {
                "title": "🎓 Trading Masterclass: EMA Golden Cross",
                "content": """
**The Trend Confirmation Setup**

Golden Cross: Fast EMA (9) crosses above Slow EMA (26). That's trend confirmation.

**The Setup:**

**Golden Cross (Bullish):**
• EMA(9) crosses ABOVE EMA(26)
• Price above both = bullish structure
• Entry: After the cross, on any pullback to EMA(9)
• Hold: Until bearish cross or break of EMA(26)

**Death Cross (Bearish):**
• EMA(9) crosses BELOW EMA(26)
• Price below both = bearish structure
• Exit longs / Consider shorts
• Stop: Break above EMA(26)

**Why it works:**
This measures TREND, not just momentum. When the fast average turns, direction is changing.

**Real Setup:**
Post-earnings stock golden crosses at resistance. Entry on pullback. Target = previous resistance.

**Pro Tip:**
Combine with volume. Golden cross on volume surge = 80+ confidence.

This is setup #4. Use it to trade WITH the trend, not against it. 🎯
                """
            },
            {
                "title": "🎓 Trading Masterclass: Volume Surge Breakout",
                "content": """
**The Breakout Setup That Actually Works**

Volume surge = institutional money moving. That's how you know it's real.

**The Setup:**

**Breakout with Volume Surge:**
• Price breaks resistance
• Volume > 1.3x (20-day average)
• Entry: Tight stop below breakout level
• Target: Next resistance level

**Why volume matters:**
Volume is INTENT. Without it, breakouts fail 60% of the time. With it, they succeed 75%+.

**Real Example:**
TSLA has been holding $250-260 for 10 days. Breaks $260 on volume 3x average. That's a real move.

Entry: $261 with stop at $259 (tight)
Target: Previous high at $285
Risk/Reward: 1:3

**Key Rules:**
✅ Volume ABOVE 1.3x 20-day average
✅ Close ABOVE resistance (not just touch)
✅ Tight stops (1-2% below entry)

This is setup #5. Use volume confirmation and you'll stop chasing fakes. 🚀
                """
            }
        ]
        
        setup = setups[day_of_week % 5]
        ok = post_to_strategy_channel(setup["title"], setup["content"].strip(), color=3066993)
        if ok:
            print(Fore.GREEN + f"[{now.strftime('%H:%M')}] Educational post #{day_of_week % 5 + 1} posted!")
        return ok
    except Exception as e:
        print(Fore.RED + f"Failed to post educational content: {e}")
        return False

def post_discussion_prompt():
    """Post engaging discussion prompt to strategy channel"""
    try:
        now = datetime.datetime.now(ET)
        
        # Different prompts for different days
        day_of_week = now.weekday()
        prompts = [
            {
                "title": "💬 Weekly Discussion: What's Your Best Trade This Week?",
                "content": """
Drop a screenshot or quick story of your best trade (win OR loss).

**We're all learning here:**
✅ Wins build confidence
❌ Losses teach the hardest lessons

**Share:**
• Entry level
• Exit level
• Why you took it
• What you learned

No judgment—only growth. 🚀
                """
            },
            {
                "title": "💬 Trading Talk: What Setup Confuses You Most?",
                "content": """
Is it:
• **RSI Divergence** - When does it work? When does it fail?
• **MACD Signals** - False crosses?
• **Volume** - How much is "enough"?
• **Entry timing** - Too early? Too late?

Drop your confusion below. The community will help break it down. That's how we all level up. 💪
                """
            },
            {
                "title": "💬 Market Check: How Did Your Setups Perform?",
                "content": """
Mid-week reality check:

• Did your watchlist stocks move?
• Any surprise gainers/losers?
• Setups work like you expected?
• What surprised you this week?

Share your observations. Real traders learn from real data. 📊
                """
            },
            {
                "title": "💬 Friday Vibes: Best Trade of the Week?",
                "content": """
It's Friday! Let's celebrate the wins. 🎉

**What was your best trade this week?**
• Biggest % gain?
• Cleanest setup execution?
• Best risk/reward hit?
• Luckiest escape?

Drop screenshots. Share the wins. We're all rooting for each other. 🚀
                """
            },
            {
                "title": "💬 Monday Momentum: What Are You Watching?",
                "content": """
Fresh week. Fresh opportunities. 📈

**What's on your watchlist this week?**
• Earnings plays?
• Technical setups forming?
• Sector rotations you're tracking?
• New stocks catching your eye?

Share your targets. We trade together. Build the list as a community. 🎯
                """
            }
        ]
        
        prompt = prompts[day_of_week % 5]
        ok = post_to_strategy_channel(prompt["title"], prompt["content"].strip(), color=15105570)
        if ok:
            print(Fore.GREEN + f"[{now.strftime('%H:%M')}] Discussion prompt posted!")
        return ok
    except Exception as e:
        print(Fore.RED + f"Failed to post discussion prompt: {e}")
        return False

# ── Main Screener ──────────────────────────────────────────────

def run_screener():
    """Run screener and post only NEW signals to Discord"""
    os.system("cls")
    print(Fore.CYAN + Style.BRIGHT + "\n" + "="*70)
    print(Fore.CYAN + Style.BRIGHT + "  STOCK SCREENER -- TOP 100 MOST TRADED (AUTOMATED)")
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
    else:
        print(Fore.YELLOW + "  No signals found this scan.")

    print(Fore.CYAN + "="*70 + "\n")

# ── Start Scheduler ────────────────────────────────────────────

if __name__ == "__main__":
    print(Fore.CYAN + Style.BRIGHT + "\n" + "="*70)
    print(Fore.CYAN + Style.BRIGHT + "  STOCK SCREENER - AUTOMATED MODE")
    print(Fore.CYAN + "="*70)
    print(Fore.WHITE + "  Running screener every minute...")
    print(Fore.WHITE + "  Scheduled strategy posts:")
    print(Fore.WHITE + "    • 9:25 AM ET - Pre-market brief")
    print(Fore.WHITE + "    • 1:00 PM ET - Daily educational lesson")
    print(Fore.WHITE + "    • 3:00 PM ET - Discussion prompt")
    print(Fore.CYAN + "="*70 + "\n")
    
    # Run screener immediately on startup
    run_screener()
    
    # Create scheduler
    scheduler = BackgroundScheduler()
    
    # Screener: every minute (Monday-Friday, market hours 9:30 AM - 4:00 PM)
    scheduler.add_job(run_screener, 'cron', day_of_week='mon-fri', hour='9-16', minute='*/1')
    
    # Strategy posts: weekdays only
    # Pre-market brief at 9:25 AM ET
    scheduler.add_job(post_market_context, 'cron', day_of_week='mon-fri', hour='9', minute='25')
    
    # Educational post at 1:00 PM ET (mid-day, rotating through 5 setups)
    scheduler.add_job(post_educational, 'cron', day_of_week='mon-fri', hour='13', minute='0')
    
    # Discussion prompt at 3:00 PM ET (end of day, rotating prompts)
    scheduler.add_job(post_discussion_prompt, 'cron', day_of_week='mon-fri', hour='15', minute='0')
    
    scheduler.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
        print(Fore.YELLOW + "\nScreener stopped.")