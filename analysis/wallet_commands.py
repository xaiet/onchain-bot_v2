from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode
from analysis.wallet_analyzer import get_wallet_pnl, get_wallet_score, compare_wallets


# ─────────────────────────────────────────────
# /wallet_pnl
# ─────────────────────────────────────────────

async def cmd_wallet_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ús: /wallet_pnl <wallet_address> <token_mint>"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ *Ús:* `/wallet_pnl <wallet> <token>`\n\n"
            "Exemple:\n`/wallet_pnl 7xKXabc... EPjFWabc...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    wallet = context.args[0].strip()
    token  = context.args[1].strip()

    msg = await update.message.reply_text("🔍 Analitzant PnL... (Helius + Birdeye, ~10s)")
    try:
        result = await get_wallet_pnl(wallet, token)
        await msg.edit_text(result, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
# /wallet_score
# ─────────────────────────────────────────────

async def cmd_wallet_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ús: /wallet_score <wallet_address>"""
    if len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ *Ús:* `/wallet_score <wallet>`\n\n"
            "Exemple:\n`/wallet_score 7xKXabc...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    wallet = context.args[0].strip()
    msg = await update.message.reply_text("🎯 Calculant score (últims 20 tokens)...")
    try:
        result = await get_wallet_score(wallet, num_tokens=20)
        await msg.edit_text(result, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
# /compare
# ─────────────────────────────────────────────

async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ús: /compare <wallet1> <wallet2> <token_mint>"""
    if len(context.args) < 3:
        await update.message.reply_text(
            "⚠️ *Ús:* `/compare <wallet1> <wallet2> <token>`\n\n"
            "Exemple:\n`/compare 7xKXabc... 9mPQdef... EPjFW...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    wallet1 = context.args[0].strip()
    wallet2 = context.args[1].strip()
    token   = context.args[2].strip()

    msg = await update.message.reply_text("🕵️ Comparant wallets...")
    try:
        result = await compare_wallets(wallet1, wallet2, token)
        await msg.edit_text(result, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
# REGISTRE
# ─────────────────────────────────────────────

def register_wallet_commands(application):
    application.add_handler(CommandHandler("wallet_pnl",    cmd_wallet_pnl))
    application.add_handler(CommandHandler("wallet_score",  cmd_wallet_score))
    application.add_handler(CommandHandler("compare",       cmd_compare))
    print("✅ Wallet commands registrats: /wallet_pnl /wallet_score /compare")
