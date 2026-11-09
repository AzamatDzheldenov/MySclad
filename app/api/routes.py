import os
import uuid
from decimal import Decimal
import aiofiles
import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.exceptions import DomainError
from app.models.entities import Order, OrderStatus, PaymentMethod, PaymentStatus
from app.schemas.dto import (
    OrderCreateRequest,
    OrderDeliveredRequest,
    OrderProblemRequest,
    OrderResponse,
    OrderStatusUpdateRequest,
    ProductResponse,
    TelegramAuthRequest,
    UserResponse,
)
from app.services.warehouse_service import WarehouseService

router = APIRouter(prefix="/api", tags=["Warehouse & Orders"])


def get_warehouse_service(
    session: AsyncSession = Depends(get_db_session),
) -> WarehouseService:
    return WarehouseService(session)


async def notify_storekeepers_assembling(order: Order, chat_ids: list[int]) -> None:
    """
    Отправляет кладовщикам push-уведомление в Telegram при переводе заказа в статус 'assembling'
    со списком позиций и inline-кнопкой «📦 Собрано (Готов к отгрузке)».
    """
    if not settings.TELEGRAM_BOT_TOKEN or not chat_ids:
        return

    lines = [
        f"📦 <b>Заказ #{order.id} передан НА СБОРКУ!</b>",
        f"👤 Клиент: <b>{order.client_name}</b> ({order.client_phone})",
        f"📍 Адрес: {order.delivery_address}",
        "",
        "<b>Список позиций для комплектации:</b>",
    ]
    for idx, item in enumerate(order.items, start=1):
        prod_name = item.product.name if item.product else f"Товар #{item.product_id}"
        prod_sku = item.product.sku if item.product else f"ID:{item.product_id}"
        unit = item.product.unit.value if item.product else "box"
        lines.append(f"{idx}. <code>[{prod_sku}]</code> {prod_name} — <b>{item.quantity} {unit}</b>")

    if order.comment:
        lines.append(f"\n💬 Комментарий: <i>{order.comment}</i>")

    text = "\n".join(lines)
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Собрано (Готов к отгрузке)",
                    "callback_data": f"assembled:{order.id}",
                }
            ]
        ]
    }

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for chat_id in chat_ids:
            try:
                await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": reply_markup,
                    },
                )
            except Exception:
                continue


# ============================================================================
# Эндпоинты авторизации сотрудников для Telegram-бота
# ============================================================================

@router.post(
    "/users/auth/telegram",
    response_model=UserResponse,
    summary="Авторизация водителя или кладовщика по номеру телефона или коду сотрудника",
)
async def auth_telegram_user(
    payload: TelegramAuthRequest,
    service: WarehouseService = Depends(get_warehouse_service),
) -> UserResponse:
    user = await service.bind_telegram_chat_id(payload)
    return user  # type: ignore[return-value]


@router.get(
    "/users/by-telegram/{chat_id}",
    response_model=UserResponse,
    summary="Получение профиля сотрудника по его Telegram Chat ID",
)
async def get_user_by_telegram_chat_id(
    chat_id: int,
    service: WarehouseService = Depends(get_warehouse_service),
) -> UserResponse:
    user = await service.get_user_by_telegram(chat_id)
    return user  # type: ignore[return-value]


# ============================================================================
# Эндпоинты товаров и заказов
# ============================================================================

@router.get(
    "/products",
    response_model=list[ProductResponse],
    summary="Получение каталога товаров с актуальными остатками в реальном времени",
)
async def get_products(
    search: str | None = Query(default=None, description="Поиск по названию или артикулу (SKU)"),
    service: WarehouseService = Depends(get_warehouse_service),
) -> list[ProductResponse]:
    products = await service.get_catalog(search=search)
    return list(products)  # type: ignore[arg-type]


@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создание заказа с атомарным резервированием остатков (ACID)",
)
async def create_order(
    payload: OrderCreateRequest,
    service: WarehouseService = Depends(get_warehouse_service),
) -> OrderResponse:
    order = await service.create_order(payload)
    return order  # type: ignore[return-value]


