CREATE TABLE orders (
    id BIGINT PRIMARY KEY,
    total_cents INTEGER NOT NULL,
    legacy_tag TEXT
);
