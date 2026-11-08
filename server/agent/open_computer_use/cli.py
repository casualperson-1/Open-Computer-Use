"""Command line interface: serve the local app, run one task, or check the host."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform as platform_module
import sys
from pathlib import Path
from typing import Optional

import click

from ..computer import ComputerToolbox, computer_availability, create_computer
from ..providers import available_providers, get_provider, normalise_provider
from ..providers.factory import PROVIDER_CLASSES, provider_env_key, provider_requires_key
from .loop import OpenComputerUseAgent, RunControl
BACKEND_DIR = Path(__file__).resolve().parents[2]


@click.group()
@click.version_option("0.2.0", prog_name="open-computer-use")
def cli() -> None:
    """Open Computer Use — a self-hosted desktop agent that uses your mouse and keyboard."""


@cli.command()
@click.option("--host", default=None, help="Bind address (default: 127.0.0.1).")
@click.option("--port", default=None, type=int, help="Port (default: 8765).")
@click.option("--reload", is_flag=True, help="Reload the server on code changes.")
def serve(host: Optional[str], port: Optional[int], reload: bool) -> None:
    """Serve the Open Computer Use web app on localhost."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    import uvicorn

    from config import settings
    from security.network import BindRefused, assert_bind_allowed

    bind_host = host or settings.HOST
    bind_port = port or settings.PORT
    try:
        assert_bind_allowed(
            bind_host,
            owner_exists=_owner_configured(),
            allow_remote_bind=settings.ALLOW_REMOTE_BIND,
        )
    except BindRefused as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(click.style(f"\nOpen Computer Use is starting at http://{bind_host}:{bind_port}\n", fg="cyan", bold=True))
    uvicorn.run(
        "app:app",
        host=bind_host,
        port=bind_port,
        reload=reload,
        app_dir=str(BACKEND_DIR),
        log_level="info",
    )


