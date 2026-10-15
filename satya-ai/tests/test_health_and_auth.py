import pytest


@pytest.mark.asyncio
async def test_protected_mutations_reject_missing_and_invalid_keys(client):
    valid_key = client.headers["X-Satya-Key"]
    payload = {"agent_id": "unauthorized-agent", "name": "Should not be created"}
    client.headers.pop("X-Satya-Key", None)
    missing = await client.post("/v1/agents", json=payload)
    assert missing.status_code == 401
    client.headers["X-Satya-Key"] = "invalid-key-that-is-not-authorized"
    invalid = await client.post("/v1/agents", json=payload)
    assert invalid.status_code == 401
    client.headers["X-Satya-Key"] = valid_key
    assert (await client.post("/v1/agents", json=payload)).status_code == 200


@pytest.mark.asyncio
async def test_health_endpoint_checks_database_and_audit_worker(client):
    response = await client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"] == {"database": "ok", "audit_worker": "ok"}
