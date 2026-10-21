"""
Solana Meme Scout v2 – Aggressivere Früherkennung + Paper + optional Live
Kostenlos auf Streamlit Community Cloud deploybar.
"""

import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json

# ====================== KONFIG ======================
DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_UPDATES = "https://api.dexscreener.com/token-profiles/recent-updates/v1"
DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/latest/v1"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana"
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"

st.set_page_config(
    page_title="Solana Meme Scout v2",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ====================== DATEN HOLEN ======================
@st.cache_data(ttl=40)
def fetch_json(url):
    try:
        r = requests.get(url, timeout=12)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return []


@st.cache_data(ttl=35)
def get_all_candidates(limit=60):
    """Aggressivere Erkennung: Profiles + Recent Updates + Boosts."""
    profiles = fetch_json(DEX_PROFILES) or []
    updates = fetch_json(DEX_UPDATES) or []
    boosts = fetch_json(DEX_BOOSTS) or []

    seen = set()
    candidates = []

    for source, items in [("profile", profiles), ("update", updates), ("boost", boosts)]:
        for item in items:
            if item.get("chainId") != "solana":
                continue
            addr = item.get("tokenAddress")
            if not addr or addr in seen:
                continue
            seen.add(addr)
            candidates.append({
                "address": addr,
                "source": source,
                "icon": item.get("icon"),
                "description": item.get("description") or "",
                "url": item.get("url"),
                "boost_amount": item.get("amount") or item.get("totalAmount") or 0
            })
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    return candidates


@st.cache_data(ttl=25)
def get_pairs(addresses):
    if not addresses:
        return {}
    addresses = list(dict.fromkeys(addresses))[:30]
    url = f"{DEX_TOKENS}/{','.join(addresses)}"
    try:
        r = requests.get(url, timeout=14)
        if r.status_code != 200:
            return {}
        pairs = r.json()
        result = {}
        for p in pairs:
            base = p.get("baseToken", {}).get("address")
            if base and base not in result:
                result[base] = p
        return result
    except:
        return {}


def get_rug(mint):
    try:
        r = requests.get(RUGCHECK.format(mint), timeout=7)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None


def score_token(pair, rug, source="profile", boost=0):
    score = 48
    reasons = []
    warnings = []

    if not pair:
        return 18, ["Keine Marktdaten"], ["Unvollständig"]

    liq = pair.get("liquidity", {}).get("usd") or 0
    vol_h1 = pair.get("volume", {}).get("h1") or 0
    vol_m5 = pair.get("volume", {}).get("m5") or 0
    change_h1 = pair.get("priceChange", {}).get("h1") or 0
    change_m5 = pair.get("priceChange", {}).get("m5") or 0
    mcap = pair.get("marketCap") or pair.get("fdv") or 0
    txns = pair.get("txns", {}).get("h1", {})
    buys = txns.get("buys") or 0
    sells = txns.get("sells") or 0

    # Alter
    created = pair.get("pairCreatedAt")
    age_min = 9999
    if created:
        age_min = (datetime.now(timezone.utc).timestamp() * 1000 - created) / 60000
        if age_min < 10:
            score += 14
            reasons.append(f"Brandneu ({age_min:.0f} Min)")
        elif age_min < 30:
            score += 10
            reasons.append(f"Sehr frisch ({age_min:.0f} Min)")
        elif age_min < 90:
            score += 5
            reasons.append(f"Frisch ({age_min:.0f} Min)")
        elif age_min > 720:
            score -= 8

    # Liquidität
    if liq >= 40000:
        score += 11
        reasons.append(f"Starke Liq ${liq:,.0f}")
    elif liq >= 12000:
        score += 7
        reasons.append(f"Gute Liq ${liq:,.0f}")
    elif liq >= 4000:
        score += 3
    elif liq < 1500:
        score -= 18
        warnings.append(f"Extrem niedrige Liq ${liq:,.0f}")

    # Volumen & Momentum
    if vol_m5 > 8000:
        score += 7
        reasons.append("Starkes 5-Min-Volumen")
    if vol_h1 > 80000:
        score += 9
        reasons.append(f"Hohes 1h-Vol ${vol_h1:,.0f}")
    elif vol_h1 > 25000:
        score += 5

    if buys + sells > 15:
        ratio = buys / (buys + sells)
        if ratio >= 0.68:
            score += 9
            reasons.append(f"Starke Käufe ({buys}/{sells})")
        elif ratio <= 0.32:
            score -= 9
            warnings.append("Verkaufsdruck")

    # Preis
    if 8 < change_h1 < 90:
        score += 6
        reasons.append(f"+{change_h1:.0f}% 1h (gesund)")
    elif change_h1 > 180:
        score -= 7
        warnings.append(f"Extremer Pump +{change_h1:.0f}%")

    if change_m5 > 15 and change_m5 < 60:
        score += 4

    # MCap Sweet Spot
    if 3000 < mcap < 180000:
        score += 9
        reasons.append(f"Interessante MCap ${mcap:,.0f}")
    elif mcap > 800000:
        score -= 7
        warnings.append("Schon teuer")

    # Boost / Source Bonus
    if source == "boost" or boost > 0:
        score += 4
        reasons.append("DexScreener Boost")
    if source == "update":
        score += 2

    # RugCheck
    if rug:
        level = (rug.get("riskLevel") or "").lower()
        if level in ["good", "low"]:
            score += 11
            reasons.append("RugCheck gut")
        elif level in ["warning", "medium"]:
            score -= 6
            warnings.append("RugCheck Warnung")
        elif level in ["danger", "high", "critical"]:
            score -= 28
            warnings.append("🚨 RugCheck HOCHES RISIKO")

        if rug.get("mintAuthority") is not None:
            score -= 18
            warnings.append("Mint Authority AKTIV")
        if rug.get("freezeAuthority") is not None:
            score -= 14
            warnings.append("Freeze Authority AKTIV")
        if rug.get("lpLocked"):
            score += 6
            reasons.append("LP gelockt")
        else:
            score -= 7
            warnings.append("LP nicht gelockt")

        top = rug.get("topHoldersPct") or 0
        if top > 45:
            score -= 12
            warnings.append(f"Top-Holder {top:.0f}%")
        elif top > 0 and top < 22:
            score += 5
            reasons.append("Gute Verteilung")

    score = max(0, min(100, int(score)))
    return score, reasons, warnings


def multi_caps(mcap):
    if not mcap or mcap <= 0:
        return None, None, None
    return mcap * 10, mcap * 100, mcap * 1000


# ====================== UI ======================
st.title("🚀 Solana Meme Scout v2")
st.caption("Aggressivere Früherkennung • Risk-Scoring • Paper-Trading • optional Live (mit harten Limits)")

st.error(
    "**KEINE GARANTIE.** Die meisten Meme-Coins gehen auf null. "
    "Live-Trading kann dein gesamtes Geld vernichten. "
    "Nutze zuerst nur Analysis + Paper. Nie mehr riskieren als du bereit bist zu verlieren."
)

# Sidebar
with st.sidebar:
    st.header("⚙️ Einstellungen")
    mode = st.radio(
        "Betriebsmodus",
        ["🔍 Analysis (empfohlen)", "📝 Paper Trading", "⚠️ Live Trading"],
        index=0
    )

    st.markdown("---")
    min_score = st.slider("Min. Score", 0, 100, 58)
    max_age_h = st.slider("Max. Alter (Stunden)", 1, 24, 8)
    min_liq = st.number_input("Min. Liquidität ($)", 0, 50000, 2500, 500)
    only_safer = st.checkbox("Kritische Risks ausblenden", True)

    st.markdown("---")
    st.subheader("Deep-Check")
    manual_ca = st.text_input("Contract Address")
    if st.button("Analysieren", use_container_width=True) and manual_ca:
        st.session_state["deep_ca"] = manual_ca.strip()

    if mode.startswith("⚠️"):
        st.markdown("---")
        st.warning("LIVE-MODUS")
        st.caption("Nur mit eigener Wallet + Jupiter. Private Key wird NIE gespeichert.")
        max_sol = st.number_input("Max. SOL pro Trade", 0.01, 2.0, 0.1, 0.01)
        st.session_state["max_sol"] = max_sol
        live_confirm = st.checkbox("Ich verstehe das Risiko und aktiviere Live bewusst")
        st.session_state["live_ok"] = live_confirm

# ====================== TABS ======================
tab1, tab2, tab3, tab4 = st.tabs(["🔥 Live-Scout", "🔍 Deep-Check", "📝 Paper Log", "⚠️ Live (Vorsicht)"])

with tab1:
    with st.spinner("Scanne aggressiv neue Tokens (Profiles + Updates + Boosts)..."):
        cands = get_all_candidates(55)
        addrs = [c["address"] for c in cands]
        pairs = get_pairs(addrs)

    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(get_rug, c["address"]): c for c in cands}
        for fut in as_completed(futs):
            c = futs[fut]
            rug = fut.result()
            pair = pairs.get(c["address"])
            sc, reasons, warns = score_token(pair, rug, c["source"], c.get("boost_amount", 0))

            age_ok = True
            if pair and pair.get("pairCreatedAt"):
                age_h = (datetime.now(timezone.utc).timestamp() * 1000 - pair["pairCreatedAt"]) / 3600000
                if age_h > max_age_h:
                    age_ok = False

            liq = (pair.get("liquidity", {}).get("usd") or 0) if pair else 0
            if sc < min_score or liq < min_liq or not age_ok:
                continue
            if only_safer and any("HOCHES RISIKO" in w or "Mint Authority" in w for w in warns):
                continue

            mcap = (pair.get("marketCap") or pair.get("fdv") or 0) if pair else 0
            t10, t100, t1000 = multi_caps(mcap)

            results.append({
                "score": sc,
                "symbol": pair.get("baseToken", {}).get("symbol") if pair else "?",
                "name": (pair.get("baseToken", {}).get("name") if pair else c.get("description", "")[:30]) or "Unknown",
                "ca": c["address"],
                "mcap": mcap,
                "liq": liq,
                "vol": pair.get("volume", {}).get("h1") if pair else 0,
                "chg": pair.get("priceChange", {}).get("h1") if pair else 0,
                "reasons": reasons,
                "warns": warns,
                "dex": pair.get("url") if pair else f"https://dexscreener.com/solana/{c['address']}",
                "rug": f"https://rugcheck.xyz/tokens/{c['address']}",
                "t10": t10, "t100": t100, "t1000": t1000,
                "risk": rug.get("riskLevel") if rug else "?",
                "source": c["source"]
            })

    results.sort(key=lambda x: x["score"], reverse=True)

    st.subheader(f"Top Signale ({len(results)})")
    if not results:
        st.info("Aktuell keine Treffer mit deinen Filtern. Score runter oder Alter hochsetzen.")
    else:
        for r in results[:30]:
            c1, c2, c3, c4 = st.columns([1.1, 2.2, 2, 2])
            with c1:
                emoji = "🟢" if r["score"] >= 72 else "🟡" if r["score"] >= 58 else "🟠"
                st.metric("Score", f"{emoji} {r['score']}")
            with c2:
                st.markdown(f"**{r['symbol']}** — {r['name']}")
                st.caption(f"`{r['ca'][:6]}...{r['ca'][-4:]}` · Quelle: {r['source']}")
            with c3:
                st.write(f"MCap **${r['mcap']:,.0f}** · Liq **${r['liq']:,.0f}**")
                st.write(f"Vol 1h **${r['vol']:,.0f}** · {r['chg']:+.1f}%")
            with c4:
                st.write(f"Risk: **{r['risk']}**")
                if r["t10"]:
                    st.caption(f"10x→${r['t10']:,.0f} | 100x→${r['t100']:,.0f}")
                st.markdown(f"[Chart]({r['dex']}) · [Rug]({r['rug']})")

            if r["reasons"]:
                st.success(" · ".join(r["reasons"][:4]))
            if r["warns"]:
                st.error(" · ".join(r["warns"][:3]))
            st.divider()

