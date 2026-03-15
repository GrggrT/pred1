"""Telegram bot for AI Office — command handlers and bot builder."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logger import get_logger

from .config import HELP_TEXT, MONITOR_SYSTEM_PROMPT
from .queries import run_all_checks

log = get_logger("ai_office.bot")


def _is_owner(update: Update) -> bool:
    """Check if the message sender is the configured owner."""
    owner_id = (settings.telegram_owner_id or "").strip()
    if not owner_id:
        return False
    user_id = str(update.effective_user.id) if update.effective_user else ""
    return user_id == owner_id


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command — run health checks and report."""
    if not _is_owner(update):
        return

    log.info("cmd_status from user=%s", update.effective_user.id)

    try:
        async with SessionLocal() as session:
            checks = await run_all_checks(session)

        failed = [c for c in checks if not c["ok"]]
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if not failed:
            text = f"✅ <b>Система в норме</b>\n🕐 {now_str}\n\n"
            for c in checks:
                val = html.escape(str(c['value']))
                text += f"• {c['label']}: {val} — ✅\n"
        else:
            text = f"🛡️ <b>Health Check</b> — {now_str}\n\n"
            for c in checks:
                emoji = "✅" if c["ok"] else ("🔴" if c["severity"] == "high" else "⚠️")
                val = html.escape(str(c['value']))
                text += f"{emoji} <b>{c['label']}</b>: {val}\n"
                if not c["ok"]:
                    detail = html.escape(str(c['detail']))
                    text += f"   └ {detail}\n"

        # Try AI summary if Groq is enabled
        if failed and settings.groq_enabled and settings.groq_api_key:
            try:
                from app.data.providers.groq import generate_text

                async with SessionLocal() as session2:
                    prompt = "Результаты health checks:\n"
                    for c in checks:
                        status = "OK" if c["ok"] else "FAIL"
                        prompt += f"- {c['label']}: {status} | {c['detail']}\n"

                    ai_summary = await generate_text(
                        session2,
                        prompt,
                        system_prompt=MONITOR_SYSTEM_PROMPT,
                        temperature=0.3,
                        max_tokens=256,
                        use_cache=False,
                    )
                    if ai_summary:
                        text += f"\n💡 <b>AI:</b> {html.escape(ai_summary)}"
            except Exception:
                log.exception("cmd_status_groq_failed")

        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        log.exception("cmd_status_error")
        await update.message.reply_text("❌ Ошибка при выполнении health check")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command — show available commands."""
    if not _is_owner(update):
        return

    log.info("cmd_help from user=%s", update.effective_user.id)
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def cmd_settled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settled command — run analyst agent on-demand."""
    if not _is_owner(update):
        return

    log.info("cmd_settled from user=%s", update.effective_user.id)

    try:
        from .agents.analyst import run as analyst_run

        async with SessionLocal() as session:
            result = await analyst_run(session)

        status = result.get("status", "done")
        if status == "skipped":
            reason = html.escape(result.get("reason", "no data"))
            await update.message.reply_text(
                f"📊 Analyst: нет данных — {reason}",
                parse_mode="HTML",
            )
        # If status == "sent", agent already sent the report via send_to_owner
    except Exception:
        log.exception("cmd_settled_error")
        await update.message.reply_text("❌ Ошибка при запуске аналитика")


async def cmd_picks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /picks command — run content writer agent on-demand."""
    if not _is_owner(update):
        return

    log.info("cmd_picks from user=%s", update.effective_user.id)

    try:
        from .agents.content import run as content_run

        async with SessionLocal() as session:
            result = await content_run(session)

        status = result.get("status", "done")
        if status == "skipped":
            reason = html.escape(result.get("reason", "no data"))
            await update.message.reply_text(
                f"✍️ Content: нет данных — {reason}",
                parse_mode="HTML",
            )
        # If status == "sent", agent already sent the post via send_to_owner
    except Exception:
        log.exception("cmd_picks_error")
        await update.message.reply_text("❌ Ошибка при запуске контентщика")


async def cmd_scout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /scout command — run scout agent on-demand."""
    if not _is_owner(update):
        return

    log.info("cmd_scout from user=%s", update.effective_user.id)

    try:
        from .agents.scout import run as scout_run

        async with SessionLocal() as session:
            result = await scout_run(session)

        status = result.get("status", "done")
        if status == "skipped":
            reason = html.escape(result.get("reason", "no data"))
            await update.message.reply_text(
                f"🔍 Scout: нет данных — {reason}",
                parse_mode="HTML",
            )
        elif status == "error":
            reason = html.escape(result.get("reason", "unknown"))
            await update.message.reply_text(
                f"🔍 Scout: ошибка — {reason}",
                parse_mode="HTML",
            )
        # If status == "sent", agent already sent the report via send_to_owner
    except Exception:
        log.exception("cmd_scout_error")
        await update.message.reply_text("❌ Ошибка при запуске скаута")


async def cmd_override(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /override command — override scout verdict.

    Usage: /override <fixture_id> <verdict> [reason...]
    Example: /override 12345 green coach confirmed full squad
    """
    if not _is_owner(update):
        return

    log.info("cmd_override from user=%s", update.effective_user.id)

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Формат: /override &lt;fixture_id&gt; &lt;verdict&gt; [причина]\n"
            "Пример: /override 12345 green основной состав подтверждён",
            parse_mode="HTML",
        )
        return

    try:
        fixture_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ fixture_id должен быть числом")
        return

    new_verdict = args[1].lower().strip()
    if new_verdict not in ("green", "yellow", "red"):
        await update.message.reply_text(
            "❌ verdict должен быть: green, yellow, red"
        )
        return

    reason = " ".join(args[2:]) if len(args) > 2 else "manual override"

    try:
        from .queries import override_scout_verdict

        async with SessionLocal() as session:
            updated = await override_scout_verdict(
                session, fixture_id, new_verdict, reason
            )

        emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(new_verdict, "⚪")
        if updated:
            await update.message.reply_text(
                f"✅ Вердикт для fixture {fixture_id} → {emoji} {new_verdict.upper()}\n"
                f"Причина: {html.escape(reason)}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"⚠️ Scout-отчёт для fixture {fixture_id} не найден",
                parse_mode="HTML",
            )
    except Exception:
        log.exception("cmd_override_error")
        await update.message.reply_text("❌ Ошибка при переопределении")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — welcome message."""
    if not _is_owner(update):
        return

    await update.message.reply_text(
        "🤖 <b>AI Office Bot</b> активен!\n\n"
        "Используй /help для списка команд.\n"
        "Используй /status для проверки системы.",
        parse_mode="HTML",
    )


def build_bot() -> Application:
    """Build and configure the Telegram bot application."""
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured for AI Office bot")

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("settled", cmd_settled))
    app.add_handler(CommandHandler("picks", cmd_picks))
    app.add_handler(CommandHandler("scout", cmd_scout))
    app.add_handler(CommandHandler("override", cmd_override))
    app.add_handler(CommandHandler("help", cmd_help))

    log.info("telegram_bot_built handlers=%d", len(app.handlers[0]))
    return app
