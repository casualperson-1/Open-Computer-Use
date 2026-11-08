"""End-to-end compose smoke test: two invited users, one fake node.

Opt-in because it builds and starts containers:

    OCU_COMPOSE_SMOKE=1 .venv/bin/python -m pytest tests/test_compose_e2e.py -q

It uses its own compose project (`ocu-e2e`) and port 18080 so it cannot touch a
running stack. The fake node answers `screenshot` and proves that only the
account that owns it can reach that computer.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
import websockets
from PIL import Image

from shared.protocol import (
    CommandMessage,
    NodeHello,
    ResultMessage,
    decode_message,
    encode_message,
)

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "ocu-e2e"
PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/ws/node"
OWNER_PASSWORD = "Correct-Horse-Battery-Staple-9"
MEMBER_PASSWORD = "Another-Long-Passphrase-42"

pytestmark = pytest.mark.skipif(
    os.environ.get("OCU_COMPOSE_SMOKE") != "1" or shutil.which("docker") is None,
    reason="set OCU_COMPOSE_SMOKE=1 to run the container end-to-end test",
)


def compose(*args: str, timeout: int = 240) -> subprocess.CompletedProcess:
    env = {**os.environ, "PROXY_PORT": str(PORT), "COMPOSE_PROJECT_NAME": PROJECT}
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *args],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def png_data() -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (20, 40, 80)).save(buffer, format="PNG")
    return {
        "png_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "width": 64,
        "height": 48,
        "screen_width": 64,
        "screen_height": 48,
        "cursor": [1, 1],
    }


async def wait_for_health(client: httpx.AsyncClient, timeout: float = 90.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            response = await client.get(f"{BASE}/api/health", timeout=3)
            if response.status_code == 200:
                return
        except Exception:
            pass
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("the stack did not become healthy")
        await asyncio.sleep(1)


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def headers(csrf: str, origin: str = BASE) -> dict:
    return {"Origin": origin, "X-CSRF-Token": csrf}


async def invite_and_signup(owner: httpx.AsyncClient, owner_csrf: str, name: str):
    invite = await owner.post(
        "/api/admin/invites",
        json={"expires_hours": 1, "label": name},
        headers=headers(owner_csrf),
    )
    assert invite.status_code == 201, invite.text
    code = invite.json()["code"]

    member = httpx.AsyncClient(base_url=BASE, timeout=30)
    signup = await member.post(
        "/api/auth/signup",
        json={"username": name, "password": MEMBER_PASSWORD, "invite_code": code},
    )
    assert signup.status_code == 201, signup.text
    csrf = signup.json()["csrf_token"]
    return member, csrf


async def fake_node(token: str, seen: asyncio.Event) -> None:
    async with websockets.connect(WS, open_timeout=20) as socket:
        await socket.send(
            encode_message(
                NodeHello(
                    token=token,
                    name="e2e",
                    backend="desktop",
                    platform="Linux",
                )
            )
        )
        welcome = decode_message(await socket.recv())
        assert welcome.type == "welcome", welcome
        async for raw in socket:
            message = decode_message(raw)
            if isinstance(message, CommandMessage):
                assert message.name == "screenshot", message.name
                seen.set()
                await socket.send(
                    encode_message(
                        ResultMessage(id=message.id, ok=True, data=png_data())
                    )
                )


async def test_compose_two_users_one_node_routing():
    compose("down", "--remove-orphans", "-v")

    secrets = ROOT / "deploy" / "secrets"
    if not (secrets / "fernet.key").exists():
        subprocess.run(
            [str(ROOT / "deploy" / "generate-secrets.sh")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    compose("build", "server")
    compose(
        "run",
        "--rm",
        "-T",
        "-e",
        f"OCU_OWNER_PASSWORD={OWNER_PASSWORD}",
        "server",
        "create-owner",
        "--password-env",
        "OCU_OWNER_PASSWORD",
    )
    compose("--profile", "proxy", "up", "-d")

    owner = httpx.AsyncClient(base_url=BASE, timeout=30)
    sam = alex = None
    node_task = None
    seen = asyncio.Event()
    try:
        await wait_for_health(owner)
        owner_csrf = await login(owner, "owner", OWNER_PASSWORD)

        sam, sam_csrf = await invite_and_signup(owner, owner_csrf, "sam")
        alex, _ = await invite_and_signup(owner, owner_csrf, "alex")

        node = await sam.post("/api/nodes", json={"name": "sam-pc"}, headers=headers(sam_csrf))
        assert node.status_code == 201, node.text
        token = node.json()["token"]

        node_task = asyncio.create_task(fake_node(token, seen))
        for _ in range(40):
            screen = await sam.get("/api/screen")
            if screen.status_code == 200:
                break
            await asyncio.sleep(0.5)
        assert screen.status_code == 200, screen.text
        assert screen.json()["data"].startswith("data:image/")
        assert seen.is_set(), "the fake node never received a screenshot command"

        # The other member has no computer and can never borrow sam's.
        alex_screen = await alex.get("/api/screen")
        assert alex_screen.status_code == 409, alex_screen.text
        assert "Connect your computer first" in alex_screen.text

        alex_nodes = await alex.get("/api/nodes")
        owner_nodes = await owner.get("/api/nodes")
        assert alex_nodes.json()["nodes"] == []
        assert owner_nodes.json()["nodes"] == []
        assert len((await sam.get("/api/nodes")).json()["nodes"]) == 1
    finally:
        if node_task is not None:
            node_task.cancel()
            try:
                await node_task
            except (asyncio.CancelledError, Exception):
                pass
        for client in (sam, alex, owner):
            if client is not None:
                await client.aclose()
        compose("--profile", "proxy", "down", "-v", "--remove-orphans")
