import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CopyTextButton
)

from src.notifier import get_active_chat_id, verify_telegram_binding


class AuthState(StatesGroup):
    waiting_for_pin = State()


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
if not TOKEN:
    print("[!] ВНИМАНИЕ: TELEGRAM_BOT_TOKEN не обнаружен в .env", flush=True)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# --- КЛАВИАТУРЫ ---

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Постоянное нижнее меню быстрых действий"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆔 Мой ID"), KeyboardButton(text="🔑 Верификация")],
            [KeyboardButton(text="📊 Статус")]
        ],
        resize_keyboard=True
    )


def get_id_inline_keyboard(user_id: str) -> InlineKeyboardMarkup:
    """Кнопки с копированием ID в буфер и шерингом"""
    try:
        copy_btn = InlineKeyboardButton(
            text=f"{user_id}",
            copy_text=CopyTextButton(text=user_id)
        )
    except (TypeError, AttributeError):
        copy_btn = InlineKeyboardButton(
            text=f"📋 {user_id}",
            callback_data=f"copy_{user_id}"
        )

    share_btn = InlineKeyboardButton(
        text="Share ID",
        switch_inline_query=f"{user_id}"
    )

    return InlineKeyboardMarkup(inline_keyboard=[[copy_btn], [share_btn]])


# --- ОБРАБОТЧИКИ СООБЩЕНИЙ ---

@dp.message(CommandStart())
async def handle_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)
    active_id = get_active_chat_id()
    is_bound = (str(active_id) == user_id)
    status_text = "🟢 <b>BOUND (Активен)</b>" if is_bound else "🔴 <b>NOT BOUND (Не привязан)</b>"

    text = (
        f"👋 <b>SRE Incident Alert Bot</b>\n\n"
        f"Ваш ID: <code>{user_id}</code>\n"
        f"Статус дежурного: {status_text}\n\n"
        f"Используйте кнопки меню внизу для быстрого управления 👇"
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")
    await message.answer(
        "Нажмите на кнопку с номером, чтобы скопировать ID в буфер:",
        reply_markup=get_id_inline_keyboard(user_id)
    )


@dp.message(F.text == "🆔 Мой ID")
async def handle_id_button(message: types.Message):
    user_id = str(message.from_user.id)
    await message.answer(
        f"Ваш персональный Telegram ID:\n👉 <code>{user_id}</code>",
        reply_markup=get_id_inline_keyboard(user_id),
        parse_mode="HTML"
    )


@dp.message(F.text == "📊 Статус")
async def handle_status_button(message: types.Message):
    user_id = str(message.from_user.id)
    active_id = get_active_chat_id()

    if str(active_id) == user_id:
        msg = f"🟢 <b>Вы являетесь активным дежурным!</b>\nID: <code>{user_id}</code>\nКритические инциденты поступают в этот чат."
    elif active_id:
        msg = f"🟡 <b>Дежурный привязан, но это другой аккаунт:</b>\nАктивный ID: <code>{active_id}</code>\nВаш ID: <code>{user_id}</code>"
    else:
        msg = "🔴 <b>Дежурный не назначен (NOT BOUND).</b>\nПройдите верификацию по кнопке «🔑 Верификация»."

    await message.answer(msg, parse_mode="HTML")


@dp.message(F.text == "🔑 Верификация")
async def handle_auth_start(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    active_id = get_active_chat_id()

    # Защита от повторной верификации
    if active_id and str(active_id) == user_id:
        rebind_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Сменить привязку / Новый PIN", callback_data="force_rebind")]
            ]
        )
        await message.answer(
            "🟢 <b>Вы уже успешно верифицированы!</b>\n\n"
            f"Ваш аккаунт (ID: <code>{user_id}</code>) уже активен в системе мониторинга.\n"
            "Все критические алерты направляются сюда.",
            reply_markup=rebind_keyboard,
            parse_mode="HTML"
        )
        return

    await state.set_state(AuthState.waiting_for_pin)
    await message.answer(
        "🔢 <b>Введите 4-значный PIN-код</b> из веб-панели управления:\n"
        "<i>(Отправьте только 4 цифры сообщением)</i>",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "force_rebind")
async def handle_force_rebind(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AuthState.waiting_for_pin)
    await callback.message.answer(
        "🔢 <b>Режим повторной привязки:</b>\n"
        "Введите новый 4-значный PIN-код из веб-панели:",
        parse_mode="HTML"
    )


@dp.message(AuthState.waiting_for_pin)
async def handle_pin_input(message: types.Message, state: FSMContext):
    pin_text = message.text.strip()

    if not (pin_text.isdigit() and len(pin_text) == 4):
        await message.answer(
            "⚠️ Неверный формат! PIN должен состоять ровно из <b>4 цифр</b> (например: <code>1234</code>).\n"
            "Попробуйте ещё раз или выберите другое действие в меню.",
            parse_mode="HTML"
        )
        return

    user_id = str(message.from_user.id)

    # Вызываем с точными именами аргументов
    success, msg = verify_telegram_binding(
        incoming_chat_id=user_id,
        entered_pin=pin_text
    )

    if success:
        await state.clear()
        await message.answer(
            f"✅ <b>Успешно!</b>\n"
            f"Ваш аккаунт (ID: <code>{user_id}</code>) подтверждён.\n"
            f"Статус переключён на 🟢 <b>BOUND</b>. Критические инциденты будут приходить сюда.",
            reply_markup=get_main_reply_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"❌ <b>Ошибка верификации!</b> {msg}\n"
            "Сгенерируйте новый PIN в веб-интерфейсе и нажмите «🔑 Верификация» снова.",
            reply_markup=get_main_reply_keyboard(),
            parse_mode="HTML"
        )
        await state.clear()


async def main():
    print("🚀 [BOT] Telegram-бот дежурного успешно запущен и слушает события...", flush=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())