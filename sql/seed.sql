-- ============================================================================
-- Начальные данные (Seed) для тестирования CRM и склада
-- ============================================================================

-- 1. Пользователи (менеджеры, кладовщик, водители)
INSERT INTO users (id, full_name, phone, role, telegram_chat_id, is_active) VALUES
    (1, 'Алия Смагулова',  '+77011000001', 'manager',     100000001, TRUE),
    (2, 'Тимур Касымов',   '+77011000002', 'manager',     100000002, TRUE),
    (3, 'Бауржан Сериков', '+77011000003', 'storekeeper', 100000003, TRUE),
    (4, 'Ерлан Жумабаев',  '+77011000004', 'driver',      100000004, TRUE),
    (5, 'Руслан Оспанов',  '+77011000005', 'driver',      100000005, TRUE),
    (6, 'Администратор',   '+77011000006', 'admin',       100000006, TRUE)
ON CONFLICT (id) DO NOTHING;

SELECT setval('users_id_seq', (SELECT MAX(id) FROM users));

-- 2. Каталог расходников (стаканчики, крышки, сиропы, аксессуары)
INSERT INTO products (id, sku, name, unit, price, stock_quantity, reserved_quantity, is_archived) VALUES
    (1, 'CUP-250-KRFT', 'Стакан бумажный двухслойный Крафт 250 мл (500 шт/кор)', 'box', 14500.00, 48, 8, FALSE),
    (2, 'CUP-350-BLK',  'Стакан бумажный двухслойный Черный 350 мл (500 шт/кор)', 'box', 16800.00, 12, 5, FALSE),
    (3, 'LID-80-BLK',   'Крышка пластиковая с питейником Ø80 мм черная (1000 шт/кор)', 'box', 9200.00, 9, 3, FALSE),
    (4, 'LID-90-WHT',   'Крышка пластиковая с клапаном Ø90 мм белая (1000 шт/кор)', 'box', 10400.00, 6, 6, FALSE),
    (5, 'SYR-MON-VAN',  'Сироп MONIN Ваниль 1000 мл стекло (6 бут/кор)', 'box', 24600.00, 25, 3, FALSE),
    (6, 'SYR-MON-CAR',  'Сироп MONIN Соленая Карамель 1000 мл (6 бут/кор)', 'box', 24600.00, 7, 2, FALSE),
    (7, 'SYR-BAR-COC',  'Сироп Barinoff Кокос 1000 мл ПЭТ (6 бут/кор)', 'box', 13800.00, 0, 0, FALSE),
    (8, 'STR-210-BLK',  'Трубочка коктейльная черная в инд. упаковке (4000 шт/кор)', 'box', 11000.00, 19, 1, FALSE),
    (9, 'HLDR-2CUP',    'Держатель-подставка на 2 стакана пульперкартон (300 шт/кор)', 'box', 8900.00, 5, 1, FALSE)
ON CONFLICT (id) DO NOTHING;

SELECT setval('products_id_seq', (SELECT MAX(id) FROM products));

-- 3. Демонстрационные заказы (согласованные с reserved_quantity выше)
INSERT INTO orders (id, client_name, client_phone, delivery_address, status, payment_method, payment_status, total_amount, manager_id, driver_id, comment) VALUES
    (1041, 'ИП Кофейня «Бодрый День»', '+7 701 234 56 78', 'пр. Абая 150, вход со двора', 'new', 'cashless', 'pending', 107200.00, 1, NULL, 'Доставить до 15:00, позвонить бариста'),
    (1040, 'ТОО «Coffee Boom Восток»', '+7 702 888 11 22', 'пр. Достык 89, служебный вход', 'assembling', 'cashless', 'paid', 111600.00, 1, 4, 'Срочная отгрузка, паллета #2'),
    (1039, 'Пекарня «Хлеб и Кофе»', '+7 707 555 43 21', 'ул. Сатпаева 22, 1 этаж', 'in_transit', 'cash', 'pending', 117100.00, 2, 4, 'Наличные забрать у администратора'),
    (1038, 'Бар «Сироп & Лёд»', '+7 747 900 10 20', 'ул. Гоголя 75, угол Фурманова', 'delivered', 'cashless', 'paid', 73800.00, 2, 5, 'Принято по накладной')
ON CONFLICT (id) DO NOTHING;

SELECT setval('orders_id_seq', (SELECT MAX(id) FROM orders));

-- Позиции заказов
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
    -- Заказ 1041 (new): 4 кор. CUP-250-KRFT + 2 кор. SYR-MON-VAN
    (1041, 1, 4, 14500.00),
    (1041, 5, 2, 24600.00),
    -- Заказ 1040 (assembling): 5 кор. CUP-350-BLK + 3 кор. LID-80-BLK
    (1040, 2, 5, 16800.00),
    (1040, 3, 3, 9200.00),
    -- Заказ 1039 (in_transit): 4 кор. CUP-250-KRFT + 2 кор. SYR-MON-CAR + 1 кор. HLDR-2CUP
    (1039, 1, 4, 14500.00),
    (1039, 6, 2, 24600.00),
    (1039, 9, 1, 8900.00),
    -- Заказ 1038 (delivered): 3 кор. SYR-MON-VAN
    (1038, 5, 3, 24600.00)
ON CONFLICT DO NOTHING;

-- Подтверждение доставки для заказа 1038
INSERT INTO deliveries (order_id, driver_id, waybill_photo_url, latitude, longitude, notes) VALUES
    (1038, 5, 'uploads/waybills/order_1038_signed.jpg', 43.258949, 76.945709, 'Подписано старшим барменом')
ON CONFLICT (order_id) DO NOTHING;

