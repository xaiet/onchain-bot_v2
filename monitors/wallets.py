import requests
from datetime import datetime, timedelta
from config import ETHERSCAN_API_KEY, HELIUS_API_KEY
from database import get_wallets, should_send_alert, mark_alert_sent, log_wallet_txn

# Threshold mínim per disparar alerta
MIN_VALUE_USD_EVM     = 50_000
MIN_VALUE_USD_SOLANA  = 10_000
MIN_VALUE_USD_BSC     = 10_000
ETH_PRICE = 3000
SOL_PRICE = 150
BNB_PRICE = 600

# ── CEX Addresses ──────────────────────────────────────────────────────────────

CEX_ADDRESSES_EVM = {
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be": "Binance",
    "0xd551234ae421e3bcba99a0da6d736074f22192ff": "Binance",
    "0x564286362092d8e7936f0549571a803b203aaced": "Binance",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0xeb2629a2734e272bcc07bda959863f316f4bd4cf": "Coinbase",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase",
    "0x77696bb39917c91a0c3908d577d5e322095425ca": "Kraken",
}

CEX_ADDRESSES_BSC = {
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be": "Binance",
    "0xd551234ae421e3bcba99a0da6d736074f22192ff": "Binance",
    "0x564286362092d8e7936f0549571a803b203aaced": "Binance",
    "0x0681d8db095565fe8a346fa0277bffde9c0edbbf": "Binance",
    "0xfe9e8709d3215310075d67e3ed32a380ccf451c8": "Binance",
    "0xa344c7ada83113b3b56941f0e993a4c2a43f3e41": "Bybit",
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit",
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
    "0xd793281182a0e3e0ab3230497b414cb5b1557da5": "OKX",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    "0x236f9f97e0e62388479bf9e5ba4889e46b0273c3": "Bitget",
    "0x1ab4973a48dc892cd9971ece8e01dcc7688f8f23": "Bitget",
}

ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"
BSCSCAN_BASE   = "https://api.bscscan.com/api"


# ── EVM (Ethereum) ─────────────────────────────────────────────────────────────

def check_evm_wallet(wallet: dict) -> list:
    alerts = []
    address = wallet["address"].lower()
    label   = wallet["label"] or address[:6] + "..." + address[-4:]
    cutoff  = datetime.utcnow() - timedelta(minutes=30)

    try:
        r = requests.get(
            ETHERSCAN_BASE,
            params={
                "chainid": "1",
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 20,
                "sort": "desc",
                "apikey": ETHERSCAN_API_KEY
            },
            timeout=10
        )
        data = r.json()
        if data["status"] != "1":
            return alerts

        for tx in data["result"]:
            tx_time   = datetime.utcfromtimestamp(int(tx["timeStamp"]))
            if tx_time < cutoff:
                break
            value_eth = int(tx["value"]) / 1e18
            value_usd = value_eth * ETH_PRICE
            if value_usd < MIN_VALUE_USD_EVM:
                continue
            alert_key = f"wallet_txn_{tx['hash']}"
            if not should_send_alert(alert_key, cooldown_hours=24):
                continue

            direction = "📤 OUT" if tx["from"].lower() == address else "📥 IN"
            to_addr   = tx.get("to", "").lower()
            cex_label = CEX_ADDRESSES_EVM.get(to_addr)
            cex_warn  = f"\n   ⚠️ *Destí: {cex_label}* — possible venda" if cex_label else ""

            alerts.append({
                "key": alert_key,
                "message": (
                    f"🐋 *Wallet Alert — {label}*\n\n"
                    f"{direction} `{value_eth:.2f}` ETH (~${value_usd:,.0f})\n"
                    f"   🕐 {tx_time.strftime('%H:%M UTC')}"
                    f"{cex_warn}\n\n"
                    f"[Etherscan](https://etherscan.io/tx/{tx['hash']})"
                )
            })
            log_wallet_txn(address=address, tx_hash=tx["hash"],
                           value_usd=value_usd, direction=direction, token="ETH")

    except Exception:
        pass

    return alerts


# ── BSC ────────────────────────────────────────────────────────────────────────

def check_bsc_wallet(wallet: dict) -> list:
    alerts = []
    address = wallet["address"].lower()
    label   = wallet["label"] or address[:6] + "..." + address[-4:]
    cutoff  = datetime.utcnow() - timedelta(minutes=30)

    try:
        r = requests.get(
            BSCSCAN_BASE,
            params={
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 20,
                "sort": "desc",
                "apikey": ETHERSCAN_API_KEY
            },
            timeout=10
        )
        data = r.json()
        if data["status"] != "1":
            return alerts

        for tx in data["result"]:
            tx_time   = datetime.utcfromtimestamp(int(tx["timeStamp"]))
            if tx_time < cutoff:
                break
            value_bnb = int(tx["value"]) / 1e18
            value_usd = value_bnb * BNB_PRICE
            if value_usd < MIN_VALUE_USD_BSC:
                continue
            alert_key = f"wallet_txn_bsc_{tx['hash']}"
            if not should_send_alert(alert_key, cooldown_hours=24):
                continue

            direction = "📤 OUT" if tx["from"].lower() == address else "📥 IN"
            to_addr   = tx.get("to", "").lower()
            cex_label = CEX_ADDRESSES_BSC.get(to_addr)
            cex_warn  = f"\n   ⚠️ *Destí: {cex_label}* — possible venda" if cex_label else ""

            alerts.append({
                "key": alert_key,
                "message": (
                    f"🐋 *Wallet Alert BSC — {label}* 🟡\n\n"
                    f"{direction} `{value_bnb:.4f}` BNB (~${value_usd:,.0f})\n"
                    f"   🕐 {tx_time.strftime('%H:%M UTC')}"
                    f"{cex_warn}\n\n"
                    f"[BscScan](https://bscscan.com/tx/{tx['hash']})"
                )
            })
            log_wallet_txn(address=address, tx_hash=tx["hash"],
                           value_usd=value_usd, direction=direction, token="BNB")

    except Exception:
        pass

    return alerts


