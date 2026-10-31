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
    # Email intake: imaps://user:password@host:port/MAILBOX, polled by the email_intake job.
    imap_url: str | None = None
    imap_tenant: str | None = None
    imap_template: str = "phishing"
    # Outbound mail for escalation notifications.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_username: str | None = None
    smtp_password: str | None = None
    mail_from: str = "casemgr@localhost"
    # Evidence storage. The public endpoint is what browsers reach for presigned uploads.
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "casemgr-evidence"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_part_size_mb: int = 64
    malware_max_mb: int = 100


settings = Settings()  # pyright: ignore[reportCallIssue]
