from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleViolationError,
    EntityNotFoundError,
    InsufficientStockError,
)
from app.models.entities import (
    Delivery,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    User,
    UserRole,
)
from app.repositories.warehouse_repo import WarehouseRepository
from app.schemas.dto import (
    OrderCreateRequest,
    OrderDeliveredRequest,
    OrderProblemRequest,
    OrderStatusUpdateRequest,
    TelegramAuthRequest,
)


# Разрешенный граф переходов между статусами заказа
ALLOWED_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.NEW: {OrderStatus.ASSEMBLING, OrderStatus.CANCELED},
    OrderStatus.ASSEMBLING: {
        OrderStatus.READY_FOR_SHIPMENT,
        OrderStatus.IN_TRANSIT,
        OrderStatus.CANCELED,
    },
    OrderStatus.READY_FOR_SHIPMENT: {OrderStatus.IN_TRANSIT, OrderStatus.CANCELED},
    OrderStatus.IN_TRANSIT: {OrderStatus.DELIVERED, OrderStatus.CANCELED},
    OrderStatus.DELIVERED: set(),  # Финальный статус
    OrderStatus.CANCELED: set(),   # Финальный статус
}


class WarehouseService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = WarehouseRepository(session)

    # ========================================================================
    # 1. GET /api/products — каталог с остатками в реальном времени
    # ========================================================================
    async def get_catalog(self, search: str | None = None) -> Sequence[Product]:
        return await self.repo.list_active_products(search=search)

    # ========================================================================
    # 2. POST /api/orders — создание заказа и резервирование в ACID-транзакции
    # ========================================================================
    async def create_order(self, payload: OrderCreateRequest) -> Order:
        """
        Выполняется в единой транзакции:
        1. Агрегирует возможные дубли product_id в запросе.
        2. Захватывает строковые блокировки SELECT ... FOR UPDATE на товары в порядке возрастания ID.
        3. Проверяет (stock_quantity - reserved_quantity) по всем позициям сразу.
        4. При дефиците хотя бы одной позиции выбрасывает InsufficientStockError (409)
           со списком ВСЕХ дефицитных позиций и откатывает транзакцию.
        5. Увеличивает reserved_quantity и сохраняет заказ и его позиции.
        """
        async with self.session.begin():
            # Проверяем менеджера
            manager = await self.repo.get_user_by_id(payload.manager_id)
            if not manager or manager.role not in (UserRole.MANAGER, UserRole.ADMIN):
                raise EntityNotFoundError(
                    f"Активный менеджер с ID={payload.manager_id} не найден"
                )

            # Схлопываем дубликаты товаров в одном заказе (если передали один product_id дважды)
            requested_map: dict[int, int] = defaultdict(int)
            for item in payload.items:
                requested_map[item.product_id] += item.quantity

            sorted_product_ids = sorted(requested_map.keys())

            # Блокируем строки товаров в БД (FOR UPDATE + ORDER BY id ASC)
            locked_products = await self.repo.get_products_for_update(sorted_product_ids)
            products_by_id: dict[int, Product] = {p.id: p for p in locked_products}

            # Проверяем, все ли запрошенные ID товаров существуют и не в архиве
            missing_ids = [
                pid for pid in sorted_product_ids
                if pid not in products_by_id or products_by_id[pid].is_archived
            ]
            if missing_ids:
                raise EntityNotFoundError(
                    f"Товары с ID {missing_ids} не найдены или находятся в архиве"
                )

            # Проверяем доступный остаток (stock_quantity - reserved_quantity)
            shortages = []
            for pid in sorted_product_ids:
                product = products_by_id[pid]
                requested_qty = requested_map[pid]
                available_qty = product.stock_quantity - product.reserved_quantity

                if available_qty < requested_qty:
                    shortages.append(
                        {
                            "product_id": product.id,
                            "sku": product.sku,
                            "name": product.name,
                            "requested_quantity": requested_qty,
                            "available_quantity": available_qty,
                            "deficit": requested_qty - available_qty,
                        }
                    )

            # Если хотя бы одного товара не хватает — откатываем транзакцию и отдаем 409 Conflict
            if shortages:
                raise InsufficientStockError(shortages=shortages)

            # Создаем заказ и позиции, атомарно увеличивая reserved_quantity
            order = Order(
                client_name=payload.client_name,
                client_phone=payload.client_phone,
                delivery_address=payload.delivery_address,
                status=OrderStatus.NEW,
                payment_method=payload.payment_method,
                manager_id=payload.manager_id,
                comment=payload.comment,
            )

            total_amount = Decimal("0.00")
            for pid in sorted_product_ids:
                product = products_by_id[pid]
                qty = requested_map[pid]

                # Атомарное увеличение резерва
                product.reserved_quantity += qty

                line_total = product.price * Decimal(qty)
                total_amount += line_total

                order.items.append(
                    OrderItem(
                        product_id=product.id,
                        quantity=qty,
                        unit_price=product.price,
                    )
                )

            order.total_amount = total_amount
            await self.repo.create_order(order)

        return await self.repo.get_order_by_id(order.id)  # type: ignore[return-value]

    # ========================================================================
    # 3. GET /api/orders — список заказов с фильтрами
    # ========================================================================
    async def list_orders(
        self,
        statuses: list[OrderStatus] | None = None,
        manager_id: int | None = None,
        driver_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Order]:
        return await self.repo.list_orders(
            statuses=statuses,
            manager_id=manager_id,
            driver_id=driver_id,
            limit=limit,
            offset=offset,
        )

    # ========================================================================
    # 4. PATCH /api/orders/{id}/status — смена статуса заказа (с возвратом резерва при отмене)
    # ========================================================================
    async def update_order_status(
        self, order_id: int, payload: OrderStatusUpdateRequest
    ) -> Order:
        async with self.session.begin():
            order = await self.repo.get_order_by_id(order_id, for_update=True)
            if not order:
                raise EntityNotFoundError(f"Заказ #{order_id} не найден")

            if order.status == payload.status:
                return order

            # Для перехода в delivered нужно использовать специальный эндпоинт водителя /delivered
            if payload.status == OrderStatus.DELIVERED:
                raise BusinessRuleViolationError(
                    "Для перевода заказа в статус 'delivered' используйте POST /api/orders/{id}/delivered с фото накладной",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            allowed_next = ALLOWED_STATUS_TRANSITIONS.get(order.status, set())
            if payload.status not in allowed_next:
                raise BusinessRuleViolationError(
                    f"Недопустимый переход статуса заказа #{order_id}: '{order.status.value}' -> '{payload.status.value}'"
                )

            # При отправке в доставку (in_transit) обязательно нужен водитель
            if payload.status == OrderStatus.IN_TRANSIT:
                target_driver_id = payload.driver_id or order.driver_id
                if not target_driver_id:
                    raise BusinessRuleViolationError(
                        "Для перевода заказа в статус 'in_transit' необходимо указать driver_id",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
                driver = await self.repo.get_user_by_id(target_driver_id)
                if not driver or driver.role != UserRole.DRIVER:
                    raise EntityNotFoundError(
                        f"Активный водитель с ID={target_driver_id} не найден"
                    )
                order.driver_id = target_driver_id

            # При отмене заказа (canceled) освобождаем зарезервированный остаток
            if payload.status == OrderStatus.CANCELED:
                product_ids = sorted(item.product_id for item in order.items)
                locked_products = await self.repo.get_products_for_update(product_ids)
                products_by_id = {p.id: p for p in locked_products}

                for item in order.items:
                    product = products_by_id[item.product_id]
                    product.reserved_quantity -= item.quantity

            order.status = payload.status

        return await self.repo.get_order_by_id(order_id)  # type: ignore[return-value]

    # ========================================================================
    # 5. POST /api/orders/{id}/delivered — подтверждение доставки водителем + списание со склада
    # ========================================================================
    async def mark_order_delivered(
        self, order_id: int, payload: OrderDeliveredRequest
    ) -> Order:
        """
        Выполняется в строгой ACID-транзакции:
        1. Блокирует заказ (FOR UPDATE) и проверяет, что он в статусе 'in_transit' (или 'assembling').
        2. Проверяет водителя.
        3. Блокирует товары заказа в порядке возрастания ID (FOR UPDATE) и уменьшает
           физический остаток (stock_quantity) и резерв (reserved_quantity).
        4. Фиксирует запись о доставке (Delivery) с фото накладной и геометкой.
        5. Обновляет статус заказа на 'delivered', способ и статус оплаты.
        """
        async with self.session.begin():
            order = await self.repo.get_order_by_id(order_id, for_update=True)
            if not order:
                raise EntityNotFoundError(f"Заказ #{order_id} не найден")

            if order.status == OrderStatus.DELIVERED:
                raise BusinessRuleViolationError(f"Заказ #{order_id} уже доставлен")

            if order.status not in (
                OrderStatus.IN_TRANSIT,
                OrderStatus.READY_FOR_SHIPMENT,
                OrderStatus.ASSEMBLING,
            ):
                raise BusinessRuleViolationError(
                    f"Нельзя подтвердить доставку заказа #{order_id} в статусе '{order.status.value}'"
                )

            driver = await self.repo.get_user_by_id(payload.driver_id)
            if not driver or driver.role != UserRole.DRIVER:
                raise EntityNotFoundError(f"Водитель с ID={payload.driver_id} не найден")

            if order.driver_id and order.driver_id != payload.driver_id:
                raise BusinessRuleViolationError(
                    f"Заказ #{order_id} назначен на другого водителя (driver_id={order.driver_id})"
                )

            # Блокируем товары в отсортированном порядке и списываем остаток и резерв
            product_ids = sorted(item.product_id for item in order.items)
            locked_products = await self.repo.get_products_for_update(product_ids)
            products_by_id = {p.id: p for p in locked_products}

            for item in order.items:
                product = products_by_id[item.product_id]
                if product.reserved_quantity < item.quantity or product.stock_quantity < item.quantity:
                    raise BusinessRuleViolationError(
                        f"Критическая ошибка целостности остатков по товару SKU={product.sku}"
                    )
                product.stock_quantity -= item.quantity
                product.reserved_quantity -= item.quantity

            # Сохраняем накладную
            delivery = Delivery(
                order_id=order.id,
                driver_id=payload.driver_id,
                waybill_photo_url=payload.waybill_photo_url,
                latitude=payload.latitude,
                longitude=payload.longitude,
                notes=payload.notes,
            )
            await self.repo.create_delivery(delivery)

            # Обновляем заказ
            order.driver_id = payload.driver_id
            order.status = OrderStatus.DELIVERED
            order.payment_method = payload.payment_method
            order.payment_status = payload.payment_status

        return await self.repo.get_order_by_id(order_id)  # type: ignore[return-value]

    # ========================================================================
    # 6. Авторизация сотрудников в Telegram-боте и фиксация проблем с заказом
    # ========================================================================
    async def bind_telegram_chat_id(self, payload: TelegramAuthRequest) -> User:
        async with self.session.begin():
            # Если этот chat_id уже был привязан к другому аккаунту — отвязываем старый
            existing = await self.repo.get_user_by_telegram_chat_id(payload.telegram_chat_id)
            user = await self.repo.find_user_by_phone_or_code(payload.phone_or_code)
            if not user:
                raise EntityNotFoundError(
                    "Сотрудник с таким номером телефона или табельным кодом не найден"
                )
            if existing and existing.id != user.id:
                existing.telegram_chat_id = None
                await self.session.flush()

            user.telegram_chat_id = payload.telegram_chat_id
            await self.session.flush()
            return user

    async def get_user_by_telegram(self, chat_id: int) -> User:
        user = await self.repo.get_user_by_telegram_chat_id(chat_id)
        if not user:
            raise EntityNotFoundError("Пользователь Telegram не авторизован в системе")
        return user

    async def get_storekeeper_chat_ids(self) -> list[int]:
        storekeepers = await self.repo.list_users_by_role(UserRole.STOREKEEPER)
        return [u.telegram_chat_id for u in storekeepers if u.telegram_chat_id is not None]

    async def report_order_problem(self, order_id: int, payload: OrderProblemRequest) -> Order:
        async with self.session.begin():
            order = await self.repo.get_order_by_id(order_id, for_update=True)
            if not order:
                raise EntityNotFoundError(f"Заказ #{order_id} не найден")

            prefix = f"[ПРОБЛЕМА ВОДИТЕЛЯ #{payload.driver_id}]: {payload.reason}"
            order.comment = f"{order.comment} | {prefix}" if order.comment else prefix

            if payload.cancel_order and order.status not in (OrderStatus.DELIVERED, OrderStatus.CANCELED):
                product_ids = sorted(item.product_id for item in order.items)
                locked_products = await self.repo.get_products_for_update(product_ids)
                products_by_id = {p.id: p for p in locked_products}
                for item in order.items:
                    products_by_id[item.product_id].reserved_quantity -= item.quantity
                order.status = OrderStatus.CANCELED

        return await self.repo.get_order_by_id(order_id)  # type: ignore[return-value]


