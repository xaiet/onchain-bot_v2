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

# CEX i entitats conegudes — si el pare és un d'aquests, és un humà normal
KNOWN_ENTITIES = {
    "5tzFkiKscXHK5ms71DkVpK5LfQfQzTMGJ5xGQ8PbDnRm": "Binance",
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": "Binance",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S": "Coinbase",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "Coinbase",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Kraken",
    "CakcnaRDHka2gXyfxNmcggyu6B9dE9psIaETIAMVGoiR": "OKX",
}


# ─────────────────────────────────────────────
# HELIUS
# ─────────────────────────────────────────────

async def _fetch_transactions(session, wallet: str, limit: int = 100) -> list:
    url    = f"{HELIUS_BASE}/addresses/{wallet}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": limit}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            return await r.json() if r.status == 200 else []
    except Exception:
        return []


async def _fetch_all_transactions(session, wallet: str, limit: int = 50) -> list:
    """Agafa TOTES les txs (no només SWAPs) — necessari per trobar transfers."""
    url    = f"{HELIUS_BASE}/addresses/{wallet}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": limit}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            return await r.json() if r.status == 200 else []
    except Exception:
        return []


# ─────────────────────────────────────────────
# BIRDEYE
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# PARSING SWAPS
# ─────────────────────────────────────────────

WSOL_MINT = "So11111111111111111111111111111111111111112"

