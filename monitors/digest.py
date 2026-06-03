from datetime import datetime
from monitors.tvl import check_tvl
from monitors.cex_flows import check_cex_flows
from monitors.solana import check_solana
from monitors.wallets import check_all_wallets
from database import get_protocols, get_wallets

def build_digest() -> str:
    """
    Construeix el digest diari unificant tots els monitors.
    Només mostra seccions amb dades reals — res d'estàtic.
    """
    now = datetime.utcnow()
    lines = []

    # Header
    lines.append(f"📊 *ON-CHAIN DIGEST*")
    lines.append(f"_{now.strftime('%d/%m/%Y %H:%M')} UTC_\n")

    # Context ràpid
    protocols = get_protocols()
    wallets = get_wallets()
    lines.append(
        f"👁️ Monitoritzant {len(protocols)} protocols "
        f"i {len(wallets)} wallets\n"
    )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")

    active_signals = 0

    # ── ETHEREUM ──────────────────────────────────────────

    lines.append("*🔷 ETHEREUM*\n")

    # TVL
    tvl_alerts = check_tvl()
    if tvl_alerts:
        active_signals += len(tvl_alerts)
        lines.append("*📉 TVL — Anomalies detectades*")
        for a in tvl_alerts:
            # Extreu el missatge net sense links per al digest
            msg_lines = a["message"].split("\n")
            for l in msg_lines[:6]:  # primeres 6 línies
                lines.append(l)
            lines.append("")
    else:
        lines.append("📉 *TVL:* Tots els protocols dins la mitjana 30d ✅\n")

    # CEX flows
    cex_alerts = check_cex_flows()
    if cex_alerts:
        active_signals += len(cex_alerts)
        lines.append("*🏦 CEX FLOWS — Moviments detectats*")
        for a in cex_alerts:
            msg_lines = a["message"].split("\n")
            for l in msg_lines[:6]:
                lines.append(l)
            lines.append("")
    else:
        lines.append("🏦 *CEX Flows:* Cap inflow significatiu ✅\n")

    # Wallets EVM
    wallet_alerts = [
        a for a in check_all_wallets()
        if "🔷" not in a.get("message", "") or "EVM" in a.get("message", "")
    ]
    if wallet_alerts:
        active_signals += len(wallet_alerts)
        lines.append("*🐋 WALLETS SEGUIDES — Activitat EVM*")
        for a in wallet_alerts:
            msg_lines = a["message"].split("\n")
            for l in msg_lines[:5]:
                lines.append(l)
            lines.append("")
    else:
        lines.append("🐋 *Wallets EVM:* Cap moviment gran ✅\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")

    # ── SOLANA ────────────────────────────────────────────

    lines.append("*🟣 SOLANA*\n")

    solana_alerts = check_solana()
    if solana_alerts:
        active_signals += len(solana_alerts)
        for a in solana_alerts:
            msg_lines = a["message"].split("\n")
            for l in msg_lines[:6]:
                lines.append(l)
            lines.append("")
    else:
        lines.append("🔍 *Clusters:* Cap coordinació detectada ✅")
        lines.append("💧 *Liquiditat:* Cap caiguda significativa ✅\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")

    # ── RESUM ─────────────────────────────────────────────

    if active_signals == 0:
        attention = "🟢 BAIX"
        summary = "Mercat quiet. Bon moment per revisar la watchlist."
    elif active_signals <= 2:
        attention = "🟡 MODERAT"
        summary = "Alguns senyals actius. Val la pena investigar."
    else:
        attention = "🔴 ALT"
        summary = "Múltiples senyals actius. Analitza amb cura."

    lines.append(f"*📋 RESUM*")
    lines.append(f"Senyals actius: *{active_signals}*")
    lines.append(f"Nivell d'atenció: *{attention}*")
    lines.append(f"_{summary}_")

    return "\n".join(lines)