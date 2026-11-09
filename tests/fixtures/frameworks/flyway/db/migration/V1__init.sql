-- initial schema
CREATE TABLE IF NOT EXISTS customers (
    id BIGINT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    balance NUMERIC(10, 2) DEFAULT 0, -- trailing comment
    CONSTRAINT uq_email UNIQUE (email)
);
CREATE TABLE public."orders" (
    id BIGINT PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(id),
    PRIMARY KEY (id)
);
