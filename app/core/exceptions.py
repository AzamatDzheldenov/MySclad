from typing import Any
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class DomainError(Exception):
    """Базовое доменное исключение."""
    def __init__(self, message: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class EntityNotFoundError(DomainError):
    """Ошибка 404: сущность не найдена."""
    def __init__(self, message: str):
        super().__init__(message=message, status_code=status.HTTP_404_NOT_FOUND)


class BusinessRuleViolationError(DomainError):
    """Ошибка 400/409: нарушение бизнес-правил (например, неверный переход статуса)."""
    def __init__(self, message: str, status_code: int = status.HTTP_409_CONFLICT):
        super().__init__(message=message, status_code=status_code)


class InsufficientStockError(DomainError):
    """
    Ошибка 409 Conflict: нехватка свободного остатка товара на складе.
    Возвращает структурированный список всех дефицитных позиций.
    """
    def __init__(self, shortages: list[dict[str, Any]]):
        super().__init__(
            message="Недостаточно свободного остатка на складе для создания заказа",
            status_code=status.HTTP_409_CONFLICT,
        )
        self.shortages = shortages


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(InsufficientStockError)
    async def insufficient_stock_handler(_: Request, exc: InsufficientStockError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "INSUFFICIENT_STOCK",
                "message": exc.message,
                "shortages": exc.shortages,
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.__class__.__name__,
                "message": exc.message,
            },
        )

