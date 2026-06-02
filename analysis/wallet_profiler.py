import requests
from datetime import datetime, timedelta
from config import ETHERSCAN_API_KEY, HELIUS_API_KEY

# Adreces conegudes de CEXs — per detectar si la wallet envia a exchange
CEX_ADDRESSES = {
    "0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE": "Binance",
    "0xD551234Ae421e3BCBA99A0Da6d736074f22192FF": "Binance",
    "0x564286362092D8e7936f0549571a803B203aAceD": "Binance",
    "0xa910f92ACdAf488fa6eF02174fb86208Ad7722ba": "Binance",
    "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b": "OKX",
    "0xEB2629a2734e272Bcc07BDA959863f316F4bD4Cf": "Coinbase",
    "0x503828976D22510aad0201ac7EC88293211D23Da": "Coinbase",
    "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3": "Coinbase",
    "0x77696bb39917C91A0c3908D577d5e322095425cA": "Kraken",
    "0xDA9dfa130Df4dE4673b89022EE50ff26f6EA73Cf": "Kraken",
}

SOL_PER_USD = 150  # aproximació — actualitza si cal


# ── EVM Profiler ───────────────────────────────────────────────────────────────

def profile_evm_wallet(address: str) -> dict:
    """
    Retorna un perfil complet d'una wallet EVM.
    Combina dades d'Etherscan per construir un perfil útil.
    """
    address = address.lower()
    profile = {
        "address": address,
        "chain": "evm",
        "error": None,

        # Identitat
        "first_tx_date": None,
        "last_tx_date": None,
        "age_days": None,
        "is_contract": False,

        # Capital
        "eth_balance": 0,
        "eth_balance_usd": 0,
        "top_tokens": [],

        # Activitat
        "total_txns": 0,
        "txns_last_30d": 0,
        "active_days_last_30d": 0,
        "avg_txns_per_day": 0,

        # Comportament
        "protocols_used": [],
        "cex_interactions": [],
        "last_10_txns": [],

        # Patró de trading
        "avg_tx_value_eth": 0,
        "largest_tx_eth": 0,
        "sends_to_cex": False,
        "profile_type": None,  # holder, trader, smart_money, etc.
    }

    try:
        # 1. Balanç ETH
        eth_balance = _get_eth_balance(address)
        profile["eth_balance"] = eth_balance
        profile["eth_balance_usd"] = eth_balance * 3000  # aproximació

        # 2. Totes les transaccions
        txns = _get_transactions(address, limit=1000)
        if not txns:
            profile["error"] = "No transactions found or API error"
            return profile

        profile["total_txns"] = len(txns)

        # 3. Primera i última transacció
        if txns:
            first_ts = int(txns[-1]["timeStamp"])
            last_ts = int(txns[0]["timeStamp"])
            profile["first_tx_date"] = datetime.utcfromtimestamp(first_ts).strftime("%Y-%m-%d")
            profile["last_tx_date"] = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d")
            profile["age_days"] = (datetime.utcnow() - datetime.utcfromtimestamp(first_ts)).days

        # 4. Activitat últims 30 dies
        cutoff_30d = datetime.utcnow() - timedelta(days=30)
        recent_txns = [
            t for t in txns
            if datetime.utcfromtimestamp(int(t["timeStamp"])) > cutoff_30d
        ]
        profile["txns_last_30d"] = len(recent_txns)
        active_days = set(
            datetime.utcfromtimestamp(int(t["timeStamp"])).date()
            for t in recent_txns
        )
        profile["active_days_last_30d"] = len(active_days)
        profile["avg_txns_per_day"] = round(len(recent_txns) / 30, 2)

        # 5. Valors de transaccions
        eth_values = [int(t["value"]) / 1e18 for t in txns if int(t["value"]) > 0]
        if eth_values:
            profile["avg_tx_value_eth"] = round(sum(eth_values) / len(eth_values), 4)
            profile["largest_tx_eth"] = round(max(eth_values), 4)

        # 6. Interaccions amb CEXs
        cex_hits = []
        for tx in txns[:200]:  # últimes 200 txns
            to_addr = tx.get("to", "").lower()
            for cex_addr, cex_name in CEX_ADDRESSES.items():
                if to_addr == cex_addr.lower():
                    if cex_name not in cex_hits:
                        cex_hits.append(cex_name)
                    profile["sends_to_cex"] = True
        profile["cex_interactions"] = cex_hits

        # 7. Protocols usats (via input data i contractes)
        protocols = _detect_protocols_used(txns[:200])
        profile["protocols_used"] = protocols

        # 8. Últimes 10 transaccions rellevants
        last_10 = []
        for tx in txns[:10]:
            value_eth = int(tx["value"]) / 1e18
            ts = datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%Y-%m-%d %H:%M")
            direction = "OUT" if tx["from"].lower() == address else "IN"
            to_label = _get_address_label(tx.get("to", ""))
            last_10.append({
                "date": ts,
                "direction": direction,
                "value_eth": round(value_eth, 4),
                "value_usd": round(value_eth * 3000, 0),
                "to": to_label or tx.get("to", "")[:10] + "...",
                "hash": tx["hash"]
            })
        profile["last_10_txns"] = last_10

        # 9. Tokens ERC-20
        token_txns = _get_token_transfers(address)
        top_tokens = _extract_top_tokens(token_txns)
        profile["top_tokens"] = top_tokens

        # 10. Classificació del perfil
        profile["profile_type"] = _classify_wallet(profile)

    except Exception as e:
        profile["error"] = str(e)

    return profile


