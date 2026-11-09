-- ============================================================================
-- Схема БД PostgreSQL для оптовой дистрибуции расходников
-- ============================================================================

CREATE TYPE user_role AS ENUM ('admin', 'manager', 'storekeeper', 'driver');
CREATE TYPE unit_type AS ENUM ('pcs', 'box', 'pack', 'bottle');
CREATE TYPE order_status AS ENUM ('new', 'assembling', 'ready_for_shipment', 'in_transit', 'delivered', 'canceled');
CREATE TYPE payment_method AS ENUM ('cash', 'cashless');
CREATE TYPE payment_status AS ENUM ('pending', 'paid');

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    full_name VARCHAR(150) NOT NULL,
    phone VARCHAR(20) NOT NULL UNIQUE,
    role user_role NOT NULL,
    telegram_chat_id BIGINT UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE products (
    id BIGSERIAL PRIMARY KEY,
    sku VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    unit unit_type NOT NULL DEFAULT 'box',
    price NUMERIC(12, 2) NOT NULL,
    stock_quantity INTEGER NOT NULL DEFAULT 0,
    reserved_quantity INTEGER NOT NULL DEFAULT 0,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_products_price_non_negative CHECK (price >= 0),
    CONSTRAINT chk_products_stock_non_negative CHECK (stock_quantity >= 0),
    CONSTRAINT chk_products_reserved_non_negative CHECK (reserved_quantity >= 0),
    CONSTRAINT chk_products_available_stock CHECK (stock_quantity >= reserved_quantity)
);

CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    client_name VARCHAR(255) NOT NULL,
    client_phone VARCHAR(20) NOT NULL,
    delivery_address TEXT NOT NULL,
    status order_status NOT NULL DEFAULT 'new',
    payment_method payment_method NOT NULL,
    payment_status payment_status NOT NULL DEFAULT 'pending',
    total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    manager_id BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    driver_id BIGINT REFERENCES users(id) ON DELETE RESTRICT,
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_orders_total_non_negative CHECK (total_amount >= 0),
    CONSTRAINT chk_orders_driver_required CHECK (
        status NOT IN ('in_transit', 'delivered') OR driver_id IS NOT NULL
    )
);

CREATE TABLE order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,

    CONSTRAINT uq_order_items_order_product UNIQUE (order_id, product_id),
    CONSTRAINT chk_order_items_quantity_positive CHECK (quantity > 0),
    CONSTRAINT chk_order_items_price_non_negative CHECK (unit_price >= 0)
);

CREATE TABLE deliveries (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,
    driver_id BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    waybill_photo_url TEXT NOT NULL,
    latitude NUMERIC(9, 6),
    longitude NUMERIC(9, 6),
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT,

    CONSTRAINT chk_deliveries_latitude CHECK (latitude IS NULL OR (latitude BETWEEN -90 AND 90)),
    CONSTRAINT chk_deliveries_longitude CHECK (longitude IS NULL OR (longitude BETWEEN -180 AND 180))
);

CREATE INDEX idx_users_telegram_chat_id ON users(telegram_chat_id) WHERE telegram_chat_id IS NOT NULL;
CREATE INDEX idx_products_active_name ON products(name) WHERE is_archived = FALSE;
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_manager_id ON orders(manager_id, created_at DESC);
CREATE INDEX idx_orders_driver_active ON orders(driver_id, status) WHERE status IN ('assembling', 'in_transit');
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);

