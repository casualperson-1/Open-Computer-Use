"""HTTP API smoke test plus an end-to-end run over the simulated desktop."""

import os
import tempfile
import time

TEST_DIR = tempfile.mkdtemp(prefix="open-computer-use-test-")
os.environ["OPEN_COMPUTER_USE_DATA_DIR"] = TEST_DIR
os.environ["COMPUTER"] = "simulated"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DIR}/open-computer-use.db"
os.environ["OPENAI_API_KEY"] = "sk-test-key"

from fastapi.testclient import TestClient as _BaseTestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

import app as app_module  # noqa: E402
from node.drivers.apps import InstalledApp  # noqa: E402
from scripted_provider import ScriptedProvider  # noqa: E402
from security.network import parse_networks  # noqa: E402

# The settings singleton may have been created by another test module first.
app_module.settings.COMPUTER = "simulated"
# This module exercises the administrator's own machine with the simulated
# desktop; members never fall back (see tests/test_phase6_multiuser.py).
app_module.settings.REMOTE_NODE_MODE = "auto"

AUTH_USERNAME = "owner"
AUTH_PASSWORD = "Correct-Horse-Battery-Staple-9"


def _seed_owner() -> None:
    """Create the owner out of band, like `python -m server.cli create-owner`."""
    import asyncio as _asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory
    from sqlalchemy.ext.asyncio import create_async_engine as _engine

    from security.owner import create_owner as _create_owner
    from security.owner import prepare_database as _prepare

    async def run() -> None:
        # Seed the database the app actually opens, not just the env URL.
        engine = _engine(app_module.settings.database_url)
        try:
            await _prepare(engine)
            try:
                await _create_owner(
                    _factory(engine, expire_on_commit=False),
                    AUTH_USERNAME,
                    AUTH_PASSWORD,
                )
            except Exception:
                pass  # already provisioned by another test module
        finally:
            await engine.dispose()

    _asyncio.run(run())


_seed_owner()