# ── Solana Profiler ────────────────────────────────────────────────────────────

def profile_solana_wallet(address: str) -> dict:
    """
    Retorna un perfil d'una wallet Solana via Helius.
    """
    profile = {
        "address": address,
        "chain": "solana",
        "error": None,
        "sol_balance": 0,
        "sol_balance_usd": 0,
        "total_txns": 0,
        "last_tx_date": None,
        "top_tokens": [],
        "uses_dex": False,
        "dexs_used": [],
        "last_10_txns": [],
        "profile_type": None,
    }

    try:
        # 1. Balanç SOL
        sol_balance = _get_sol_balance(address)
        profile["sol_balance"] = sol_balance
        profile["sol_balance_usd"] = sol_balance * SOL_PER_USD

        # 2. Transaccions recents via Helius
        txns = _get_solana_transactions(address, limit=100)
        profile["total_txns"] = len(txns)

        if txns:
            last_ts = txns[0].get("timestamp", 0)
            profile["last_tx_date"] = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M")

        # 3. Detecta ús de DEXs
        dexs = set()
        for tx in txns:
            source = tx.get("source", "").upper()
            if source in ["JUPITER", "RAYDIUM", "ORCA", "METEORA", "PUMP_FUN"]:
                dexs.add(source)
        profile["dexs_used"] = list(dexs)
        profile["uses_dex"] = len(dexs) > 0

        # 4. Últimes 10 transaccions
        last_10 = []
        for tx in txns[:10]:
            ts = datetime.utcfromtimestamp(tx.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M")
            tx_type = tx.get("type", "UNKNOWN")
            source = tx.get("source", "")
            description = tx.get("description", "")
            last_10.append({
                "date": ts,
                "type": tx_type,
                "source": source,
                "description": description[:80] if description else "",
                "signature": tx.get("signature", "")[:20] + "..."
            })
        profile["last_10_txns"] = last_10

        # 5. Classificació
        profile["profile_type"] = _classify_solana_wallet(profile)

    except Exception as e:
        profile["error"] = str(e)

    return profile


# ── Formatador de missatge Telegram ───────────────────────────────────────────

def format_wallet_profile(profile: dict) -> str:
    """Formata el perfil per enviar-lo per Telegram"""

    if profile.get("error") and not profile.get("total_txns"):
        return f"❌ Error analitzant wallet: {profile['error']}"

    addr = profile["address"]
    short_addr = addr[:6] + "..." + addr[-4:]
    chain = profile["chain"]

    lines = []

    if chain == "evm":
        lines.append(f"🔍 *Wallet Profile — EVM*")
        lines.append(f"`{addr}`\n")

        # Identitat
        lines.append(f"*📋 Identitat*")
        lines.append(f"   Activa des de: {profile.get('first_tx_date', 'N/A')} ({profile.get('age_days', '?')} dies)")
        lines.append(f"   Última activitat: {profile.get('last_tx_date', 'N/A')}")
        lines.append(f"   Tipus: *{profile.get('profile_type', 'Unknown')}*\n")

        # Capital
        lines.append(f"*💰 Capital*")
        lines.append(f"   ETH: {profile['eth_balance']:.3f} (~${profile['eth_balance_usd']:,.0f})")
        if profile.get("top_tokens"):
            for token in profile["top_tokens"][:3]:
                lines.append(f"   {token['symbol']}: {token['balance']}")
        lines.append("")

        # Activitat
        lines.append(f"*📊 Activitat*")
        lines.append(f"   Total txns: {profile['total_txns']}")
        lines.append(f"   Últims 30d: {profile['txns_last_30d']} txns ({profile['active_days_last_30d']} dies actiu)")
        lines.append(f"   Mitjana: {profile['avg_txns_per_day']} txns/dia\n")

        # Comportament
        lines.append(f"*🎯 Comportament*")
        lines.append(f"   Tx mitjana: {profile['avg_tx_value_eth']:.3f} ETH")
        lines.append(f"   Tx màxima: {profile['largest_tx_eth']:.3f} ETH")

        if profile["protocols_used"]:
            lines.append(f"   Protocols: {', '.join(profile['protocols_used'][:5])}")

        if profile["cex_interactions"]:
            lines.append(f"   ⚠️ Envia a CEX: {', '.join(profile['cex_interactions'])}")
        lines.append("")

        # Últimes txns
        if profile["last_10_txns"]:
            lines.append(f"*🕐 Últimes transaccions*")
            for tx in profile["last_10_txns"][:5]:
                icon = "📤" if tx["direction"] == "OUT" else "📥"
                lines.append(
                    f"   {icon} {tx['date']} | "
                    f"{tx['value_eth']} ETH → {tx['to']}"
                )

        # Links
        lines.append(f"\n[Etherscan](https://etherscan.io/address/{addr}) | "
                    f"[Arkham](https://platform.arkhamintelligence.com/explorer/address/{addr})")

    else:  # Solana
        lines.append(f"🔍 *Wallet Profile — Solana*")
        lines.append(f"`{addr}`\n")

        lines.append(f"*💰 Capital*")
        lines.append(f"   SOL: {profile['sol_balance']:.2f} (~${profile['sol_balance_usd']:,.0f})\n")

        lines.append(f"*📊 Activitat*")
        lines.append(f"   Txns analitzades: {profile['total_txns']}")
        lines.append(f"   Última activitat: {profile.get('last_tx_date', 'N/A')}")
        lines.append(f"   Tipus: *{profile.get('profile_type', 'Unknown')}*\n")

        if profile["dexs_used"]:
            lines.append(f"*🔄 DEXs usats*")
            lines.append(f"   {', '.join(profile['dexs_used'])}\n")

        if profile["last_10_txns"]:
            lines.append(f"*🕐 Últimes transaccions*")
            for tx in profile["last_10_txns"][:5]:
                lines.append(f"   [{tx['date']}] {tx['type']} via {tx['source']}")
                if tx["description"]:
                    lines.append(f"   _{tx['description']}_")

        lines.append(f"\n[Solscan](https://solscan.io/account/{addr}) | "
                    f"[Helius](https://xray.helius.xyz/account/{addr})")

    return "\n".join(lines)


# ── Helpers EVM ───────────────────────────────────────────────────────────────

def _get_eth_balance(address):
    try:
        r = requests.get(
            "https://api.etherscan.io/api",
            params={
                "module": "account", "action": "balance",
                "address": address, "tag": "latest",
                "apikey": ETHERSCAN_API_KEY
            }, timeout=10
        )
        return int(r.json()["result"]) / 1e18
    except Exception:
        return 0


def _get_transactions(address, limit=1000):
    try:
        r = requests.get(
            "https://api.etherscan.io/api",
            params={
                "module": "account", "action": "txlist",
                "address": address, "startblock": 0,
                "endblock": 99999999, "page": 1,
                "offset": limit, "sort": "desc",
                "apikey": ETHERSCAN_API_KEY
            }, timeout=15
        )
        data = r.json()
        return data["result"] if data["status"] == "1" else []
    except Exception:
        return []


def _get_token_transfers(address):
    try:
        r = requests.get(
            "https://api.etherscan.io/api",
            params={
                "module": "account", "action": "tokentx",
                "address": address, "page": 1,
                "offset": 200, "sort": "desc",
                "apikey": ETHERSCAN_API_KEY
            }, timeout=15
        )
        data = r.json()
        return data["result"] if data["status"] == "1" else []
    except Exception:
        return []


def _extract_top_tokens(token_txns):
    """Extreu els tokens més actius de les transferències"""
    token_counts = {}
    for tx in token_txns:
        symbol = tx.get("tokenSymbol", "?")
        if symbol not in token_counts:
            token_counts[symbol] = {
                "symbol": symbol,
                "name": tx.get("tokenName", ""),
                "balance": tx.get("tokenDecimal", ""),
                "count": 0
            }
        token_counts[symbol]["count"] += 1

    sorted_tokens = sorted(token_counts.values(), key=lambda x: x["count"], reverse=True)
    return sorted_tokens[:5]


def _detect_protocols_used(txns):
    """Detecta protocols coneguts per adreça de contracte"""
    KNOWN_CONTRACTS = {
        "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": "Uniswap V2",
        "0xe592427a0aece92de3edee1f18e0157c05861564": "Uniswap V3",
        "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": "Uniswap V3",
        "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2": "Aave V3",
        "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9": "Aave V2",
        "0xc36442b4a4522e871399cd717abdd847ab11fe88": "Uniswap V3 LP",
        "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch",
        "0x3ee18b2214aff97000d974cf647e7c347e8fa585": "Wormhole Bridge",
        "0xa0c68c638235ee32657e8f720a23cec1bfc77c77": "Polygon Bridge",
    }

    protocols = set()
    for tx in txns:
        to = (tx.get("to") or "").lower()
        if to in KNOWN_CONTRACTS:
            protocols.add(KNOWN_CONTRACTS[to])
    return list(protocols)


def _get_address_label(address):
    """Retorna etiqueta coneguda d'una adreça"""
    if not address:
        return None
    address = address.lower()
    for cex_addr, name in CEX_ADDRESSES.items():
        if address == cex_addr.lower():
            return f"[{name}]"
    return None


def _classify_wallet(profile):
    """Classifica el tipus de wallet basant-se en el comportament"""
    txns_per_day = profile.get("avg_txns_per_day", 0)
    age_days = profile.get("age_days", 0) or 1
    sends_to_cex = profile.get("sends_to_cex", False)
    eth_balance = profile.get("eth_balance", 0)
    largest_tx = profile.get("largest_tx_eth", 0)
    protocols = profile.get("protocols_used", [])

    if txns_per_day > 20:
        return "🤖 Bot / HFT"
    if txns_per_day > 5 and protocols:
        return "⚡ DeFi Power User"
    if largest_tx > 100 and sends_to_cex:
        return "🐋 Whale Trader"
    if largest_tx > 100 and not sends_to_cex:
        return "💎 Whale Holder"
    if txns_per_day < 0.1 and eth_balance > 10:
        return "🏦 Long-term Holder"
    if sends_to_cex and txns_per_day > 1:
        return "📈 Active Trader"
    if age_days < 30:
        return "🆕 New Wallet"
    return "👤 Regular User"


# ── Helpers Solana ─────────────────────────────────────────────────────────────

def _get_sol_balance(address):
    try:
        r = requests.post(
            f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getBalance",
                "params": [address]
            }, timeout=10
        )
        result = r.json().get("result", {})
        lamports = result.get("value", 0)
        return lamports / 1e9
    except Exception:
        return 0


def _get_solana_transactions(address, limit=100):
    try:
        r = requests.get(
            f"https://api.helius.xyz/v0/addresses/{address}/transactions",
            params={"api-key": HELIUS_API_KEY, "limit": limit},
            timeout=15
        )
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _classify_solana_wallet(profile):
    dexs = profile.get("dexs_used", [])
    total_txns = profile.get("total_txns", 0)

    if "PUMP_FUN" in dexs and total_txns > 50:
        return "🎰 Memecoin Trader"
    if "JUPITER" in dexs and total_txns > 30:
        return "⚡ Active DEX Trader"
    if len(dexs) > 2:
        return "🔄 Multi-DEX User"
    if dexs:
        return "📊 DEX User"
    if total_txns < 10:
        return "🆕 New / Inactive"
    return "👤 Regular User"