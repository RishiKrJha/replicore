import concurrent.futures

import pytest

from vault import create_app
from vault.integrity import sha256_bytes


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE_PATH": str(tmp_path / "vault.sqlite3"),
        "STORAGE_ROOT": str(tmp_path / "storage"),
        "NODE_COUNT": 3,
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
    })


@pytest.fixture
def vault(app):
    return app.extensions["vault"]


def test_real_upload_replicates_and_retrieves(app, vault):
    obj = vault.create_real("hello.txt", b"hello vault", 3)
    assert obj["state"] == "HEALTHY"
    assert len([r for r in obj["replicas"] if r["state"] == "HEALTHY"]) == 3
    data, _ = vault.retrieve(obj["id"])
    assert data == b"hello vault"
    assert obj["checksum"] == sha256_bytes(data)


def test_simulated_object_is_small_and_replicated(vault):
    obj = vault.create_simulated("movie.mp4", 500_000_000, 3)
    assert obj["kind"] == "simulated"
    assert obj["size"] == 500_000_000
    assert obj["physical_size"] < 3000
    for replica in obj["replicas"]:
        assert vault.storage.path(replica["node_id"], obj["id"]).stat().st_size < 1000


def test_insufficient_nodes_is_degraded(vault):
    obj = vault.create_real("small", b"x", 5)
    assert obj["state"] == "DEGRADED"
    assert sum(r["state"] == "HEALTHY" for r in obj["replicas"]) == 3


def test_failed_node_still_retrieves_and_restores(vault):
    obj = vault.create_real("small", b"x", 2)
    node = obj["replicas"][0]["node_id"]
    vault.set_node(node, "FAILED")
    degraded = vault.get_object(obj["id"])
    assert any(r["state"] == "UNAVAILABLE" for r in degraded["replicas"])
    assert vault.retrieve(obj["id"])[0] == b"x"
    vault.set_node(node, "ONLINE")
    assert vault.get_object(obj["id"])["state"] == "HEALTHY"


@pytest.mark.parametrize("action", ["remove", "corrupt", "stale"])
def test_faults_are_detected_and_repaired(vault, action):
    obj = vault.create_real("small", b"original", 3)
    target = obj["replicas"][0]["node_id"]
    repaired = vault.inject(obj["id"], target, action)
    assert repaired["state"] == "HEALTHY"
    assert all(r["state"] == "HEALTHY" for r in repaired["replicas"])
    assert vault.retrieve(obj["id"])[0] == b"original"


def test_simulated_corruption_repairs_from_manifest(vault):
    obj = vault.create_simulated("logical.bin", 900_000_000, 3)
    target = obj["replicas"][0]["node_id"]
    repaired = vault.inject(obj["id"], target, "corrupt")
    assert repaired["state"] == "HEALTHY"
    assert vault.storage.path(target, obj["id"]).stat().st_size < 1000


def test_all_replicas_invalid_are_unavailable(vault):
    obj = vault.create_real("small", b"original", 3)
    for replica in obj["replicas"]:
        vault.storage.corrupt(replica["node_id"], obj["id"])
    vault.repair(obj["id"])
    assert vault.get_object(obj["id"])["state"] == "UNAVAILABLE"
    with pytest.raises(FileNotFoundError):
        vault.retrieve(obj["id"])


def test_concurrent_verification_does_not_create_unverified_health(vault):
    obj = vault.create_real("small", b"concurrent", 3)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: vault.repair(obj["id"]), range(8)))
    result = vault.get_object(obj["id"])
    assert all(r["state"] == "HEALTHY" for r in result["replicas"])


def test_admin_requires_authentication_and_can_login(app):
    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 302
    assert client.post("/admin/login", data={"username": "admin", "password": "secret"}).status_code == 302
    assert client.get("/admin").status_code == 200


def test_admin_fault_action_is_protected_and_real(app, vault):
    obj = vault.create_real("portal.txt", b"portal", 3)
    node = obj["replicas"][0]["node_id"]
    client = app.test_client()
    assert client.post(f"/admin/objects/{obj['id']}/replicas/{node}/corrupt").status_code == 302
    assert client.get("/admin").status_code == 302
    assert client.post("/admin/login", data={"username": "admin", "password": "secret"}).status_code == 302
    assert client.post(f"/admin/objects/{obj['id']}/replicas/{node}/corrupt").status_code == 302
    assert vault.get_object(obj["id"])["state"] == "HEALTHY"


def test_fault_history_records_detection_and_repair(vault):
    obj = vault.create_real("history.txt", b"history", 3)
    target = obj["replicas"][0]["node_id"]

    vault.inject(obj["id"], target, "corrupt")
    events = vault.events(obj["id"])

    assert any(event["event_type"] == "FAULT_INJECTED" for event in events)
    assert any(event["event_type"] == "VERIFICATION" and event["resulting_state"] == "CORRUPTED"
               for event in events)
    repair = next(event for event in events if event["event_type"] == "REPAIR")
    assert repair["source_node"] != target
    assert repair["destination_node"] == target
    assert repair["expected_checksum"] == repair["actual_checksum"]


def test_admin_response_exposes_operation_history(app, vault):
    obj = vault.create_real("portal-history.txt", b"portal", 3)
    target = obj["replicas"][0]["node_id"]
    client = app.test_client()
    client.post("/admin/login", data={"username": "admin", "password": "secret"})

    response = client.post(
        f"/admin/objects/{obj['id']}/replicas/{target}/remove",
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "FAULT_INJECTED" in body
    assert "MISSING" in body
    assert "REPAIR" in body
    assert target in body
