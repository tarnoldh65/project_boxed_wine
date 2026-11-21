"""Named connections to outside services, read from the deployment secret CASEMGR_CONNECTIONS.

Credentials stay here: tenants refer to a connection by name, and only names and types
ever reach the database, the API, or the browser.
"""

import json
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from app.config import settings

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$", max_length=40)]
TenantSlug = Annotated[str, StringConstraints(min_length=1, max_length=63)]


def _https(url: str, what: str) -> None:
    if not url.startswith("https://"):
        raise ValueError(f"{what} must use https")


class SlackConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["slack"]
    # The incoming-webhook URL is itself the credential.
    webhook_url: SecretStr
    tenants: list[TenantSlug] = Field(min_length=1)

    @model_validator(mode="after")
    def check_url(self) -> Self:
        _https(self.webhook_url.get_secret_value(), "webhook_url")
        return self


class AtlassianConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["jira", "confluence"]
    base_url: str
    # basic: Atlassian Cloud account email and API token. bearer: Data Center personal access token.
    auth: Literal["basic", "bearer"]
    user: str | None = None
    token: SecretStr
    tenants: list[TenantSlug] = Field(min_length=1)

    @model_validator(mode="after")
    def check_auth(self) -> Self:
        _https(self.base_url, "base_url")
        if self.auth == "basic" and not self.user:
            raise ValueError("basic auth needs a user")
        return self


Connection = Annotated[SlackConnection | AtlassianConnection, Field(discriminator="type")]
_parse = TypeAdapter(dict[Name, Connection])


def parse(raw: str) -> dict[str, SlackConnection | AtlassianConnection]:
    """Validate the setting. Errors say what is wrong but never repeat a value: it may be secret."""
    try:
        return _parse.validate_python(json.loads(raw))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"CASEMGR_CONNECTIONS is not valid JSON (line {e.lineno})") from None
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, error['loc'])) or 'value'}: {error['msg']}"
            for error in e.errors(include_input=False, include_url=False, include_context=False)
        )
        raise RuntimeError(f"CASEMGR_CONNECTIONS is invalid: {problems}") from None


CONNECTIONS = parse(settings.connections.get_secret_value())


def available(
    slug: str, kind: str | None = None
) -> dict[str, SlackConnection | AtlassianConnection]:
    """The connections a tenant may use, optionally of one type."""
    return {
        name: connection
        for name, connection in sorted(CONNECTIONS.items())
        if slug in connection.tenants and kind in (None, connection.type)
    }


class ConnectionOut(BaseModel):
    name: str
    type: str
