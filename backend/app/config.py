from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CASEMGR_")

    database_url: str
    public_url: str = "http://localhost:8080"
    oidc_metadata_url: str = "http://localhost:8180/realms/casemgr/.well-known/openid-configuration"
    oidc_client_id: str = "casemgr"
    oidc_client_secret: str
    # Signs the short-lived cookie that carries OIDC state between login and callback.
    secret_key: str
    session_idle_minutes: int = 30
    session_max_hours: int = 12
    # How often a session re-reads the user's groups from the IdP.
    group_sync_minutes: int = 5


settings = Settings()  # pyright: ignore[reportCallIssue]
