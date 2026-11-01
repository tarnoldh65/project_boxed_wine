import secrets
from typing import Any

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import SessionLocal
from tests.conftest import MakeToken, MakeUser, SignedIn, audit_actions, new_client, new_tenant


@pytest.fixture
async def analyst(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["analyst"]})


@pytest.fixture
async def insider(make_user: MakeUser, tenant: str) -> SignedIn:
    return await make_user({tenant: ["ir_lead", "restricted_case"]})


async def post(client: AsyncClient, path: str, body: dict[str, Any], status: int = 201) -> Any:
    response = await client.post(path, json=body)
    assert response.status_code == status, response.text
    return response.json()


async def seed(client: AsyncClient, tenant: str, word: str, restricted: bool = False) -> int:
    """A case where `word` appears in the title, a note, an evidence name, and an observable."""
    base = f"/api/tenants/{tenant}/cases"
    case = await post(client, base, {"template": "insider", "title": f"Review of {word} activity"})
    number = case["number"]
    await post(client, f"{base}/{number}/notes", {"body": f"Interviewed the {word} team lead"})
    await post(
        client,
        f"{base}/{number}/evidence/references",
        {
            "name": f"{word}-badge-log.csv",
            "location": "HR share",
            "collection": {"collected_by": "HR", "collected_at": "2026-10-31T10:00:00Z"},
        },
    )
    await post(
        client,
        f"{base}/{number}/observables",
        {"observables": [{"type": "domain", "value": f"{word}.example"}]},
    )
    if restricted:
        response = await client.patch(f"{base}/{number}", json={"restricted": True})
        assert response.status_code == 200, response.text
    return number


async def search(client: AsyncClient, tenant: str, q: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/tenants/{tenant}/search", params={"q": q})
    assert response.status_code == 200, response.text
    return response.json()


async def test_searches_cases_notes_evidence_and_observables(
    analyst: SignedIn, tenant: str, owner: AsyncEngine
) -> None:
    word = f"zephyr{secrets.token_hex(3)}"
    number = await seed(analyst.client, tenant, word)

    hits = await search(analyst.client, tenant, word)

    assert {h["kind"] for h in hits} == {"case", "note", "evidence"}
    assert {h["case_number"] for h in hits} == {number}
    note = next(h for h in hits if h["kind"] == "note")
    assert note["excerpt"] == f"Interviewed the {word} team lead"
    assert note["case_title"] == f"Review of {word} activity"
    observables = await search(analyst.client, tenant, f"{word}.example")
    assert [(h["kind"], h["excerpt"]) for h in observables] == [
        ("observable", f"domain: {word}.example")
    ]
    assert await search(analyst.client, tenant, '"team lead" -' + word) == []
    assert "search.run" in await audit_actions(owner, f"user:{analyst.id}")


async def test_restricted_content_never_appears_for_unauthorized_users(
    analyst: SignedIn, insider: SignedIn, make_user: MakeUser, tenant: str, owner: AsyncEngine
) -> None:
    word = f"osprey{secrets.token_hex(3)}"
    number = await seed(insider.client, tenant, word, restricted=True)

    assert await search(analyst.client, tenant, word) == []
    assert await search(analyst.client, tenant, f"{word}.example") == []
    hits = await search(insider.client, tenant, word)
    assert {h["case_number"] for h in hits} == {number}
    assert len(hits) == 3

    # Even with the restricted grant, another tenant's cases are out of reach.
    other = await new_tenant(owner)
    outsider = await make_user({other: ["ir_lead", "restricted_case"]})
    assert await search(outsider.client, other, word) == []


async def test_search_function_finds_nothing_outside_a_tenant(
    insider: SignedIn, tenant: str
) -> None:
    # search_hits reads past row-level security, so it must apply the tenant filter itself.
    word = f"heron{secrets.token_hex(3)}"
    await seed(insider.client, tenant, word)
    async with SessionLocal() as db:
        hits = await db.execute(text("SELECT * FROM search_hits(:q, true, 50)"), {"q": word})
        assert hits.all() == []


async def test_rejects_empty_queries(analyst: SignedIn, tenant: str) -> None:
    response = await analyst.client.get(f"/api/tenants/{tenant}/search", params={"q": ""})
    assert response.status_code == 422


async def test_saved_searches_belong_to_their_owner(
    analyst: SignedIn, insider: SignedIn, tenant: str, make_token: MakeToken
) -> None:
    base = f"/api/tenants/{tenant}/searches"
    saved = await post(analyst.client, base, {"name": "Badge logs", "query": "badge log"})
    assert (saved["name"], saved["query"]) == ("Badge logs", "badge log")
    duplicate = await analyst.client.post(base, json={"name": "Badge logs", "query": "other"})
    assert duplicate.status_code == 422
    await post(insider.client, base, {"name": "Badge logs", "query": "mine"})

    assert [s["query"] for s in (await analyst.client.get(base)).json()] == ["badge log"]
    assert (await insider.client.delete(f"{base}/{saved['id']}")).status_code == 404
    assert (await analyst.client.delete(f"{base}/{saved['id']}")).status_code == 204
    assert (await analyst.client.get(base)).json() == []

    token = await make_token(tenant, ["cases:read"])
    async with new_client(headers={"Authorization": f"Bearer {token}"}) as client:
        assert (await client.post(base, json={"name": "x", "query": "y"})).status_code == 403
        assert (await client.get(base)).json() == []