def _owner_configured() -> bool:
    """Whether an owner exists, creating the schema if needed."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from config import settings
    from database.models import Base
    from security.owner import owner_exists, prepare_database

    async def check() -> bool:
        engine = create_async_engine(settings.database_url)
        try:
            await prepare_database(engine)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            return await owner_exists(factory)
        finally:
            await engine.dispose()

    return asyncio.run(check())


def _build_provider(
    provider_name: str,
    model: Optional[str],
    api_key: Optional[str],
    base_url: Optional[str],
):
    provider_id = normalise_provider(provider_name)
    if provider_id not in PROVIDER_CLASSES or provider_id == "mock":
        raise click.ClickException(f"Unknown provider '{provider_name}'.")
    key = api_key or os.environ.get(provider_env_key(provider_id), "")
    if provider_requires_key(provider_id) and not key:
        raise click.ClickException(
            f"No API key for {provider_id}. Pass --api-key or set {provider_env_key(provider_id)}."
        )
    return get_provider(
        provider_id,
        api_key=key,
        model=model,
        base_url=base_url,
    )


@cli.command("run")
@click.argument("goal")
@click.option("--provider", default=None, help="anthropic|openai|gemini|deepseek|xai|openrouter|groq|nvidia|mistral|together|fireworks|cerebras|perplexity|ollama|openai-compatible")
@click.option("--model", default=None, help="Model id, e.g. gpt-4o or qwen2.5vl:7b")
@click.option("--api-key", default=None, envvar=None, help="BYO API key (or use PROVIDER_API_KEY env vars)")
@click.option("--base-url", default=None, help="Custom endpoint (OpenAI-compatible or Ollama)")
@click.option("--max-steps", default=30, type=int, show_default=True)
@click.option("--max-cost", default=1.0, type=float, show_default=True)
@click.option("--confirm", is_flag=True, help="Ask before every action.")
@click.option("--simulated", "--dry-run", "simulated", is_flag=True, help="Use the safe simulated desktop.")
@click.option("--fast", is_flag=True, help="Disable human-like mouse animation.")
@click.option("--screenshots", default=None, type=click.Path(file_okay=False), help="Save every screenshot to this folder.")
def run_goal(
    goal: str,
    provider: Optional[str],
    model: Optional[str],
    api_key: Optional[str],
    base_url: Optional[str],
    max_steps: int,
    max_cost: float,
    confirm: bool,
    simulated: bool,
    fast: bool,
    screenshots: Optional[str],
) -> None:
    """Run a single task on the desktop."""
    from config import settings

    provider_id = provider or settings.DEFAULT_PROVIDER
    instance = _build_provider(provider_id, model, api_key, base_url)

    click.echo(click.style(f"\nOpen Computer Use · {instance.label} · {instance.model}", fg="cyan", bold=True))
    click.echo(click.style(f"Goal: {goal}\n", fg="white"))

    exit_code = asyncio.run(
        _run_once(
            goal=goal,
            provider=instance,
            max_steps=max_steps,
            max_cost=max_cost,
            confirm=confirm,
            simulated=simulated,
            fast=fast,
            screenshots=screenshots,
        )
    )
    raise SystemExit(exit_code)


async def _run_once(
    goal: str,
    provider,
    max_steps: int,
    max_cost: float,
    confirm: bool,
    simulated: bool,
    fast: bool,
    screenshots: Optional[str],
) -> int:
    from config import settings

    if simulated:
        computer = create_computer(
            backend="simulated",
            max_width=settings.SCREEN_MAX_WIDTH,
            human_mouse=not fast,
        )
    else:
        # A one-off local run drives *this* machine, so it uses the node drivers
        # directly instead of the server's remote/simulated factory.
        from node.drivers import create_driver

        computer = create_driver(
            settings.COMPUTER,
            max_width=settings.SCREEN_MAX_WIDTH,
            human_mouse=not fast,
            user_data_dir=str(settings.data_path / "browser_profile")
            if settings.COMPUTER == "browser"
            else None,
        )
    click.echo(
        click.style(
            f"Computer: {computer.describe().get('name', computer.backend)} "
            f"({'simulated — no real input' if computer.backend == 'simulated' else 'real input'})\n",
            fg="bright_black",
        )
    )

    shot_dir = Path(screenshots).expanduser() if screenshots else None
    if shot_dir:
        shot_dir.mkdir(parents=True, exist_ok=True)
    shot_count = 0
    control = RunControl()

    async def on_event(event: dict) -> None:
        nonlocal shot_count
        kind = event.get("type")
        if kind == "assistant" and event.get("text"):
            click.echo(click.style(f"  {event['text']}", fg="bright_black"))
        elif kind == "action":
            args = json.dumps(event.get("arguments") or {}, ensure_ascii=False)
            click.echo(click.style(f"  → {event.get('tool')} {args[:120]}", fg="cyan"))
        elif kind == "observation":
            mark = "✓" if event.get("ok") else "✗"
            color = "green" if event.get("ok") else "yellow"
            click.echo(click.style(f"    {mark} {event.get('text')}", fg=color))
        elif kind == "screenshot" and shot_dir:
            data = event.get("data") or ""
            if "," in data:
                header, encoded = data.split(",", 1)
                extension = ".jpg" if "jpeg" in header else ".png"
                shot_count += 1
                (shot_dir / f"step-{shot_count:03d}{extension}").write_bytes(base64.b64decode(encoded))
        elif kind == "ask_user":
            click.echo(click.style(f"\n  ? {event.get('question')}", fg="magenta"))
        elif kind == "error":
            click.echo(click.style(f"\n  error: {event.get('message')}", fg="red"))

    async def request_approval(call) -> bool:
        prompt = f"Allow {call.name} {json.dumps(call.arguments, ensure_ascii=False)}?"
        return await asyncio.to_thread(click.confirm, prompt, default=True)

    async def ask_user(question: str) -> Optional[str]:
        return await asyncio.to_thread(
            click.prompt, "Your reply (blank to stop)", default="", show_default=False
        ) or None

    agent = OpenComputerUseAgent(
        provider=provider,
        toolbox=ComputerToolbox(computer),
        control=control,
        max_steps=max_steps,
        max_cost=max_cost,
        on_event=on_event,
        request_approval=request_approval if confirm else None,
        ask_user=ask_user,
    )

    try:
        result = await agent.run(goal)
    except KeyboardInterrupt:
        control.stop()
        click.echo(click.style("\nStopped.", fg="yellow"))
        return 130

    color = "green" if result.status == "completed" else "yellow"
    if result.status in ("failed", "error"):
        color = "red"
    click.echo(click.style(f"\n{result.status.upper()}: {result.summary}", fg=color, bold=True))
    if result.error:
        click.echo(click.style(f"Error: {result.error}", fg="red"))
    click.echo(
        click.style(
            f"{result.steps} steps · ${result.cost:.4f} · "
            f"{result.tokens_in} in / {result.tokens_out} out tokens",
            fg="bright_black",
        )
    )
    if shot_dir:
        click.echo(click.style(f"Screenshots saved to {shot_dir}", fg="bright_black"))
    return 0 if result.status == "completed" else 1


@cli.command()
def doctor() -> None:
    """Check desktop access, provider keys and environment."""
    from config import settings

    click.echo(click.style("\nOpen Computer Use doctor\n", fg="cyan", bold=True))
    click.echo(f"Python      {sys.version.split()[0]} ({platform_module.system()})")
    click.echo(f"Platform    {platform_module.platform()}")
    click.echo(f"Data dir    {settings.data_path.resolve()}")
    click.echo(f"Server      http://{settings.HOST}:{settings.PORT}")

    from node.config import session_info
    from node.drivers.desktop import DesktopComputer

    session = session_info()
    click.echo(
        click.style(
            f"Session     {session['type']} — {session['note']}",
            fg="green" if session["supported"] else "yellow",
        )
    )
    desktop_ok, desktop_reason = DesktopComputer.available()
    if desktop_ok:
        click.echo(click.style("Desktop     available (real mouse and keyboard)", fg="green"))
        try:
            import pyautogui

            size = pyautogui.size()
            click.echo(f"Screen      {size.width}x{size.height}")
        except Exception as exc:
            click.echo(click.style(f"Screen      could not read ({exc})", fg="yellow"))
    else:
        click.echo(click.style(f"Desktop     unavailable — {desktop_reason}", fg="yellow"))
        click.echo("            Start the node agent on a machine with a desktop session.")

    click.echo("\nProviders (BYOK):")
    for meta in available_providers():
        env_name = provider_env_key(meta["id"])
        has_env = bool(os.environ.get(env_name))
        key_state = click.style("env key set", fg="green") if has_env else click.style("no env key", fg="bright_black")
        vision = "vision" if meta["has_vision"] else "text-only"
        click.echo(
            f"  {meta['label']:<22} {meta['default_model']:<26} {vision:<10} {key_state}"
        )

    click.echo("\nKeys can also be saved (encrypted) in the web UI at Settings.")
    if availability["desktop_available"]:
        click.echo(click.style("Tip: move the cursor into a screen corner for an emergency stop.", fg="bright_black"))
    click.echo("")


if __name__ == "__main__":
    cli()
