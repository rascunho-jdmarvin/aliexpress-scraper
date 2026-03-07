import logging
import uuid
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status

from app.db.supabase import db
from app.security import get_secret_hash, encrypt_data

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/clients", tags=["clients"])

# ---------------------------------------------------------------------------
# Pydantic Models for Client Creation
# ---------------------------------------------------------------------------

class ClientCreateRequest(BaseModel):
    """Request model to create a new client."""
    name: str = Field(..., description="Um nome legível para o cliente (ex: 'Meu-Ecommerce-App').")
    scrapfly_api_key: str = Field(..., description="A chave de API do ScrapFly para este cliente.")

class ClientCreateResponse(BaseModel):
    """Response model after creating a new client."""
    client_id: str = Field(..., description="O ID do cliente, também conhecido como 'key_id'. Use-o no cabeçalho X-Client-ID.")
    client_secret: str = Field(..., description="O segredo do cliente. **Armazene com segurança, pois não será mostrado novamente.** Use-o no cabeçalho X-Client-Secret.")
    client_name: str = Field(..., description="O nome fornecido para o cliente.")
    message: str = "Cliente criado com sucesso. Guarde o client_secret em um local seguro."

# ---------------------------------------------------------------------------
# POST /clients — Endpoint to create a new API client
# ---------------------------------------------------------------------------

@router.post(
    "/",
    response_model=ClientCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cria um novo cliente de API",
    description="Registra um novo cliente e gera um par de chaves de API (ID e Segredo) para autenticação.",
)
async def create_client(
    request: ClientCreateRequest,
):
    """
    Este endpoint cria um novo cliente no sistema.

    1.  **Gera um `client_id` (key_id) e um `client_secret`**: O segredo é único e seguro.
    2.  **Faz o Hash do `client_secret`**: Apenas o hash é armazenado no banco de dados por segurança.
    3.  **Criptografa a `scrapfly_api_key`**: A chave do ScrapFly é criptografada antes de ser salva.
    4.  **Armazena no Banco de Dados**: Salva o novo cliente na tabela `clients`.
    5.  **Retorna as credenciais**: O `client_id` e o `client_secret` (em texto puro) são retornados.
        **Esta é a única vez que o `client_secret` será exibido.**
    """
    logger.info("Recebida solicitação para criar um novo cliente com o nome: %s", request.name)
    try:
        # 1. Gerar credenciais
        key_id = f"client_{uuid.uuid4().hex[:16]}"
        plain_secret = f"secret_{uuid.uuid4().hex}"

        # 2. Hash do segredo e criptografia da chave do ScrapFly
        hashed_secret = get_secret_hash(plain_secret)
        encrypted_scrapfly_key = encrypt_data(request.scrapfly_api_key)

        # 3. Criar cliente no banco de dados
        new_client_data = await db.create_client(
            name=request.name,
            key_id=key_id,
            hashed_secret=hashed_secret,
            encrypted_scrapfly_api_key=encrypted_scrapfly_key,
        )
        
        logger.info("Cliente '%s' criado com sucesso. Key ID: %s", new_client_data["name"], new_client_data["key_id"])

        return ClientCreateResponse(
            client_id=new_client_data["key_id"],
            client_secret=plain_secret,
            client_name=new_client_data["name"],
        )
    
    except Exception as e:
        logger.exception("Falha ao criar um novo cliente. Nome: %s", request.name)
        # O erro pode ser por duplicação de nome, problema no DB, etc.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Não foi possível criar o cliente: {e}",
        )
