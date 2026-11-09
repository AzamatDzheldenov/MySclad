"""
Асинхронный Telegram-бот на aiogram 3.x для водителей доставки и кладовщиков,
интегрированный с бэкенд-API FastAPI (/api/...).

Реализованные сценарии:
1. Авторизация (/start):
   - По кнопке «📱 Отправить свой контакт» ИЛИ вводом номера телефона / табельного кода сотрудника.
   - Сохраняет `telegram_chat_id` в таблицу `users` через `POST /api/users/auth/telegram`.

2. Сценарий водителя:
   - Команда `/my_orders` запрашивает назначенные на водителя заказы со статусом «В пути» (`in_transit`)
     и выводит карточки с адресами, телефонами клиентов, составом и суммами.
   - Под каждой карточкой заказа — inline-кнопки:
     • «✅ Доставлен»
     • «⚠️ Проблема с заказом»
   - При нажатии «✅ Доставлен»:
     Шаг 1 (FSM `DeliveryFlow.choosing_payment`): Бот запрашивает способ оплаты:
            «💵 Оплата получена наличными» или «💳 Безналичный расчет».
     Шаг 2 (FSM `DeliveryFlow.waiting_for_photo`): Бот требует сфотографировать и отправить
            подписанную бумажную накладную.
     Шаг 3: При отправке фото бот скачивает бинарник фотографии из Telegram, отправляет
            `multipart/form-data` запрос `POST /api/orders/{id}/delivered` на бэкенд,
            переводит заказ в статус `delivered` и списывает остатки (`stock_quantity` и `reserved_quantity`).

3. Сценарий кладовщика:
   - При переводе заказа в статус «На сборке» (`assembling`) бэкенд автоматически присылает
     кладовщику уведомление со списком позиций и inline-кнопкой «✅ Собрано (Готов к отгрузке)».
   - Также доступна команда `/assembling` для просмотра всех заказов на сборке.
   - При нажатии кнопки «✅ Собрано» бот отправляет `PATCH /api/orders/{id}/status`
     со статусом `ready_for_shipment` («Готов к отгрузке»).

Запуск:
    export TELEGRAM_BOT_TOKEN="ваш_токен_от_BotFather"
    export API_BASE_URL="http://localhost:8000/api"
    python3 -m app.bot.telegram_bot
"""

