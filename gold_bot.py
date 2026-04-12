"""
GOLD INTELLIGENCE BOT — FULLY AUTOMATED
========================================
Run once. Runs forever.
Commands: /call /status
"""

import feedparser, json, sys, math, time, threading
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone
from time import mktime

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
OPENROUTER_KEY  = "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"
TELEGRAM_TOKEN  = "8792606097:AAFB4i281pitGuOvC0W7_cELCSijQozNAwY"
TELEGRAM_CHATID = "1308372460"
V7_MACRO_SCORE  = 2.1

DAILY_HOUR      = 6
CHECK_INTERVAL  = 4 * 3600
FLASH_THRESHOLD = 7.0

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════
OR_URL   = "https://openrouter.ai/api/v1/chat/completions"
GOLD_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1h&range=2d"
TG_URL   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

RSS_FEEDS = {
    "FXStreet":  "https://www.fxstreet.com/rss/news",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "Investing": "https://www.investing.com/rss/news_25.rss",
    "Kitco":     "https://www.kitco.com/rss/kitco-news-gold.rss",
}
MAX_PER_FEED   = 5
SOURCE_WEIGHTS = {"Bloomberg":1.0,"Kitco":0.9,"FXStreet":0.7,"Investing":0.5}
HALF_LIVES     = {"SHOCK_EVENT":6,"MONETARY_REGIME":48,"USD_LIQUIDITY":24,
                  "GEOPOLITICAL_RISK":72,"POSITIONING":96,"IRRELEVANT":1}
PREFERRED_MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-26b-a4b-it-20260403:free",
    "google/gemma-3-27b-it:free",
    "google/gemma-3-12b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "nvidia/llama-3.3-nemotron-super-49b-v1:free",
    "qwen/qwen2.5-72b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
]

# pipeline lock — prevents two pipelines running simultaneously
_pipeline_lock = threading.Lock()
_pipeline_busy = False

# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════
def tg_send(text):
    payload = json.dumps({
        "chat_id": TELEGRAM_CHATID,
        "text": text[:4000],
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(
        f"{TG_URL}/sendMessage", data=payload,
        headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [TG send error] {e}")
        return None

def tg_get_updates(offset=None):
    params = {"timeout": 20, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    url = f"{TG_URL}/getUpdates?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.loads(r.read())
    except:
        return {"ok": False, "result": []}

# ═══════════════════════════════════════════════════════════════════════════════
# MODEL LIST
# ═══════════════════════════════════════════════════════════════════════════════
def get_models():
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization":f"Bearer {OPENROUTER_KEY}",
                     "User-Agent":"GoldBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        live    = {m["id"] for m in data.get("data",[])}
        ordered = [m for m in PREFERRED_MODELS if m in live]
        extras  = [m["id"] for m in data.get("data",[])
                   if m["id"].endswith(":free")
                   and m["id"] not in ordered
                   and "thinking" not in m["id"]
                   and not any(x in m["id"] for x in ["1.2b","3b-","2b-"])
                   and m.get("context_length",0) >= 8000]
        return (ordered + extras) or PREFERRED_MODELS
    except:
        return PREFERRED_MODELS

# ═══════════════════════════════════════════════════════════════════════════════
# FETCH
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_headlines():
    articles, now = [], datetime.now(timezone.utc)
    for src, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:MAX_PER_FEED]:
                title   = e.get("title","").strip()
                summary = e.get("summary",e.get("description","")).strip()
                pt      = e.get("published_parsed") or e.get("updated_parsed")
                if pt:
                    dt  = datetime.fromtimestamp(mktime(pt),tz=timezone.utc)
                    age = round((now-dt).total_seconds()/3600,1)
                    pub = dt.strftime("%d %b %H:%M UTC")
                else:
                    age, pub = 99.0, "Unknown"
                if title:
                    articles.append({"source":src,"title":title,
                                     "summary":summary[:300],
                                     "published_str":pub,"age_hours":age})
        except:
            pass
    return articles

# ═══════════════════════════════════════════════════════════════════════════════
# API CALL
# ═══════════════════════════════════════════════════════════════════════════════
def call_api(model, messages, max_tokens=2500):
    payload = json.dumps({
        "model":model,"temperature":0.1,
        "max_tokens":max_tokens,"messages":messages
    }).encode()
    req = urllib.request.Request(OR_URL, data=payload, method="POST",
          headers={"Content-Type":"application/json",
                   "Authorization":f"Bearer {OPENROUTER_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        content = data["choices"][0]["message"]["content"]
        return content, data.get("model",model)
    except urllib.error.HTTPError as e:
        code = e.code
        msg  = e.read().decode()[:100]
        print(f"    ✗ {model.split('/')[-1]} HTTP {code}: {msg}")
        return None, None
    except Exception as e:
        print(f"    ✗ {model.split('/')[-1]}: {e}")
        return None, None

# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFY
# ═══════════════════════════════════════════════════════════════════════════════
def parse_json(raw):
    if not raw: return []
    if "```" in raw:
        for p in raw.split("```"):
            p = p.strip().lstrip("json").strip()
            if p.startswith("["): raw = p; break
    s,e = raw.find("["),raw.rfind("]")
    if s!=-1 and e!=-1: raw=raw[s:e+1]
    elif not raw.startswith("["): raw="["+raw
    if not raw.endswith("]"): raw=raw.rstrip(",")+"]"
    try: return json.loads(raw)
    except: return []

def classify(articles, models):
    lines = "".join(
        f"[{i}] {a['source']} | {a['age_hours']}h\n    {a['title']}\n    {a['summary'][:150] or 'N/A'}\n"
        for i,a in enumerate(articles,1)
    )
    prompt = (
        "Classify each article for GOLD (XAUUSD) impact.\n"
        "Categories: MONETARY_REGIME|USD_LIQUIDITY|GEOPOLITICAL_RISK|POSITIONING|SHOCK_EVENT|IRRELEVANT\n"
        "Return ONLY JSON array. Fields: id(int) category direction(BULLISH/BEARISH/NEUTRAL/N/A) "
        "impact_score(0.0-1.0) reason(cause->effect->gold)\n"
        f"Articles:\n{lines}["
    )
    messages = [
        {"role":"system","content":"Return ONLY valid JSON array starting [ ending ]. No markdown."},
        {"role":"user","content":prompt}
    ]
    for model in models[:8]:
        print(f"    Trying {model.split('/')[-1]}...", end="", flush=True)
        raw, used = call_api(model, messages, max_tokens=2000)
        if raw and raw.strip() not in ("","null"):
            results = parse_json(raw)
            if results:
                print(f" ✓")
                return results, used
            print(" ✗ bad JSON")
        elif raw is not None:
            print(" ✗ empty")
    return [], None

# ═══════════════════════════════════════════════════════════════════════════════
# SCORE + CONVICTION + TARGET
# ═══════════════════════════════════════════════════════════════════════════════
def decay(age, cat):
    return math.exp(-0.693 * age / HALF_LIVES.get(cat,24))

def compute_news_score(articles, classifications):
    cls_map = {c["id"]:c for c in classifications}
    wsum = wtotal = 0.0
    top_arts, cat_scores = [], {}
    for i,a in enumerate(articles,1):
        c = cls_map.get(i)
        if not c: continue
        cat,direc = c.get("category","IRRELEVANT"),c.get("direction","N/A")
        score,age,src = float(c.get("impact_score",0)),float(a.get("age_hours",99)),a.get("source","?")
        if cat=="IRRELEVANT" or direc in ("N/A","NEUTRAL"): continue
        dm=1.0 if direc=="BULLISH" else -1.0
        d=decay(age,cat); sw=SOURCE_WEIGHTS.get(src,0.5); contrib=score*dm*d*sw
        wsum+=contrib; wtotal+=score*d*sw
        cat_scores[cat]=cat_scores.get(cat,0.0)+contrib
        top_arts.append({"source":src,"title":a.get("title",""),
                         "published_str":a.get("published_str",""),
                         "age_hours":age,"category":cat,"direction":direc,
                         "score":score,"contrib":contrib,"reason":c.get("reason","")})
    norm=round(max(-10.0,min(10.0,(wsum/wtotal*10.0) if wtotal>0 else 0.0)),2)
    return norm, top_arts, cat_scores

def fetch_gold():
    try:
        req=urllib.request.Request(GOLD_URL,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=15) as r:
            data=json.loads(r.read())
        q=data["chart"]["result"][0]["indicators"]["quote"][0]
        closes=[x for x in q["close"] if x]
        highs=[x for x in q["high"] if x]
        lows=[x for x in q["low"] if x]
        price=closes[-1]
        ranges=[h-l for h,l in zip(highs[-14:],lows[-14:])]
        atr=sum(ranges)/len(ranges) if ranges else price*0.003
        return round(price,2),round(atr,2)
    except:
        return None,None

def compute_conviction(news_sc, v7, price, atr, cat_scores):
    nd=1 if news_sc>0 else(-1 if news_sc<0 else 0)
    md=1 if v7>0 else(-1 if v7<0 else 0)
    aligned=(nd==md) and nd!=0
    base=(abs(news_sc)/10*0.6)+(abs(v7)/10*0.4)
    conv=base*(1.3 if aligned else(0.8 if nd==0 else 0.4))
    conv=round(min(1.5,conv),3)
    direction=nd if abs(news_sc)>=3.0 else(md if md!=0 else nd)
    em_map={"SHOCK_EVENT":1.8,"GEOPOLITICAL_RISK":1.5,"MONETARY_REGIME":1.3,"USD_LIQUIDITY":1.1,"POSITIONING":1.0}
    dom=max(cat_scores,key=lambda x:abs(cat_scores[x])) if cat_scores else None
    em=em_map.get(dom,1.0)
    tf="2–4 hours" if conv>=1.2 else("6–12 hours" if conv>=0.8 else None)
    target=round(price+(atr*direction*conv*em),1) if(tf and atr) else None
    return {"conviction":conv,"direction":direction,"aligned":aligned,
            "timeframe":tf,"target":target,"confidence":int(min(95,conv/1.5*100)),
            "nd":nd,"md":md}

# ═══════════════════════════════════════════════════════════════════════════════
# NARRATIVE
# ═══════════════════════════════════════════════════════════════════════════════
def generate_narrative(top_arts, cat_scores, conv, price, models):
    drivers="\n".join(
        f"- {cat}: {'BULLISH' if v>0 else 'BEARISH'} ({v:+.2f})"
        for cat,v in sorted(cat_scores.items(),key=lambda x:abs(x[1]),reverse=True)[:3]
    )
    src_list="\n".join(
        f"[{a['source']}] {a['title'][:75]} — {a['age_hours']:.0f}h ago"
        for a in sorted(top_arts,key=lambda x:abs(x["contrib"]),reverse=True)[:4]
    )
    reasons="\n".join(
        f"- {a['reason']}"
        for a in sorted(top_arts,key=lambda x:abs(x["contrib"]),reverse=True)[:3]
    )
    dir_word="UP" if conv["direction"]>0 else "DOWN"
    prompt=(
        f"Senior macro analyst. Gold ${price:,.1f} moving {dir_word}. "
        f"Target ${conv['target']:,.1f} in {conv['timeframe']}. Confidence {conv['confidence']}%.\n\n"
        f"DRIVERS:\n{drivers}\nREASONS:\n{reasons}\nSOURCES:\n{src_list}\n\n"
        f"Write exactly three sections:\n\n"
        f"WHY:\n2-3 sentences. Each: [event]->[mechanism]->[gold impact]. No vague words.\n\n"
        f"WHAT STOPS THIS:\nOne sentence with specific price or event.\n\n"
        f"SOURCES:\nList sources exactly as given.\n\nNothing else."
    )
    messages=[
        {"role":"system","content":"Concise macro analyst. Causal. No hype."},
        {"role":"user","content":prompt}
    ]
    for model in models[:5]:
        raw,_=call_api(model,messages,max_tokens=800)
        if raw and len(raw.strip())>80:
            return raw.strip()
    return f"WHY:\n{chr(10).join(reasons.split(chr(10))[:2])}\n\nWHAT STOPS THIS:\nRisk reversal or DXY spike invalidates call.\n\nSOURCES:\n{src_list}"

# ═══════════════════════════════════════════════════════════════════════════════
# FORMAT TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════
def format_message(conv, price, atr, news_sc, narrative, call_type="DAILY"):
    now=datetime.now()
    icon="🟢" if conv["direction"]>0 else "🔴"
    dw="UP  ↑" if conv["direction"]>0 else "DOWN  ↓"
    ai="✅ ALIGNED" if conv["aligned"] else "⚠️ DIVERGING"

    if conv["timeframe"] is None:
        return (
            f"⚪ <b>GOLD CALL — {call_type}</b>\n"
            f"📅 {now.strftime('%d %b %Y  %I:%M %p')}\n\n"
            f"<b>NO CALL — LOW CONVICTION</b>\n"
            f"Signals weak or diverging. Defer to V7 macro.\n\n"
            f"News: {news_sc:+.2f}/10   V7: {V7_MACRO_SCORE:+.1f}/10"
        )

    return (
        f"{icon} <b>GOLD CALL — {call_type}</b>\n"
        f"📅 {now.strftime('%d %b %Y   %I:%M %p')}\n\n"
        f"<b>Gold is moving {dw}</b>\n"
        f"💰 Price      : <b>${price:,.1f}</b>\n"
        f"🎯 Target     : <b>${conv['target']:,.1f}</b>  within {conv['timeframe']}\n"
        f"📊 Confidence : <b>{conv['confidence']}%</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 News  : {news_sc:+.2f}/10\n"
        f"📉 V7    : {V7_MACRO_SCORE:+.1f}/10\n"
        f"🧠 Conv  : {conv['conviction']:.2f}/1.50\n"
        f"📏 ATR   : ${atr:,.1f}\n"
        f"{ai}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{narrative}"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════
def run_pipeline(call_type="DAILY"):
    global _pipeline_busy
    with _pipeline_lock:
        if _pipeline_busy:
            return None, None
        _pipeline_busy = True

    try:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] Pipeline starting ({call_type})...")

        models = get_models()
        print(f"  Models: {len(models)} available")

        articles = fetch_headlines()
        print(f"  Articles: {len(articles)}")
        if not articles:
            return "⚠️ No articles fetched.", 0.0

        print(f"  Classifying...")
        classifications, model_used = classify(articles, models)
        if not classifications:
            return "⚠️ Classification failed. Will retry next cycle.", 0.0
        print(f"  Classified with: {(model_used or '?').split('/')[-1]}")

        news_sc, top_arts, cat_scores = compute_news_score(articles, classifications)
        print(f"  News score: {news_sc:+.2f}")

        price, atr = fetch_gold()
        if not price:
            price, atr = 3250.0, 18.0
        print(f"  Gold: ${price:,.1f}  ATR: ${atr}")

        conv = compute_conviction(news_sc, V7_MACRO_SCORE, price, atr, cat_scores)
        print(f"  Conviction: {conv['conviction']:.2f}  Target: ${conv['target']}")

        print(f"  Generating narrative...")
        narrative = generate_narrative(top_arts, cat_scores, conv, price, models)

        message = format_message(conv, price, atr, news_sc, narrative, call_type)
        print(f"  Done.")
        return message, news_sc

    except Exception as e:
        print(f"  Pipeline error: {e}")
        return f"⚠️ Pipeline error: {e}", 0.0
    finally:
        with _pipeline_lock:
            _pipeline_busy = False

# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND LISTENER — runs in its own thread
# ═══════════════════════════════════════════════════════════════════════════════
def command_listener():
    offset = None
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Command listener ready.")
    while True:
        try:
            updates = tg_get_updates(offset)
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                msg    = update.get("message", {})
                text   = msg.get("text","").strip().lower()
                cid    = str(msg.get("chat",{}).get("id",""))
                if cid != TELEGRAM_CHATID:
                    continue
                print(f"  [Command] {text}")

                if text in ("/call", "/call@goldflowvpbot"):
                    tg_send("⏳ Generating fresh Gold Call, please wait 60–90 seconds...")
                    threading.Thread(target=lambda: tg_send(
                        run_pipeline("ON-DEMAND")[0] or "⚠️ Pipeline busy, try again in a moment."
                    ), daemon=True).start()

                elif text in ("/status", "/status@goldflowvpbot"):
                    busy = _pipeline_busy
                    tg_send(
                        f"✅ <b>Gold Bot — ONLINE</b>\n"
                        f"🕐 {datetime.now().strftime('%d %b %Y %H:%M')}\n"
                        f"📡 V7 Score  : {V7_MACRO_SCORE:+.1f}/10\n"
                        f"⏰ Daily call: {DAILY_HOUR}:00 AM\n"
                        f"🔔 Flash at  : >{FLASH_THRESHOLD}/10\n"
                        f"⚙️ Pipeline  : {'RUNNING' if busy else 'IDLE'}"
                    )
        except Exception as e:
            print(f"  [Listener error] {e}")
        time.sleep(1)

# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER — runs in its own thread
# ═══════════════════════════════════════════════════════════════════════════════
def scheduler():
    last_flash = 0
    last_daily = -1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scheduler ready.")
    while True:
        now = datetime.now()

        # Daily anchor
        if now.hour == DAILY_HOUR and now.minute == 0 and now.day != last_daily:
            last_daily = now.day
            print(f"[{now.strftime('%H:%M:%S')}] Daily anchor firing...")
            msg, _ = run_pipeline("DAILY ANCHOR")
            if msg: tg_send(msg)

        # Flash check every 4 hours
        if time.time() - last_flash >= CHECK_INTERVAL:
            last_flash = time.time()
            print(f"[{now.strftime('%H:%M:%S')}] Flash check...")
            msg, news_sc = run_pipeline("FLASH CHECK")
            if msg and news_sc is not None and abs(news_sc) >= FLASH_THRESHOLD:
                tg_send(f"⚡ <b>FLASH ALERT</b> — News score {news_sc:+.2f}/10\n\n{msg}")
                print(f"  Flash alert sent ({news_sc:+.2f})")
            elif news_sc is not None:
                print(f"  No flash ({news_sc:+.2f} below threshold)")

        time.sleep(30)

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    if "PASTE_YOUR" in OPENROUTER_KEY:
        sys.exit("  Paste your OpenRouter API key at the top of the script.")

    print("=" * 55)
    print("  GOLD INTELLIGENCE BOT")
    print("=" * 55)
    print(f"  Bot     : @GoldFlowvpBot")
    print(f"  Daily   : {DAILY_HOUR}:00 AM")
    print(f"  Flash   : every 4h if score > {FLASH_THRESHOLD}")
    print(f"  Commands: /call  /status")
    print("=" * 55 + "\n")

    # Send startup message
    tg_send(
        f"🤖 <b>Gold Intelligence Bot — ONLINE</b>\n\n"
        f"• Daily Gold Call at {DAILY_HOUR}:00 AM\n"
        f"• Flash alerts when news score &gt; {FLASH_THRESHOLD}/10\n\n"
        f"Commands:\n/call — fresh call now\n/status — health check\n\n"
        f"⏳ Generating startup call..."
    )

    # Start command listener
    threading.Thread(target=command_listener, daemon=True).start()

    # Start scheduler
    threading.Thread(target=scheduler, daemon=True).start()

    # Startup pipeline in background — doesn't block anything
    def startup():
        msg, _ = run_pipeline("STARTUP")
        if msg:
            tg_send(msg)

    threading.Thread(target=startup, daemon=True).start()

    print("  Bot running. Press Ctrl+C to stop.\n")

    # Keep main thread alive
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
