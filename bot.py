"""
Telegram-бот дежурного SRE-инженера (на базе aiogram 3.x).

Архитектурная роль:
1. Мобильное рабочее место:
   - Доставка критических алертов с анализом первопричины (RCA) и Runbook-командами.
   - Просмотр актуального статуса мониторинга и последних инцидентов (/status, /incidents).
2. Двухфакторная верификация инженера:
   - Авторизация через 4-значный временный PIN-код, генерируемый в веб-панели управления.
   - Защита от спама и несанкционированного получения производственных алертов.
3. Интерактивное управление инцидентами:
   - Кнопки смены статуса (Resolved / Reopen) и сброса кэша прямо под карточкой алерта.
   - Двусторонняя синхронизация с базой данных SQLite и веб-интерфейсом платформы.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    CopyTextButton,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from src.notifier import (
    get_active_chat_id,
    verify_telegram_binding,
    clear_chat_id_from_env,
    get_watched_log_path,
    get_active_bot_token,
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or get_active_bot_token()

if not TELEGRAM_BOT_TOKEN:
    print("[ОШИБКА] Переменная TELEGRAM_BOT_TOKEN не задана в .env файле!")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("sre-bot")

dp = Dispatcher()


# ---------------------------------------------------------------------------
# Клавиатуры интерфейса
# ---------------------------------------------------------------------------

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Постоянное нижнее меню быстрых действий дежурного инженера с эмодзи."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆔 Мой ID"), KeyboardButton(text="🔐 Верификация")],
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="🚨 Инциденты")],
            [KeyboardButton(text="ℹ️ Помощь"), KeyboardButton(text="❌ Отвязать")],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def get_id_inline_keyboard(user_id: str) -> InlineKeyboardMarkup:
    """Инлайн-кнопки под карточкой ID: моментальное копирование в буфер и переход в веб-панель."""
    try:
        copy_btn = InlineKeyboardButton(
            text=f"📋 Скопировать ID: {user_id}",
            copy_text=CopyTextButton(text=str(user_id))
        )
    except Exception:
        copy_btn = InlineKeyboardButton(
            text=f"📋 Скопировать ID: {user_id}",
            callback_data=f"copy_id:{user_id}"
        )

    dash_btn = InlineKeyboardButton(
        text="🌐 Открыть Веб-Панель",
        url="http://127.0.0.1:8000"
    )

    return InlineKeyboardMarkup(inline_keyboard=[[copy_btn], [dash_btn]])


def get_incident_action_keyboard(incident_id: int, is_resolved: bool = False) -> InlineKeyboardMarkup:
    """Генерация интерактивной клавиатуры действий под карточкой инцидента."""
    resolve_btn_text = "🔄 Открыть заново" if is_resolved else "✅ Пометить решённым"
    resolve_btn_data = f"reopen:{incident_id}" if is_resolved else f"resolve:{incident_id}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=resolve_btn_text, callback_data=resolve_btn_data),
                InlineKeyboardButton(text="🧹 Очистить кэш", callback_data=f"clearcache:{incident_id}"),
            ],
            [
                InlineKeyboardButton(text="📊 Открыть панель", url="http://127.0.0.1:8000"),
            ]
        ]
    )
    return kb


# ---------------------------------------------------------------------------
# Обработчики команд и кнопок меню
# ---------------------------------------------------------------------------

@dp.message(CommandStart())
async def handle_start(message: types.Message):
    active_cid = get_active_chat_id()
    user_cid = str(message.chat.id)
    is_bound = (active_cid == user_cid)
    status_badge = "✅ <b>BOUND (Активен)</b>" if is_bound else "⚠️ <b>NOT BOUND (Не привязан)</b>"

    welcome_text = (
        "👋 <b>Добро пожаловать в SRE Incident Alerting Bot!</b>\n\n"
        f"• 🆔 <b>Ваш Chat ID:</b> <code>{user_cid}</code>\n"
        f"• 🛡 <b>Статус дежурного:</b> {status_badge}\n\n"
        "🔍 Система круглосуточно мониторит системные логи, агрегирует каскадные сбои и отправляет детализированный Root Cause Analysis (RCA).\n\n"
        "Используйте кнопки меню внизу для быстрого управления:"
    )
    await message.answer(welcome_text, reply_markup=get_main_reply_keyboard())
    await message.answer(
        "Нажмите на кнопку с номером, чтобы скопировать ваш Chat ID в буфер обмена:",
        reply_markup=get_id_inline_keyboard(user_cid)
    )


@dp.message(Command("id"))
@dp.message(Command("myid"))
@dp.message(F.text.in_(["Мой ID", "🆔 Мой ID", "ID", "id", "мой id", "мой ID"]))
async def handle_id_button(message: types.Message):
    user_cid = str(message.chat.id)
    text = (
        "🆔 <b>Ваш персональный Telegram Chat ID:</b>\n"
        f"<code>{user_cid}</code>\n\n"
        "<i>Вставьте этот ID в веб-интерфейсе панели управления для генерации одноразового 2FA PIN-кода.</i>"
    )
    await message.answer(text, reply_markup=get_id_inline_keyboard(user_cid))


@dp.message(Command("status"))
@dp.message(F.text.in_(["Статус", "📊 Статус", "статус"]))
async def handle_status_button(message: types.Message):
    active_cid = get_active_chat_id()
    user_cid = str(message.chat.id)
    is_bound = (active_cid == user_cid)
    log_path = get_watched_log_path()

    if is_bound:
        role_status = "✅ <b>Вы являетесь активным дежурным инженером.</b> Все критические алерты направляются в этот чат."
    elif active_cid:
        role_status = f"⚠️ <b>К системе привязан другой аккаунт</b> (ID: <code>{active_cid}</code>).\nЧтобы переключить алерты на этот чат, пройдите авторизацию по PIN-коду."
    else:
        role_status = "⏸ <b>Дежурный не назначен.</b> Оповещения в Telegram временно приостановлены."

    status_text = (
        "📊 <b>Статус платформы мониторинга SRE:</b>\n\n"
        f"• 🆔 <b>Ваш Chat ID:</b> <code>{user_cid}</code>\n"
        f"• 👨‍💻 <b>Активный дежурный:</b> <code>{active_cid or 'Не привязан'}</code>\n"
        f"• 🔐 <b>Статус авторизации:</b> {'✅ Привязан' if is_bound else '❌ Не привязан'}\n"
        f"• 📁 <b>Отслеживаемый файл:</b> <code>{log_path}</code>\n"
        f"• ⚡ <b>Файл существует:</b> {'✅ Да' if log_path.exists() else '❌ Нет'}\n\n"
        f"{role_status}"
    )
    await message.answer(status_text, reply_markup=get_main_reply_keyboard())


@dp.message(Command("verify"))
@dp.message(F.text.in_(["Верификация", "🔐 Верификация", "верификация"]))
async def handle_verification_button(message: types.Message):
    active_cid = get_active_chat_id()
    user_cid = str(message.chat.id)
    is_bound = (active_cid == user_cid)

    if is_bound:
        rebind_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Сменить привязку / Ввести новый PIN", callback_data="force_rebind")],
                [InlineKeyboardButton(text="🌐 Открыть Веб-Панель", url="http://127.0.0.1:8000")],
            ]
        )
        await message.answer(
            "✅ <b>Вы уже успешно верифицированы!</b>\n\n"
            f"Ваш аккаунт (ID: <code>{user_cid}</code>) активен в системе мониторинга 🛡.\n"
            "Все критические инциденты и отчёты RCA направляются в этот чат.",
            reply_markup=rebind_keyboard
        )
        return

    text = (
        "🔐 <b>Верификация дежурного инженера (2FA):</b>\n\n"
        "1. 🌐 Откройте веб-панель управления: http://127.0.0.1:8000\n"
        "2. 💬 В верхнем правом углу нажмите кнопку <b>Telegram</b>.\n"
        f"3. 🔑 Укажите ваш ID: <code>{user_cid}</code> и нажмите <b>«Получить PIN»</b>.\n"
        "4. 📲 Отправьте полученный 4-значный PIN-код прямо в этот чат (например: <code>1234</code>)."
    )
    await message.answer(text, reply_markup=get_id_inline_keyboard(user_cid))


@dp.message(Command("incidents"))
@dp.message(F.text.in_(["Инциденты", "🚨 Инциденты", "инциденты"]))
async def handle_incidents_button(message: types.Message):
    try:
        from src.db import SessionLocal
        from src.models import LogEvent
        from sqlalchemy import desc

        session = SessionLocal()
        events = (
            session.query(LogEvent)
            .filter(LogEvent.log_level.in_(["ERROR", "CRITICAL"]))
            .order_by(desc(LogEvent.timestamp))
            .limit(5)
            .all()
        )
        session.close()

        if not events:
            await message.answer(
                "✅ <b>Всё спокойно!</b> В базе данных нет активных ошибок или критических инцидентов.",
                reply_markup=get_main_reply_keyboard()
            )
            return

        await message.answer("🚨 <b>Последние 5 инцидентов в системе:</b>")
        for ev in events:
            ts = ev.timestamp.strftime("%Y-%m-%d %H:%M:%S") if ev.timestamp else "--:--:--"
            card_text = (
                f"• <b>[{ts}]</b> <code>{ev.service_name}</code> (<b>{ev.log_level}</b>)\n"
                f"  <i>Trace ID:</i> <code>{ev.trace_id or 'none'}</code>\n"
                f"  <i>Ошибка:</i> {ev.raw_message[:140]}..."
            )
            await message.answer(
                card_text,
                reply_markup=get_incident_action_keyboard(ev.event_id)
            )
    except Exception as e:
        logger.error(f"Ошибка при получении инцидентов: {e}")
        await message.answer(f"Не удалось получить список инцидентов: {e}")


@dp.message(Command("unbind"))
@dp.message(F.text.in_(["Отвязать", "❌ Отвязать", "отвязать"]))
async def handle_unbind_button(message: types.Message):
    active_cid = get_active_chat_id()
    user_cid = str(message.chat.id)

    if active_cid == user_cid:
        clear_chat_id_from_env()
        await message.answer(
            "ℹ️ <b>Привязка чата успешно сброшена!</b>\n\n"
            "Уведомления в этот чат приостановлены. Для возобновления нажмите кнопку «🔐 Верификация».",
            reply_markup=get_main_reply_keyboard()
        )
    else:
        await message.answer(
            "⚠️ Этот чат не является активным зарегистрированным дежурным.",
            reply_markup=get_main_reply_keyboard()
        )


@dp.message(Command("help"))
@dp.message(F.text.in_(["Помощь", "ℹ️ Помощь", "помощь", "Справка", "справка"]))
async def handle_help_button(message: types.Message):
    help_text = (
        "📖 <b>Справочник команд и кнопок SRE-бота:</b>\n\n"
        "• 🆔 <b>Мой ID</b> — узнать и скопировать в буфер ваш Telegram Chat ID\n"
        "• 🔐 <b>Верификация</b> — инструкция по привязке аккаунта через одноразовый PIN\n"
        "• 📊 <b>Статус</b> — текущее состояние мониторинга и отслеживаемого файла логов\n"
        "• 🚨 <b>Инциденты</b> — список 5 последних зафиксированных ошибок из базы\n"
        "• ❌ <b>Отвязать</b> — отключить получение уведомлений об инцидентах\n\n"
        "<i>Для подтверждения привязки просто отправьте в чат полученный 4-значный PIN-код (например: <code>1234</code>).</i>"
    )
    await message.answer(help_text, reply_markup=get_main_reply_keyboard())


# ---------------------------------------------------------------------------
# Обработка ввода PIN-кода и произвольного текста
# ---------------------------------------------------------------------------

@dp.message(F.text)
async def handle_pin_or_arbitrary_text(message: types.Message):
    text = message.text.strip()
    user_cid = str(message.chat.id)

    # 1. Проверяем, введён ли PIN-код (напрямую 4 цифры или через /bind 1234)
    pin_candidate = ""
    if text.startswith("/bind"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            pin_candidate = parts[1].strip()
    elif text.isdigit() and len(text) == 4:
        pin_candidate = text

    if pin_candidate:
        success = verify_telegram_binding(user_cid, pin_candidate)
        if success:
            await message.answer(
                "✅ <b>Авторизация успешно подтверждена!</b> 🎉\n\n"
                f"Чат <code>{user_cid}</code> надёжно привязан к системе мониторинга 🛡.\n"
                "Сюда будут незамедлительно поступать алерты о критических сбоях 🚨 и отчёты RCA с кнопками быстрого реагирования ⚡.",
                reply_markup=get_main_reply_keyboard()
            )
        else:
            await message.answer(
                "❌ <b>Неверный или истёкший PIN-код.</b>\n\n"
                f"Убедитесь, что вы запросили PIN в веб-панели (http://127.0.0.1:8000) для Chat ID <code>{user_cid}</code> 🔑.\n"
                "Срок действия одноразового кода — 5 минут (максимум 3 попытки).",
                reply_markup=get_main_reply_keyboard()
            )
        return

    # 2. Если отправлен произвольный текст — напоминаем о кнопках меню
    await message.answer(
        "ℹ️ <b>Используйте меню быстрых действий внизу</b> для работы с ботом или отправьте 4-значный PIN-код для привязки.",
        reply_markup=get_main_reply_keyboard()
    )


# ---------------------------------------------------------------------------
# Callback-обработчики inline-кнопок
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "force_rebind")
async def handle_force_rebind_callback(callback: CallbackQuery):
    await callback.answer()
    user_cid = str(callback.message.chat.id)
    await callback.message.answer(
        "<b>Режим повторной привязки:</b>\n\n"
        f"1. Сгенерируйте новый PIN-код в веб-панели (http://127.0.0.1:8000) для ID <code>{user_cid}</code>.\n"
        "2. Отправьте полученные 4 цифры сообщением в этот чат.",
        reply_markup=get_id_inline_keyboard(user_cid)
    )


@dp.callback_query(F.data.startswith("copy_id:"))
async def handle_copy_id_callback(callback: CallbackQuery):
    user_id = callback.data.split(":")[1]
    await callback.answer(f"ID {user_id} готов к копированию!", show_alert=True)


@dp.callback_query(F.data.startswith("resolve:"))
async def handle_resolve_callback(callback: CallbackQuery):
    inc_id = int(callback.data.split(":")[1])
    try:
        from src.incident_store import set_incident_resolved
        set_incident_resolved(str(inc_id), True, user_id=f"tg_{callback.from_user.id}")
        await callback.message.edit_reply_markup(reply_markup=get_incident_action_keyboard(inc_id, is_resolved=True))
        await callback.answer(f"Инцидент #{inc_id} помечен как решённый")
    except Exception as e:
        logger.error(f"Ошибка resolve callback: {e}")
        await callback.answer("Ошибка обновления статуса")


@dp.callback_query(F.data.startswith("reopen:"))
async def handle_reopen_callback(callback: CallbackQuery):
    inc_id = int(callback.data.split(":")[1])
    try:
        from src.incident_store import set_incident_resolved
        set_incident_resolved(str(inc_id), False, user_id=f"tg_{callback.from_user.id}")
        await callback.message.edit_reply_markup(reply_markup=get_incident_action_keyboard(inc_id, is_resolved=False))
        await callback.answer(f"Инцидент #{inc_id} снова открыт")
    except Exception as e:
        logger.error(f"Ошибка reopen callback: {e}")
        await callback.answer("Ошибка обновления статуса")


@dp.callback_query(F.data.startswith("clearcache:"))
async def handle_clearcache_callback(callback: CallbackQuery):
    inc_id = str(callback.data.split(":")[1])
    try:
        from src.incident_store import clear_cached_report
        clear_cached_report(inc_id)
        await callback.answer(f"Кэш RCA для #{inc_id} очищен")
    except Exception as e:
        logger.error(f"Ошибка clearcache callback: {e}")
        await callback.answer("Ошибка очистки кэша")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def main():
    session = AiohttpSession()
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    try:
        logger.info("Сброс устаревших вебхуков и ожидающих апдейтов...")
        await bot.delete_webhook(drop_pending_updates=True)

        logger.info("[BOT] Telegram-бот дежурного успешно запущен и слушает события...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("[BOT] Остановка процесса бота.")