import asyncio
import io
import logging
import os
from typing import Any

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("moysklad_bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or settings.TELEGRAM_BOT_TOKEN
API_BASE_URL = (os.getenv("API_BASE_URL") or settings.API_BASE_URL).rstrip("/")

router = Router()


# ============================================================================
# 1. СОСТОЯНИЯ КОНЕЧНОГО АВТОМАТА (FSM)
# ============================================================================

class AuthFlow(StatesGroup):
    waiting_for_phone_or_code = State()


class DeliveryFlow(StatesGroup):
    choosing_payment = State()
    waiting_for_photo = State()


class ProblemFlow(StatesGroup):
    waiting_for_reason = State()


# ============================================================================
# 2. HTTP-КЛИЕНТ ДЛЯ ВЗАИМОДЕЙСТВИЯ С FASTAPI БЭКЕНДОМ
# ============================================================================

class BackendAPIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def get_user_by_chat_id(self, chat_id: int) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/users/by-telegram/{chat_id}")
            if resp.status_code == 200:
                return resp.json()
            return None

    async def authenticate_user(self, chat_id: int, phone_or_code: str) -> tuple[bool, dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/users/auth/telegram",
                json={"telegram_chat_id": chat_id, "phone_or_code": phone_or_code},
            )
            return resp.status_code == 200, resp.json()

    async def get_orders(
        self,
        statuses: list[str],
        driver_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [("statuses", s) for s in statuses]
        if driver_id is not None:
            params.append(("driver_id", driver_id))

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/orders", params=params)
            resp.raise_for_status()
            return resp.json()

    async def update_order_status(
        self, order_id: int, new_status: str, driver_id: int | None = None
    ) -> tuple[bool, dict[str, Any]]:
        payload: dict[str, Any] = {"status": new_status}
        if driver_id is not None:
            payload["driver_id"] = driver_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(f"{self.base_url}/orders/{order_id}/status", json=payload)
            return resp.status_code == 200, resp.json()

    async def submit_order_delivered(
        self,
        order_id: int,
        driver_id: int,
        payment_method: str,
        photo_bytes: bytes,
        filename: str,
    ) -> tuple[bool, dict[str, Any]]:
        data = {
            "driver_id": str(driver_id),
            "payment_method": payment_method,
            "payment_status": "paid",
        }
        files = {
            "waybill_photo": (filename, photo_bytes, "image/jpeg"),
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                f"{self.base_url}/orders/{order_id}/delivered",
                data=data,
                files=files,
            )
            return resp.status_code == 200, resp.json()

    async def report_problem(
        self, order_id: int, driver_id: int, reason: str, cancel_order: bool = False
    ) -> tuple[bool, dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/orders/{order_id}/problem",
                json={
                    "driver_id": driver_id,
                    "reason": reason,
                    "cancel_order": cancel_order,
                },
            )
            return resp.status_code == 200, resp.json()


api_client = BackendAPIClient(API_BASE_URL)


# ============================================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ КЛАВИАТУРЫ И ФОРМАТИРОВАНИЕ
# ============================================================================

ROLE_NAMES = {
    "driver": "🚚 Водитель-экспедитор",
    "storekeeper": "📦 Кладовщик",
    "manager": "💼 Менеджер продаж",
    "admin": "🛡 Администратор",
}


def get_auth_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить мой номер телефона", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_driver_order_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Доставлен",
                    callback_data=f"deliver_start:{order_id}",
                ),
                InlineKeyboardButton(
                    text="⚠️ Проблема с заказом",
                    callback_data=f"problem_start:{order_id}",
                ),
            ]
        ]
    )


def get_payment_choice_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💵 Оплата получена наличными",
                    callback_data=f"deliver_pay:{order_id}:cash",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Безналичный расчет",
                    callback_data=f"deliver_pay:{order_id}:cashless",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Отмена",
                    callback_data=f"deliver_cancel:{order_id}",
                )
            ],
        ]
    )


def get_storekeeper_order_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Собрано (Готов к отгрузке)",
                    callback_data=f"assembled:{order_id}",
                )
            ]
        ]
    )


def format_driver_order_card(order: dict[str, Any]) -> str:
    items_lines = []
    for idx, item in enumerate(order.get("items", []), start=1):
        prod = item.get("product") or {}
        name = prod.get("name") or f"Товар #{item['product_id']}"
        sku = prod.get("sku") or f"ID:{item['product_id']}"
        unit = prod.get("unit") or "box"
        items_lines.append(f"  {idx}. <code>[{sku}]</code> {name} — <b>{item['quantity']} {unit}</b>")

    pay_label = "Наличными при получении" if order.get("payment_method") == "cash" else "Безналичный расчет"
    total_str = f"{float(order.get('total_amount', 0)):,.0f}".replace(",", " ")

    lines = [
        f"🚚 <b>Заказ #{order['id']}</b> (Статус: <b>В пути</b>)",
        f"🏢 Клиент: <b>{order['client_name']}</b>",
        f"📞 Телефон: <code>{order['client_phone']}</code>",
        f"📍 Адрес: <b>{order['delivery_address']}</b>",
        f"💰 Сумма: <b>{total_str} ₸</b> ({pay_label})",
        "",
        "<b>Состав заказа:</b>",
        *items_lines,
    ]
    if order.get("comment"):
        lines.append(f"\n💬 <i>{order['comment']}</i>")
    return "\n".join(lines)


