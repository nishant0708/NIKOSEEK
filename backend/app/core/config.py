from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase project's Postgres connection string (Dashboard > Connect).
    database_url: str
    jwt_secret_key: str = "development-only-change-me"
    access_token_expire_minutes: int = 1440
    # Keep this server-only. Do not send the service-role key to a frontend.
    supabase_url: str
    supabase_service_role_key: str
    supabase_storage_bucket: str = "job-files"
    gemini_api_key: str | None = None
    ai_model: str = "gemini-3.5-flash-lite"
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
