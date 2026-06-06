import logging
import os
import asyncio
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from monitors.wallets import check_all_wallets
from monitors.tvl import check_tvl
from monitors.cex_flows import check_cex_flows
from monitors.solana import check_solana
from monitors.digest import build_digest
from apscheduler.schedulers.background import BackgroundScheduler
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from database import (
    init_db,
    get_protocols, add_protocol, remove_protocol,
    get_wallets, add_wallet, remove_wallet, update_wallet_label,
    mark_alert_sent
)
from analysis.wallet_profiler import (
    profile_evm_wallet, profile_solana_wallet, format_wallet_profile
)
from analysis.wallet_analyzer import get_wallet_pnl, get_wallet_score, compare_wallets, get_wallet_parent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
HELIUS_API_KEY   = os.getenv("HELIUS_API_KEY")

# ── Helpers ────────────────────────────────────────────────────────────────────

def is_solana_address(address: str) -> bool:
    return not address.startswith("0x") and 32 <= len(address) <= 44

def is_evm_address(address: str) -> bool:
    return address.startswith("0x") and len(address) == 42

def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n_...missatge truncat (massa llarg)_"

# ── Comandaments — Wallets ─────────────────────────────────────────────────────

async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Ús: `/wallet <address>`\n\n"
            "Exemples:\n"
            "`/wallet 0x123...abc` — EVM\n"
            "`/wallet 7xKX...sol` — Solana",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    address = context.args[0].strip()
    if not is_evm_address(address) and not is_solana_address(address):
        await update.message.reply_text(
            "❌ Adreça no vàlida.\n"
            "EVM ha de començar per `0x` i tenir 42 caràcters.\n"
            "Solana ha de tenir entre 32 i 44 caràcters.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    await update.message.reply_text("🔍 Analitzant wallet... (pot trigar 10-20 segons)")
    if is_evm_address(address):
        profile = profile_evm_wallet(address)
    else:
        profile = profile_solana_wallet(address)
    message = format_wallet_profile(profile)
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )
    await update.message.reply_text(
        f"💾 Vols guardar aquesta wallet a la teva llista de seguiment?\n"
        f"`/addwallet {address} <etiqueta>`\n\n"
        f"Exemple: `/addwallet {address} smart_money_1`",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_addwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text(
            "Ús: `/addwallet <address> <etiqueta opcional>`\n\n"
            "Exemples:\n"
            "`/addwallet 0x123...abc Jump Trading`\n"
            "`/addwallet 7xKX...sol Solana whale 1`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    address = context.args[0].strip()
    label = " ".join(context.args[1:]) if len(context.args) > 1 else None
    if not is_evm_address(address) and not is_solana_address(address):
        await update.message.reply_text("❌ Adreça no vàlida.")
        return
    chain = "evm" if is_evm_address(address) else "solana"
    success = add_wallet(address, label=label, chain=chain)
    if success:
        label_str = f" com *{label}*" if label else ""
        chain_emoji = "🔷" if chain == "evm" else "🟣"
        await update.message.reply_text(
            f"{chain_emoji} Wallet afegida{label_str}!\n\n"
            f"`{address}`\n\n"
            f"A partir d'ara el bot la monitora.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("⚠️ Aquesta wallet ja està a la llista.", parse_mode=ParseMode.MARKDOWN)

async def cmd_removewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Ús: `/removewallet <address>`", parse_mode=ParseMode.MARKDOWN)
        return
    address = context.args[0].strip()
    success = remove_wallet(address)
    if success:
        await update.message.reply_text("🗑️ Wallet eliminada del seguiment.")
    else:
        await update.message.reply_text("❌ No he trobat aquesta wallet a la llista.")

async def cmd_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallets = get_wallets()
    if not wallets:
        await update.message.reply_text(
            "La llista de wallets és buida.\n"
            "Afegeix-ne amb /wallet <address> i després /addwallet"
        )
        return
    evm_wallets = [w for w in wallets if w["chain"] == "evm"]
    sol_wallets = [w for w in wallets if w["chain"] == "solana"]
    lines = [f"👁️ Wallets en seguiment ({len(wallets)})\n"]
    if evm_wallets:
        lines.append("🔷 EVM")
        for w in evm_wallets:
            addr_short = w["address"][:6] + "..." + w["address"][-4:]
            label = f" — {w['label']}" if w["label"] else ""
            lines.append(f" {addr_short}{label}")
    if sol_wallets:
        lines.append("\n🟣 Solana")
        for w in sol_wallets:
            addr_short = w["address"][:6] + "..." + w["address"][-4:]
            label = f" — {w['label']}" if w["label"] else ""
            lines.append(f" {addr_short}{label}")
    await update.message.reply_text("\n".join(lines))

# ── Wallet Analysis ────────────────────────────────────────────────────────────

async def cmd_wallet_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ *Ús:* `/wallet_pnl <wallet> <token>`\n\nEx: `/wallet_pnl 7xKX... EPjF...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    wallet = context.args[0].strip()
    token  = context.args[1].strip()
    msg = await update.message.reply_text("🔍 Analitzant PnL... (Helius + Birdeye, ~10s)")
    try:
        result = await get_wallet_pnl(wallet, token)
        await msg.edit_text(truncate(result), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_wallet_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ *Ús:* `/wallet_score <wallet>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    wallet = context.args[0].strip()
    msg = await update.message.reply_text("🎯 Calculant score...")
    try:
        # DEBUG TEMPORAL
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url    = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
            params = {"api-key": HELIUS_API_KEY, "limit": 5}
            async with session.get(url, params=params) as r:
                status = r.status
                txs    = await r.json()

        debug_info = f"Status: {status}\nTxs rebudes: {len(txs)}\n"
        if txs and isinstance(txs, list):
            for tx in txs[:2]:
                debug_info += f"\ntype: {tx.get('type')}\n"
                debug_info += f"swap: {bool(tx.get('events', {}).get('swap'))}\n"
                debug_info += f"tokenTransfers: {len(tx.get('tokenTransfers', []))}\n"
                debug_info += f"mints: {[t.get('mint','')[:8] for t in tx.get('tokenTransfers', [])]}\n"
        elif isinstance(txs, dict):
            debug_info += f"Error API: {txs}\n"

        await msg.edit_text(f"```\n{debug_info}\n```", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text(
            "⚠️ *Ús:* `/compare <wallet1> <wallet2> <token>`\n\nEx: `/compare 7xKX... 9mPQ... EPjF...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    wallet1 = context.args[0].strip()
    wallet2 = context.args[1].strip()
    token   = context.args[2].strip()
    msg = await update.message.reply_text("🕵️ Comparant wallets...")
    try:
        result = await compare_wallets(wallet1, wallet2, token)
        await msg.edit_text(truncate(result), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_wallet_parent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ *Ús:* `/wallet_parent <wallet>`\n\nEx: `/wallet_parent 7xKX...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    wallet = context.args[0].strip()
    msg = await update.message.reply_text("🔗 Rastrejant wallet pare... (2 hops, ~15s)")
    try:
        result = await get_wallet_parent(wallet, hops=2)
        await msg.edit_text(result, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

# ── Scheduled alert senders ────────────────────────────────────────────────────

async def send_wallet_alerts():
    alerts = check_all_wallets()
    if not alerts:
        return
    bot = Bot(token=TELEGRAM_TOKEN)
    for alert in alerts:
        try:
            await bot.send_message(
                chat_id=int(TELEGRAM_CHAT_ID),
                text=alert["message"],
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            mark_alert_sent(alert["key"])
            logger.info(f"Wallet alert enviada: {alert['key']}")
        except Exception as e:
            logger.error(f"Error wallet alert: {e}")

async def send_tvl_alerts():
    alerts = check_tvl()
    if not alerts:
        return
    bot = Bot(token=TELEGRAM_TOKEN)
    for alert in alerts:
        try:
            await bot.send_message(
                chat_id=int(TELEGRAM_CHAT_ID),
                text=alert["message"],
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            mark_alert_sent(alert["key"])
        except Exception as e:
            logger.error(f"Error TVL alert: {e}")

async def send_cex_alerts():
    alerts = check_cex_flows()
    if not alerts:
        return
    bot = Bot(token=TELEGRAM_TOKEN)
    for alert in alerts:
        try:
            await bot.send_message(
                chat_id=int(TELEGRAM_CHAT_ID),
                text=alert["message"],
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            mark_alert_sent(alert["key"])
        except Exception as e:
            logger.error(f"Error CEX alert: {e}")

async def send_solana_alerts():
    alerts = check_solana()
    if not alerts:
        return
    bot = Bot(token=TELEGRAM_TOKEN)
    for alert in alerts:
        try:
            await bot.send_message(
                chat_id=int(TELEGRAM_CHAT_ID),
                text=alert["message"],
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            mark_alert_sent(alert["key"])
        except Exception as e:
            logger.error(f"Error Solana alert: {e}")

async def send_digest():
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        message = build_digest()
        await bot.send_message(
            chat_id=int(TELEGRAM_CHAT_ID),
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        logger.info("Digest enviat")
    except Exception as e:
        logger.error(f"Error digest: {e}")

# ── Comandaments — Monitors ────────────────────────────────────────────────────

async def cmd_tvl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Analitzant TVL de la watchlist...")
    alerts = check_tvl()
    if not alerts:
        await update.message.reply_text(
            "✅ Cap anomalia de TVL detectada.\n"
            "_Tots els protocols dins la mitjana dels últims 30 dies._",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    for alert in alerts:
        await update.message.reply_text(alert["message"], parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_cex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏦 Analitzant CEX flows...")
    alerts = check_cex_flows()
    if not alerts:
        await update.message.reply_text(
            "✅ Cap CEX inflow significatiu detectat.\n"
            "_Cap wallet seguida ni protocol amb txns grans recents._",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    for alert in alerts:
        await update.message.reply_text(alert["message"], parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_solana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🟣 Analitzant Solana...")
    alerts = check_solana()
    if not alerts:
        await update.message.reply_text("✅ Cap cluster ni caiguda de liquiditat detectada ara mateix.")
        return
    for alert in alerts:
        await update.message.reply_text(alert["message"], parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Construint digest...")
    try:
        message = build_digest()
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ── Comandaments — Protocols ───────────────────────────────────────────────────

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Ús: `/add <slug>` — ex: `/add aave`", parse_mode=ParseMode.MARKDOWN)
        return
    slug = context.args[0].lower().strip()
    await update.message.reply_text(f"🔍 Verificant `{slug}` a DefiLlama...", parse_mode=ParseMode.MARKDOWN)
    try:
        import requests
        response = requests.get(f"https://api.llama.fi/protocol/{slug}", timeout=10)
        if response.status_code != 200:
            await update.message.reply_text(
                f"❌ No he trobat `{slug}` a DefiLlama.\n"
                f"Comprova el slug a [defillama.com](https://defillama.com)",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        data = response.json()
        name = data.get("name", slug.capitalize())
    except Exception:
        await update.message.reply_text("⚠️ Error connectant amb DefiLlama.")
        return
    success = add_protocol(name, slug)
    if success:
        await update.message.reply_text(f"✅ *{name}* afegit!", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"⚠️ `{slug}` ja està a la llista.", parse_mode=ParseMode.MARKDOWN)

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Ús: `/remove <slug>`", parse_mode=ParseMode.MARKDOWN)
        return
    slug = context.args[0].lower().strip()
    success = remove_protocol(slug)
    if success:
        await update.message.reply_text(f"🗑️ `{slug}` eliminat.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ No he trobat `{slug}`.", parse_mode=ParseMode.MARKDOWN)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    protocols = get_protocols()
    if not protocols:
        await update.message.reply_text("La watchlist és buida. Afegeix protocols amb `/add <slug>`")
        return
    lines = ["📋 *Protocols en seguiment:*\n"]
    for p in protocols:
        lines.append(f"• {p['name']} (`{p['defillama_slug']}`)")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# ── Comandament Start ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "`/digest` — digest complet ara mateix\n"
        "`/tvl` — TVL watchlist\n"
        "`/cex` — CEX inflows\n"
        "`/solana` — clusters Solana\n"
        "`/wallet <addr>` — analitza wallet\n"
        "`/addwallet <addr> <label>` — segueix wallet\n"
        "`/removewallet <addr>` — deixa de seguir\n"
        "`/wallets` — llista wallets\n"
        "`/wallet_pnl <addr> <token>` — PnL detallat amb USD\n"
        "`/wallet_score <addr>` — score últims 20 tokens\n"
        "`/compare <w1> <w2> <token>` — detecta coordinació\n"
        "`/wallet_parent <addr>` — troba la wallet pare del cluster\n"
        "`/add <slug>` — afegeix protocol\n"
        "`/remove <slug>` — elimina protocol\n"
        "`/list` — llista protocols\n",
        parse_mode=ParseMode.MARKDOWN
    )

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Wallets
    app.add_handler(CommandHandler("wallet",       cmd_wallet))
    app.add_handler(CommandHandler("addwallet",    cmd_addwallet))
    app.add_handler(CommandHandler("removewallet", cmd_removewallet))
    app.add_handler(CommandHandler("wallets",      cmd_wallets))

    # Wallet Analysis
    app.add_handler(CommandHandler("wallet_pnl",   cmd_wallet_pnl))
    app.add_handler(CommandHandler("wallet_score", cmd_wallet_score))
    app.add_handler(CommandHandler("compare",      cmd_compare))
    app.add_handler(CommandHandler("wallet_parent", cmd_wallet_parent))

    # Protocols
    app.add_handler(CommandHandler("add",    cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list",   cmd_list))

    # Monitors
    app.add_handler(CommandHandler("tvl",    cmd_tvl))
    app.add_handler(CommandHandler("cex",    cmd_cex))
    app.add_handler(CommandHandler("solana", cmd_solana))
    app.add_handler(CommandHandler("digest", cmd_digest))

    # Start
    app.add_handler(CommandHandler("start", cmd_start))

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(lambda: asyncio.run(send_wallet_alerts()), "interval", minutes=30)
    scheduler.add_job(lambda: asyncio.run(send_tvl_alerts()),    "cron", hour=8,  minute=30)
    scheduler.add_job(lambda: asyncio.run(send_tvl_alerts()),    "cron", hour=18, minute=30)
    scheduler.add_job(lambda: asyncio.run(send_cex_alerts()),    "interval", hours=1)
    scheduler.add_job(lambda: asyncio.run(send_solana_alerts()), "interval", hours=1)
    scheduler.add_job(lambda: asyncio.run(send_digest()),        "cron", hour=8,  minute=0)
    scheduler.add_job(lambda: asyncio.run(send_digest()),        "cron", hour=18, minute=0)
    scheduler.start()

    logger.info("Scheduler iniciat")
    logger.info("Bot v2 escoltant...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()