# ============================================================================
# 4. АВТОРИЗАЦИЯ СОТРУДНИКА (/start, контакт или код сотрудника)
# ============================================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    chat_id = message.chat.id

    user = await api_client.get_user_by_chat_id(chat_id)
    if user:
        role_title = ROLE_NAMES.get(user["role"], user["role"])
        help_cmd = (
            "Используйте /my_orders для просмотра назначенных рейсов «В пути»."
            if user["role"] == "driver"
            else "Используйте /assembling для просмотра заказов на сборке."
        )
        await message.answer(
            f"👋 С возвращением, <b>{user['full_name']}</b>!\n"
            f"Роль: {role_title}\n\n"
            f"{help_cmd}\n"
            f"Для смены аккаунта отправьте /login.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await state.set_state(AuthFlow.waiting_for_phone_or_code)
    await message.answer(
        "🔐 <b>Авторизация в системе «МойСклад»</b>\n\n"
        "Нажмите кнопку ниже, чтобы отправить ваш номер телефона, "
        "либо введите номер телефона (например, <code>+77011000004</code>) "
        "или ваш табельный код сотрудника (например, <code>4</code> для водителя, <code>3</code> для кладовщика):",
        reply_markup=get_auth_reply_keyboard(),
    )


@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext) -> None:
    await state.set_state(AuthFlow.waiting_for_phone_or_code)
    await message.answer(
        "Введите номер телефона или табельный код сотрудника (например, <code>4</code> — водитель, <code>3</code> — кладовщик):",
        reply_markup=get_auth_reply_keyboard(),
    )


@router.message(AuthFlow.waiting_for_phone_or_code, F.contact)
async def handle_contact_auth(message: Message, state: FSMContext) -> None:
    phone = message.contact.phone_number if message.contact else ""
    await _perform_auth(message, state, phone)


@router.message(AuthFlow.waiting_for_phone_or_code, F.text)
async def handle_text_auth(message: Message, state: FSMContext) -> None:
    await _perform_auth(message, state, message.text or "")


async def _perform_auth(message: Message, state: FSMContext, phone_or_code: str) -> None:
    ok, data = await api_client.authenticate_user(message.chat.id, phone_or_code)
    if not ok:
        err_msg = data.get("message", "Сотрудник не найден")
        await message.answer(
            f"❌ <b>Ошибка авторизации:</b> {err_msg}\n\n"
            "Попробуйте еще раз отправить номер телефона или табельный код:",
            reply_markup=get_auth_reply_keyboard(),
        )
        return

    await state.clear()
    role_title = ROLE_NAMES.get(data["role"], data["role"])
    next_hint = (
        "Нажмите /my_orders для получения списка назначенных доставок."
        if data["role"] == "driver"
        else "Вы будете автоматически получать уведомления о заказах «На сборке». Также доступна команда /assembling."
    )
    await message.answer(
        f"✅ <b>Авторизация успешна!</b>\n"
        f"Сотрудник: <b>{data['full_name']}</b> (#{data['id']})\n"
        f"Роль: {role_title}\n"
        f"Telegram Chat ID <code>{message.chat.id}</code> сохранен в базе.\n\n"
        f"{next_hint}",
        reply_markup=ReplyKeyboardRemove(),
    )


# ============================================================================
# 5. СЦЕНАРИЙ ВОДИТЕЛЯ (/my_orders, Доставлен -> Оплата -> Фото накладной)
# ============================================================================

@router.message(Command("my_orders"))
async def cmd_my_orders(message: Message) -> None:
    user = await api_client.get_user_by_chat_id(message.chat.id)
    if not user:
        await message.answer("Вы не авторизованы. Введите /start для входа.")
        return

    # Для водителя фильтруем по его driver_id; для админа показываем все заказы 'in_transit'
    driver_filter = user["id"] if user["role"] == "driver" else None
    orders = await api_client.get_orders(statuses=["in_transit"], driver_id=driver_filter)

    if not orders:
        await message.answer("📭 На данный момент у вас нет назначенных заказов со статусом <b>«В пути»</b>.")
        return

    await message.answer(f"📋 Найдено заказов «В пути»: <b>{len(orders)}</b>")
    for order in orders:
        await message.answer(
            format_driver_order_card(order),
            reply_markup=get_driver_order_keyboard(order["id"]),
        )