def _authenticate(client):
    response = client.post(
        "/api/auth/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


class TestClient(_BaseTestClient):
    """Authenticate once per test client, mirroring the real browser session."""

    def __enter__(self):
        client = super().__enter__()
        _authenticate(client)
        return client


def test_health_settings_and_screen():
    with TestClient(app_module.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        settings = client.get("/api/settings").json()
        providers = {item["id"]: item for item in settings["providers"]}
        assert providers["anthropic"]["needs_key"] is True
        assert providers["ollama"]["needs_key"] is False
        assert providers["openai"]["has_key"] is True  # from OPENAI_API_KEY

        screen = client.get("/api/screen").json()
        assert screen["data"].startswith("data:image/")
        assert screen["backend"] == "simulated"

        missing = client.post("/api/run", json={"goal": "test", "provider": "anthropic"})
        assert missing.status_code == 400
        assert "API key" in missing.json()["detail"]

        unknown = client.post("/api/run", json={"goal": "test", "provider": "nope"})
        assert unknown.status_code == 400


def test_provider_key_test_without_key_is_reported_cleanly():
    with TestClient(app_module.app) as client:
        response = client.post("/api/providers/gemini/test", json={})
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert "API key" in response.json()["error"]


def test_mcp_server_crud_masks_secrets():
    with TestClient(app_module.app) as client:
        created = client.post(
            "/api/mcp/servers",
            json={
                "name": "Files",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "env": {"TOKEN": "super-secret"},
            },
        )
        assert created.status_code == 201
        server = created.json()
        assert server["name"] == "Files"
        assert server["has_env"] is True
        assert "super-secret" not in str(server)
        assert "TOKEN" not in str(server)

        listed = client.get("/api/mcp/servers").json()["servers"]
        assert any(item["id"] == server["id"] for item in listed)

        toggled = client.put(f"/api/mcp/servers/{server['id']}", json={"enabled": False})
        assert toggled.status_code == 200
        assert toggled.json()["enabled"] is False

        removed = client.delete(f"/api/mcp/servers/{server['id']}")
        assert removed.status_code == 200
        assert all(
            item["id"] != server["id"]
            for item in client.get("/api/mcp/servers").json()["servers"]
        )

        bad = client.post("/api/mcp/servers", json={"name": "x", "transport": "ftp"})
        assert bad.status_code == 400


def test_mcp_test_endpoint_reports_tools(monkeypatch):
    async def fake_probe(config, timeout=20.0):
        return [{"name": "read_file", "description": "Read a file"}]

    monkeypatch.setattr(app_module, "probe_mcp_server", fake_probe)
    with TestClient(app_module.app) as client:
        response = client.post(
            "/api/mcp/test",
            json={
                "name": "Files",
                "transport": "stdio",
                "command": "npx",
                "args": [],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["tools"][0]["name"] == "read_file"

        invalid = client.post("/api/mcp/test", json={"name": "x", "transport": "http"})
        assert invalid.json()["ok"] is False
        assert "URL" in invalid.json()["error"]


def test_provider_model_search_filters_results(monkeypatch):
    class FakeProvider:
        model = "fake"

        async def list_models(self):
            return ["llama-3.2", "mixtral-8x7b", "llama-3.3"]

    monkeypatch.setattr(app_module, "build_provider", lambda *args, **kwargs: FakeProvider())
    with TestClient(app_module.app) as client:
        response = client.get("/api/providers/ollama/models?q=llama&refresh=1")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["models"] == ["llama-3.2", "llama-3.3"]
        assert body["total"] == 2

        all_models = client.get("/api/providers/ollama/models?refresh=1").json()
        assert all_models["total"] == 3


def test_ollama_library_search_and_pull_job(monkeypatch):
    async def fake_search(query):
        return [{"name": "llama3.2", "description": f"match for {query}"}]

    async def fake_pull(base_url, model, on_progress):
        on_progress({"status": "downloading", "completed": 5, "total": 10})
        on_progress({"status": "success"})

    monkeypatch.setattr(app_module, "search_library", fake_search)
    monkeypatch.setattr(app_module, "pull_model", fake_pull)
    with TestClient(app_module.app) as client:
        library = client.get("/api/ollama/library?q=llama").json()
        assert library["results"][0]["name"] == "llama3.2"

        started = client.post("/api/ollama/pull", json={"model": "llama3.2:3b"})
        assert started.status_code == 202
        job = started.json()
        for _ in range(60):
            job = client.get(f"/api/ollama/pull/{job['id']}").json()
            if job["done"]:
                break
            time.sleep(0.05)
        assert job["done"] is True
        assert job["percent"] == 100.0
        assert job["error"] is None

        bad = client.post("/api/ollama/pull", json={"model": "bad name!"})
        assert bad.status_code == 400


def test_websocket_hello_and_screen_metadata():
    with TestClient(app_module.app) as client:
        with client.websocket_connect("/ws") as websocket:
            hello = websocket.receive_json()
            assert hello["type"] == "hello"
            assert "computer" in hello
            computer = hello["computer"]
            assert isinstance(computer["desktop_available"], bool)
            assert isinstance(computer["reason"], str)
            assert computer["real_input"] is computer["desktop_available"]
            assert "platform" in computer


def test_provider_settings_round_trip():
    with TestClient(app_module.app) as client:
        saved = client.put(
            "/api/settings/providers/ollama",
            json={"model": "llava:latest", "base_url": "http://localhost:11434"},
        )
        assert saved.status_code == 200

        settings = client.get("/api/settings").json()
        ollama = next(item for item in settings["providers"] if item["id"] == "ollama")
        assert ollama["model"] == "llava:latest"
        assert ollama["base_url"] == "http://localhost:11434"


def test_chats_and_last_provider_are_remembered():
    with TestClient(app_module.app) as client:
        created = client.post("/api/chats", json={})
        assert created.status_code == 201
        chat = created.json()
        assert chat["title"] == "New task"

        chats = client.get("/api/chats").json()["chats"]
        assert any(item["id"] == chat["id"] for item in chats)

        detail = client.get(f"/api/chats/{chat['id']}").json()
        assert detail["chat"]["id"] == chat["id"]
        assert detail["runs"] == []
        assert detail["active_run"] is None

        saved = client.put(
            "/api/settings/providers/gemini",
            json={"model": "gemini-3.8-flash"},
        )
        assert saved.status_code == 200
        settings = client.get("/api/settings").json()
        assert settings["default_provider"] == "gemini"
        assert settings["last_model"] == "gemini-3.8-flash"


def test_memory_and_default_apps_round_trip(monkeypatch):
    fake_apps = [
        InstalledApp(
            id="test.editor",
            name="Test Editor",
            command="test-editor %f",
            mime_types=["application/pdf"],
        )
    ]
    monkeypatch.setattr(app_module, "installed_apps", lambda: fake_apps)

    with TestClient(app_module.app) as client:
        saved = client.put(
            "/api/preferences/memory",
            json={"memory": "Downloads live in ~/Downloads"},
        )
        assert saved.status_code == 200

        prefs = client.get("/api/preferences").json()
        assert prefs["memory"] == "Downloads live in ~/Downloads"
        assert prefs["installed_apps"][0]["id"] == "test.editor"

        added = client.post(
            "/api/preferences/default-apps",
            json={"kind": "pdf", "app_id": "test.editor"},
        )
        assert added.status_code == 200, added.text
        rows = added.json()["default_apps"]
        assert rows[0]["kind"] == ".pdf"
        assert rows[0]["app_name"] == "Test Editor"

        missing = client.post(
            "/api/preferences/default-apps",
            json={"kind": ".pdf", "app_id": "not.installed"},
        )
        assert missing.status_code == 404

        assert client.get("/api/settings").status_code == 200

        removed = client.delete("/api/preferences/default-apps/.pdf")
        assert removed.status_code == 200
        assert removed.json()["default_apps"] == []


def test_chat_delete_and_file_upload():
    with TestClient(app_module.app) as client:
        uploaded = client.post(
            "/api/uploads",
            files={"file": ("notes.txt", b"March total: 42\n", "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        attachment = uploaded.json()
        assert attachment["name"] == "notes.txt"
        assert attachment["size"] == 16
        assert attachment["has_text"] is True

        chat = client.post("/api/chats", json={"title": "Delete me"}).json()
        assert client.get(f"/api/chats/{chat['id']}").status_code == 200
        deleted = client.delete(f"/api/chats/{chat['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True
        assert client.get(f"/api/chats/{chat['id']}").status_code == 404
        assert all(item["id"] != chat["id"] for item in client.get("/api/chats").json()["chats"])


def test_end_to_end_run_with_scripted_model(monkeypatch):
    scripted = ScriptedProvider(
        [
            {"tool_calls": [("click", {"x": 420, "y": 260})]},
            {"tool_calls": [("done", {"summary": "Clicked the demo window."})]},
        ]
    )
    monkeypatch.setattr(app_module, "build_provider", lambda *args, **kwargs: scripted)

    with TestClient(app_module.app) as client:
        uploaded = client.post(
            "/api/uploads",
            files={"file": ("notes.txt", b"March total: 42", "text/plain")},
        ).json()
        chat = client.post("/api/chats", json={}).json()
        started = client.post(
            "/api/run",
            json={
                "goal": "Click the demo window",
                "chat_id": chat["id"],
                "attachment_ids": [uploaded["id"]],
                "provider": "openai",
                "model": "gpt-4o",
                "computer": "simulated",
                "max_steps": 4,
            },
        )
        assert started.status_code == 202
        run_id = started.json()["id"]
        assert started.json()["chat_id"] == chat["id"]

        deadline = time.time() + 8
        snapshot = {}
        while time.time() < deadline:
            snapshot = client.get("/api/run").json()["run"]
            if snapshot and snapshot["status"] not in (
                "running",
                "paused",
                "waiting",
                "awaiting_approval",
                "stopping",
            ):
                break
            time.sleep(0.05)

        assert snapshot["id"] == run_id
        assert snapshot["status"] == "completed", snapshot
        assert snapshot["summary"] == "Clicked the demo window."
        assert snapshot["steps"] == 1
        assert snapshot["attachments"] == ["notes.txt"]

        detail = client.get(f"/api/chats/{chat['id']}").json()
        assert detail["chat"]["title"] == "Click the demo window"
        assert len(detail["runs"]) == 1
        assert detail["runs"][0]["id"] == run_id
        assert detail["runs"][0]["status"] == "completed"
        assert detail["active_run"]["id"] == run_id

        settings = client.get("/api/settings").json()
        assert settings["default_provider"] == "openai"
        assert settings["last_model"] == snapshot["model"]


def test_chat_mode_run_creates_canvas_artifact(monkeypatch):
    scripted = ScriptedProvider(
        [
            {
                "tool_calls": [
                    (
                        "create_artifact",
                        {
                            "kind": "slides",
                            "title": "Launch plan",
                            "content": "# Launch plan\n\nShip it\n\n---\n\n## Timeline\n\n- This week",
                        },
                    )
                ]
            },
            {"text": "Your launch deck is on the Canvas."},
        ]
    )
    monkeypatch.setattr(app_module, "build_provider", lambda *args, **kwargs: scripted)

    with TestClient(app_module.app) as client:
        chat = client.post("/api/chats", json={}).json()
        started = client.post(
            "/api/run",
            json={
                "goal": "Make a launch deck",
                "chat_id": chat["id"],
                "provider": "openai",
                "mode": "chat",
                "max_steps": 4,
            },
        )
        assert started.status_code == 202, started.text
        assert started.json()["mode"] == "chat"
        assert started.json()["computer"] == "chat"
        run_id = started.json()["id"]

        deadline = time.time() + 8
        snapshot = {}
        while time.time() < deadline:
            snapshot = client.get("/api/run").json()["run"]
            if snapshot and snapshot["status"] not in (
                "running",
                "paused",
                "waiting",
                "awaiting_approval",
                "stopping",
            ):
                break
            time.sleep(0.05)

        assert snapshot["id"] == run_id
        assert snapshot["status"] == "completed", snapshot
        assert snapshot["summary"] == "Your launch deck is on the Canvas."

        detail = client.get(f"/api/chats/{chat['id']}").json()
        assert detail["runs"][0]["mode"] == "chat"
        assert detail["runs"][0]["summary"] == "Your launch deck is on the Canvas."

        listed = client.get(f"/api/artifacts?chat_id={chat['id']}").json()["artifacts"]
        assert len(listed) == 1
        assert listed[0]["kind"] == "slides"
        assert listed[0]["title"] == "Launch plan"
        assert "content" not in listed[0]

        artifact = client.get(f"/api/artifacts/{listed[0]['id']}").json()
        assert artifact["chat_id"] == chat["id"]
        assert artifact["run_id"] == run_id
        assert artifact["content"].startswith("# Launch plan")
        assert "---" in artifact["content"]

        deleted = client.delete(f"/api/artifacts/{listed[0]['id']}")
        assert deleted.status_code == 200
        assert client.get(f"/api/artifacts/{listed[0]['id']}").status_code == 404


def test_auth_csrf_and_logout_flow():
    with TestClient(app_module.app) as client:
        status = client.get("/api/auth/status").json()
        assert status["authenticated"] is True
        assert status["username"] == AUTH_USERNAME
        csrf = status["csrf_token"]
        assert csrf

        cross_site = client.post(
            "/api/chats", json={}, headers={"Origin": "https://evil.example"}
        )
        assert cross_site.status_code == 403

        missing_csrf = client.post(
            "/api/chats",
            json={},
            headers={"Origin": "http://testserver", "X-CSRF-Token": ""},
        )
        assert missing_csrf.status_code == 403

        allowed = client.post(
            "/api/chats",
            json={},
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert allowed.status_code == 201

        # Signup exists in Phase 6, but the default mode is invite-only, so a
        # request without a code is refused and creates nothing.
        signup = client.post(
            "/api/auth/signup",
            json={"username": "second", "password": AUTH_PASSWORD},
        )
        assert signup.status_code == 400
        assert "invite" in signup.json()["detail"].lower()

        wrong = client.post(
            "/api/auth/login",
            json={"username": AUTH_USERNAME, "password": "Wrong-" + AUTH_PASSWORD},
        )
        assert wrong.status_code == 401
        assert wrong.json()["detail"] == "Invalid username or password."

        logged_out = client.post(
            "/api/auth/logout", headers={"Origin": "http://testserver"}
        )
        assert logged_out.status_code == 200
        assert client.get("/api/settings").status_code == 401

        relogin = client.post(
            "/api/auth/login",
            json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD},
        )
        assert relogin.status_code == 200
        assert relogin.json()["csrf_token"] != csrf
        cookie = relogin.headers.get("set-cookie", "").lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "path=/" in cookie
        client.headers["X-CSRF-Token"] = relogin.json()["csrf_token"]


def test_mobile_page_is_phone_first_and_has_no_mcp_surface():
    iphone = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )

    with TestClient(app_module.app) as client:
        desktop = client.get(
            "/", headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
        )
        assert "DESKTOP AGENT" in desktop.text

        phone = client.get("/", headers={"User-Agent": iphone})
        assert phone.status_code == 200
        assert "PHONE REMOTE" in phone.text
        assert 'id="composer"' in phone.text
        assert 'id="node-banner"' in phone.text
        assert "/api/nodes" in phone.text
        assert "/api/mcp" not in phone.text

        direct = client.get("/m")
        assert direct.status_code == 200
        assert "PHONE REMOTE" in direct.text

        forced = client.get("/?desktop=1", headers={"User-Agent": iphone})
        assert "DESKTOP AGENT" in forced.text


def _request_from(peer: str, forwarded: str = "") -> Request:
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/login",
            "headers": headers,
            "client": (peer, 1234),
        }
    )


def test_forwarded_headers_are_ignored_unless_the_proxy_is_trusted(monkeypatch):
    # No TRUSTED_PROXIES by default: forwarded headers cannot change the client.
    assert app_module._client_ip(_request_from("8.8.8.8", "127.0.0.1")) == "8.8.8.8"
    assert not app_module._client_is_local(_request_from("8.8.8.8", "127.0.0.1"))
    # An ignored header cannot make a loopback peer look remote either.
    assert app_module._client_ip(_request_from("127.0.0.1", "1.2.3.4")) == "127.0.0.1"
    assert app_module._client_is_local(_request_from("127.0.0.1", "1.2.3.4"))

    monkeypatch.setattr(
        app_module, "TRUSTED_PROXIES", parse_networks("127.0.0.1/32,::1/128")
    )
    # Trusted proxy: the right-most hop that is not the proxy itself is the client.
    assert (
        app_module._client_ip(_request_from("127.0.0.1", "127.0.0.1, 8.8.8.8"))
        == "8.8.8.8"
    )
    assert not app_module._client_is_local(_request_from("127.0.0.1", "127.0.0.1, 8.8.8.8"))
    assert (
        app_module._client_ip(_request_from("127.0.0.1", "203.0.113.9, 127.0.0.1"))
        == "203.0.113.9"
    )


def test_anonymous_requests_are_rejected():
    with _BaseTestClient(app_module.app) as anonymous:
        assert anonymous.get("/").status_code == 200
        assert anonymous.get("/api/health").json()["status"] == "ok"
        assert anonymous.get("/api/health").json().get("computer") is None
        assert anonymous.get("/api/settings").status_code == 401
        assert anonymous.get("/api/screen").status_code == 401
        assert anonymous.get("/openapi.json").status_code == 404
        assert anonymous.get("/docs").status_code == 404
        assert anonymous.post("/api/auth/logout").status_code == 401
        assert anonymous.post("/api/run", json={"goal": "x"}).status_code == 401

        locked = anonymous.post(
            "/api/auth/login",
            json={"username": "intruder", "password": AUTH_PASSWORD},
        )
        assert locked.status_code == 401
        assert locked.json()["detail"] == "Invalid username or password."
