from fastapi import Depends, HTTPException, status, Header
from typing import Annotated

from app.db.supabase import db
from app.models.client import Client
from app.security import verify_secret, decrypt_data

# Define exceções HTTP padrão para erros de autenticação.
credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credenciais inválidas",
    headers={"WWW-Authenticate": "Bearer"},
)

inactive_client_exception = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Cliente inativo",
)

async def get_current_client(
    x_client_id: Annotated[str | None, Header()] = None,
    x_client_secret: Annotated[str | None, Header()] = None,
) -> Client:
    """
    Dependência do FastAPI para autenticar um cliente.

    Verifica os cabeçalhos `X-Client-Id` e `X-Client-Secret`, valida as credenciais
    e retorna um modelo Pydantic do cliente com a `scrapfly_api_key` descriptografada.
    """
    if not x_client_id or not x_client_secret:
        raise credentials_exception

    # Busca o cliente no banco de dados.
    client_data = await db.get_client_by_key_id(x_client_id)
    if not client_data:
        raise credentials_exception

    # Verifica se a chave secreta está correta.
    if not verify_secret(x_client_secret, client_data["key_secret_hash"]):
        raise credentials_exception

    # Verifica se o cliente está ativo.
    if not client_data.get("is_active", False):
        raise inactive_client_exception

    # Descriptografa a chave da ScrapFly.
    try:
        decrypted_scrapfly_key = decrypt_data(client_data["scrapfly_api_key"])
    except Exception:
        # Se a descriptografia falhar, é um erro crítico.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível processar a chave da API do cliente.",
        )

    # Monta o objeto final do cliente.
    client_obj = Client(
        id=client_data["id"],
        name=client_data["name"],
        key_id=client_data["key_id"],
        scrapfly_api_key=decrypted_scrapfly_key,
        is_active=client_data["is_active"],
        created_at=client_data["created_at"],
        updated_at=client_data["updated_at"],
    )

    return client_obj