def _parse_swaps(transactions: list, token_mint: str) -> list:
    mint  = token_mint.lower()
    swaps = []

    for tx in transactions:
        ts = tx.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M UTC")

        # ── Intent 1: events.swap ──────────────────────────────────────────
        swap = tx.get("events", {}).get("swap", {})
        if swap:
            tok_ins    = swap.get("tokenInputs", [])
            tok_outs   = swap.get("tokenOutputs", [])
            native_in  = swap.get("nativeInput")
            native_out = swap.get("nativeOutput")

            def _amt(tok):
                dec = tok.get("tokenAmount", {}).get("decimals", 6)
                raw = tok.get("tokenAmount", {}).get("amount", 0) or 0
                return raw / (10 ** dec)

            if native_in and any(t.get("mint","").lower() == mint for t in tok_outs):
                sol_amt = (native_in.get("amount", 0) or 0) / 1e9
                tok = next((t for t in tok_outs if t.get("mint","").lower() == mint), None)
                if tok:
                    tok_amt = _amt(tok)
                    swaps.append({"type": "BUY", "token_amount": tok_amt, "sol_amount": sol_amt,
                                  "price_sol": sol_amt / tok_amt if tok_amt else 0,
                                  "price_usd": None, "timestamp": ts, "datetime": dt})
                    continue

            if any(t.get("mint","") == WSOL_MINT for t in tok_ins) and \
               any(t.get("mint","").lower() == mint for t in tok_outs):
                wsol = next((t for t in tok_ins if t.get("mint","") == WSOL_MINT), None)
                tok  = next((t for t in tok_outs if t.get("mint","").lower() == mint), None)
                if wsol and tok:
                    sol_amt = _amt(wsol)
                    tok_amt = _amt(tok)
                    swaps.append({"type": "BUY", "token_amount": tok_amt, "sol_amount": sol_amt,
                                  "price_sol": sol_amt / tok_amt if tok_amt else 0,
                                  "price_usd": None, "timestamp": ts, "datetime": dt})
                    continue

            if native_out and any(t.get("mint","").lower() == mint for t in tok_ins):
                sol_amt = (native_out.get("amount", 0) or 0) / 1e9
                tok = next((t for t in tok_ins if t.get("mint","").lower() == mint), None)
                if tok:
                    tok_amt = _amt(tok)
                    swaps.append({"type": "SELL", "token_amount": tok_amt, "sol_amount": sol_amt,
                                  "price_sol": sol_amt / tok_amt if tok_amt else 0,
                                  "price_usd": None, "timestamp": ts, "datetime": dt})
                    continue

            if any(t.get("mint","").lower() == mint for t in tok_ins) and \
               any(t.get("mint","") == WSOL_MINT for t in tok_outs):
                tok  = next((t for t in tok_ins if t.get("mint","").lower() == mint), None)
                wsol = next((t for t in tok_outs if t.get("mint","") == WSOL_MINT), None)
                if tok and wsol:
                    sol_amt = _amt(wsol)
                    tok_amt = _amt(tok)
                    swaps.append({"type": "SELL", "token_amount": tok_amt, "sol_amount": sol_amt,
                                  "price_sol": sol_amt / tok_amt if tok_amt else 0,
                                  "price_usd": None, "timestamp": ts, "datetime": dt})
                    continue

        # ── Intent 2: tokenTransfers ───────────────────────────────────────
        transfers = tx.get("tokenTransfers", [])
        if transfers:
            tok_in   = next((t for t in transfers if t.get("mint","").lower() == mint
                             and t.get("toUserAccount")), None)
            tok_out  = next((t for t in transfers if t.get("mint","").lower() == mint
                             and t.get("fromUserAccount")), None)
            wsol_out = next((t for t in transfers if t.get("mint","") == WSOL_MINT
                             and t.get("fromUserAccount")), None)
            wsol_in  = next((t for t in transfers if t.get("mint","") == WSOL_MINT
                             and t.get("toUserAccount")), None)

            if wsol_out and tok_in:
                sol_amt = float(wsol_out.get("tokenAmount", 0) or 0)
                tok_amt = float(tok_in.get("tokenAmount", 0) or 0)
                if sol_amt > 0 and tok_amt > 0:
                    swaps.append({"type": "BUY", "token_amount": tok_amt, "sol_amount": sol_amt,
                                  "price_sol": sol_amt / tok_amt,
                                  "price_usd": None, "timestamp": ts, "datetime": dt})
                    continue

            if tok_out and wsol_in:
                sol_amt = float(wsol_in.get("tokenAmount", 0) or 0)
                tok_amt = float(tok_out.get("tokenAmount", 0) or 0)
                if sol_amt > 0 and tok_amt > 0:
                    swaps.append({"type": "SELL", "token_amount": tok_amt, "sol_amount": sol_amt,
                                  "price_sol": sol_amt / tok_amt,
                                  "price_usd": None, "timestamp": ts, "datetime": dt})
                    continue

        # ── Intent 3: accountData (Pump.fun raw, type=TRANSFER) ───────────
        account_data = tx.get("accountData", [])
        if not account_data:
            continue

        # Busca canvis de token pel mint objectiu i canvis de SOL natius
        token_delta = None
        sol_delta   = None

        for acc in account_data:
            # Canvi de token
            for tok in acc.get("tokenBalanceChanges", []):
                if tok.get("mint","").lower() == mint:
                    raw     = tok.get("rawTokenAmount", {})
                    dec     = int(raw.get("decimals", 6))
                    amount  = int(raw.get("tokenAmount", 0) or 0) / (10 ** dec)
                    token_delta = amount  # positiu = rebut (BUY), negatiu = enviat (SELL)

            # Canvi de SOL natiu
            native_change = acc.get("nativeBalanceChange", 0) or 0
            if native_change != 0 and sol_delta is None:
                sol_delta = native_change / 1e9

        if token_delta is not None and sol_delta is not None:
            if token_delta > 0 and sol_delta < 0:
                # Rebem token, perdem SOL → BUY
                sol_amt = abs(sol_delta)
                tok_amt = token_delta
                swaps.append({"type": "BUY", "token_amount": tok_amt, "sol_amount": sol_amt,
                              "price_sol": sol_amt / tok_amt if tok_amt else 0,
                              "price_usd": None, "timestamp": ts, "datetime": dt})
            elif token_delta < 0 and sol_delta > 0:
                # Perdem token, rebem SOL → SELL
                sol_amt = sol_delta
                tok_amt = abs(token_delta)
                swaps.append({"type": "SELL", "token_amount": tok_amt, "sol_amount": sol_amt,
                              "price_sol": sol_amt / tok_amt if tok_amt else 0,
                              "price_usd": None, "timestamp": ts, "datetime": dt})

    return sorted(swaps, key=lambda x: x["timestamp"])


