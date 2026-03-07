-- Cria um tipo ENUM para o status do job, facilitando o controle.
CREATE TYPE job_status AS ENUM (
    'PENDING',
    'PROCESSING',
    'SUCCESS',
    'FAILED',
    'RETRY'
);

-- Criação da tabela `import_jobs`.
CREATE TABLE import_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    product_url_encrypted BYTEA NOT NULL, -- URL do produto criptografada.
    status job_status NOT NULL DEFAULT 'PENDING',
    celery_task_id VARCHAR(255) UNIQUE,
    result_encrypted BYTEA, -- Resultado/log da operação, também criptografado.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para otimizar buscas por cliente e status.
CREATE INDEX idx_import_jobs_client_id ON import_jobs(client_id);
CREATE INDEX idx_import_jobs_status ON import_jobs(status);

-- Reutiliza o mesmo trigger da migração anterior para atualizar `updated_at`.
CREATE TRIGGER set_timestamp
BEFORE UPDATE ON import_jobs
FOR EACH ROW
EXECUTE PROCEDURE trigger_set_timestamp();
