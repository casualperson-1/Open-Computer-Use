"""HTTP API smoke test plus an end-to-end run over the simulated desktop."""

import os
import tempfile
import time

TEST_DIR = tempfile.mkdtemp(prefix="open-computr-use-test-")
os.environ["OPEN_COMPUTR_USE_DATA_DIR"] = TEST_DIR
os.environ["COMPUTER"] = "simulated"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DIR}/open-computr-use.db"
os.environ["OPENAI_API_KEY"] = "sk-test-key"

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from agent.providers.mock import ScriptedProvider  # noqa: E402


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
