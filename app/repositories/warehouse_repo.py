from collections.abc import Sequence
import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import Delivery, Order, OrderItem, OrderStatus, Product, User, UserRole


class WarehouseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_id(self, user_id: int) -> User | None:
        stmt = select(User).where(User.id == user_id, User.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_telegram_chat_id(self, chat_id: int) -> User | None:
        stmt = select(User).where(User.telegram_chat_id == chat_id, User.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_user_by_phone_or_code(self, phone_or_code: str) -> User | None:
        cleaned = phone_or_code.strip()
        # Если ввели короткий числовой код сотрудника (например, ID: "3" или "4" или "#4")
        code_str = cleaned.lstrip("#")
        if code_str.isdigit() and len(code_str) <= 6:
            user = await self.get_user_by_id(int(code_str))
            if user:
                return user

        # Поиск по последним 10 цифрам номера телефона (независимо от +7 / 8 / скобок / пробелов)
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) >= 10:
            tail10 = digits[-10:]
            stmt = select(User).where(User.is_active.is_(True))
            result = await self.session.execute(stmt)
            for user in result.scalars().all():
                user_digits = re.sub(r"\D", "", user.phone)
                if user_digits.endswith(tail10):
                    return user
        return None

    async def list_users_by_role(self, role: UserRole) -> Sequence[User]:
        stmt = select(User).where(User.role == role, User.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_active_products(self, search: str | None = None) -> Sequence[Product]:
        stmt = select(Product).where(Product.is_archived.is_(False)).order_by(Product.name.asc())
        if search:
            stmt = stmt.where(
                (Product.name.ilike(f"%{search}%")) | (Product.sku.ilike(f"%{search}%"))
            )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_products_for_update(self, product_ids: list[int]) -> Sequence[Product]:
        """
        Захватывает пессимистическую блокировку строк товаров (SELECT ... FOR UPDATE).
        ВАЖНО: ORDER BY Product.id ASC гарантирует одинаковый порядок блокировки
        во всех параллельных транзакциях и полностью исключает Deadlock!
        """
        stmt = (
            select(Product)
            .where(Product.id.in_(product_ids))
            .order_by(Product.id.asc())
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create_order(self, order: Order) -> Order:
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_order_by_id(self, order_id: int, for_update: bool = False) -> Order | None:
        stmt = (
            select(Order)
            .options(
                selectinload(Order.items).selectinload(OrderItem.product),
                selectinload(Order.delivery),
            )
            .where(Order.id == order_id)
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_orders(
        self,
        statuses: list[OrderStatus] | None = None,
        manager_id: int | None = None,
        driver_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Order]:
        stmt = (
            select(Order)
            .options(
                selectinload(Order.items),
                selectinload(Order.delivery),
            )
            .order_by(Order.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if statuses:
            stmt = stmt.where(Order.status.in_(statuses))
        if manager_id is not None:
            stmt = stmt.where(Order.manager_id == manager_id)
        if driver_id is not None:
            stmt = stmt.where(Order.driver_id == driver_id)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create_delivery(self, delivery: Delivery) -> Delivery:
        self.session.add(delivery)
        await self.session.flush()
        return delivery

