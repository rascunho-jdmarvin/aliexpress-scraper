-- Drop the existing unique constraint on aliexpress_id.
-- Since the name of the constraint might be products_aliexpress_id_key, we can drop it.
ALTER TABLE products DROP CONSTRAINT products_aliexpress_id_key;

-- Add client_id column.
ALTER TABLE products ADD COLUMN client_id UUID REFERENCES clients(id) ON DELETE CASCADE;

-- Add a new unique constraint on (client_id, aliexpress_id).
ALTER TABLE products ADD CONSTRAINT products_client_id_aliexpress_id_key UNIQUE (client_id, aliexpress_id);

-- Create an index to query products by client_id efficiently.
CREATE INDEX idx_products_client_id ON products(client_id);
