import requests
from datetime import datetime, timedelta
from config import ETHERSCAN_API_KEY
from database import (
    get_wallets, get_protocols,
    should_send_alert, mark_alert_sent
)

ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"
ETHERSCAN_CHAIN = "1"

# Threshold mínim per alertar
MIN_VALUE_ETH_WALLET = 50       # wallets seguides → CEX
MIN_VALUE_ETH_PROTOCOL = 200    # txns grans en protocols watchlist
ETH_PRICE = 3000

CEX_ADDRESSES = {
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be": "Binance",
    "0xd551234ae421e3bcba99a0da6d736074f22192ff": "Binance",
    "0x564286362092d8e7936f0549571a803b203aaced": "Binance",
    "0xa910f92acdaf488fa6ef02174fb86208ad7722ba": "Binance",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0xeb2629a2734e272bcc07bda959863f316f4bd4cf": "Coinbase",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase",
    "0x77696bb39917c91a0c3908d577d5e322095425ca": "Kraken",
    "0xda9dfa130df4de4673b89022ee50ff26f6ea73cf": "Kraken",
}

# Adreces conegudes de contractes de protocols
# slug → adreça del contracte principal
PROTOCOL_CONTRACTS = {
    "aave":       "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",  # Aave V3 Pool
    "uniswap":    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",  # Uniswap V3 Router
    "lido":       "0xae7ab96520de3a18e5e111b5eaab095312d7fe84",  # stETH
    "eigenlayer": "0x858646372cc42e1a627fce94aa7a7033e7cf075a",  # EigenLayer Strategy
    "pendle":     "0x888888888889758f76e7103c6cbf23abbf58f946",  # Pendle Router
    "curve-dex":  "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",  # Curve Router
    "makerdao":   "0x9759a6ac90977b93b58547b4a71c78317f391a28",  # MakerDAO PSM
}


# ── Mode A — Wallets seguides → CEX ───────────────────────────────────────────

def check_followed_wallets_to_cex() -> list:
    """
    Detecta quan una wallet seguida envia fons a un CEX.
    Senyal fort de venda imminent.
    """
    alerts = []
    wallets = get_wallets(chain="evm")
    cutoff = datetime.utcnow() - timedelta(hours=1)

    for wallet in wallets:
        address = wallet["address"].lower()
        label = wallet["label"] or address[:6] + "..." + address[-4:]

        try:
            r = requests.get(
                ETHERSCAN_BASE,
                params={
                    "chainid": ETHERSCAN_CHAIN,
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "page": 1,
                    "offset": 20,
                    "sort": "desc",
                    "apikey": ETHERSCAN_API_KEY
                },
                timeout=10
            )
            data = r.json()
            if data["status"] != "1":
                continue

            for tx in data["result"]:
                tx_time = datetime.utcfromtimestamp(int(tx["timeStamp"]))
                if tx_time < cutoff:
                    break

                to_addr = tx.get("to", "").lower()
                cex_name = CEX_ADDRESSES.get(to_addr)
                if not cex_name:
                    continue

                value_eth = int(tx["value"]) / 1e18
                if value_eth < MIN_VALUE_ETH_WALLET:
                    continue

                alert_key = f"cex_flow_{tx['hash']}"
                if not should_send_alert(alert_key, cooldown_hours=24):
                    continue

                value_usd = value_eth * ETH_PRICE
                alerts.append({
                    "key": alert_key,
                    "message": (
                        f"🏦 *CEX Inflow — {label}*\n\n"
                        f"📤 `{value_eth:.2f}` ETH (~${value_usd:,.0f})\n"
                        f"   → *{cex_name}*\n"
                        f"   🕐 {tx_time.strftime('%H:%M UTC')}\n\n"
                        f"⚠️ *Possible venda imminent*\n\n"
                        f"❓ Preguntes:\n"
                        f"   → Altres wallets fan el mateix?\n"
                        f"   → Hi ha unlock proper d'aquest protocol?\n"
                        f"   → El TVL del protocol està caient?\n\n"
                        f"[Etherscan](https://etherscan.io/tx/{tx['hash']})"
                    )
                })

        except Exception:
            continue

    return alerts


# ── Mode C — Txns grans en protocols watchlist ─────────────────────────────────

def check_protocol_large_txns() -> list:
    """
    Detecta txns grans en protocols de la watchlist.
    Útil per descobrir smart money nou.
    """
    alerts = []
    protocols = get_protocols()
    cutoff = datetime.utcnow() - timedelta(hours=6)

    for protocol in protocols:
        slug = protocol["defillama_slug"]
        name = protocol["name"]
        contract = PROTOCOL_CONTRACTS.get(slug)

        if not contract:
            continue

        try:
            r = requests.get(
                ETHERSCAN_BASE,
                params={
                    "chainid": ETHERSCAN_CHAIN,
                    "module": "account",
                    "action": "txlist",
                    "address": contract,
                    "page": 1,
                    "offset": 50,
                    "sort": "desc",
                    "apikey": ETHERSCAN_API_KEY
                },
                timeout=10
            )
            data = r.json()
            if data["status"] != "1":
                continue

            for tx in data["result"]:
                tx_time = datetime.utcfromtimestamp(int(tx["timeStamp"]))
                if tx_time < cutoff:
                    break

                value_eth = int(tx["value"]) / 1e18
                if value_eth < MIN_VALUE_ETH_PROTOCOL:
                    continue

                # Ignora si és una adreça CEX coneguda (és normal)
                from_addr = tx.get("from", "").lower()
                if from_addr in CEX_ADDRESSES:
                    continue

                alert_key = f"protocol_tx_{tx['hash']}"
                if not should_send_alert(alert_key, cooldown_hours=24):
                    continue

                value_usd = value_eth * ETH_PRICE
                direction = "📥 IN" if tx.get("to", "").lower() == contract.lower() else "📤 OUT"

                alerts.append({
                    "key": alert_key,
                    "message": (
                        f"🔍 *Txn gran — {name}*\n\n"
                        f"{direction} `{value_eth:.0f}` ETH (~${value_usd:,.0f})\n"
                        f"   From: `{from_addr[:6]}...{from_addr[-4:]}`\n"
                        f"   🕐 {tx_time.strftime('%H:%M UTC')}\n\n"
                        f"❓ *Qui és aquesta wallet?*\n"
                        f"`/wallet {tx.get('from', '')}`\n\n"
                        f"[Etherscan](https://etherscan.io/tx/{tx['hash']})"
                    )
                })

        except Exception:
            continue

    return alerts


# ── Monitor principal ──────────────────────────────────────────────────────────

def check_cex_flows() -> list:
    all_alerts = []
    all_alerts.extend(check_followed_wallets_to_cex())
    all_alerts.extend(check_protocol_large_txns())
    return all_alerts