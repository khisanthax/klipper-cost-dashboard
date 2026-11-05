CREATE TABLE business_orders (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'complete')),
    customer TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    due_date TEXT,
    priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high')),
    estimated_cost_cents INTEGER CHECK (estimated_cost_cents >= 0),
    quote_cents INTEGER CHECK (quote_cents >= 0),
    selling_price_cents INTEGER CHECK (selling_price_cents >= 0),
    revenue_cents INTEGER CHECK (revenue_cents >= 0),
    expenses_cents INTEGER CHECK (expenses_cents >= 0),
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
