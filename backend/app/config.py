from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CASEMGR_")

    database_url: str = "postgresql+asyncpg://casemgr:casemgr@localhost:5432/casemgr"


settings = Settings()
