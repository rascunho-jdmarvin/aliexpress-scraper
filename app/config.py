from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    us_proxy_url: Optional[str] = None

    browser_headless: bool = True
    browser_timeout_ms: int = 30000

    # ScrapFly
    scrapfly_api_key: str = ""

    debug: bool = False
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
