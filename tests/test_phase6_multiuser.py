"""Phase 6: multiple accounts, data isolation, node binding and migration."""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app as app_module
import cli as server_cli
from config import settings
from database.migrations import apply_migrations
from database.models import (
    Artifact,
    Base,
    Chat,
    NodeRecord,
    ProviderSetting,
    RunRecord,
    User,
)
from nodes.hub import NodeConnection, NodeHub, NodeUnavailable, hub as node_hub
from nodes.registry import configure_registry, node_registry
from security.accounts import (
    InviteError,
    claim_orphan_rows,
    create_invite,
    create_user,
    find_valid_invite,
)
from security.owner import create_owner

REPO_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Correct-Horse-Battery-Staple-9"
OTHER_PASSWORD = "Another-Long-Passphrase-42"


class FakeSocket:
    def __init__(self) -> None:
        self.sent = []
        self.closed = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed.append(code)


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    """A throwaway database wired into the module-level app globals."""
    url = f"sqlite+aiosqlite:///{tmp_path}/multi.db"
    engine = create_async_engine(url)

    async def prepare() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await apply_migrations(conn)

    asyncio.run(prepare())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    original_registry = node_registry()._sessions
    monkeypatch.setattr(app_module, "engine", engine)
    monkeypatch.setattr(app_module, "SessionLocal", factory)
    monkeypatch.setattr(app_module.settings, "REMOTE_NODE_MODE", "required")
    configure_registry(factory)
    app_module.RUN_LIMITER._hits.clear()
    app_module.MODEL_CACHE.clear()

    yield SimpleNamespace(engine=engine, factory=factory, url=url, tmp_path=tmp_path)

    asyncio.run(node_hub.disconnect_all())
    configure_registry(original_registry)
    app_module.RUN_LIMITER._hits.clear()
    asyncio.run(engine.dispose())


def _run(coroutine):
    return asyncio.run(coroutine)


def _seed_owner(factory, username: str = "owner", password: str = PASSWORD):
    return _run(create_owner(factory, username, password))


def _seed_member(factory, username: str, password: str = PASSWORD, **kwargs):
    return _run(
        create_user(
            factory, username, password, role="user", require_invite=False, **kwargs
        )
    )


def _login(client: TestClient, username: str, password: str = PASSWORD) -> dict:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _client(stack, username: str, password: str = PASSWORD):
    client = TestClient(app_module.app)
    client.__enter__()
    headers = _login(client, username, password)
    return client, headers


# ---------------------------------------------------------------------------
# Invites and signup modes
# ---------------------------------------------------------------------------


def test_invite_is_single_use_and_expires(stack):
    owner = _seed_owner(stack.factory)

    async def scenario():
        row, code = await create_invite(stack.factory, owner.id, ttl_hours=1, label="sam")
        assert await find_valid_invite(stack.factory, code) is not None
        await create_user(
            stack.factory, "sam", PASSWORD, invite_code=code, require_invite=True
        )
        assert await find_valid_invite(stack.factory, code) is None
        with pytest.raises(InviteError):
            await create_user(
                stack.factory, "sam2", PASSWORD, invite_code=code, require_invite=True
            )

    _run(scenario())

    async def expired():
        row, code = await create_invite(stack.factory, owner.id, ttl_hours=1)
        async with stack.factory() as session:
            stored = await session.get(type(row), row.id)
            stored.expires_at = datetime.utcnow() - timedelta(minutes=1)
            await session.commit()
        assert await find_valid_invite(stack.factory, code) is None

    _run(expired())


