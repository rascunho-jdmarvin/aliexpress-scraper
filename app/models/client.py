from pydantic import BaseModel, UUID4
from datetime import datetime

class Client(BaseModel):
    """
    Modelo Pydantic para representar os dados de um cliente autenticado.
    """
    id: UUID4
    name: str
    key_id: str
    scrapfly_api_key: str  # A chave da ScrapFly já descriptografada.
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
