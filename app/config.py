from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central place for all config. Values are loaded from environment
    variables (which docker-compose injects from .env). Never hardcode
    secrets here — this file just defines the shape of the config.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    environment: str = "local"
    log_level: str = "INFO"

    # Platform credentials — populated in Phase 4 of the build guide.
    # Adapters check these are present before attempting to call out.
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://localhost:8000/auth/youtube/callback"

    meta_app_id: str = ""
    meta_app_secret: str = ""

    x_client_id: str = ""
    x_client_secret: str = ""

    # Where local dev stores OAuth tokens. In AWS this is replaced by
    # Secrets Manager — see app/services/secrets_service.py.
    local_secrets_path: str = "/app/.local_secrets.json"


settings = Settings()
