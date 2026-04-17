"""Telegram bot for productivity system.

Provides commands for daily schedule, task management, and quick notes.
Reuses patterns from automation_nanobot/nanobot/channels/telegram.py:
- _markdown_to_telegram_html() for message formatting
- Error handling with HTML→plain text fallback
- HTTPXRequest for connection pooling

New features not in existing projects:
- InlineKeyboard buttons (Feito/Pular/Adiar) for reminders
- CallbackQueryHandler for button presses
- APScheduler integration for periodic reminder checks
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config.settings import TELEGRAM_ALLOWED_IDS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE

TELEGRAM_MAX_MESSAGE_LEN = 4000

# --- Allowlist ---
# Owner (TELEGRAM_CHAT_ID) is always allowed.
# Additional IDs can be set via TELEGRAM_ALLOWED_IDS in .env or /allowlist command.
_extra_allowed: set[str] = set()
if TELEGRAM_ALLOWED_IDS:
    _extra_allowed = {x.strip() for x in TELEGRAM_ALLOWED_IDS.split(",") if x.strip()}


def _is_allowed(chat_id: int | str) -> bool:
    """Check if a chat_id is allowed to use the bot."""
    sid = str(chat_id)
    if not TELEGRAM_CHAT_ID:
        return True  # No owner set — allow all (first-run mode)
    return sid == TELEGRAM_CHAT_ID or sid in _extra_allowed


# ---------------------------------------------------------------------------
# Markdown → Telegram HTML (adapted from nanobot/channels/telegram.py)
# ---------------------------------------------------------------------------

def _strip_md(s: str) -> str:
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'~~(.+?)~~', r'\1', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    return s.strip()


def _render_table_box(table_lines: list[str]) -> str:
    def dw(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)
    rows: list[list[str]] = []
    has_sep = False
    for line in table_lines:
        cells = [_strip_md(c) for c in line.strip().strip('|').split('|')]
        if all(re.match(r'^:?-+:?$', c) for c in cells if c):
            has_sep = True
            continue
        rows.append(cells)
    if not rows or not has_sep:
        return '\n'.join(table_lines)
    ncols = max(len(r) for r in rows)
    for r in rows:
        r.extend([''] * (ncols - len(r)))
    widths = [max(dw(r[c]) for r in rows) for c in range(ncols)]
    def dr(cells: list[str]) -> str:
        return '  '.join(f'{c}{" " * (w - dw(c))}' for c, w in zip(cells, widths))
    out = [dr(rows[0])]
    out.append('  '.join('─' * w for w in widths))
    for row in rows[1:]:
        out.append(dr(row))
    return '\n'.join(out)


def _markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram-safe HTML (from nanobot)."""
    if not text:
        return ""
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)

    lines = text.split('\n')
    rebuilt: list[str] = []
    li = 0
    while li < len(lines):
        if re.match(r'^\s*\|.+\|', lines[li]):
            tbl: list[str] = []
            while li < len(lines) and re.match(r'^\s*\|.+\|', lines[li]):
                tbl.append(lines[li])
                li += 1
            box = _render_table_box(tbl)
            if box != '\n'.join(tbl):
                code_blocks.append(box)
                rebuilt.append(f"\x00CB{len(code_blocks) - 1}\x00")
            else:
                rebuilt.extend(tbl)
        else:
            rebuilt.append(lines[li])
            li += 1
    text = '\n'.join(rebuilt)

    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"
    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)

    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")
    return text


# ---------------------------------------------------------------------------
# Telegram message sending (stateless, for scheduled scripts)
# ---------------------------------------------------------------------------