def test_signup_modes_are_enforced(stack, monkeypatch):
    _seed_owner(stack.factory)

    with TestClient(app_module.app) as anonymous:
        monkeypatch.setattr(settings, "SIGNUP_MODE", "closed")
        assert anonymous.post(
            "/api/auth/signup", json={"username": "sam", "password": PASSWORD}
        ).status_code == 403

        monkeypatch.setattr(settings, "SIGNUP_MODE", "invite")
        missing = anonymous.post(
            "/api/auth/signup", json={"username": "sam", "password": PASSWORD}
        )
        assert missing.status_code == 400
        assert "invite" in missing.json()["detail"].lower()

        owner = _seed_owner_cached(stack)
        row, code = _run(create_invite(stack.factory, owner.id, ttl_hours=1))

        monkeypatch.setattr(settings, "SIGNUP_MODE", "open")
        response = anonymous.post(
            "/api/auth/signup",
            json={"username": "openuser", "password": OTHER_PASSWORD},
        )
        assert response.status_code == 201, response.text
        assert response.json()["is_admin"] is False

    # The invite-only default works with the generated code.
    with TestClient(app_module.app) as anonymous:
        monkeypatch.setattr(settings, "SIGNUP_MODE", "invite")
        response = anonymous.post(
            "/api/auth/signup",
            json={"username": "invited", "password": OTHER_PASSWORD, "invite_code": code},
        )
        assert response.status_code == 201, response.text


def _seed_owner_cached(stack):
    async def get():
        async with stack.factory() as session:
            return (
                await session.execute(select(User).where(User.role == "admin"))
            ).scalars().first()

    return _run(get())


# ---------------------------------------------------------------------------
# Per-user data isolation
# ---------------------------------------------------------------------------


def test_chats_artifacts_and_runs_are_invisible_across_users(stack):
    owner = _seed_owner(stack.factory)
    member = _seed_member(stack.factory, "sam")

    async def seed():
        async with stack.factory() as session:
            session.add(Chat(id="owner-chat", user_id=owner.id, title="owner private"))
            session.add(Chat(id="sam-chat", user_id=member.id, title="sam private"))
            session.add(
                Artifact(
                    id="owner-artifact",
                    user_id=owner.id,
                    chat_id="owner-chat",
                    title="owner doc",
                    content="secret",
                )
            )
            session.add(
                RunRecord(
                    user_id=owner.id,
                    run_uuid="owner-run",
                    goal="owner goal",
                    provider="anthropic",
                    model="m",
                )
            )
            session.add(
                RunRecord(
                    user_id=member.id,
                    run_uuid="sam-run",
                    goal="sam goal",
                    provider="anthropic",
                    model="m",
                )
            )
            await session.commit()

    _run(seed())

    sam, sam_headers = _client(stack, "sam")
    try:
        chats = sam.get("/api/chats").json()["chats"]
        assert [chat["id"] for chat in chats] == ["sam-chat"]

        # Guessing another user's ids must look exactly like "not found".
        assert sam.get("/api/chats/owner-chat").status_code == 404
        assert sam.delete("/api/chats/owner-chat", headers=sam_headers).status_code == 404
        assert sam.get("/api/artifacts/owner-artifact").status_code == 404
        assert (
            sam.delete("/api/artifacts/owner-artifact", headers=sam_headers).status_code
            == 404
        )
        assert sam.get("/api/artifacts?chat_id=owner-chat").json()["artifacts"] == []

        runs = sam.get("/api/runs").json()["runs"]
        assert [run["id"] for run in runs] == ["sam-run"]

        # The owner can still read their own rows.
        assert sam.get("/api/artifacts").json()["artifacts"] == []
    finally:
        sam.__exit__(None, None, None)

    owner_client, _ = _client(stack, "owner")
    try:
        assert [c["id"] for c in owner_client.get("/api/chats").json()["chats"]] == [
            "owner-chat"
        ]
        assert owner_client.get("/api/artifacts/owner-artifact").status_code == 200
    finally:
        owner_client.__exit__(None, None, None)


