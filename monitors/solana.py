import requests
from datetime import datetime, timedelta
from config import HELIUS_API_KEY
from database import should_send_alert, mark_alert_sent

HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Thresholds
MIN_WALLETS_CLUSTER = 4          # mínim wallets comprant el mateix token
MIN_BUY_USD = 2_000              # mínim $2k per compra
MIN_COORDINATED_USD = 30_000     # mínim $30k per alerta de capital coordinat
MIN_LIQUIDITY_USD = 50_000       # mínim liquiditat del pool per ser rellevant
LIQUIDITY_DROP_PCT = 30          # % de caiguda de liquiditat per alertar
SOL_PRICE = 150


# ── Clusters ───────────────────────────────────────────────────────────────────

def get_active_solana_tokens(limit=30) -> list:
    """
    Agafa tokens actius a Solana via DexScreener.
    Filtra per liquiditat mínima per evitar scams.
    """
    try:
        r = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=10
        )
        if r.status_code != 200:
            return []

        tokens = r.json()
        solana_tokens = [
            t for t in tokens
            if t.get("chainId") == "solana"
        ]
        return solana_tokens[:limit]

    except Exception:
        return []


def get_token_buyers(token_address: str) -> dict:
    """
    Agafa compradors recents d'un token via Helius.
    Retorna dict de wallets → {count, total_usd, timestamps}
    """
    from collections import defaultdict
    buyers = defaultdict(lambda: {"count": 0, "total_usd": 0, "timestamps": []})
    cutoff = datetime.utcnow() - timedelta(hours=12)

    try:
        r = requests.get(
            f"https://api.helius.xyz/v0/addresses/{token_address}/transactions",
            params={
                "api-key": HELIUS_API_KEY,
                "limit": 100,
                "type": "SWAP"
            },
            timeout=15
        )
        if r.status_code != 200:
            return buyers

        for tx in r.json():
            tx_time = datetime.utcfromtimestamp(tx.get("timestamp", 0))
            if tx_time < cutoff:
                continue

            buyer = tx.get("feePayer", "")
            if not buyer:
                continue

            # Calcula valor en USD
            native = tx.get("nativeTransfers", [])
            sol_spent = sum(
                t.get("amount", 0) for t in native
                if t.get("fromUserAccount") == buyer
            ) / 1e9
            usd_value = sol_spent * SOL_PRICE

            if usd_value >= MIN_BUY_USD:
                buyers[buyer]["count"] += 1
                buyers[buyer]["total_usd"] += usd_value
                buyers[buyer]["timestamps"].append(tx_time)

    except Exception:
        pass

    return buyers


