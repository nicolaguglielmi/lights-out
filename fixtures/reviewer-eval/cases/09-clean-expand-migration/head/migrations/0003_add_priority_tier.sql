ALTER TABLE orders ADD COLUMN priority_tier TEXT NOT NULL DEFAULT 'standard';
CREATE INDEX IF NOT EXISTS idx_orders_priority_tier ON orders (priority_tier);