with tab2:
    ca = st.session_state.get("deep_ca", "")
    if ca:
        st.subheader(f"Deep-Check `{ca}`")
        with st.spinner("Lade..."):
            pair = get_pairs([ca]).get(ca)
            rug = get_rug(ca)
            sc, reasons, warns = score_token(pair, rug)

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Score", sc)
            if pair:
                st.write(f"**{pair.get('baseToken',{}).get('name')} ({pair.get('baseToken',{}).get('symbol')})**")
                st.write(f"Preis: ${float(pair.get('priceUsd') or 0):.8f}")
                st.write(f"MCap: ${pair.get('marketCap') or pair.get('fdv') or 0:,.0f}")
                st.write(f"Liq: ${pair.get('liquidity',{}).get('usd') or 0:,.0f}")
                st.write(f"Vol 1h: ${pair.get('volume',{}).get('h1') or 0:,.0f}")
        with c2:
            if rug:
                st.write(f"RiskLevel: **{rug.get('riskLevel')}**")
                st.write(f"Mint Auth: {'⚠️ AKTIV' if rug.get('mintAuthority') else '✅ Renounced'}")
                st.write(f"Freeze Auth: {'⚠️ AKTIV' if rug.get('freezeAuthority') else '✅ Renounced'}")
                st.write(f"LP Locked: {'✅ Ja' if rug.get('lpLocked') else '⚠️ Nein'}")
                st.write(f"Top Holders: {rug.get('topHoldersPct') or '?'}%")
            else:
                st.warning("RugCheck keine Daten")

        if reasons:
            st.success("**Positiv:** " + " · ".join(reasons))
        if warns:
            st.error("**Warnungen:** " + " · ".join(warns))

        mcap = (pair.get("marketCap") or pair.get("fdv") or 0) if pair else 0
        t10, t100, t1000 = multi_caps(mcap)
        if t10:
            st.info(f"Nur Mathematik: 10x → ${t10:,.0f} · 100x → ${t100:,.0f} · 1000x → ${t1000:,.0f}\n\nDas ist **keine** Prognose.")

        st.markdown(f"[DexScreener](https://dexscreener.com/solana/{ca}) · [RugCheck](https://rugcheck.xyz/tokens/{ca}) · [Solscan](https://solscan.io/token/{ca})")
    else:
        st.info("Links eine CA eingeben und analysieren.")

