import requests
from datetime import datetime, timedelta
from database import get_protocols, should_send_alert, mark_alert_sent

def check_tvl() -> list:
    """
    Comprova TVL de tots els protocols de la watchlist.
    Compara TVL actual vs mitjana 30 dies — més fiable que canvi 24h.
    """
    alerts = []

    for protocol in get_protocols():
        slug = protocol["defillama_slug"]
        name = protocol["name"]

        try:
            r = requests.get(
                f"https://api.llama.fi/protocol/{slug}",
                timeout=10
            )
            data = r.json()
            tvl_history = data.get("tvl", [])

            if len(tvl_history) < 31:
                continue

            tvl_now = tvl_history[-1]["totalLiquidityUSD"]
            tvl_7d_ago = tvl_history[-8]["totalLiquidityUSD"]
            tvl_30d_ago = tvl_history[-31]["totalLiquidityUSD"]

            # Mitjana dels últims 30 dies
            last_30 = [t["totalLiquidityUSD"] for t in tvl_history[-31:]]
            tvl_avg_30d = sum(last_30) / len(last_30)

            if tvl_avg_30d == 0:
                continue

            # Canvis
            change_24h = ((tvl_now - tvl_history[-2]["totalLiquidityUSD"])
                         / tvl_history[-2]["totalLiquidityUSD"]) * 100 if tvl_history[-2]["totalLiquidityUSD"] else 0
            change_7d = ((tvl_now - tvl_7d_ago) / tvl_7d_ago) * 100 if tvl_7d_ago else 0
            change_30d = ((tvl_now - tvl_30d_ago) / tvl_30d_ago) * 100 if tvl_30d_ago else 0

            # Desviació respecte la mitjana 30d
            deviation = ((tvl_now - tvl_avg_30d) / tvl_avg_30d) * 100

            # Només alerta si desviació significativa (>15%) O caiguda 7d >10%
            if abs(deviation) < 15 and abs(change_7d) < 10:
                continue

            alert_key = f"tvl_{slug}_{datetime.utcnow().strftime('%Y-%m-%d')}"
            if not should_send_alert(alert_key, cooldown_hours=24):
                continue

            # Determina direcció i urgència
            if deviation < -15 or change_7d < -10:
                emoji = "🔴"
                signal = "Caiguda significativa"
            else:
                emoji = "🟢"
                signal = "Creixement significatiu"

            alerts.append({
                "key": alert_key,
                "message": (
                    f"{emoji} *TVL Alert — {name}*\n\n"
                    f"TVL ara: ${tvl_now/1e9:.2f}B\n\n"
                    f"📊 Canvis:\n"
                    f"   24h: {change_24h:+.1f}%\n"
                    f"   7d:  {change_7d:+.1f}%\n"
                    f"   30d: {change_30d:+.1f}%\n\n"
                    f"📈 vs mitjana 30d: {deviation:+.1f}%\n\n"
                    f"⚡ *{signal}*\n\n"
                    f"❓ *Preguntes a investigar:*\n"
                    f"   → Hi ha unlock proper?\n"
                    f"   → Qui són les wallets que surten?\n"
                    f"   → El preu ja ho ha pricejat?\n\n"
                    f"[DefiLlama](https://defillama.com/protocol/{slug})"
                )
            })

        except Exception:
            continue

    return alerts