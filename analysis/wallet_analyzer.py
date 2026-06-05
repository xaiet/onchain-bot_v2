"""
analysis/wallet_analyzer.py
Lògica pura per analitzar wallets Solana via Helius + Birdeye.
"""

import asyncio
import aiohttp
import os
from datetime import datetime, timezone
from typing import Optional

HELIUS_API_KEY   = os.getenv("HELIUS_API_KEY")
BIRDEYE_API_KEY  = os.getenv("BIRDEYE_API_KEY")

HELIUS_BASE      = "https://api.helius.xyz/v0"
BIRDEYE_BASE     = "https://public-api.birdeye.so"
DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"

SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


async def _fetch_transactions(session, wallet: str, limit: int = 100) -> list:
    url    = f"{HELIUS_BASE}/addresses/{wallet}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": limit, "type": "SWAP"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            return await r.json() if r.status == 200 else []
    except Exception:
        return []


async def _birdeye_price_at(session, mint: str, timestamp: int) -> Optional[float]:
    if not BIRDEYE_API_KEY:
        return None
    url     = f"{BIRDEYE_BASE}/defi/history_price"
    params  = {"address": mint, "address_type": "token", "type": "1m",
                "time_from": timestamp - 120, "time_to": timestamp + 120}
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            data  = await r.json()
            items = data.get("data", {}).get("items", [])
            if not items:
                return None
            closest = min(items, key=lambda x: abs(x.get("unixTime", 0) - timestamp))
            return float(closest.get("value", 0) or 0)
    except Exception:
        return None