with tab3:
    st.subheader("Paper-Trading Log")
    if "paper" not in st.session_state:
        st.session_state.paper = []

    with st.form("pform"):
        pca = st.text_input("CA")
        pentry = st.number_input("Entry $", 0.0, format="%.10f")
        psol = st.number_input("Simulierte SOL", 0.01, 5.0, 0.1)
        pnote = st.text_input("Notiz / Score")
        if st.form_submit_button("Speichern") and pca:
            st.session_state.paper.append({
                "Zeit": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "CA": pca,
                "Entry": pentry,
                "SOL": psol,
                "Notiz": pnote
            })
            st.success("Gespeichert")

    if st.session_state.paper:
        st.dataframe(pd.DataFrame(st.session_state.paper), use_container_width=True)
        if st.button("Log leeren"):
            st.session_state.paper = []
            st.rerun()

with tab4:
    st.subheader("⚠️ Live Trading – echte Trades")
    st.error(
        "Hier wird echtes Geld bewegt. Die meisten verlieren damit. "
        "Private Keys werden **niemals** gespeichert oder geloggt. "
        "Du musst eine eigene Wallet (Phantom / Solflare) + Jupiter nutzen."
    )

    if not (mode.startswith("⚠️") and st.session_state.get("live_ok")):
        st.warning("Aktiviere zuerst in der Sidebar den Live-Modus und bestätige das Risiko.")
    else:
        st.info(
            f"Max. Positionsgröße: **{st.session_state.get('max_sol', 0.1)} SOL**\n\n"
            "Aktuell ist die Ausführung über Jupiter Swap vorbereitet, aber aus Sicherheitsgründen "
            "noch nicht vollautomatisch freigeschaltet. "
            "Nächster Schritt: Du gibst einen CA + Betrag ein → der Bot öffnet den fertigen Jupiter-Swap-Link "
            "oder (optional später) signiert über deine Wallet-Extension."
        )
        live_ca = st.text_input("Token CA für Live-Kauf")
        live_sol = st.number_input("SOL Betrag", 0.01, float(st.session_state.get("max_sol", 0.1)), 0.05)
        if st.button("Jupiter Swap Link erzeugen"):
            if live_ca:
                # Jupiter Quote/Swap Link (einfachste sichere Variante)
                jup = f"https://jup.ag/swap/SOL-{live_ca}"
                st.success("Öffne diesen Link und bestätige den Swap **manuell** in deiner Wallet:")
                st.markdown(f"[Jupiter Swap öffnen]({jup})")
                st.caption("Vollautomatisches Signieren kommt nur, wenn du später explizit eine sichere Backend-Lösung willst.")

st.markdown("---")
st.caption("Solana Meme Scout v2 · Daten: DexScreener + RugCheck · Keine Finanzberatung · Keine Gewinn-Garantie")
