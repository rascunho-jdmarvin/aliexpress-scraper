from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # Chave para criptografia de dados
    encryption_key: str

    # URL de conexão do Redis para o Celery
    redis_url: str

    us_proxy_url: Optional[str] = None

    browser_headless: bool = True
    browser_timeout_ms: int = 30000

    # ScrapFly
    scrapfly_api_key: str = ""

    debug: bool = False
    log_level: str = "INFO"
    
    encription_key: str = ""
    
    redis_url: str = "redis://localhost:6379/0"
    capsolver_secret_key: str = ""
    
    zenrow_api_key: str = ""
    
    webhook_url: Optional[str] = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
