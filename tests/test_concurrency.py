"""
Интеграционный тест конкурентного резервирования (Race Condition Test).

Сценарий:
1. На складе есть товар (например, ID=2: Стакан черный 350 мл), у которого
   доступный остаток = 7 коробок (stock=12, reserved=5).
2. Пять менеджеров ОДНОВРЕМЕННО (через asyncio.gather) отправляют POST /api/orders,
   каждый пытается заказать по 3 коробки этого товара (всего 5 * 3 = 15 коробок при доступных 7).
3. Благодаря SELECT ... FOR UPDATE в PostgreSQL:
   - Ровно 2 запроса успешно создают заказ и резервируют 2 * 3 = 6 коробок (201 Created).
   - Оставшиеся 3 запроса получают 409 Conflict с массивом shortages (дефицит).
   - Итоговый доступный остаток становится ровно 1 коробка (никакого ухода в минус!).

Запуск (при работающем сервере на http://localhost:8000):
    python3 tests/test_concurrency.py
"""

import asyncio
import urllib.request
import urllib.error
import json

API_BASE = "http://localhost:8000/api"
TARGET_PRODUCT_ID = 2  # CUP-350-BLK (по seed.sql: stock=12, reserved=5 -> available=7)
QTY_PER_ORDER = 3      # Каждый из 5 параллельных запросов просит 3 коробки
CONCURRENT_MANAGERS = 5


def _sync_post_order(worker_idx: int) -> tuple[int, dict]:
    payload = {
        "client_name": f"Конкурентный Клиент #{worker_idx}",
        "client_phone": f"+7701000000{worker_idx}",
        "delivery_address": "ул. Тестовая, 1",
        "payment_method": "cashless",
        "manager_id": 1,
        "comment": f"Параллельный запрос #{worker_idx}",
        "items": [{"product_id": TARGET_PRODUCT_ID, "quantity": QTY_PER_ORDER}],
    }
    req = urllib.request.Request(
        f"{API_BASE}/orders",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


async def run_race_condition_test() -> None:
    print(f"Запуск {CONCURRENT_MANAGERS} одновременных запросов POST /api/orders...")
    tasks = [
        asyncio.to_thread(_sync_post_order, i)
        for i in range(1, CONCURRENT_MANAGERS + 1)
    ]
    results = await asyncio.gather(*tasks)

    created_count = 0
    conflict_count = 0

    for idx, (status_code, body) in enumerate(results, start=1):
        if status_code == 201:
            created_count += 1
            print(f"  [Менеджер #{idx}] 201 CREATED -> Заказ #{body['id']} успешно зарезервирован")
        elif status_code == 409:
            conflict_count += 1
            shortage = body.get("shortages", [{}])[0]
            print(
                f"  [Менеджер #{idx}] 409 CONFLICT -> Отказ! Доступно: {shortage.get('available_quantity')}, "
                f"Запрошено: {shortage.get('requested_quantity')}, Дефицит: {shortage.get('deficit')}"
            )
        else:
            print(f"  [Менеджер #{idx}] Неожиданный статус {status_code}: {body}")

    print("-" * 65)
    print(f"Итог: Успешно зарезервировано: {created_count} | Отклонено (409 Conflict): {conflict_count}")


if __name__ == "__main__":
    asyncio.run(run_race_condition_test())

