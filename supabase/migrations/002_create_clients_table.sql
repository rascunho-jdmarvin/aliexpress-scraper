-- Habilita a extensão pgcrypto se ainda não estiver habilitada.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Função para atualizar o timestamp da coluna `updated_at` automaticamente.
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Criação da tabela `clients`.
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    key_id TEXT NOT NULL UNIQUE,
    key_secret_hash TEXT NOT NULL,
    scrapfly_api_key BYTEA NOT NULL, -- Armazenará o dado criptografado.
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índice para busca rápida pelo `key_id`.
CREATE INDEX idx_clients_key_id ON clients(key_id);

-- Trigger para chamar a função `trigger_set_timestamp` antes de qualquer atualização na tabela.
CREATE TRIGGER set_timestamp
BEFORE UPDATE ON clients
FOR EACH ROW
EXECUTE PROCEDURE trigger_set_timestamp();
