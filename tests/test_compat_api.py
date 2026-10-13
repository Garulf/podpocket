import pytest

NO_FILES = {"totalFiles": 0, "totalSize": 0, "usedSize": 0}


async def test_files_list_is_empty_so_plus_sync_continues(client, alice_auth):
    response = await client.get("/api/files", headers=alice_auth)

    assert response.status_code == 200
    assert response.json() == {"files": [], "account": NO_FILES}


async def test_files_usage_and_upload_are_accepted(client, alice_auth):
    assert (await client.get("/api/files/usage", headers=alice_auth)).json() == NO_FILES
    upload = await client.post("/api/files", json={"files": []}, headers=alice_auth)
    assert upload.status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/user/podcast_rating/list"), ("POST", "/api/user/whats_new/read_state/list")],
)
async def test_protobuf_lists_are_empty(client, alice_auth, method, path):
    response = await client.request(method, path, headers=alice_auth)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == b""


@pytest.mark.parametrize("path", ["/api/user/whats_new/read", "/api/user/whats_new/unread"])
async def test_whats_new_marks_are_accepted(client, alice_auth, path):
    assert (await client.put(path, headers=alice_auth)).status_code == 200


async def test_single_podcast_rating_is_not_found(client, alice_auth):
    response = await client.post("/api/user/podcast_rating/show", headers=alice_auth)

    assert response.status_code == 404


async def test_compat_routes_need_a_token(client):
    assert (await client.get("/api/files")).status_code == 401