@router.get(
    "/orders",
    response_model=list[OrderResponse],
    summary="Получение списка заказов с фильтрацией по статусам, менеджеру и водителю",
)
async def list_orders(
    statuses: list[OrderStatus] | None = Query(
        default=None, description="Фильтр по одному или нескольким статусам"
    ),
    manager_id: int | None = Query(default=None, gt=0),
    driver_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: WarehouseService = Depends(get_warehouse_service),
) -> list[OrderResponse]:
    orders = await service.list_orders(
        statuses=statuses,
        manager_id=manager_id,
        driver_id=driver_id,
        limit=limit,
        offset=offset,
    )
    return list(orders)  # type: ignore[arg-type]


@router.patch(
    "/orders/{order_id}/status",
    response_model=OrderResponse,
    summary="Обновление статуса заказа (и push-уведомление кладовщику при переводе в 'assembling')",
)
async def update_order_status(
    order_id: int,
    payload: OrderStatusUpdateRequest,
    background_tasks: BackgroundTasks,
    service: WarehouseService = Depends(get_warehouse_service),
) -> OrderResponse:
    order = await service.update_order_status(order_id=order_id, payload=payload)

    # Если заказ переведен в статус «На сборке» — уведомляем всех активных кладовщиков в Telegram
    if order.status == OrderStatus.ASSEMBLING:
        storekeeper_chat_ids = await service.get_storekeeper_chat_ids()
        if storekeeper_chat_ids:
            background_tasks.add_task(notify_storekeepers_assembling, order, storekeeper_chat_ids)

    return order  # type: ignore[return-value]


@router.post(
    "/orders/{order_id}/problem",
    response_model=OrderResponse,
    summary="Фиксация проблемы с доставкой заказа водителем (с опциональной отменой и возвратом резерва)",
)
async def report_order_problem(
    order_id: int,
    payload: OrderProblemRequest,
    service: WarehouseService = Depends(get_warehouse_service),
) -> OrderResponse:
    order = await service.report_order_problem(order_id=order_id, payload=payload)
    return order  # type: ignore[return-value]


@router.post(
    "/orders/{order_id}/delivered",
    response_model=OrderResponse,
    summary="Подтверждение доставки водителем (принимает как multipart/form-data с бинарником фото, так и JSON)",
)
async def confirm_order_delivered(
    order_id: int,
    request: Request,
    service: WarehouseService = Depends(get_warehouse_service),
) -> OrderResponse:
    content_type = request.headers.get("content-type", "")

    # 1. Прием multipart/form-data с бинарником фотографии накладной (от Telegram-бота или формы)
    if "multipart/form-data" in content_type:
        form = await request.form()
        driver_id = int(form["driver_id"])  # type: ignore[arg-type]
        payment_method = PaymentMethod(str(form["payment_method"]))
        payment_status = PaymentStatus(str(form.get("payment_status", "paid")))
        latitude = Decimal(str(form["latitude"])) if form.get("latitude") else None
        longitude = Decimal(str(form["longitude"])) if form.get("longitude") else None
        notes = str(form["notes"]) if form.get("notes") else None

        upload_file: UploadFile | None = form.get("waybill_photo")  # type: ignore[assignment]
        if not upload_file or not hasattr(upload_file, "read"):
            raise DomainError(
                "Не передан файл фотографии подписанной накладной (поле waybill_photo)",
                status.HTTP_400_BAD_REQUEST,
            )

        os.makedirs(settings.WAYBILL_UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(upload_file.filename or "waybill.jpg")[1] or ".jpg"
        filename = f"order_{order_id}_{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(settings.WAYBILL_UPLOAD_DIR, filename)

        file_bytes = await upload_file.read()
        if not file_bytes:
            raise DomainError("Загруженный файл фото накладной пуст", status.HTTP_400_BAD_REQUEST)

        async with aiofiles.open(file_path, "wb") as out_file:
            await out_file.write(file_bytes)

        payload = OrderDeliveredRequest(
            driver_id=driver_id,
            payment_method=payment_method,
            payment_status=payment_status,
            waybill_photo_url=file_path,
            latitude=latitude,
            longitude=longitude,
            notes=notes,
        )
    else:
        # 2. Прием JSON-запроса (от веб-панели CRM)
        body = await request.json()
        payload = OrderDeliveredRequest.model_validate(body)

    order = await service.mark_order_delivered(order_id=order_id, payload=payload)
    return order  # type: ignore[return-value]