# ─────────────────────────────────────────────
# PnL
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# WALLET PARENT — helpers
# ─────────────────────────────────────────────

def _extract_sol_senders(transactions: list, wallet: str) -> list:
    """
    Extreu les adreces que han enviat SOL a la wallet donada.
    Retorna llista de {sender, amount_sol, timestamp} ordenada per timestamp ASC.
    """
    wallet  = wallet.lower()
    senders = []

    for tx in transactions:
        tx_type = tx.get("type", "")

        # Helius marca transfers de SOL com TRANSFER o SOL_TRANSFER
        if tx_type not in ("TRANSFER", "SOL_TRANSFER", "UNKNOWN"):
            continue

        ts = tx.get("timestamp", 0)

        # Mirem nativeTransfers (SOL natiu)
        for nt in tx.get("nativeTransfers", []):
            to_acc   = (nt.get("toUserAccount")   or "").lower()
            from_acc = (nt.get("fromUserAccount") or "").lower()
            amount   = (nt.get("amount") or 0) / 1e9

            if to_acc == wallet and from_acc and from_acc != wallet and amount > 0.001:
                senders.append({
                    "sender":     nt.get("fromUserAccount"),
                    "amount_sol": amount,
                    "timestamp":  ts,
                    "datetime":   datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M UTC"),
                })

    return sorted(senders, key=lambda x: x["timestamp"])


async def _count_wallets_funded(session, parent_wallet: str) -> int:
    """
    Compta quantes wallets diferents ha finançat el pare.
    Agafa les seves txs de TRANSFER i compta destinataris únics.
    """
    txs = await _fetch_all_transactions(session, parent_wallet, limit=100)
    funded = set()
    parent_lower = parent_wallet.lower()

    for tx in txs:
        if tx.get("type") not in ("TRANSFER", "SOL_TRANSFER", "UNKNOWN"):
            continue
        for nt in tx.get("nativeTransfers", []):
            from_acc = (nt.get("fromUserAccount") or "").lower()
            to_acc   = (nt.get("toUserAccount")   or "").lower()
            amount   = (nt.get("amount") or 0) / 1e9
            if from_acc == parent_lower and to_acc and to_acc != parent_lower and amount > 0.001:
                funded.add(to_acc)

    return len(funded)


async def _get_wallet_age(session, wallet: str) -> Optional[str]:
    """Retorna la data de la primera transacció coneguda de la wallet."""
    url    = f"{HELIUS_BASE}/addresses/{wallet}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": 100}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            txs = await r.json()
            if not txs:
                return None
            # Les txs venen en ordre DESC, l'última és la més antiga
            oldest = txs[-1].get("timestamp", 0)
            if oldest:
                return datetime.fromtimestamp(oldest, tz=timezone.utc).strftime("%d %b %Y")
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# /wallet_parent — funció pública
# ─────────────────────────────────────────────