async def send_telegram_message(text: str, parse_mode: str = "HTML") -> None:
    """Send a message to the configured chat. Used by standalone scripts."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping message")
        return
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        html = _markdown_to_telegram_html(text)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=html, parse_mode="HTML")
    except Exception:
        # Fallback to plain text
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        except Exception as e:
            logger.error("Failed to send Telegram message: {}", e)


# ---------------------------------------------------------------------------
# ProductivityBot — the interactive Telegram bot
# ---------------------------------------------------------------------------

class ProductivityBot:
    """Interactive Telegram bot with commands, inline buttons, and reminders."""

    BOT_COMMANDS = [
        BotCommand("start", "Iniciar o bot"),
        BotCommand("today", "Agenda de hoje"),
        BotCommand("todos", "Tarefas pendentes"),
        BotCommand("add", "Adicionar tarefa — /add Texto @prazo"),
        BotCommand("novo", "Novo compromisso — /novo Texto @quando [ate HH:MM] [em local] [repete regra]"),
        BotCommand("agenda", "Listar compromissos — /agenda [dias]"),
        BotCommand("editar", "Editar compromisso — /editar id campo=valor"),
        BotCommand("cancelar", "Cancelar compromisso — /cancelar id"),
        BotCommand("prazos", "Ver prazos proximos"),
        BotCommand("note", "Salvar nota — /note Texto"),
        BotCommand("done", "Concluir tarefa — /done numero"),
        BotCommand("recap", "Resumo do dia"),
        BotCommand("help", "Mostrar comandos"),
        BotCommand("sync", "Sincronizar calendario com Anytype"),
        BotCommand("ia", "Comando em linguagem natural — /ia texto livre"),
        BotCommand("classificar", "Classifica itens pendentes no Anytype"),
        BotCommand("buscar", "Busca inteligente — /buscar pergunta"),
        BotCommand("allowlist", "Gerenciar acessos — /allowlist"),
    ]

    def __init__(self, calendar_client=None, anytype_client=None, task_manager=None):
        self.calendar = calendar_client
        self.anytype = anytype_client
        self.task_manager = task_manager
        self._app: Application | None = None
        self._running = False
        self.tz = ZoneInfo(TIMEZONE)
        # Pending edit/cancel ops awaiting scope choice for recurring events.
        # token (8-char hex) → {"action": "edit"|"cancel", "event_id": str, "fields": dict|None}
        self._pending_ops: dict = {}

    def build_application(self) -> Application:
        """Build and configure the Telegram Application."""
        builder = Application.builder().token(TELEGRAM_BOT_TOKEN)
        builder = builder.connect_timeout(60.0).read_timeout(60.0).write_timeout(60.0)
        app = builder.build()

        # Command handlers
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("today", self._cmd_today))
        app.add_handler(CommandHandler("todos", self._cmd_todos))
        app.add_handler(CommandHandler("add", self._cmd_add))
        app.add_handler(CommandHandler("novo", self._cmd_novo))
        app.add_handler(CommandHandler("agenda", self._cmd_agenda))
        app.add_handler(CommandHandler("editar", self._cmd_editar))
        app.add_handler(CommandHandler("cancelar", self._cmd_cancelar))
        app.add_handler(CommandHandler("prazos", self._cmd_prazos))
        app.add_handler(CommandHandler("note", self._cmd_note))
        app.add_handler(CommandHandler("done", self._cmd_done))
        app.add_handler(CommandHandler("recap", self._cmd_recap))
        app.add_handler(CommandHandler("sync", self._cmd_sync))
        app.add_handler(CommandHandler("ia", self._cmd_ia))
        app.add_handler(CommandHandler("classificar", self._cmd_classificar))
        app.add_handler(CommandHandler("buscar", self._cmd_buscar))
        app.add_handler(CommandHandler("allowlist", self._cmd_allowlist))

        # Inline button callback handler
        app.add_handler(CallbackQueryHandler(self._callback_handler))

        # Error handler
        app.add_error_handler(self._on_error)

        self._app = app
        return app

    async def run(self) -> None:
        """Start the bot polling loop."""
        if not TELEGRAM_BOT_TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN not configured")
            return

        app = self.build_application()
        self._running = True

        # Retry initialization up to 3 times (network can be flaky)
        for attempt in range(1, 4):
            try:
                await app.initialize()
                break
            except Exception as e:
                logger.warning("Bot init attempt {}/3 failed: {}", attempt, e)
                if attempt == 3:
                    raise
                await asyncio.sleep(5 * attempt)

        await app.start()

        bot_info = await app.bot.get_me()
        logger.info("Bot @{} connected", bot_info.username)

        try:
            await app.bot.set_my_commands(self.BOT_COMMANDS)
        except Exception as e:
            logger.warning("Failed to register commands: {}", e)

        await app.updater.start_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )

        logger.info("Bot polling started. Press Ctrl+C to stop.")
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        self._running = False
        if self._app:
            try:
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.warning("Error during bot shutdown: {}", e)
            self._app = None

    # --- Send helpers ---

    async def _send(self, chat_id: int | str, text: str, reply_markup=None) -> None:
        """Send a message with HTML formatting and fallback."""
        if not self._app:
            return
        try:
            html = _markdown_to_telegram_html(text)
            await self._app.bot.send_message(
                chat_id=chat_id, text=html, parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception:
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id, text=text, reply_markup=reply_markup,
                )
            except Exception as e:
                logger.error("Failed to send message: {}", e)

    async def send_reminder(self, task_id: str, title: str, minutes_until: int) -> None:
        """Send a reminder with inline buttons."""
        if not TELEGRAM_CHAT_ID:
            return
        text = f"⏰ <b>Em {minutes_until} min:</b> {title}"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Feito", callback_data=f"done:{task_id}"),
                InlineKeyboardButton("⏭ Pular", callback_data=f"skip:{task_id}"),
                InlineKeyboardButton("⏰ Adiar 30min", callback_data=f"snooze:{task_id}"),
            ]
        ])
        if self._app:
            try:
                await self._app.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID, text=text,
                    parse_mode="HTML", reply_markup=keyboard,
                )
            except Exception as e:
                logger.error("Failed to send reminder: {}", e)

    # --- Access control ---

    def _is_owner(self, chat_id: int | str) -> bool:
        """Check if a chat_id is the bot owner."""
        return str(chat_id) == TELEGRAM_CHAT_ID

    async def _check_access(self, update: Update) -> bool:
        """Check if the user is allowed. Returns False and sends warning if not."""
        if not update.message:
            return False
        chat_id = update.message.chat_id
        if _is_allowed(chat_id):
            return True
        user = update.effective_user
        name = user.first_name if user else "?"
        logger.warning("Blocked access from {} (chat_id: {})", name, chat_id)
        await self._send(chat_id, "Acesso negado. Voce nao esta na allowlist.")
        return False

    # --- Command handlers ---

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        chat_id = update.message.chat_id
        # /start always responds (so new users can see their chat_id)
        logger.info("User {} started bot (chat_id: {})", user.first_name, chat_id)
        if not _is_allowed(chat_id):
            await self._send(
                chat_id,
                f"Acesso negado.\n\nSeu chat_id: <code>{chat_id}</code>\n"
                "Peca ao dono do bot para adicionar voce com /allowlist."
            )
            return
        await self._send(
            chat_id,
            f"👋 Ola {user.first_name}! Sou seu bot de produtividade.\n\n"
            "<b>Comandos disponiveis:</b>\n"
            "/today — Agenda de hoje\n"
            "/todos — Tarefas pendentes\n"
            "/prazos — Ver prazos proximos\n"
            "/add Texto @prazo — Adicionar tarefa\n"
            "/note Texto — Salvar nota rapida\n"
            "/done N — Concluir tarefa N\n"
            "/recap — Resumo do dia\n"
            "/help — Mostrar ajuda\n\n"
            f"💡 Seu chat_id: <code>{chat_id}</code>"
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_access(update):
            return
        await self._send(
            update.message.chat_id,
            "<b>Comandos:</b>\n\n"
            "/today — Agenda + tarefas de hoje\n"
            "/todos — Lista de tarefas pendentes\n"
            "/prazos — Ver todos os prazos proximos\n"
            "/add Tarefa @prazo — Nova tarefa com prazo\n"
            "/note Texto — Salvar nota no Anytype\n"
            "/done 3 — Marcar tarefa #3 como concluida\n"
            "/recap — Gerar resumo do dia\n\n"
            "<b>Formatos de prazo:</b>\n"
            "  @14:30 — hoje\n"
            "  @15/04 — dia especifico\n"
            "  @15/04 17:00 — dia + hora\n"
            "  @amanha 09:00\n"
        )

    async def _cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show today's agenda: events + pending todos."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id

        if not self.calendar:
            await self._send(chat_id, "⚠️ Google Calendar nao configurado.")
            return

        try:
            events = self.calendar.get_today_events()
        except Exception as e:
            await self._send(chat_id, f"❌ Erro ao buscar agenda: {e}")
            return

        now = datetime.now(self.tz)
        date_str = now.strftime("%d/%m/%Y (%A)")

        regular_events = [e for e in events if not e.is_todo]
        todos = [e for e in events if e.is_todo]

        lines = [f"📅 <b>{date_str}</b>\n"]

        if regular_events:
            lines.append("🗓 <b>Eventos:</b>")
            for ev in regular_events:
                if ev.is_all_day:
                    lines.append(f"  • {ev.title} (dia todo)")
                else:
                    start = ev.start.strftime("%H:%M")
                    end = ev.end.strftime("%H:%M")
                    lines.append(f"  • {start}-{end} {ev.title}")
        else:
            lines.append("🗓 Nenhum evento hoje")

        lines.append("")
        if todos:
            lines.append("✅ <b>Tarefas:</b>")
            for i, td in enumerate(todos, 1):
                due = td.start.strftime("%H:%M") if not td.is_all_day else "sem horario"
                lines.append(f"  {i}. {td.title} ({due})")
        else:
            lines.append("✅ Nenhuma tarefa pendente")

        await self._send(chat_id, "\n".join(lines))

    async def _cmd_todos(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List pending tasks."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id

        if not self.calendar:
            await self._send(chat_id, "⚠️ Google Calendar nao configurado.")
            return

        try:
            todos = self.calendar.get_todos()
        except Exception as e:
            await self._send(chat_id, f"❌ Erro: {e}")
            return

        if not todos:
            await self._send(chat_id, "✅ Nenhuma tarefa pendente! 🎉")
            return

        lines = ["<b>Tarefas pendentes (hoje):</b>\n"]
        for i, task in enumerate(todos, 1):
            due_str = task.due.strftime("%H:%M") if task.due else "sem horario"
            lines.append(f"  {i}. {task.title} ({due_str})")
        lines.append(f"\nUse /done N para concluir uma tarefa")
        lines.append("Use /prazos para ver todos os prazos")

        await self._send(chat_id, "\n".join(lines))

    async def _cmd_prazos(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show upcoming deadlines for the next 30 days."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id

        if not self.calendar:
            await self._send(chat_id, "Google Calendar nao configurado.")
            return

        try:
            todos = self.calendar.get_upcoming_todos(days=60)
        except Exception as e:
            await self._send(chat_id, f"Erro: {e}")
            return

        if not todos:
            await self._send(chat_id, "Nenhum prazo nos proximos 60 dias!")
            return

        now = datetime.now(self.tz)
        lines = ["<b>Prazos proximos (60 dias):</b>\n"]

        # Group by urgency
        atrasados = []
        hoje_list = []
        amanha_list = []
        semana_list = []
        depois_list = []

        for td in todos:
            days_left = (td.start.date() - now.date()).days
            entry = (td, days_left)
            if days_left < 0:
                atrasados.append(entry)
            elif days_left == 0:
                hoje_list.append(entry)
            elif days_left == 1:
                amanha_list.append(entry)
            elif days_left <= 7:
                semana_list.append(entry)
            else:
                depois_list.append(entry)

        if atrasados:
            lines.append("🔴 <b>ATRASADO:</b>")
            for td, d in atrasados:
                lines.append(f"  ‼️ {td.title} ({td.start.strftime('%d/%m %H:%M')}) — {abs(d)} dia(s) atras")
            lines.append("")

        if hoje_list:
            lines.append("🟠 <b>HOJE:</b>")
            for td, d in hoje_list:
                lines.append(f"  ⏰ {td.title} ({td.start.strftime('%H:%M')})")
            lines.append("")

        if amanha_list:
            lines.append("🟡 <b>AMANHA:</b>")
            for td, d in amanha_list:
                lines.append(f"  📌 {td.title} ({td.start.strftime('%H:%M')})")
            lines.append("")

        if semana_list:
            lines.append("🔵 <b>ESTA SEMANA:</b>")
            for td, d in semana_list:
                lines.append(f"  📋 {td.title} ({td.start.strftime('%d/%m %H:%M')}) — {d} dias")
            lines.append("")

        if depois_list:
            lines.append("⚪ <b>DEPOIS:</b>")
            for td, d in depois_list:
                lines.append(f"  📋 {td.title} ({td.start.strftime('%d/%m %H:%M')}) — {d} dias")

        total = len(todos)
        lines.append(f"\n<b>Total: {total} prazo(s)</b>")

        await self._send(chat_id, "\n".join(lines))

    def _parse_deadline(self, text: str) -> tuple[str, datetime | None]:
        """Parse task text and extract deadline.

        Supported formats:
            @HH:MM              → today (or tomorrow if past)
            @DD/MM HH:MM        → specific date + time
            @DD/MM/AAAA HH:MM   → full date + time
            @DD/MM              → specific date, no time (all day)
            @amanha HH:MM       → tomorrow + time
            @amanha             → tomorrow, no time

        Returns (clean_text, deadline_datetime_or_None).
        """
        now = datetime.now(self.tz)

        # @DD/MM/AAAA HH:MM
        m = re.search(r'@(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})', text)
        if m:
            d, mo, y, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            due = datetime(y, mo, d, h, mi, tzinfo=self.tz)
            return text[:m.start()].strip(), due

        # @DD/MM HH:MM
        m = re.search(r'@(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})', text)
        if m:
            d, mo, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            due = now.replace(month=mo, day=d, hour=h, minute=mi, second=0, microsecond=0)
            if due < now:
                due = due.replace(year=due.year + 1)
            return text[:m.start()].strip(), due

        # @DD/MM (date only, defaults to 23:59)
        m = re.search(r'@(\d{1,2})/(\d{1,2})(?!/)', text)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            due = now.replace(month=mo, day=d, hour=23, minute=59, second=0, microsecond=0)
            if due < now:
                due = due.replace(year=due.year + 1)
            return text[:m.start()].strip(), due

        # @amanha HH:MM
        m = re.search(r'@amanha\s+(\d{1,2}):(\d{2})', text, re.IGNORECASE)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            due = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
            return text[:m.start()].strip(), due

        # @amanha (no time)
        m = re.search(r'@amanha', text, re.IGNORECASE)
        if m:
            due = (now + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)
            return text[:m.start()].strip(), due

        # @HH:MM (today/tomorrow)
        m = re.search(r'@(\d{1,2}):(\d{2})', text)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if due < now:
                due += timedelta(days=1)
            return text[:m.start()].strip(), due

        return text, None

    async def _cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Add a new task with optional deadline.

        Formats:
            /add Tarefa @HH:MM
            /add Tarefa @DD/MM
            /add Tarefa @DD/MM HH:MM
            /add Tarefa @amanha 14:00
        """
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        text = update.message.text or ""

        raw = text.replace("/add", "", 1).strip()
        if not raw:
            await self._send(
                chat_id,
                "<b>Uso:</b> /add Texto @prazo\n\n"
                "<b>Formatos de prazo:</b>\n"
                "  /add Tarefa @14:30 — hoje as 14:30\n"
                "  /add Tarefa @15/04 — dia 15/04\n"
                "  /add Tarefa @15/04 17:00 — dia e hora\n"
                "  /add Tarefa @amanha 09:00\n"
                "  /add Tarefa — sem prazo"
            )
            return

        title, due = self._parse_deadline(raw)

        if not title:
            await self._send(chat_id, "Informe o texto da tarefa.")
            return

        if not self.calendar:
            await self._send(chat_id, "Google Calendar nao configurado.")
            return

        try:
            event_id = self.calendar.create_todo(title, due)

            # Also save to Anytype as native Task
            if self.anytype:
                self.anytype.create_task(
                    title=title,
                    due_date=due.isoformat() if due else None,
                    done=False,
                )

            if due:
                due_str = due.strftime("%d/%m %H:%M")
                days_left = (due.date() - datetime.now(self.tz).date()).days
                if days_left == 0:
                    label = "hoje"
                elif days_left == 1:
                    label = "amanha"
                else:
                    label = f"em {days_left} dias"
                await self._send(
                    chat_id,
                    f"Tarefa adicionada: <b>{title}</b>\n"
                    f"Prazo: {due_str} ({label})"
                )
            else:
                await self._send(chat_id, f"Tarefa adicionada: <b>{title}</b> (sem prazo)")
        except Exception as e:
            await self._send(chat_id, f"Erro ao criar tarefa: {e}")

    async def _cmd_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Save a quick note to Anytype: /note Text"""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        text = (update.message.text or "").replace("/note", "", 1).strip()

        if not text:
            await self._send(chat_id, "Uso: /note Texto da nota")
            return

        if not self.anytype:
            await self._send(chat_id, "⚠️ Anytype nao configurado.")
            return

        # Use first line as title, rest as body
        parts = text.split("\n", 1)
        title = parts[0][:100]
        body = parts[1] if len(parts) > 1 else ""

        # Extract tags from #hashtags
        tags = re.findall(r'#(\w+)', text)

        obj_id = self.anytype.save_note(title, body, tags or None)
        if obj_id:
            await self._send(chat_id, f"📝 Nota salva: <b>{title}</b>")
        else:
            await self._send(chat_id, "❌ Erro ao salvar nota no Anytype.")

    async def _cmd_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Mark a task as done: /done N"""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        text = (update.message.text or "").replace("/done", "", 1).strip()

        if not text.isdigit():
            await self._send(chat_id, "Uso: /done N (numero da tarefa)")
            return

        idx = int(text) - 1  # 1-indexed

        if not self.calendar:
            await self._send(chat_id, "⚠️ Google Calendar nao configurado.")
            return

        try:
            todos = self.calendar.get_todos()
            if idx < 0 or idx >= len(todos):
                await self._send(chat_id, f"❌ Tarefa #{text} nao encontrada. Use /todos para ver a lista.")
                return

            task = todos[idx]
            self.calendar.mark_todo_done(task.calendar_event_id or task.id)

            # Log to Anytype if available
            if self.anytype:
                self.anytype.log_completed_task(task.title)

            await self._send(chat_id, f"✅ Concluida: <b>{task.title}</b>")
        except Exception as e:
            await self._send(chat_id, f"❌ Erro: {e}")

    async def _cmd_recap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Trigger evening recap manually."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id

        # Import here to avoid circular deps
        from services.evening_recap import generate_evening_recap

        try:
            text = await generate_evening_recap(self.calendar, self.anytype)
            await self._send(chat_id, text)
        except Exception as e:
            await self._send(chat_id, f"❌ Erro ao gerar resumo: {e}")

    # --- Sync command ---

    async def _cmd_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Sync Google Calendar events to Anytype."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id

        if not self.calendar or not self.anytype:
            await self._send(chat_id, "Google Calendar ou Anytype nao configurados.")
            return

        await self._send(chat_id, "Sincronizando calendario com Anytype...")

        from services.calendar_sync import sync_calendar_to_anytype
        try:
            counts = await sync_calendar_to_anytype(self.calendar, self.anytype)
            await self._send(
                chat_id,
                f"<b>Sync concluido:</b>\n"
                f"  Criados: {counts['created']}\n"
                f"  Atualizados: {counts['updated']}\n"
                f"  Removidos: {counts['deleted']}\n"
                f"  Inalterados: {counts['skipped']}\n"
                f"  Erros: {counts['errors']}"
            )
        except Exception as e:
            await self._send(chat_id, f"Erro no sync: {e}")

    # --- /novo, /agenda, /editar, /cancelar (compromissos com recorrência) ---

    def _parse_novo(self, raw: str):
        """Parse '/novo Texto @quando [ate HH:MM] [em local] [repete regra]'.

        Keyword order is significant: repete must be last; em before repete; ate before em.
        Returns (title, start, end, location, rrule) or None on parse failure.
        """
        from services.recurrence import parse_recurrence

        rrule = None
        m = re.search(r'\brepete\s+(.+)$', raw, re.IGNORECASE)
        if m:
            rrule = parse_recurrence(m.group(1))
            raw = raw[: m.start()].strip()

        location = ""
        m = re.search(r'\bem\s+(.+)$', raw, re.IGNORECASE)
        if m:
            location = m.group(1).strip()
            raw = raw[: m.start()].strip()

        end_hm = None
        m = re.search(r'\bat[eé]\s+(\d{1,2}):(\d{2})\b', raw, re.IGNORECASE)
        if m:
            end_hm = (int(m.group(1)), int(m.group(2)))
            raw = (raw[: m.start()] + raw[m.end() :]).strip()

        title, start = self._parse_deadline(raw)
        if not start:
            return None

        if end_hm:
            end = start.replace(hour=end_hm[0], minute=end_hm[1], second=0, microsecond=0)
            if end <= start:
                end += timedelta(days=1)
        else:
            end = start + timedelta(hours=1)

        return title.strip(), start, end, location, rrule

    async def _cmd_novo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Create a calendar event (optionally recurring)."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        raw = (update.message.text or "").replace("/novo", "", 1).strip()

        if not raw:
            await self._send(
                chat_id,
                "<b>Uso:</b> /novo Texto @quando [ate HH:MM] [em local] [repete regra]\n\n"
                "<b>Exemplos:</b>\n"
                "  /novo Dentista @15/04 14:00 ate 15:00 em Clinica X\n"
                "  /novo Academia @07:00 ate 08:00 repete seg, qua, sex\n"
                "  /novo Reuniao @amanha 10:00 ate 11:00 repete semanal ate 30/06"
            )
            return

        if not self.calendar:
            await self._send(chat_id, "Google Calendar nao configurado.")
            return

        parsed = self._parse_novo(raw)
        if not parsed:
            await self._send(chat_id, "Nao entendi o horario. Use @HH:MM, @DD/MM HH:MM ou @amanha HH:MM.")
            return
        title, start, end, location, rrule = parsed

        if not title:
            await self._send(chat_id, "Informe o titulo do compromisso.")
            return

        recurrence = [f"RRULE:{rrule}"] if rrule else None
        try:
            event_id = self.calendar.create_event(
                title=title, start=start, end=end, location=location, recurrence=recurrence
            )
        except Exception as e:
            await self._send(chat_id, f"Erro ao criar compromisso: {e}")
            return

        msg = [
            f"Compromisso criado: <b>{title}</b>",
            f"Quando: {start.strftime('%d/%m %H:%M')}–{end.strftime('%H:%M')}",
        ]
        if location:
            msg.append(f"Local: {location}")
        if rrule:
            msg.append(f"Recorrencia: <code>{rrule}</code>")
        await self._send(chat_id, "\n".join(msg))

        await self._sync_incremental({event_id})

    async def _cmd_agenda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List upcoming calendar events with short IDs for /editar and /cancelar."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id

        if not self.calendar:
            await self._send(chat_id, "Google Calendar nao configurado.")
            return

        raw = (update.message.text or "").replace("/agenda", "", 1).strip()
        days = 7
        if raw:
            try:
                days = max(1, min(60, int(raw.split()[0])))
            except ValueError:
                pass

        try:
            events = self.calendar.get_all_events_range(days_back=0, days_forward=days)
        except Exception as e:
            await self._send(chat_id, f"Erro: {e}")
            return

        events = [e for e in events if not e.is_todo]
        if not events:
            await self._send(chat_id, f"Sem compromissos nos proximos {days} dias.")
            return

        lines = [f"<b>Agenda ({days} dias):</b>\n"]
        for ev in events[:50]:
            short = ev.id[:6]
            marker = " 🔁" if ev.recurring_event_id else ""
            time_s = ev.start.strftime("%d/%m %H:%M") + "–" + ev.end.strftime("%H:%M")
            line = f"<code>{short}</code> {time_s} — {ev.title}{marker}"
            if ev.location:
                line += f" ({ev.location})"
            lines.append(line)
        lines.append("\nUse /editar &lt;id&gt; campo=valor ou /cancelar &lt;id&gt;")
        await self._send(chat_id, "\n".join(lines))

    async def _resolve_event_by_prefix(self, chat_id: int, prefix: str):
        """Find a single event whose ID starts with `prefix`. Sends error if 0 or >1 match."""
        if not self.calendar:
            await self._send(chat_id, "Google Calendar nao configurado.")
            return None
        try:
            events = self.calendar.get_all_events_range(days_back=1, days_forward=120)
        except Exception as e:
            await self._send(chat_id, f"Erro ao buscar eventos: {e}")
            return None
        matches = [e for e in events if e.id.startswith(prefix)]
        if not matches:
            await self._send(chat_id, f"Nenhum compromisso com id <code>{prefix}</code>.")
            return None
        if len(matches) > 1:
            await self._send(chat_id, f"Prefixo ambiguo ({len(matches)} eventos). Use mais caracteres.")
            return None
        return matches[0]

    def _parse_editar_fields(self, text: str) -> dict:
        """Parse 'titulo=... inicio=DD/MM HH:MM fim=HH:MM local=...' into a dict."""
        fields: dict = {}
        keys = ["titulo", "inicio", "fim", "local"]
        # Find all key=value spans by scanning for next key
        positions = []
        for k in keys:
            for m in re.finditer(rf"\b{k}=", text, re.IGNORECASE):
                positions.append((m.start(), m.end(), k.lower()))
        positions.sort()
        for i, (s, e, k) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            fields[k] = text[e:end].strip()
        return fields

    async def _apply_edit(self, chat_id: int, event_id: str, fields: dict, scope: str) -> None:
        update_kwargs: dict = {}
        if "titulo" in fields:
            update_kwargs["title"] = fields["titulo"]
        if "local" in fields:
            update_kwargs["location"] = fields["local"]

        if "inicio" in fields or "fim" in fields:
            current = self.calendar.get_event_raw(event_id)
            cur_start = datetime.fromisoformat(current["start"]["dateTime"])
            cur_end = datetime.fromisoformat(current["end"]["dateTime"])
            if "inicio" in fields:
                _, new_start = self._parse_deadline("@" + fields["inicio"])
                if new_start:
                    update_kwargs["start"] = new_start
                    if "fim" not in fields:
                        update_kwargs["end"] = new_start + (cur_end - cur_start)
            if "fim" in fields:
                base = update_kwargs.get("start", cur_start)
                m = re.match(r"(\d{1,2}):(\d{2})", fields["fim"])
                if m:
                    update_kwargs["end"] = base.replace(
                        hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0
                    )

        try:
            updated_id = self.calendar.update_event(event_id, scope=scope, **update_kwargs)
            await self._send(chat_id, f"Compromisso atualizado (escopo: {scope}).")
            await self._sync_incremental({updated_id, event_id})
        except Exception as e:
            await self._send(chat_id, f"Erro ao editar: {e}")

    async def _cmd_editar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Edit a calendar event. Usage: /editar <prefix> campo=valor [campo=valor]"""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        raw = (update.message.text or "").replace("/editar", "", 1).strip()
        parts = raw.split(None, 1)
        if len(parts) < 2:
            await self._send(
                chat_id,
                "<b>Uso:</b> /editar &lt;id&gt; campo=valor\n\n"
                "<b>Campos:</b> titulo, inicio (DD/MM HH:MM), fim (HH:MM), local\n"
                "<b>Exemplo:</b> /editar abc123 titulo=Nova reuniao inicio=18/04 14:00"
            )
            return

        prefix, rest = parts[0], parts[1]
        ev = await self._resolve_event_by_prefix(chat_id, prefix)
        if not ev:
            return
        fields = self._parse_editar_fields(rest)
        if not fields:
            await self._send(chat_id, "Nenhum campo reconhecido. Use: titulo=, inicio=, fim=, local=")
            return

        if ev.recurring_event_id:
            token = ev.id[:8]
            self._pending_ops[token] = {"action": "edit", "event_id": ev.id, "fields": fields}
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Só esta", callback_data=f"scope:single:{token}"),
                InlineKeyboardButton("Toda a série", callback_data=f"scope:all:{token}"),
            ]])
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=f"<b>{ev.title}</b> é recorrente. Aplicar a:",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        await self._apply_edit(chat_id, ev.id, fields, scope="single")

    async def _cmd_cancelar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cancel a calendar event. Usage: /cancelar <prefix>"""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        raw = (update.message.text or "").replace("/cancelar", "", 1).strip()
        if not raw:
            await self._send(chat_id, "<b>Uso:</b> /cancelar &lt;id&gt;")
            return

        prefix = raw.split()[0]
        ev = await self._resolve_event_by_prefix(chat_id, prefix)
        if not ev:
            return

        if ev.recurring_event_id:
            token = ev.id[:8]
            self._pending_ops[token] = {"action": "cancel", "event_id": ev.id, "fields": None}
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Só esta", callback_data=f"scope:single:{token}"),
                InlineKeyboardButton("Toda a série", callback_data=f"scope:all:{token}"),
            ]])
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=f"<b>{ev.title}</b> é recorrente. Cancelar:",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        try:
            deleted_id = self.calendar.delete_event(ev.id, scope="single")
            await self._send(chat_id, f"Compromisso cancelado: <b>{ev.title}</b>")
            await self._sync_incremental({deleted_id})
        except Exception as e:
            await self._send(chat_id, f"Erro ao cancelar: {e}")

    async def _sync_incremental(self, event_ids: set[str]) -> None:
        """Run an incremental sync for the given event IDs (best-effort)."""
        if not self.calendar or not self.anytype:
            return
        from services.calendar_sync import sync_calendar_to_anytype
        try:
            await sync_calendar_to_anytype(self.calendar, self.anytype, only_event_ids=event_ids)
        except Exception as e:
            logger.warning("Incremental sync failed: {}", e)

    # --- AI-powered commands (/ia, /classificar, /buscar) ---

    async def _build_agenda_snippet(self, days_forward: int = 14) -> str:
        """Compact agenda block for grounding /ia resolutions ('a reuniao de amanha')."""
        if not self.calendar:
            return ""
        try:
            events = self.calendar.get_all_events_range(days_back=1, days_forward=days_forward)
        except Exception:
            return ""
        lines = []
        for ev in events[:30]:
            if ev.is_todo:
                continue
            lines.append(
                f"{ev.id[:6]} | {ev.start.strftime('%d/%m %H:%M')} | {ev.title}"
            )
        return "\n".join(lines)

    async def _cmd_ia(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Natural-language command: parse with Claude then execute."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        text = (update.message.text or "").replace("/ia", "", 1).strip()
        if not text:
            await self._send(
                chat_id,
                "<b>Uso:</b> /ia &lt;texto livre&gt;\n\n"
                "Exemplos:\n"
                "  /ia marca dentista quarta 14h na clinica central\n"
                "  /ia anota ideia pra monografia sobre redes neurais\n"
                "  /ia cancela a reuniao de amanha"
            )
            return

        await self._send(chat_id, "🧠 Processando...")

        from services.ai_parser import parse_ia_message
        from services.ai_subprocess import AISubprocessError

        agenda = await self._build_agenda_snippet()
        try:
            action = await parse_ia_message(text, agenda_snippet=agenda)
        except AISubprocessError as e:
            await self._send(chat_id, f"❌ Erro no modelo: {e}")
            return

        reply = await self._execute_ia_action(chat_id, action)
        if reply:
            await self._send(chat_id, reply)

    async def _execute_ia_action(self, chat_id: int, action: dict) -> str:
        """Dispatch the parsed /ia action. Returns a human-readable reply."""
        from datetime import datetime as _dt

        from services.ai_classifier import clamp_to_taxonomy

        act = action.get("action", "unknown")
        reply = action.get("reply") or ""
        tags = [t for t in (action.get("tags") or []) if isinstance(t, str)]
        area = action.get("area")
        prioridade = action.get("prioridade")
        # Clamp to taxonomy
        clean = clamp_to_taxonomy(
            {"area": area, "prioridade": prioridade, "tags": tags}
        )
        now_iso = datetime.now(self.tz).isoformat()

        if act == "create_appointment":
            if not self.calendar:
                return "Google Calendar nao configurado."
            title = action.get("title") or "(sem titulo)"
            try:
                start = _dt.fromisoformat(action["start"])
                end = _dt.fromisoformat(action["end"]) if action.get("end") else None
            except (KeyError, ValueError):
                return "Horario invalido retornado pelo modelo."
            if end is None:
                from datetime import timedelta as _td
                end = start + _td(hours=1)
            recurrence = None
            if action.get("recurrence"):
                recurrence = [f"RRULE:{action['recurrence']}"]
            try:
                event_id = self.calendar.create_event(
                    title=title, start=start, end=end,
                    location=action.get("location", "") or "",
                    recurrence=recurrence,
                )
            except Exception as e:
                return f"Erro ao criar compromisso: {e}"

            # Trigger sync; classify the new Anytype mirror once it exists
            await self._sync_incremental({event_id})
            self._classify_by_calendar_id_best_effort(
                event_id, clean, now_iso,
            )
            return reply or f"📅 Criado: <b>{title}</b>"

        if act == "create_task":
            if not self.calendar:
                return "Google Calendar nao configurado."
            title = action.get("title") or "(sem titulo)"
            due = None
            if action.get("start"):
                try:
                    due = _dt.fromisoformat(action["start"])
                except ValueError:
                    due = None
            try:
                self.calendar.create_todo(title, due)
            except Exception as e:
                return f"Erro ao criar tarefa: {e}"
            obj_id = None
            if self.anytype:
                obj_id = self.anytype.create_task(
                    title=title,
                    due_date=due.isoformat() if due else None,
                    done=False,
                )
            if obj_id and self.anytype:
                self.anytype.set_classification(
                    obj_id,
                    area=clean["area"],
                    prioridade=clean["prioridade"],
                    tags=clean["tags"],
                    classified_at=now_iso,
                )
            return reply or f"✅ Tarefa criada: <b>{title}</b>"

        if act == "create_note":
            if not self.anytype:
                return "Anytype nao configurado."
            title = action.get("title") or "Nota"
            body = action.get("body") or ""
            obj_id = self.anytype.save_note(title, body)
            if obj_id:
                self.anytype.set_classification(
                    obj_id,
                    area=clean["area"],
                    prioridade=clean["prioridade"],
                    tags=clean["tags"],
                    classified_at=now_iso,
                )
            return reply or f"📝 Nota salva: <b>{title}</b>"

        if act == "update_event":
            prefix = action.get("event_id_prefix") or ""
            fields = action.get("fields") or {}
            if not prefix or not fields:
                return "Modelo nao forneceu id/campos para edicao."
            ev = await self._resolve_event_by_prefix(chat_id, prefix)
            if not ev:
                return ""
            scope = "all" if ev.recurring_event_id else "single"
            await self._apply_edit(chat_id, ev.id, fields, scope=scope)
            return reply or ""

        if act == "cancel_event":
            prefix = action.get("event_id_prefix") or ""
            if not prefix:
                return "Modelo nao forneceu id para cancelar."
            ev = await self._resolve_event_by_prefix(chat_id, prefix)
            if not ev:
                return ""
            scope = "all" if ev.recurring_event_id else "single"
            try:
                deleted_id = self.calendar.delete_event(ev.id, scope=scope)
                await self._sync_incremental({deleted_id, ev.id})
            except Exception as e:
                return f"Erro ao cancelar: {e}"
            return reply or f"🗑 Cancelado: <b>{ev.title}</b>"

        return reply or "Nao entendi o pedido."

    def _classify_by_calendar_id_best_effort(
        self, calendar_event_id: str, clean: dict, now_iso: str,
    ) -> None:
        """Find the Anytype mirror of a just-created calendar event and tag it."""
        if not self.anytype:
            return
        try:
            from services.calendar_sync import _load_sync_state
            entry = _load_sync_state().get("events", {}).get(calendar_event_id)
            if not entry or not entry.get("anytype_id"):
                return
            self.anytype.set_classification(
                entry["anytype_id"],
                area=clean["area"],
                prioridade=clean["prioridade"],
                tags=clean["tags"],
                classified_at=now_iso,
            )
        except Exception as e:
            logger.debug("Best-effort classify after create failed: {}", e)

    async def _cmd_classificar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Batch-classify all unclassified Anytype items via a single LLM call."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        if not self.anytype:
            await self._send(chat_id, "Anytype nao configurado.")
            return

        await self._send(chat_id, "🏷 Classificando itens pendentes...")

        from services.ai_classifier import classify_unclassified
        try:
            counts = await classify_unclassified(self.anytype)
        except Exception as e:
            await self._send(chat_id, f"Erro ao classificar: {e}")
            return

        if counts["total"] == 0:
            await self._send(chat_id, "Nada a classificar — todos os itens ja tem label.")
            return

        by_area = counts["by_area"]
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(by_area.items(), key=lambda x: -x[1])) or "-"
        await self._send(
            chat_id,
            f"<b>Classificacao concluida</b>\n"
            f"  Processados: {counts['total']}\n"
            f"  Classificados: {counts['classified']}\n"
            f"  Falhas: {counts['failed']}\n"
            f"  Por area: {breakdown}",
        )

    async def _cmd_buscar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Natural-language search across Anytype + Calendar."""
        if not await self._check_access(update):
            return
        chat_id = update.message.chat_id
        question = (update.message.text or "").replace("/buscar", "", 1).strip()
        if not question:
            await self._send(
                chat_id,
                "<b>Uso:</b> /buscar &lt;pergunta&gt;\n\n"
                "Exemplos:\n"
                "  /buscar o que tenho de tcc essa semana?\n"
                "  /buscar quais compromissos esse mes?\n"
                "  /buscar resuma minhas notas sobre financas"
            )
            return

        await self._send(chat_id, "🔎 Buscando...")

        from services.ai_search import search
        try:
            result = await search(question, self.anytype, self.calendar)
        except Exception as e:
            await self._send(chat_id, f"Erro na busca: {e}")
            return

        answer = result.get("answer") or "(sem resposta)"
        cited = result.get("cited_ids") or []
        msg = answer
        if cited:
            msg += "\n\n<b>Itens citados:</b>"
            for cid in cited[:10]:
                msg += f"\n<code>{cid[:6]}</code>"
        await self._send(chat_id, msg)

    # --- Allowlist management (owner-only) ---

    async def _cmd_allowlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manage the allowlist: /allowlist, /allowlist add ID, /allowlist remove ID"""
        if not update.message:
            return
        chat_id = update.message.chat_id

        # Only the owner can manage the allowlist
        if not self._is_owner(chat_id):
            await self._send(chat_id, "Acesso negado. Apenas o dono pode gerenciar a allowlist.")
            return

        text = (update.message.text or "").replace("/allowlist", "", 1).strip()
        parts = text.split(None, 1)

        if not parts:
            # Show current allowlist
            allowed = sorted(_extra_allowed) if _extra_allowed else ["(vazia)"]
            lines = [
                "<b>Allowlist:</b>\n",
                f"Dono: <code>{TELEGRAM_CHAT_ID}</code> (sempre permitido)\n",
                "IDs extras:",
            ]
            for aid in allowed:
                lines.append(f"  - <code>{aid}</code>")
            lines.append("\n<b>Uso:</b>")
            lines.append("/allowlist add ID — adicionar")
            lines.append("/allowlist remove ID — remover")
            await self._send(chat_id, "\n".join(lines))
            return

        action = parts[0].lower()
        if len(parts) < 2:
            await self._send(chat_id, "Uso: /allowlist add ID ou /allowlist remove ID")
            return

        target_id = parts[1].strip()

        if action == "add":
            _extra_allowed.add(target_id)
            logger.info("Allowlist: added {}", target_id)
            await self._send(chat_id, f"Adicionado: <code>{target_id}</code>")
        elif action in ("remove", "rm", "del"):
            _extra_allowed.discard(target_id)
            logger.info("Allowlist: removed {}", target_id)
            await self._send(chat_id, f"Removido: <code>{target_id}</code>")
        else:
            await self._send(chat_id, "Uso: /allowlist add ID ou /allowlist remove ID")

    # --- Callback handler for inline buttons ---

    async def _callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button presses (done/skip/snooze)."""
        query = update.callback_query
        if not query or not query.data:
            return

        await query.answer()  # Acknowledge the callback

        parts = query.data.split(":", 1)
        if len(parts) != 2:
            return

        action = parts[0]

        # Scope chooser for /editar and /cancelar on recurring events.
        # callback_data: "scope:<single|all>:<token>"
        if action == "scope":
            sub = parts[1].split(":", 1)
            if len(sub) != 2:
                return
            scope, token = sub[0], sub[1]
            op = self._pending_ops.pop(token, None)
            if not op:
                await query.edit_message_text("Operacao expirada.")
                return
            chat_id = query.message.chat_id if query.message else None
            if chat_id is None:
                return
            if op["action"] == "edit":
                await query.edit_message_text(f"Aplicando edicao (escopo: {scope})...")
                await self._apply_edit(chat_id, op["event_id"], op["fields"], scope=scope)
            elif op["action"] == "cancel":
                try:
                    deleted_id = self.calendar.delete_event(op["event_id"], scope=scope)
                    await query.edit_message_text(f"Cancelado (escopo: {scope}).")
                    await self._sync_incremental({deleted_id, op["event_id"]})
                except Exception as e:
                    await query.edit_message_text(f"Erro: {e}")
            return

        task_id = parts[1]

        if action == "done":
            if self.calendar:
                self.calendar.mark_todo_done(task_id)
            if self.anytype:
                # Get task title from event
                try:
                    event = self.calendar.service.events().get(
                        calendarId=self.calendar.calendar_id, eventId=task_id
                    ).execute() if self.calendar and self.calendar.service else None
                    title = event.get("summary", "Tarefa").replace("[DONE]", "").replace("[TODO]", "").strip() if event else "Tarefa"
                except Exception:
                    title = "Tarefa"
                self.anytype.log_completed_task(title)
            await query.edit_message_text("✅ <b>Concluida!</b>", parse_mode="HTML")

        elif action == "skip":
            await query.edit_message_text("⏭ <b>Pulada.</b>", parse_mode="HTML")

        elif action == "snooze":
            if self.calendar:
                self.calendar.snooze_todo(task_id, minutes=30)
            await query.edit_message_text("⏰ <b>Adiada 30 min.</b>", parse_mode="HTML")

    # --- Error handler ---

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Telegram bot error: {}", context.error)