@router.callback_query(F.data.startswith("deliver_start:"))
async def cb_deliver_start(callback: CallbackQuery, state: FSMContext) -> None:
    user = await api_client.get_user_by_chat_id(callback.message.chat.id)  # type: ignore[union-attr]
    if not user:
        await callback.answer("Сначала авторизуйтесь через /start", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    await state.set_state(DeliveryFlow.choosing_payment)
    await state.update_data(order_id=order_id, driver_id=user["id"])

    await callback.message.answer(  # type: ignore[union-attr]
        f"💳 <b>Заказ #{order_id} — Шаг 1 из 2: Способ оплаты</b>\n\n"
        "Укажите, каким способом фактически получена оплата от клиента:",
        reply_markup=get_payment_choice_keyboard(order_id),
    )
    await callback.answer()


@router.callback_query(DeliveryFlow.choosing_payment, F.data.startswith("deliver_pay:"))
async def cb_deliver_payment_selected(callback: CallbackQuery, state: FSMContext) -> None:
    _, order_id_str, payment_method = callback.data.split(":")  # type: ignore[union-attr]
    order_id = int(order_id_str)

    await state.update_data(order_id=order_id, payment_method=payment_method)
    await state.set_state(DeliveryFlow.waiting_for_photo)

    pay_human = "Наличными" if payment_method == "cash" else "Безналичный расчет"
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"📷 <b>Заказ #{order_id} — Шаг 2 из 2: Фото накладной</b>\n"
        f"Выбранный способ оплаты: <b>{pay_human}</b>\n\n"
        "Пожалуйста, сфотографируйте и <b>отправьте фотографию подписанной бумажной накладной</b> в этот чат:",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deliver_cancel:"))
async def cb_deliver_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Оформление доставки отменено.")  # type: ignore[union-attr]
    await callback.answer()


@router.message(DeliveryFlow.waiting_for_photo, F.photo)
async def handle_waybill_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    fsm_data = await state.get_data()
    order_id: int = fsm_data["order_id"]
    driver_id: int = fsm_data["driver_id"]
    payment_method: str = fsm_data["payment_method"]

    # Берем фото в самом высоком разрешении (последний элемент массива photo)
    photo = message.photo[-1]  # type: ignore[index]
    buffer = io.BytesIO()
    await bot.download(photo, destination=buffer)
    photo_bytes = buffer.getvalue()

    filename = f"waybill_order_{order_id}_{photo.file_unique_id}.jpg"

    wait_msg = await message.answer("⏳ Загружаю фото накладной на сервер и списываю остатки со склада...")

    ok, resp_data = await api_client.submit_order_delivered(
        order_id=order_id,
        driver_id=driver_id,
        payment_method=payment_method,
        photo_bytes=photo_bytes,
        filename=filename,
    )

    if not ok:
        err_msg = resp_data.get("message", "Неизвестная ошибка сервера")
        await wait_msg.edit_text(f"❌ <b>Не удалось закрыть доставку заказа #{order_id}:</b>\n{err_msg}")
        await state.clear()
        return

    await state.clear()
    pay_human = "Наличные" if payment_method == "cash" else "Безналичный расчет"
    saved_path = resp_data.get("delivery", {}).get("waybill_photo_url", filename)

    await wait_msg.edit_text(
        f"✅ <b>Заказ #{order_id} успешно переведен в статус «Доставлен»!</b>\n\n"
        f"• Способ оплаты: <b>{pay_human}</b>\n"
        f"• Накладная сохранена: <code>{saved_path}</code>\n"
        f"• Физический остаток и резерв списаны со склада."
    )


@router.message(DeliveryFlow.waiting_for_photo)
async def handle_non_photo_in_delivery(message: Message) -> None:
    await message.answer(
        "⚠️ Пожалуйста, отправьте именно <b>фотографию</b> подписанной бумажной накладной "
        "(или нажмите /my_orders для отмены)."
    )


# ============================================================================
# 6. ОБРАБОТКА ПРОБЛЕМЫ С ЗАКАЗОМ («Проблема с заказом»)
# ============================================================================

@router.callback_query(F.data.startswith("problem_start:"))
async def cb_problem_start(callback: CallbackQuery, state: FSMContext) -> None:
    user = await api_client.get_user_by_chat_id(callback.message.chat.id)  # type: ignore[union-attr]
    if not user:
        await callback.answer("Сначала авторизуйтесь через /start", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    await state.set_state(ProblemFlow.waiting_for_reason)
    await state.update_data(order_id=order_id, driver_id=user["id"])

    await callback.message.answer(  # type: ignore[union-attr]
        f"⚠️ <b>Проблема с заказом #{order_id}</b>\n\n"
        "Опишите причину проблемы одним сообщением (например: <i>«Клиент не берет трубку, точка закрыта»</i>):"
    )
    await callback.answer()


@router.message(ProblemFlow.waiting_for_reason, F.text)
async def handle_problem_reason(message: Message, state: FSMContext) -> None:
    fsm_data = await state.get_data()
    order_id: int = fsm_data["order_id"]
    driver_id: int = fsm_data["driver_id"]
    reason = (message.text or "").strip()

    ok, resp_data = await api_client.report_problem(
        order_id=order_id,
        driver_id=driver_id,
        reason=reason,
        cancel_order=False,
    )
    await state.clear()

    if not ok:
        await message.answer(f"❌ Ошибка при отправке отчета: {resp_data.get('message')}")
        return

    await message.answer(
        f"📝 <b>Проблема по заказу #{order_id} зафиксирована и передана менеджеру!</b>\n"
        f"Комментарий: <i>{reason}</i>"
    )


# ============================================================================
# 7. СЦЕНАРИЙ КЛАДОВЩИКА (Уведомление «На сборке» + кнопка «Собрано»)
# ============================================================================

@router.message(Command("assembling"))
async def cmd_assembling_orders(message: Message) -> None:
    user = await api_client.get_user_by_chat_id(message.chat.id)
    if not user:
        await message.answer("Сначала авторизуйтесь через /start")
        return

    orders = await api_client.get_orders(statuses=["assembling"])
    if not orders:
        await message.answer("📭 Сейчас нет заказов со статусом <b>«На сборке»</b>.")
        return

    for order in orders:
        lines = [
            f"📦 <b>Заказ #{order['id']} — НА СБОРКЕ</b>",
            f"👤 Клиент: <b>{order['client_name']}</b>",
            f"📍 Адрес: {order['delivery_address']}",
            "",
            "<b>Позиции для комплектации:</b>",
        ]
        for idx, item in enumerate(order.get("items", []), start=1):
            prod = item.get("product") or {}
            name = prod.get("name") or f"Товар #{item['product_id']}"
            sku = prod.get("sku") or f"ID:{item['product_id']}"
            unit = prod.get("unit") or "box"
            lines.append(f"{idx}. <code>[{sku}]</code> {name} — <b>{item['quantity']} {unit}</b>")

        await message.answer(
            "\n".join(lines),
            reply_markup=get_storekeeper_order_keyboard(order["id"]),
        )


@router.callback_query(F.data.startswith("assembled:"))
async def cb_order_assembled(callback: CallbackQuery) -> None:
    user = await api_client.get_user_by_chat_id(callback.message.chat.id)  # type: ignore[union-attr]
    if not user:
        await callback.answer("Сначала авторизуйтесь через /start", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    ok, data = await api_client.update_order_status(order_id, new_status="ready_for_shipment")

    if not ok:
        err_msg = data.get("message", "Не удалось обновить статус заказа")
        await callback.answer(f"Ошибка: {err_msg}", show_alert=True)
        return

    original_html = callback.message.html_text if callback.message else ""
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"{original_html}\n\n"
        f"✅ <b>СОБРАНО! Статус изменен на «Готов к отгрузке» (ready_for_shipment)</b>\n"
        f"Комплектовщик: {user['full_name']}",
        reply_markup=None,
    )
    await callback.answer("Заказ переведен в статус «Готов к отгрузке»!")


# ============================================================================
# 8. ТОЧКА ВХОДА БОТА
# ============================================================================

async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан TELEGRAM_BOT_TOKEN! Укажите его в .env или переменной окружения TELEGRAM_BOT_TOKEN."
        )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Telegram-бот (aiogram 3.x) запущен и подключен к %s", API_BASE_URL)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