# ── Solana ─────────────────────────────────────────────────────────────────────

def check_solana_wallet(wallet: dict) -> list:
    alerts = []
    address = wallet["address"]
    label   = wallet["label"] or address[:6] + "..." + address[-4:]
    cutoff  = datetime.utcnow() - timedelta(minutes=30)

    try:
        r = requests.get(
            f"https://api.helius.xyz/v0/addresses/{address}/transactions",
            params={"api-key": HELIUS_API_KEY, "limit": 20},
            timeout=15
        )
        if r.status_code != 200:
            return alerts

        for tx in r.json():
            tx_time = datetime.utcfromtimestamp(tx.get("timestamp", 0))
            if tx_time < cutoff:
                break
            sol_moved = sum(
                t.get("amount", 0) for t in tx.get("nativeTransfers", [])
                if t.get("fromUserAccount") == address
            ) / 1e9
            value_usd = sol_moved * SOL_PRICE
            if value_usd < MIN_VALUE_USD_SOLANA:
                continue
            alert_key = f"wallet_txn_{tx.get('signature', '')}"
            if not should_send_alert(alert_key, cooldown_hours=24):
                continue

            tx_type     = tx.get("type", "UNKNOWN")
            source      = tx.get("source", "")
            description = tx.get("description", "")

            alerts.append({
                "key": alert_key,
                "message": (
                    f"🐋 *Wallet Alert — {label}* 🟣\n\n"
                    f"📤 `{sol_moved:.2f}` SOL (~${value_usd:,.0f})\n"
                    f"   🔄 {tx_type} via {source}\n"
                    f"   🕐 {tx_time.strftime('%H:%M UTC')}\n"
                    + (f"   _{description[:80]}_\n" if description else "") +
                    f"\n[Solscan](https://solscan.io/tx/{tx.get('signature', '')})"
                )
            })
            log_wallet_txn(address=address, tx_hash=tx.get("signature", ""),
                           value_usd=value_usd, direction="OUT", token="SOL")

    except Exception:
        pass

    return alerts

def check_bsc_token_wallet(wallet: dict, token_contract: str = None) -> list:
    """
    Monitora transfers de tokens BEP20 d'una wallet BSC.
    Si token_contract és None, monitora tots els tokens.
    """
    alerts = []
    address = wallet["address"].lower()
    label   = wallet["label"] or address[:6] + "..." + address[-4:]
    cutoff  = datetime.utcnow() - timedelta(minutes=30)

    params = {
        "module": "account",
        "action": "tokentx",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": 20,
        "sort": "desc",
        "apikey": ETHERSCAN_API_KEY
    }
    if token_contract:
        params["contractaddress"] = token_contract

    try:
        r = requests.get(BSCSCAN_BASE, params=params, timeout=10)
        data = r.json()
        if data["status"] != "1":
            return alerts

        for tx in data["result"]:
            tx_time = datetime.utcfromtimestamp(int(tx["timeStamp"]))
            if tx_time < cutoff:
                break

            decimals  = int(tx.get("tokenDecimal", 18))
            amount    = int(tx["value"]) / (10 ** decimals)
            symbol    = tx.get("tokenSymbol", "???")
            token_name = tx.get("tokenName", "???")

            # Filtre mínim — ignora moviments petits
            if amount < 1000:
                continue

            alert_key = f"wallet_tokentx_bsc_{tx['hash']}_{tx.get('transactionIndex','')}"
            if not should_send_alert(alert_key, cooldown_hours=24):
                continue

            direction = "📤 OUT" if tx["from"].lower() == address else "📥 IN"
            to_addr   = tx.get("to", "").lower()
            cex_label = CEX_ADDRESSES_BSC.get(to_addr)
            cex_warn  = f"\n   ⚠️ *Destí: {cex_label}* — possible venda" if cex_label else ""

            alerts.append({
                "key": alert_key,
                "message": (
                    f"🐋 *Token Alert BSC — {label}* 🟡\n\n"
                    f"{direction} `{amount:,.0f}` {symbol}\n"
                    f"   📋 {token_name}\n"
                    f"   🕐 {tx_time.strftime('%H:%M UTC')}"
                    f"{cex_warn}\n\n"
                    f"[BscScan](https://bscscan.com/tx/{tx['hash']})"
                )
            })
            log_wallet_txn(
                address=address,
                tx_hash=tx["hash"],
                value_usd=0,
                direction=direction,
                token=symbol
            )

    except Exception:
        pass

    return alerts


# ── Monitor principal ──────────────────────────────────────────────────────────

def check_all_wallets() -> list:
    all_alerts = []
    wallets    = get_wallets()
    if not wallets:
        return all_alerts

    for wallet in wallets:
        if wallet["chain"] == "evm":
            all_alerts.extend(check_evm_wallet(wallet))
        elif wallet["chain"] == "solana":
            all_alerts.extend(check_solana_wallet(wallet))
        elif wallet["chain"] == "bsc":
            all_alerts.extend(check_bsc_wallet(wallet))
            all_alerts.extend(check_bsc_token_wallet(wallet))

    return all_alerts