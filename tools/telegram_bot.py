"""Telegram gateway. Three capabilities:
1. push_summary() — send the kanban snapshot after every orchestration loop.
2. Interactive chat — human asks questions, agent queries data and responds.
3. On-demand commands — "/outreach @handle", "/score", "/status", "/changes"

This is what makes the agent team *conversational*, not just a batch pipeline.
"""
import json
import os
import importlib.util
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Import sibling modules explicitly to avoid conflict with hermes-agent's tools package
def _sibling_import(name, filename):
    path = Path(__file__).parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

kanban = _sibling_import("tools.kanban", "kanban.py")
memory = _sibling_import("tools.memory", "memory.py")
llm_client = _sibling_import("tools.llm_client", "llm_client.py")
ask = llm_client.ask

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault"))


def push_summary(text: str | None = None) -> None:
    if not TOKEN or not CHAT_ID:
        print("[telegram_bot] TELEGRAM_BOT_TOKEN/CHAT_ID not set — skipping push.")
        return
    import asyncio

    async def _send():
        bot = Bot(token=TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=text or kanban.snapshot(), parse_mode="Markdown")

    try:
        asyncio.run(_send())
    except Exception as e:
        print(f"[telegram_bot] Failed to send: {e}")


def send_document(file_path: str, caption: str = "") -> None:
    """Send a file (PDF, JSON, etc.) to the Telegram chat."""
    if not TOKEN or not CHAT_ID:
        print("[telegram_bot] TELEGRAM_BOT_TOKEN/CHAT_ID not set — skipping document send.")
        return
    import asyncio

    async def _send():
        bot = Bot(token=TOKEN)
        with open(file_path, "rb") as f:
            await bot.send_document(chat_id=CHAT_ID, document=f, caption=caption[:1024])

    try:
        asyncio.run(_send())
        print(f"[telegram_bot] Sent document: {file_path}")
    except Exception as e:
        print(f"[telegram_bot] Failed to send document: {e}")


def push_run_report() -> None:
    """Send a detailed run summary with key metrics to Telegram."""
    if not TOKEN or not CHAT_ID:
        return

    stats = memory.get_stats()
    board = kanban.snapshot()

    # Gather key output counts
    ads_count = len(list((VAULT / "Ads").glob("*.md"))) if (VAULT / "Ads").exists() else 0
    outreach_count = len(list((VAULT / "Outreach").glob("*.md"))) if (VAULT / "Outreach").exists() else 0
    content_count = len(list((VAULT / "Content").glob("*.md"))) if (VAULT / "Content").exists() else 0
    competitors_count = len([f for f in (VAULT / "Competitors").glob("*.md") if not f.name.startswith("_")]) if (VAULT / "Competitors").exists() else 0

    msg = (
        f"*Pipeline Run Complete*\n\n"
        f"*Kanban Board:*\n{board}\n\n"
        f"*Outputs Generated:*\n"
        f"- Competitor briefs: {competitors_count}\n"
        f"- Ad scripts (incl. revisions): {ads_count}\n"
        f"- Influencer outreach drafts: {outreach_count}\n"
        f"- Content pieces: {content_count}\n\n"
        f"*Memory:* {stats['total_processed']} items tracked | Run #{stats['run_count']}\n\n"
        f"_PDF report attached below_"
    )
    push_summary(msg)


# --- Interactive handlers ---

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show pipeline status + memory stats."""
    board = kanban.snapshot()
    stats = memory.get_stats()
    msg = (
        f"📊 *Pipeline Status*\n\n"
        f"{board}\n\n"
        f"🧠 *Memory:* {stats['total_processed']} items tracked | "
        f"Run #{stats['run_count']} | Last: {stats['last_run']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show ad script scorecard."""
    scorecard_path = VAULT / "Ads" / "_scorecard.md"
    if scorecard_path.exists():
        text = scorecard_path.read_text(encoding="utf-8")[:3000]
        await update.message.reply_text(text)
    else:
        await update.message.reply_text("No scorecard yet. Run the pipeline first.")


async def cmd_outreach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate outreach for a specific handle on-demand."""
    if not context.args:
        await update.message.reply_text("Usage: /outreach <channel_name>")
        return

    handle = " ".join(context.args)
    await update.message.reply_text(f"Drafting outreach for {handle}...")

    draft = ask(
        "You write short, genuine cold outreach messages (under 120 words) from "
        "CrowdWisdomTrading to trading content creators, asking for their honest "
        "opinion on crowdwisdomtrading.com. Not pitching a paid sponsorship.",
        f"Creator: {handle}\nDraft a personalized cold outreach message.",
    )
    await update.message.reply_text(f"📨 *Outreach draft for {handle}:*\n\n{draft}", parse_mode="Markdown")


async def cmd_changes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show what changed since last run."""
    history = memory.get_run_history()
    if len(history) < 2:
        await update.message.reply_text("Need at least 2 runs to compare. Run the pipeline again.")
        return

    last = history[-1]
    prev = history[-2]
    msg = (
        f"🔄 *Changes (Run #{last['run']} vs #{prev['run']})*\n\n"
        f"Previous: {prev['timestamp'][:16]}\n"
        f"Current: {last['timestamp'][:16]}\n\n"
        f"Current board:\n{last['summary'][:500]}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_competitors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show competitor synthesis."""
    synth_path = VAULT / "Competitors" / "_synthesis.md"
    if synth_path.exists():
        text = synth_path.read_text(encoding="utf-8")[:3000]
        await update.message.reply_text(text)
    else:
        await update.message.reply_text("No competitor data yet. Run the pipeline first.")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free-form chat — agent answers questions using all available data as context."""
    user_text = update.message.text
    board = kanban.snapshot()
    stats = memory.get_stats()

    # Gather context from available data
    context_parts = [f"Kanban board:\n{board}"]
    context_parts.append(f"Memory: {stats['total_processed']} items tracked, {stats['run_count']} runs")

    # Add competitor synthesis if available
    synth_path = VAULT / "Competitors" / "_synthesis.md"
    if synth_path.exists():
        context_parts.append(f"Competitor synthesis:\n{synth_path.read_text(encoding='utf-8')[:1000]}")

    # Add scorecard if available
    scorecard_path = VAULT / "Ads" / "_scorecard.md"
    if scorecard_path.exists():
        context_parts.append(f"Ad scorecard:\n{scorecard_path.read_text(encoding='utf-8')[:500]}")

    full_context = "\n\n".join(context_parts)

    reply = ask(
        "You are the CrowdWisdomTrading marketing agent team's assistant. "
        "Answer questions about current progress, strategy, competitors, ads, "
        "and influencers using the context given. Be concise and actionable. "
        "If asked to do something (like draft an outreach), do it.",
        f"Context:\n{full_context}\n\nQuestion: {user_text}",
    )
    await update.message.reply_text(reply)


def run_listener() -> None:
    """Blocking call — run in its own process to let the team chat over Telegram."""
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CommandHandler("outreach", cmd_outreach))
    app.add_handler(CommandHandler("changes", cmd_changes))
    app.add_handler(CommandHandler("competitors", cmd_competitors))

    # Free-form chat
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    print("🤖 Telegram bot running. Commands: /status /score /outreach /changes /competitors")
    app.run_polling()


if __name__ == "__main__":
    run_listener()