async def get_wallet_parent(wallet: str, hops: int = 2) -> str:
    """
    Troba la wallet pare d'una wallet operativa seguint els SOL transfers
    fins a hops nivells enrere. Detecta si el pare és un coordinador de cluster.
    """
    async with aiohttp.ClientSession() as session:
        # Pas 1: txs de la wallet objectiu (sense filtre de tipus per agafar transfers)
        txs = await _fetch_all_transactions(session, wallet, limit=100)

    if not txs:
        return "❌ No s'han trobat transaccions per aquesta wallet."

    # Pas 2: busca qui li ha enviat SOL (hop 1)
    senders = _extract_sol_senders(txs, wallet)

    if not senders:
        return (
            f"🔍 *Wallet:* `{wallet[:6]}...{wallet[-4:]}`\n\n"
            f"❓ No s'ha trobat cap transfer de SOL entrant.\n"
            f"Pot ser que la wallet s'hagi finançat via CEX directament "
            f"o que les txs siguin massa antigues per Helius."
        )

    # El primer sender és el candidat a pare (finançament inicial)
    first_sender = senders[0]
    parent       = first_sender["sender"]
    parent_short = f"`{parent[:6]}...{parent[-4:]}`"

    # Pas 3: anàlisi del pare
    async with aiohttp.ClientSession() as session:
        funded_count, parent_age, parent_txs = await asyncio.gather(
            _count_wallets_funded(session, parent),
            _get_wallet_age(session, parent),
            _fetch_all_transactions(session, parent, limit=100),
        )

    # Pas 4: hop 2 — qui ha finançat el pare?
    grandparent      = None
    grandparent_info = ""
    if hops >= 2 and parent_txs:
        gp_senders = _extract_sol_senders(parent_txs, parent)
        if gp_senders:
            grandparent       = gp_senders[0]["sender"]
            gp_short          = f"`{grandparent[:6]}...{grandparent[-4:]}`"
            gp_entity         = KNOWN_ENTITIES.get(grandparent, "")
            gp_entity_str     = f" ({gp_entity})" if gp_entity else ""
            async with aiohttp.ClientSession() as session:
                gp_funded = await _count_wallets_funded(session, grandparent)
            grandparent_info  = (
                f"\n\n🔗 *Hop 2 — Avi:* {gp_short}{gp_entity_str}\n"
                f"  • Wallets finançades: {gp_funded}"
            )
            if gp_funded >= 5:
                grandparent_info += " 🚨 *possible coordinador major*"

    # Pas 5: classifica el pare
    known_entity  = KNOWN_ENTITIES.get(parent, "")
    entity_str    = f" — *{known_entity}*" if known_entity else ""

    if known_entity:
        cluster_verdict = "🏦 *Origen: Exchange conegut* — actor humà normal, difícil de seguir més amunt."
    elif funded_count >= 10:
        cluster_verdict = f"🚨 *Alta probabilitat de coordinador de cluster* — ha finançat {funded_count} wallets."
    elif funded_count >= 3:
        cluster_verdict = f"⚠️ *Possible coordinador* — ha finançat {funded_count} wallets."
    else:
        cluster_verdict = f"✅ *Pare individual* — ha finançat {funded_count} wallet(s), no sembla coordinador."

    # Pas 6: tots els senders (per si n'hi ha més d'un)
    extra_senders = ""
    if len(senders) > 1:
        extra_lines = []
        for s in senders[1:4]:  # màxim 3 addicionals
            s_entity = KNOWN_ENTITIES.get(s["sender"], "")
            s_entity_str = f" ({s_entity})" if s_entity else ""
            extra_lines.append(
                f"  • `{s['sender'][:6]}...{s['sender'][-4:]}`{s_entity_str} "
                f"— {s['amount_sol']:.3f} SOL el {s['datetime']}"
            )
        extra_senders = "\n\n📨 *Altres senders de SOL:*\n" + "\n".join(extra_lines)

    age_str = f"\n  • Primera tx: {parent_age}" if parent_age else ""

    lines = [
        f"🔍 *Wallet analitzada:* `{wallet[:6]}...{wallet[-4:]}`",
        f"",
        f"👆 *Hop 1 — Pare directe:* {parent_short}{entity_str}",
        f"  • Va enviar: {first_sender['amount_sol']:.3f} SOL el {first_sender['datetime']}",
        f"  • Wallets finançades pel pare: {funded_count}{age_str}",
        f"",
        f"📊 *Veredicte:* {cluster_verdict}",
    ]

    if grandparent_info:
        lines.append(grandparent_info)

    if extra_senders:
        lines.append(extra_senders)

    lines += [
        f"",
        f"🔗 *Solscan:* https://solscan.io/account/{parent}",
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────
# /wallet_pnl
# ─────────────────────────────────────────────

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

    buys  = [s for s in swaps if s["type"] == "BUY"]
    sells = [s for s in swaps if s["type"] == "SELL"]

    # Preu mig USD d'entrada i sortida
    buy_usd_prices  = [b["price_usd"] for b in buys  if b["price_usd"]]
    sell_usd_prices = [s["price_usd"] for s in sells if s["price_usd"]]
    avg_buy_usd  = sum(buy_usd_prices)  / len(buy_usd_prices)  if buy_usd_prices  else None
    avg_sell_usd = sum(sell_usd_prices) / len(sell_usd_prices) if sell_usd_prices else None

    lines = [
        f"🔍 *Wallet:* `{wallet[:6]}...{wallet[-4:]}`",
        f"🪙 *Token:* {name} (${symbol})",
        f"",
        f"📥 *Compres:* {len(buys)} txs",
        f"  • Primera: {buys[0]['datetime']}",
        f"  • Última:  {buys[-1]['datetime']}",
        f"  • SOL total: {pnl['sol_spent']:.4f} SOL",
    ]
    if avg_buy_usd:
        lines.append(f"  • Preu mig entrada: ${avg_buy_usd:.6f}")

    if sells:
        lines += [
            f"",
            f"📤 *Vendes:* {len(sells)} txs",
            f"  • Primera: {sells[0]['datetime']}",
            f"  • Última:  {sells[-1]['datetime']}",
            f"  • SOL total rebut: {pnl['sol_received']:.4f} SOL",
        ]
        if avg_sell_usd:
            lines.append(f"  • Preu mig sortida: ${avg_sell_usd:.6f}")
    else:
        lines += ["", "📤 *Vendes:* cap (posició oberta)"]

    lines += [
        f"",
        f"─────────────────────",
        f"💰 *PnL Realitzat:* {sign}{pnl['realized_sol']:.4f} SOL ({sign}{pnl['roi_pct']:.1f}%) {emoji}",
    ]
    if pnl["realized_usd"] is not None:
        us = "+" if pnl["realized_usd"] >= 0 else ""
        lines.append(f"💵 *PnL USD:* {us}${pnl['realized_usd']:.2f}")
    if pnl["time_in_pos"]:
        lines.append(f"⏱ *Temps en posició:* {pnl['time_in_pos']}")
    if pnl["tok_left"] > 0:
        if pnl["unrealized_usd"] is not None:
            lines.append(f"📦 *Unrealized:* {pnl['tok_left']:,.0f} tokens × ${current_usd:.6f} = ${pnl['unrealized_usd']:.2f}")
        else:
            lines.append(f"📦 *Posició oberta:* {pnl['tok_left']:,.0f} tokens restants")

    return "\n".join(lines)

# ─────────────────────────────────────────────
# /wallet_score
# ─────────────────────────────────────────────

async def get_wallet_score(wallet: str, num_tokens: int = 20) -> str:
    async with aiohttp.ClientSession() as session:
        txs = await _fetch_transactions(session, wallet, limit=200)
    if not txs:
        return "❌ No s'han trobat transaccions per aquesta wallet."

    mints_seen, seen_set = [], set()
    for tx in txs:
        swap = tx.get("events", {}).get("swap", {})
        for t in swap.get("tokenOutputs", []) + swap.get("tokenInputs", []):
            mint = t.get("mint", "")
            if mint and mint not in SKIP_MINTS and mint not in seen_set:
                seen_set.add(mint)
                mints_seen.append(mint)
        # Fallback: també mira tokenTransfers
        for t in tx.get("tokenTransfers", []):
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


# ─────────────────────────────────────────────
# /compare
# ─────────────────────────────────────────────

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