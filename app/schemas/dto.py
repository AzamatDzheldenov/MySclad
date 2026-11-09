from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.entities import OrderStatus, PaymentMethod, PaymentStatus, UnitType, UserRole


# ============================================================================
# Схемы пользователей и Telegram-авторизации (Users)
# ============================================================================

class TelegramAuthRequest(BaseModel):
    telegram_chat_id: int = Field(..., description="Telegram Chat ID сотрудника")
    phone_or_code: str = Field(..., min_length=1, max_length=32, description="Номер телефона или табельный ID сотрудника")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    phone: str
    role: UserRole
    telegram_chat_id: int | None
    is_active: bool


class OrderProblemRequest(BaseModel):
    driver_id: int = Field(..., gt=0)
    reason: str = Field(..., min_length=3, max_length=1000)
    cancel_order: bool = Field(default=False, description="Если True — заказ отменяется и резерв возвращается на склад")


# ============================================================================
# Схемы товаров (Products)
# ============================================================================

class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    unit: UnitType
    price: Decimal
    stock_quantity: int
    reserved_quantity: int
    available_quantity: int
    updated_at: datetime


# ============================================================================
# Схемы создания и отображения заказов (Orders)
# ============================================================================

class OrderItemCreateRequest(BaseModel):
    product_id: int = Field(..., gt=0, description="ID товара")
    quantity: int = Field(..., gt=0, description="Заказываемое количество (> 0)")


class OrderCreateRequest(BaseModel):
    client_name: str = Field(..., min_length=2, max_length=255)
    client_phone: str = Field(..., min_length=6, max_length=20)
    delivery_address: str = Field(..., min_length=5)
    payment_method: PaymentMethod
    manager_id: int = Field(..., gt=0)
    comment: str | None = Field(default=None, max_length=1000)
    items: list[OrderItemCreateRequest] = Field(..., min_length=1)

    @field_validator("client_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = v.strip()
        if not any(ch.isdigit() for ch in cleaned):
            raise ValueError("Некорректный формат телефона")
        return cleaned


class OrderStatusUpdateRequest(BaseModel):
    status: OrderStatus
    driver_id: int | None = Field(
        default=None,
        gt=0,
        description="Обязателен при переводе заказа в статус in_transit",
    )


class OrderDeliveredRequest(BaseModel):
    driver_id: int = Field(..., gt=0, description="ID водителя, доставившего заказ")
    payment_method: PaymentMethod = Field(..., description="Фактический способ оплаты при получении")
    payment_status: PaymentStatus = Field(default=PaymentStatus.PAID)
    waybill_photo_url: str = Field(..., min_length=3, description="Путь к файлу или URL фото накладной")
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    notes: str | None = Field(default=None, max_length=1000)


class ProductNestedInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    unit: UnitType


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: int
    unit_price: Decimal
    product: ProductNestedInfo | None = None


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    driver_id: int
    waybill_photo_url: str
    latitude: Decimal | None
    longitude: Decimal | None
    delivered_at: datetime
    notes: str | None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_name: str
    client_phone: str
    delivery_address: str
    status: OrderStatus
    payment_method: PaymentMethod
    payment_status: PaymentStatus
    total_amount: Decimal
    manager_id: int
    driver_id: int | None
    comment: str | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse]
    delivery: DeliveryResponse | None = None
