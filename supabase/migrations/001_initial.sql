-- Products table: stores scraped AliExpress product info
CREATE TABLE IF NOT EXISTS products (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    aliexpress_id TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    description TEXT,
    main_image TEXT,
    images JSONB DEFAULT '[]',
    store_name TEXT,
    store_id TEXT,
    store_url TEXT,
    rating DECIMAL(3, 2),
    reviews_count INTEGER,
    orders_count INTEGER,
    currency TEXT DEFAULT 'USD',
    price_min DECIMAL(12, 2),
    price_max DECIMAL(12, 2),
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Product variants: each row is a unique SKU combination with its own price
CREATE TABLE IF NOT EXISTS product_variants (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    product_id UUID REFERENCES products(id) ON DELETE CASCADE NOT NULL,
    sku_id TEXT,
    variant_attributes JSONB NOT NULL DEFAULT '{}',  -- {"color": "Red", "size": "XL"}
    price_original DECIMAL(12, 2),
    price_discounted DECIMAL(12, 2),
    currency TEXT DEFAULT 'USD',
    stock INTEGER,
    image_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Scrape jobs: track status of each scrape request
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    url TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    error TEXT,
    product_id UUID REFERENCES products(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_products_aliexpress_id ON products(aliexpress_id);
CREATE INDEX IF NOT EXISTS idx_product_variants_product_id ON product_variants(product_id);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_status ON scrape_jobs(status);
CREATE INDEX IF NOT EXISTS idx_scrape_jobs_url ON scrape_jobs(url);

-- Auto-update updated_at on products
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