def check_solana_clusters() -> list:
    """
    Detecta clusters de wallets comprant el mateix token.
    """
    alerts = []
    tokens = get_active_solana_tokens()

    for token in tokens:
        token_address = token.get("tokenAddress", "")
        if not token_address:
            continue

        # Agafa info de preu/liquiditat via DexScreener
        try:
            pair_r = requests.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                timeout=10
            )
            if pair_r.status_code != 200:
                continue

            pairs = pair_r.json().get("pairs", [])
            if not pairs:
                continue

            # Agafa el pair amb més liquiditat
            best_pair = max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
            liquidity = float(best_pair.get("liquidity", {}).get("usd", 0) or 0)
            volume_24h = float(best_pair.get("volume", {}).get("h24", 0) or 0)
            price_change_24h = float(best_pair.get("priceChange", {}).get("h24", 0) or 0)
            token_name = best_pair.get("baseToken", {}).get("name", "Unknown")
            token_symbol = best_pair.get("baseToken", {}).get("symbol", "?")

            # Filtra tokens amb poca liquiditat
            if liquidity < MIN_LIQUIDITY_USD:
                continue

        except Exception:
            continue

        # Analitza compradors
        buyers = get_token_buyers(token_address)
        if not buyers:
            continue

        unique_buyers = [
            w for w, d in buyers.items()
            if d["total_usd"] >= MIN_BUY_USD
        ]
        big_buyers = [
            w for w, d in buyers.items()
            if d["total_usd"] >= MIN_COORDINATED_USD
        ]

        # Alerta 1 — Cluster de wallets
        if len(unique_buyers) >= MIN_WALLETS_CLUSTER:
            total_vol = sum(buyers[w]["total_usd"] for w in unique_buyers)
            alert_key = f"sol_cluster_{token_address}_{datetime.utcnow().strftime('%Y-%m-%d')}"

            if should_send_alert(alert_key, cooldown_hours=12):
                alerts.append({
                    "key": alert_key,
                    "message": (
                        f"🔍 *Cluster Solana — {token_name} (${token_symbol})*\n\n"
                        f"*{len(unique_buyers)} wallets* comprant en 12h\n"
                        f"💰 Volum cluster: ${total_vol:,.0f}\n"
                        f"💧 Liquiditat: ${liquidity:,.0f}\n"
                        f"📊 Volum 24h: ${volume_24h:,.0f}\n"
                        f"📈 Canvi 24h: {price_change_24h:+.1f}%\n\n"
                        f"❓ *Investiga:*\n"
                        f"   → Són wallets noves o amb historial?\n"
                        f"   → Hi ha narrativa darrere aquest token?\n"
                        f"   → Quin és el market cap?\n\n"
                        f"[DexScreener](https://dexscreener.com/solana/{token_address})"
                    )
                })

        # Alerta 2 — Capital coordinat gran
        if len(big_buyers) >= 2:
            total_capital = sum(buyers[w]["total_usd"] for w in big_buyers)
            alert_key = f"sol_capital_{token_address}_{datetime.utcnow().strftime('%Y-%m-%d')}"

            if should_send_alert(alert_key, cooldown_hours=12):
                alerts.append({
                    "key": alert_key,
                    "message": (
                        f"🐋 *Capital coordinat Solana — {token_name} (${token_symbol})*\n\n"
                        f"*{len(big_buyers)} wallets* movent "
                        f">${MIN_COORDINATED_USD/1000:.0f}k cadascuna\n"
                        f"💰 Capital total: ${total_capital:,.0f}\n"
                        f"💧 Liquiditat: ${liquidity:,.0f}\n"
                        f"📈 Canvi 24h: {price_change_24h:+.1f}%\n\n"
                        f"[DexScreener](https://dexscreener.com/solana/{token_address})"
                    )
                })

    return alerts


# ── Liquiditat pools ───────────────────────────────────────────────────────────

def check_liquidity_drops() -> list:
    """
    Detecta caigudes brusques de liquiditat en pools de Solana.
    LP retirant liquiditat = possible rug o exit imminent.
    """
    alerts = []

    try:
        r = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=10
        )
        if r.status_code != 200:
            return alerts

        tokens = [t for t in r.json() if t.get("chainId") == "solana"]

        for token in tokens[:20]:
            token_address = token.get("tokenAddress", "")
            if not token_address:
                continue

            try:
                pair_r = requests.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                    timeout=10
                )
                if pair_r.status_code != 200:
                    continue

                pairs = pair_r.json().get("pairs", [])
                if not pairs:
                    continue

                best_pair = max(
                    pairs,
                    key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0)
                )
                liquidity_now = float(best_pair.get("liquidity", {}).get("usd", 0) or 0)
                liquidity_change = float(best_pair.get("liquidity", {}).get("change24h", 0) or 0)
                token_name = best_pair.get("baseToken", {}).get("name", "Unknown")
                token_symbol = best_pair.get("baseToken", {}).get("symbol", "?")

                if liquidity_now < MIN_LIQUIDITY_USD:
                    continue

                # Alerta si liquiditat ha caigut >30% en 24h
                if liquidity_change <= -LIQUIDITY_DROP_PCT:
                    alert_key = f"sol_liq_{token_address}_{datetime.utcnow().strftime('%Y-%m-%d')}"

                    if should_send_alert(alert_key, cooldown_hours=12):
                        alerts.append({
                            "key": alert_key,
                            "message": (
                                f"💧 *Caiguda liquiditat — {token_name} (${token_symbol})*\n\n"
                                f"Liquiditat: ${liquidity_now:,.0f} "
                                f"({liquidity_change:+.1f}% en 24h)\n\n"
                                f"⚠️ *LP retirant liquiditat — possible exit*\n\n"
                                f"❓ *Investiga:*\n"
                                f"   → On van els fons dels LPs?\n"
                                f"   → El preu ha caigut també?\n"
                                f"   → És un rug o rotació normal?\n\n"
                                f"[DexScreener](https://dexscreener.com/solana/{token_address})"
                            )
                        })

            except Exception:
                continue

    except Exception:
        pass

    return alerts


# ── Monitor principal ──────────────────────────────────────────────────────────

def check_solana() -> list:
    all_alerts = []
    all_alerts.extend(check_solana_clusters())
    all_alerts.extend(check_liquidity_drops())
    return all_alerts