def test_provider_keys_are_per_user_and_not_readable_by_anyone(stack, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _seed_owner(stack.factory)
    _seed_member(stack.factory, "sam")

    owner_client, owner_headers = _client(stack, "owner")
    member_client, _ = _client(stack, "sam")
    try:
        saved = owner_client.put(
            "/api/settings/providers/anthropic",
            json={"api_key": "sk-owner-secret-value", "model": "claude-test"},
            headers=owner_headers,
        )
        assert saved.status_code == 200, saved.text

        owner_settings = owner_client.get("/api/settings")
        assert "sk-owner-secret-value" not in owner_settings.text
        owner_provider = next(
            item for item in owner_settings.json()["providers"] if item["id"] == "anthropic"
        )
        assert owner_provider["has_key"] is True

        member_settings = member_client.get("/api/settings")
        member_provider = next(
            item for item in member_settings.json()["providers"] if item["id"] == "anthropic"
        )
        assert member_provider["has_key"] is False
        assert "sk-owner-secret-value" not in member_settings.text

        # The database stores the key encrypted, per user.
        async def stored():
            async with stack.factory() as session:
                rows = (await session.execute(select(ProviderSetting))).scalars().all()
                return rows

        rows = _run(stored())
        assert len(rows) == 1
        assert rows[0].provider == "anthropic"
        assert rows[0].encrypted_key and "sk-owner" not in rows[0].encrypted_key
        assert rows[0].user_id != 0
    finally:
        owner_client.__exit__(None, None, None)
        member_client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Nodes belong to one user
# ---------------------------------------------------------------------------


def test_nodes_are_owner_scoped_and_cross_user_routing_is_impossible(stack):
    _seed_owner(stack.factory)
    sam = _seed_member(stack.factory, "sam")
    alex = _seed_member(stack.factory, "alex")

    sam_client, sam_headers = _client(stack, "sam")
    alex_client, alex_headers = _client(stack, "alex")
    try:
        created = sam_client.post(
            "/api/nodes", json={"name": "sam-laptop"}, headers=sam_headers
        )
        assert created.status_code == 201, created.text
        node = created.json()["node"]
        token = created.json()["token"]
        assert token and "connect --server" in created.json()["command"]

        assert _run(node_registry().find_by_token(token)).user_id == sam.id

        assert alex_client.get("/api/nodes").json()["nodes"] == []
        assert (
            alex_client.post(
                f"/api/nodes/{node['id']}/rotate", headers=alex_headers
            ).status_code
            == 404
        )
        assert (
            alex_client.delete(
                f"/api/nodes/{node['id']}", headers=alex_headers
            ).status_code
            == 404
        )
        assert [item["id"] for item in sam_client.get("/api/nodes").json()["nodes"]] == [
            node["id"]
        ]

        # A live node of Sam's must never answer Alex's request.
        connection = NodeConnection(
            node["id"], FakeSocket(), user_id=sam.id, name="sam-laptop"
        )
        _run(node_hub.register(connection))
        assert node_hub.connection(node["id"], alex.id) is None
        with pytest.raises(NodeUnavailable):
            _run(
                node_hub.dispatch(
                    "screenshot", {}, node_id=node["id"], user_id=alex.id
                )
            )
        assert node_hub.connection(node["id"], sam.id) is connection

        # /api/screen is bound to the caller's own node, not anyone else's.
        assert alex_client.get("/api/screen").status_code == 409
    finally:
        sam_client.__exit__(None, None, None)
        alex_client.__exit__(None, None, None)


def test_desktop_run_requires_the_users_own_node(stack, monkeypatch):
    _seed_owner(stack.factory, "owner")
    member = _seed_member(stack.factory, "sam")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    sam, sam_headers = _client(stack, "sam")
    try:
        response = sam.post(
            "/api/run",
            json={"goal": "open the browser", "mode": "desktop", "computer": "desktop"},
            headers=sam_headers,
        )
        assert response.status_code == 409
        assert "Connect your computer first" in response.json()["detail"]

        # The simulated desktop is administrator-only, never a member fallback.
        simulated = sam.post(
            "/api/run",
            json={"goal": "x", "mode": "desktop", "computer": "simulated"},
            headers=sam_headers,
        )
        assert simulated.status_code == 403
        assert sam.get("/api/screen?computer=simulated").status_code == 403
    finally:
        sam.__exit__(None, None, None)


def test_rate_limit_and_usage_cap_are_per_account(stack):
    _seed_owner(stack.factory)
    member = _seed_member(stack.factory, "sam", run_limit_per_hour=1)
    capped = _seed_member(stack.factory, "alex", usage_cap_usd=0.0)

    sam, sam_headers = _client(stack, "sam")
    alex, alex_headers = _client(stack, "alex")
    try:
        body = {"mode": "desktop", "computer": "desktop"}
        first = sam.post(
            "/api/run", json={"goal": "a", **body}, headers=sam_headers
        )
        assert first.status_code == 409  # no node, but the attempt counts
        second = sam.post(
            "/api/run", json={"goal": "b", **body}, headers=sam_headers
        )
        assert second.status_code == 429
        assert "limit" in second.json()["detail"].lower()

        blocked = alex.post(
            "/api/run",
            json={"goal": "c", "mode": "desktop", "computer": "desktop"},
            headers=alex_headers,
        )
        assert blocked.status_code == 429
        assert "cap" in blocked.json()["detail"].lower()
    finally:
        sam.__exit__(None, None, None)
        alex.__exit__(None, None, None)


def test_approvals_and_run_controls_only_touch_my_run(stack):
    owner = _seed_owner(stack.factory)
    _seed_member(stack.factory, "sam")

    run = app_module.ActiveRun(
        user_id=owner.id,
        goal="owner task",
        provider_name="anthropic",
        model="m",
        computer_backend="desktop",
        approval_mode="confirm",
        max_steps=1,
        chat_id="owner-chat",
    )
    app_module.runs._runs[owner.id] = run

    sam, sam_headers = _client(stack, "sam")
    try:
        assert sam.get("/api/run").json()["run"] is None
        assert sam.post("/api/run/approve", json={"approved": True}, headers=sam_headers).status_code == 409
        assert sam.post("/api/run/pause", headers=sam_headers).status_code == 409
        assert sam.post("/api/run/stop", headers=sam_headers).status_code == 409
        assert app_module.runs.current(owner.id) is run
    finally:
        sam.__exit__(None, None, None)
        app_module.runs._runs.pop(owner.id, None)


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------


def test_admin_lists_disables_and_deletes_members(stack):
    _seed_owner(stack.factory)
    member = _seed_member(stack.factory, "sam")

    owner, owner_headers = _client(stack, "owner")
    sam, sam_headers = _client(stack, "sam")
    try:
        listing = owner.get("/api/admin/users")
        assert listing.status_code == 200
        names = {user["username"]: user for user in listing.json()["users"]}
        assert names["owner"]["role"] == "admin"
        assert names["sam"]["role"] == "user"

        # A member cannot reach the admin API.
        assert sam.get("/api/admin/users").status_code == 403
        assert (
            sam.post(
                f"/api/admin/users/{member.id}/disable", headers=sam_headers
            ).status_code
            == 403
        )

        assert (
            owner.post(
                f"/api/admin/users/{member.id}/disable", headers=owner_headers
            ).status_code
            == 200
        )
        assert sam.get("/api/chats").status_code == 401  # sessions revoked
        disabled_login = TestClient(app_module.app).post(
            "/api/auth/login", json={"username": "sam", "password": PASSWORD}
        )
        assert disabled_login.status_code == 401

        assert (
            owner.post(
                f"/api/admin/users/{member.id}/enable", headers=owner_headers
            ).status_code
            == 200
        )
        assert (
            owner.delete(
                f"/api/admin/users/{member.id}", headers=owner_headers
            ).status_code
            == 200
        )
        assert "sam" not in {
            user["username"] for user in owner.get("/api/admin/users").json()["users"]
        }

        # The administrator cannot delete or disable itself.
        owner_row = next(
            user
            for user in owner.get("/api/admin/users").json()["users"]
            if user["username"] == "owner"
        )
        assert (
            owner.delete(
                f"/api/admin/users/{owner_row['id']}", headers=owner_headers
            ).status_code
            == 400
        )
    finally:
        owner.__exit__(None, None, None)
        sam.__exit__(None, None, None)


def test_invite_api_returns_a_single_use_link(stack):
    _seed_owner(stack.factory)
    owner, owner_headers = _client(stack, "owner")
    try:
        response = owner.post(
            "/api/admin/invites",
            json={"expires_hours": 24, "label": "sam"},
            headers=owner_headers,
        )
        assert response.status_code == 201, response.text
        code = response.json()["code"]
        assert code in response.json()["url"]

        signup = TestClient(app_module.app).post(
            "/api/auth/signup",
            json={"username": "sam", "password": OTHER_PASSWORD, "invite_code": code},
        )
        assert signup.status_code == 201, signup.text

        replay = TestClient(app_module.app).post(
            "/api/auth/signup",
            json={"username": "sam2", "password": OTHER_PASSWORD, "invite_code": code},
        )
        assert replay.status_code == 400
    finally:
        owner.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Migration and CLI
# ---------------------------------------------------------------------------


def test_single_user_rows_are_claimed_by_the_owner(stack):
    owner = _seed_owner(stack.factory)

    async def seed_orphans():
        async with stack.factory() as session:
            session.add(
                ProviderSetting(user_id=0, provider="gemini", encrypted_key="x")
            )
            session.add(Chat(id="orphan", user_id=0, title="old chat"))
            await session.commit()

    _run(seed_orphans())
    claimed = _run(claim_orphan_rows(stack.factory))
    assert claimed["cowork_chats"] == 1
    assert claimed["provider_settings"] == 1

    async def verify():
        async with stack.factory() as session:
            chat = await session.get(Chat, "orphan")
            row = (
                await session.execute(
                    select(ProviderSetting).where(ProviderSetting.provider == "gemini")
                )
            ).scalars().first()
            return chat.user_id, row.user_id

    assert _run(verify()) == (owner.id, owner.id)
    # Idempotent: a second run claims nothing.
    assert _run(claim_orphan_rows(stack.factory)) == {}


def test_owner_and_member_share_one_users_table(stack):
    owner = _seed_owner(stack.factory)
    member = _seed_member(stack.factory, "sam")

    async def read():
        async with stack.factory() as session:
            rows = (await session.execute(select(User))).scalars().all()
            return [(row.id, row.username, row.role) for row in rows]

    assert _run(read()) == [
        (owner.id, "owner", "admin"),
        (member.id, "sam", "user"),
    ]


def test_cli_signin_url_uses_public_url_then_proxy_port(monkeypatch):
    monkeypatch.setattr(server_cli.settings, "PUBLIC_BASE_URL", "https://agent.example.com")
    assert server_cli._signin_url() == "https://agent.example.com/"

    monkeypatch.setattr(server_cli.settings, "PUBLIC_BASE_URL", None)
    monkeypatch.setattr(server_cli.settings, "PORT", 8765)
    monkeypatch.setenv("PROXY_PORT", "8080")
    assert server_cli._signin_url() == "http://127.0.0.1:8080/"

    monkeypatch.delenv("PROXY_PORT")
    assert server_cli._signin_url() == "http://127.0.0.1:8765/"


def test_compose_defaults_are_multi_user_safe():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    server_block = compose.split("  caddy:", 1)[0]
    assert "SIGNUP_MODE: ${SIGNUP_MODE:-invite}" in server_block
    assert "REMOTE_NODE_MODE: ${REMOTE_NODE_MODE:-required}" in server_block
    assert "\n    ports:" not in server_block
    assert "USER_RUN_LIMIT_PER_HOUR" in server_block


def test_docs_cover_node_install_limits_and_open_signup_warning():
    node_docs = (REPO_ROOT / "docs" / "node.md").read_text(encoding="utf-8")
    assert "NODE_APPROVAL_MODE=risky" in node_docs
    assert "Wayland" in node_docs
    assert "macOS" in node_docs and "Windows" in node_docs
    assert "systemctl --user enable --now occ-node" in node_docs

    docker_docs = (REPO_ROOT / "docs" / "docker.md").read_text(encoding="utf-8")
    assert "SIGNUP_MODE" in docker_docs
    assert "private network" in docker_docs.lower()
    assert "deploy/secrets/fernet.key" in docker_docs


def test_password_storage_stays_argon2id(stack):
    member = _seed_member(stack.factory, "sam")

    async def read():
        async with stack.factory() as session:
            return (await session.get(User, member.id)).password_hash

    assert _run(read()).startswith("$argon2id$")


def test_blank_user_limits_from_compose_defaults_are_accepted():
    """Compose renders unset numeric limits as an empty string, not as unset."""
    from server.config import Settings

    parsed = Settings(USER_USAGE_CAP_USD="", USER_RUN_LIMIT_PER_HOUR="")
    assert parsed.USER_USAGE_CAP_USD is None
    assert parsed.USER_RUN_LIMIT_PER_HOUR == 30