async def _birdeye_price_current(session, mint: str) -> Optional[float]:
    if not BIRDEYE_API_KEY:
        return None
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    try:
        async with session.get(f"{BIRDEYE_BASE}/defi/price", params={"address": mint},
                               headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None
            data = await r.json()
            return float(data.get("data", {}).get("value", 0) or 0)
    except Exception:
        return None


async def _dexscreener_meta(session, mint: str) -> dict:
    try:
        async with session.get(f"{DEXSCREENER_BASE}/tokens/{mint}",
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return {}
            data  = await r.json()
            pairs = data.get("pairs") or []
            if not pairs:
                return {}
            p = pairs[0]
            return {"name":   p.get("baseToken", {}).get("name", "Unknown"),
                    "symbol": p.get("baseToken", {}).get("symbol", "???")}
    except Exception:
        return {}


def _parse_swaps(transactions: list, token_mint: str) -> list:
    mint  = token_mint.lower()
    swaps = []
    for tx in transactions:
        if tx.get("type") != "SWAP":
            continue
        swap = tx.get("events", {}).get("swap", {})
        if not swap:
            continue
        ts         = tx.get("timestamp", 0)
        dt         = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M UTC")
        native_in  = swap.get("nativeInput")
        native_out = swap.get("nativeOutput")
        tok_ins    = swap.get("tokenInputs", [])
        tok_outs   = swap.get("tokenOutputs", [])
        is_buy  = native_in  is not None and any(t.get("mint","").lower() == mint for t in tok_outs)
        is_sell = native_out is not None and any(t.get("mint","").lower() == mint for t in tok_ins)
        if is_buy:
            sol_amt = (native_in.get("amount", 0) or 0) / 1e9
            tok     = next((t for t in tok_outs if t.get("mint","").lower() == mint), None)
            if not tok:
                continue
            dec     = tok.get("tokenAmount", {}).get("decimals", 6)
            tok_amt = (tok.get("tokenAmount", {}).get("amount", 0) or 0) / (10 ** dec)
            swaps.append({"type": "BUY", "token_amount": tok_amt, "sol_amount": sol_amt,
                          "price_sol": sol_amt / tok_amt if tok_amt else 0,
                          "price_usd": None, "timestamp": ts, "datetime": dt})
        elif is_sell:
            sol_amt = (native_out.get("amount", 0) or 0) / 1e9
            tok     = next((t for t in tok_ins if t.get("mint","").lower() == mint), None)
            if not tok:
                continue
            dec     = tok.get("tokenAmount", {}).get("decimals", 6)
            tok_amt = (tok.get("tokenAmount", {}).get("amount", 0) or 0) / (10 ** dec)
            swaps.append({"type": "SELL", "token_amount": tok_amt, "sol_amount": sol_amt,
                          "price_sol": sol_amt / tok_amt if tok_amt else 0,
                          "price_usd": None, "timestamp": ts, "datetime": dt})
    return sorted(swaps, key=lambda x: x["timestamp"])


async def _enrich_usd(session, swaps: list, mint: str) -> list:
    prices = await asyncio.gather(*[_birdeye_price_at(session, mint, s["timestamp"]) for s in swaps])
    for swap, price in zip(swaps, prices):
        swap["price_usd"] = price
    return swaps


def _calc_pnl(swaps: list, current_usd: Optional[float] = None) -> dict:
    buys  = [s for s in swaps if s["type"] == "BUY"]
    sells = [s for s in swaps if s["type"] == "SELL"]
    sol_spent    = sum(b["sol_amount"]   for b in buys)
    sol_received = sum(s["sol_amount"]   for s in sells)
    tok_bought   = sum(b["token_amount"] for b in buys)
    tok_sold     = sum(s["token_amount"] for s in sells)
    tok_left     = tok_bought - tok_sold
    realized_sol = sol_received - sol_spent
    roi_pct      = (realized_sol / sol_spent * 100) if sol_spent > 0 else 0
    usd_spent    = sum(b["token_amount"] * b["price_usd"] for b in buys  if b["price_usd"])
    usd_received = sum(s["token_amount"] * s["price_usd"] for s in sells if s["price_usd"])
    realized_usd   = (usd_received - usd_spent) if (usd_spent or usd_received) else None
    unrealized_usd = (tok_left * current_usd) if (tok_left > 0 and current_usd) else None
    time_str = ""
    if buys and sells:
        delta    = sells[-1]["timestamp"] - buys[0]["timestamp"]
        time_str = f"{delta // 3600}h {(delta % 3600) // 60}m"
    elif buys:
        time_str = "Posició oberta"
    return {
        "sol_spent": sol_spent, "sol_received": sol_received,
        "tok_bought": tok_bought, "tok_sold": tok_sold, "tok_left": tok_left,
        "realized_sol": realized_sol, "roi_pct": roi_pct,
        "realized_usd": realized_usd, "unrealized_usd": unrealized_usd,
        "avg_buy_sol":  sol_spent    / tok_bought if tok_bought else 0,
        "avg_sell_sol": sol_received / tok_sold   if tok_sold   else 0,
        "time_in_pos": time_str, "num_buys": len(buys), "num_sells": len(sells),
        "profitable": realized_sol > 0, "current_usd": current_usd, "tok_left": tok_left,
    }


async def get_wallet_pnl(wallet: str, token_mint: str) -> str:
    async with aiohttp.ClientSession() as session:
        txs, current_usd, meta = await asyncio.gather(
            _fetch_transactions(session, wallet, limit=100),
            _birdeye_price_current(session, token_mint),
            _dexscreener_meta(session, token_mint),
        )
        if not txs:
            return "❌ No s'han trobat transaccions. Comprova l'adreça."
        swaps = _parse_swaps(txs, token_mint)
        if not swaps:
            return f"❌ Aquesta wallet no té swaps del token `{token_mint[:8]}...`"
        swaps = await _enrich_usd(session, swaps, token_mint)

    pnl    = _calc_pnl(swaps, current_usd)
    symbol = meta.get("symbol", "???")
    name   = meta.get("name", "Unknown")
    sign   = "+" if pnl["realized_sol"] >= 0 else ""
    emoji  = "✅" if pnl["profitable"] else "❌"

    lines = [f"🔍 *Wallet:* `{wallet[:6]}...{wallet[-4:]}`",
             f"🪙 *Token:* {name} (${symbol})", "",
             f"📥 *Compres ({pnl['num_buys']}):*"]
    for b in [s for s in swaps if s["type"] == "BUY"]:
        usd = f" ≈ ${b['price_usd']:.6f}" if b["price_usd"] else ""
        lines.append(f"  • {b['datetime']} — {b['sol_amount']:.3f} SOL @ {b['price_sol']:.2e}{usd}")
    lines += ["", f"📤 *Vendes ({pnl['num_sells']}):*"]
    for s in [s for s in swaps if s["type"] == "SELL"]:
        usd = f" ≈ ${s['price_usd']:.6f}" if s["price_usd"] else ""
        lines.append(f"  • {s['datetime']} — {s['sol_amount']:.3f} SOL @ {s['price_sol']:.2e}{usd}")
    lines += ["", "─────────────────────",
              f"💰 *PnL Realitzat:* {sign}{pnl['realized_sol']:.4f} SOL ({sign}{pnl['roi_pct']:.1f}%) {emoji}"]
    if pnl["realized_usd"] is not None:
        us = "+" if pnl["realized_usd"] >= 0 else ""
        lines.append(f"💵 *PnL USD:* {us}${pnl['realized_usd']:.2f}")
    lines.append(f"📊 *Preu mig entrada:* {pnl['avg_buy_sol']:.2e} SOL/token")
    if pnl["num_sells"] > 0:
        lines.append(f"📊 *Preu mig sortida:* {pnl['avg_sell_sol']:.2e} SOL/token")
    if pnl["time_in_pos"]:
        lines.append(f"⏱ *Temps en posició:* {pnl['time_in_pos']}")
    if pnl["tok_left"] > 0:
        if pnl["unrealized_usd"] is not None:
            lines.append(f"📦 *Unrealized:* {pnl['tok_left']:,.0f} tokens × ${current_usd:.6f} = ${pnl['unrealized_usd']:.2f}")
        else:
            lines.append(f"📦 *Posició oberta:* {pnl['tok_left']:,.0f} tokens restants")
    return "\n".join(lines)


async def get_wallet_score(wallet: str, num_tokens: int = 20) -> str:
    async with aiohttp.ClientSession() as session:
        txs = await _fetch_transactions(session, wallet, limit=200)
    if not txs:
        return "❌ No s'han trobat transaccions per aquesta wallet."

    mints_seen, seen_set = [], set()
    for tx in txs:
        if tx.get("type") != "SWAP":
            continue
        swap = tx.get("events", {}).get("swap", {})
        for t in swap.get("tokenOutputs", []) + swap.get("tokenInputs", []):
            mint = t.get("mint", "")
            if mint and mint not in SKIP_MINTS and mint not in seen_set:
                seen_set.add(mint)
                mints_seen.append(mint)

    results = []
    for mint in mints_seen[:num_tokens]:
        swaps = _parse_swaps(txs, mint)
        if swaps and any(s["type"] == "BUY" for s in swaps):
            results.append(_calc_pnl(swaps))
    if not results:
        return "❌ No hi ha prou dades per calcular un score."

    total     = len(results)
    wins      = sum(1 for r in results if r["profitable"])
    wr        = wins / total * 100
    avg_roi   = sum(r["roi_pct"] for r in results) / total
    total_pnl = sum(r["realized_sol"] for r in results)
    best      = max(results, key=lambda r: r["roi_pct"])
    worst     = min(results, key=lambda r: r["roi_pct"])
    score     = min(100, max(0, int(wr * 0.5 + min(avg_roi, 200) * 0.25 + min(total * 2, 25))))
    s_emoji   = "🔥" if score >= 75 else "✅" if score >= 50 else "⚠️" if score >= 30 else "❌"
    verdicts  = [(75, "Molt consistent i profitable. Val la pena seguir."),
                 (50, "Decent. Bons resultats però no excepcional."),
                 (30, "Resultats mixtes. Vigilar abans d'afegir al watchlist."),
                 (0,  "Poc profitable. Probablement retail o bot perdedor.")]
    verdict   = next(v for threshold, v in verdicts if score >= threshold)

    return "\n".join([
        f"🎯 *Wallet Score* — `{wallet[:6]}...{wallet[-4:]}`", "",
        f"{s_emoji} *Score: {score}/100*", "",
        f"📊 *Estadístiques ({total} tokens analitzats):*",
        f"  • Win Rate: {wr:.1f}% ({wins}/{total})",
        f"  • ROI mig per trade: {avg_roi:+.1f}%",
        f"  • PnL total: {total_pnl:+.4f} SOL",
        f"  • Millor trade: {best['roi_pct']:+.1f}%",
        f"  • Pitjor trade: {worst['roi_pct']:+.1f}%", "",
        f"💡 *Interpretació:* {verdict}",
    ])


async def compare_wallets(wallet1: str, wallet2: str, token_mint: str) -> str:
    async with aiohttp.ClientSession() as session:
        txs1, txs2, meta = await asyncio.gather(
            _fetch_transactions(session, wallet1, limit=100),
            _fetch_transactions(session, wallet2, limit=100),
            _dexscreener_meta(session, token_mint),
        )
    swaps1 = _parse_swaps(txs1, token_mint)
    swaps2 = _parse_swaps(txs2, token_mint)
    if not swaps1 and not swaps2:
        return "❌ Cap de les dues wallets té activitat en aquest token."

    symbol = meta.get("symbol", "???")
    pnl1   = _calc_pnl(swaps1) if swaps1 else None
    pnl2   = _calc_pnl(swaps2) if swaps2 else None

    lines = [f"🔄 *Comparació de Wallets* — ${symbol}", "",
             f"*Wallet A:* `{wallet1[:6]}...{wallet1[-4:]}`",
             f"*Wallet B:* `{wallet2[:6]}...{wallet2[-4:]}`", ""]

    for label, pnl in [("A", pnl1), ("B", pnl2)]:
        if not pnl:
            lines.append(f"📭 Wallet {label}: sense activitat\n")
            continue
        sign  = "+" if pnl["realized_sol"] >= 0 else ""
        emoji = "✅" if pnl["profitable"] else "❌"
        usd_line = f"\n  • PnL USD: {'+' if (pnl['realized_usd'] or 0) >= 0 else ''}${pnl['realized_usd']:.2f}" if pnl["realized_usd"] is not None else ""
        lines += [f"📊 *Wallet {label}:*",
                  f"  • Compres: {pnl['num_buys']} | Vendes: {pnl['num_sells']}",
                  f"  • SOL gastat: {pnl['sol_spent']:.3f}",
                  f"  • PnL SOL: {sign}{pnl['realized_sol']:.4f} ({sign}{pnl['roi_pct']:.1f}%) {emoji}{usd_line}",
                  f"  • Temps: {pnl['time_in_pos']}", ""]

    lines.append("🕵️ *Anàlisi de Coordinació:*")
    signals = 0
    buys1   = [s for s in swaps1 if s["type"] == "BUY"]
    buys2   = [s for s in swaps2 if s["type"] == "BUY"]
    if buys1 and buys2:
        diff = abs(buys1[0]["timestamp"] - buys2[0]["timestamp"])
        if diff < 30:
            lines.append(f"  🚨 Primers buys en {diff}s → coordinació molt probable"); signals += 2
        elif diff < 120:
            lines.append(f"  ⚠️ Primers buys en {diff}s → possible coordinació"); signals += 1
        else:
            lines.append(f"  ✅ Primers buys separats {diff // 60}m → timing independent")
        sol1, sol2 = buys1[0]["sol_amount"], buys2[0]["sol_amount"]
        ratio = min(sol1, sol2) / max(sol1, sol2) if max(sol1, sol2) > 0 else 0
        if ratio > 0.85:
            lines.append(f"  ⚠️ Mides molt similars ({sol1:.3f} vs {sol2:.3f} SOL)"); signals += 1
        else:
            lines.append(f"  ✅ Mides diferents ({sol1:.3f} vs {sol2:.3f} SOL)")
        sells1 = [s for s in swaps1 if s["type"] == "SELL"]
        sells2 = [s for s in swaps2 if s["type"] == "SELL"]
        if sells1 and sells2:
            sdiff = abs(sells1[-1]["timestamp"] - sells2[-1]["timestamp"])
            if sdiff < 60:
                lines.append(f"  ⚠️ Últimes vendes en {sdiff}s → possible exit coordinat"); signals += 1
    lines += ["",
              "🚨 *Veredicte: Alta probabilitat de cluster coordinat*" if signals >= 3
              else "⚠️ *Veredicte: Senyals de coordinació, investigar més*" if signals >= 1
              else "✅ *Veredicte: No semblen coordinades*"]
    return "\n".join(lines)