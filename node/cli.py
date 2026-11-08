"""``python -m node.cli`` — run and control the local desktop node."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import click

import node  # noqa: F401  (sets up sys.path for `shared`)
from node.config import (
    DEFAULT_CONTROL_PORT,
    ConfigError,
    NodeConfig,
    load_config,
    normalise_server_url,
    save_config,
    session_info,
)
from node.safety import SafetyState, read_pid, read_secret_file, write_secret_file


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option("1.0.0", prog_name="occ-node")
def cli() -> None:
    """Open Computer Use — local desktop node."""


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--server", envvar="OCC_NODE_SERVER", default=None, help="Server URL, e.g. https://agent.example.com")
@click.option("--token", envvar="OCC_NODE_TOKEN", default=None, help="Node token from the server UI")
@click.option("--token-file", type=click.Path(dir_okay=False), default=None, help="Read the token from a file")
@click.option("--name", default=None, help="Friendly node name (default: hostname)")
@click.option("--data-dir", default=None, type=click.Path(file_okay=False), help="State directory (default ./node-data)")
@click.option("--backend", type=click.Choice(["desktop", "browser"]), default=None, help="What this node controls")
@click.option("--control-port", type=int, default=None, help=f"Loopback control page port (default {DEFAULT_CONTROL_PORT})")
@click.option("--headless/--headed", default=None, help="Browser backend window mode")
@click.option("--policy", type=click.Path(dir_okay=False), default=None, help="JSON policy file (allowlists, rate limit, audit)")
@click.option("--indicator/--no-indicator", default=True, help="Show a corner overlay while the agent is in control")
@click.option("--no-control-page", is_flag=True, help="Do not start the local control page")
@click.option("--no-hotkeys", is_flag=True, help="Do not register the global hotkeys")
@click.option("--verbose", is_flag=True, help="Debug logging")
def run(
    server: Optional[str],
    token: Optional[str],
    token_file: Optional[str],
    name: Optional[str],
    data_dir: Optional[str],
    backend: Optional[str],
    control_port: Optional[int],
    headless: Optional[bool],
    policy: Optional[str],
    indicator: bool,
    no_control_page: bool,
    no_hotkeys: bool,
    verbose: bool,
) -> None:
    """Connect this machine to the server and start accepting commands."""
    _configure_logging(verbose)
    from node.audit import AuditLog
    from node.client import NodeClient
    from node.control import ControlServer
    from node.hotkey import start_hotkeys
    from node.indicator import ScreenIndicator
    from node.policy import NodePolicy

    # `connect` saves these, so `run` needs no arguments after setup.
    saved = load_config(
        Path(data_dir or os.environ.get("OCC_NODE_DATA_DIR") or "./node-data").expanduser()
    )
    server = server or saved.get("server") or None
    if not token_file:
        token = token or saved.get("token") or None
    name = name or saved.get("name") or None
    backend = backend or saved.get("backend") or None

    try:
        config = NodeConfig.from_env(
            server=server,
            token=token,
            token_file=token_file,
            name=name,
            data_dir=data_dir,
            backend=backend,
            control_port=control_port,
            headless=headless,
            policy=policy,
            indicator=indicator,
        )
        node_policy = NodePolicy.from_env(path=policy)
        config.ensure_dirs()
        url = config.websocket_url
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    audit = AuditLog(config.audit_path, enabled=node_policy.audit)
    safety = SafetyState()
    client = NodeClient(config, safety, policy=node_policy, audit=audit)

    control: Optional[ControlServer] = None
    if not no_control_page:
        try:
            control = ControlServer(
                safety,
                client.status,
                host=config.control_host,
                port=config.control_port,
                audit=audit,
            )
            control.start()
            control.write_token_file(config.control_token_path)
            click.echo(
                click.style(
                    f"Control page: {control.url}\n"
                    "(loopback only — pause, resume or stop this node from here)",
                    fg="bright_black",
                )
            )
        except OSError as exc:
            click.echo(
                click.style(f"Control page unavailable on {config.control_host}:{config.control_port} ({exc}).", fg="yellow")
            )
            control = None

    write_secret_file(config.pid_path, str(os.getpid()))

    info = session_info()
    click.echo(click.style(f"\nOpen Computer Use node · {config.label}", fg="cyan", bold=True))
    click.echo(f"Server     {url}")
    click.echo(f"Backend    {config.backend}")
    click.echo(f"Session    {info['type']} · {info['note']}")
    if not info["supported"] and config.backend == "desktop":
        click.echo(click.style("           Desktop control will not work in this session.", fg="yellow"))
    click.echo(f"Policy     {node_policy.summary()} ({node_policy.source})")
    if node_policy.audit:
        click.echo(f"Audit      {config.audit_path}")
    click.echo("")

    listener = None if no_hotkeys else start_hotkeys(safety, on_stop=client.request_stop)
    screen_indicator = ScreenIndicator(client.status, enabled=config.indicator)
    if screen_indicator.start():
        click.echo(
            click.style(
                "Indicator  a corner overlay shows when the agent is in control",
                fg="bright_black",
            )
        )

    async def _main() -> int:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: client.request_stop("Signal received."))
            except NotImplementedError:  # pragma: no cover - Windows
                pass
        try:
            return await client.run()
        finally:
            await client.close()

    exit_code = 0
    try:
        exit_code = asyncio.run(_main())
    except KeyboardInterrupt:  # pragma: no cover - handled by the signal handler
        exit_code = 130
    finally:
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # pragma: no cover - optional dependency
                pass
        if control is not None:
            control.stop()
        screen_indicator.stop()
        try:
            if read_pid(config.pid_path) == os.getpid():
                config.pid_path.unlink(missing_ok=True)
        except OSError:
            pass
    raise SystemExit(exit_code)


# ---------------------------------------------------------------------------
# status / stop
# ---------------------------------------------------------------------------


def _control_request(config: NodeConfig, method: str, path: str) -> dict:
    token = read_secret_file(config.control_token_path)
    if not token:
        raise click.ClickException(
            "No control token found. Is the node running with its control page enabled?"
        )
    request = urllib.request.Request(
        f"http://{config.control_host}:{config.control_port}{path}",
        method=method,
        headers={"X-Node-Token": token},
        data=b"" if method == "POST" else None,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _offline_config(data_dir: Optional[str], control_port: Optional[int]) -> NodeConfig:
    directory = Path(data_dir or os.environ.get("OCC_NODE_DATA_DIR") or "./node-data").expanduser()
    return NodeConfig(
        server_url=os.environ.get("OCC_NODE_SERVER") or "ws://localhost",
        token="unused-for-local-control",
        data_dir=directory,
        control_port=control_port or DEFAULT_CONTROL_PORT,
    )


@cli.command()
@click.option("--data-dir", default=None, type=click.Path(file_okay=False))
@click.option("--control-port", type=int, default=None)
def status(data_dir: Optional[str], control_port: Optional[int]) -> None:
    """Show the running node's state."""
    config = _offline_config(data_dir, control_port)
    try:
        payload = _control_request(config, "GET", "/api/status")
    except (urllib.error.URLError, OSError) as exc:
        pid = read_pid(config.pid_path)
        detail = f" (pid {pid})" if pid else ""
        raise click.ClickException(f"The node control page is not reachable{detail}: {exc}") from exc
    state = "stopped" if payload.get("stopped") else "paused" if payload.get("paused") else "running"
    click.echo(f"State      {state}")
    click.echo(f"Node       {payload.get('node_id') or '—'} ({payload.get('name') or '—'})")
    click.echo(f"Server     {payload.get('server') or '—'}")
    click.echo(f"Connected  {'yes' if payload.get('connected') else 'no'}")
    click.echo(f"Backend    {payload.get('backend') or '—'}")
    click.echo(f"Commands   {payload.get('commands_run', 0)}")
    if payload.get("last_error"):
        click.echo(click.style(f"Last error {payload['last_error']}", fg="yellow"))


@cli.command()
@click.option("--data-dir", default=None, type=click.Path(file_okay=False))
@click.option("--control-port", type=int, default=None)
def stop(data_dir: Optional[str], control_port: Optional[int]) -> None:
    """Stop this node (kill switch; does not need the server)."""
    config = _offline_config(data_dir, control_port)
    try:
        _control_request(config, "POST", "/api/stop")
        click.echo(click.style("Node stopped.", fg="green"))
        return
    except (urllib.error.URLError, OSError) as exc:
        pid = read_pid(config.pid_path)
        if pid is None:
            raise click.ClickException(f"Could not stop the node: {exc}") from exc
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise click.ClickException(f"Could not signal node process {pid}: {exc}") from exc
    click.echo(click.style(f"Sent SIGTERM to node process {pid}.", fg="green"))


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--server", envvar="OCC_NODE_SERVER", default=None)
@click.option("--backend", type=click.Choice(["desktop", "browser"]), default="desktop")
def doctor(server: Optional[str], backend: str) -> None:
    """Check this machine before starting the node."""
    click.echo(click.style("\nOpen Computer Use node doctor\n", fg="cyan", bold=True))
    click.echo(f"Python     {sys.version.split()[0]} ({sys.platform})")

    if server:
        try:
            click.echo(f"Server URL {normalise_server_url(server)}")
        except ConfigError as exc:
            click.echo(click.style(f"Server URL invalid — {exc}", fg="red"))
    else:
        click.echo(click.style("Server URL not set (OCC_NODE_SERVER)", fg="yellow"))

    info = session_info()
    colour = "green" if info["supported"] else "yellow"
    click.echo(click.style(f"Session    {info['type']} — {info['note']}", fg=colour))
    if info["display"]:
        click.echo(f"Display    {info['display']}")

    if backend == "desktop":
        try:
            from node.drivers.desktop import DesktopComputer

            ok, reason = DesktopComputer.available()
        except Exception as exc:  # pragma: no cover - host dependent
            ok, reason = False, str(exc)
        click.echo(
            click.style(f"Desktop    {'available' if ok else 'unavailable'} — {reason}", fg="green" if ok else "yellow")
        )
    else:
        try:
            import playwright  # noqa: F401

            click.echo(click.style("Browser    Playwright is installed", fg="green"))
        except Exception as exc:
            click.echo(click.style(f"Browser    Playwright unavailable — {exc}", fg="yellow"))
        else:
            from node.drivers.browser import display_mode

            mode = display_mode()
            if mode == "x11":
                click.echo(
                    click.style(
                        "Display    Chromium uses the X11 (XWayland) platform, so PyAutoGUI "
                        "and screenshots can see the window",
                        fg="green",
                    )
                )
            elif mode == "headless":
                click.echo(click.style("Display    headless: no browser window is shown", fg="bright_black"))
            else:
                click.echo(
                    click.style(
                        "Display    no DISPLAY — Chromium would run headless and no window exists",
                        fg="yellow",
                    )
                )
            cache = Path.home() / ".cache/ms-playwright"
            chromium = sorted(cache.glob("chromium-*")) if cache.is_dir() else []
            if chromium:
                click.echo(click.style("Chromium   Playwright's Chromium is installed", fg="green"))
            else:
                click.echo(
                    click.style(
                        "Chromium   not found — run: playwright install chromium", fg="yellow"
                    )
                )

    try:
        import pynput  # noqa: F401

        click.echo(click.style("Hotkeys    pynput is installed", fg="green"))
    except Exception:
        click.echo(click.style("Hotkeys    optional: pip install pynput for a global kill switch", fg="bright_black"))
    if info["type"] == "wayland":
        click.echo(
            click.style(
                "           Best-effort under Wayland: a focused native Wayland window can swallow "
                "hotkeys. Use `python -m node.cli stop` or the control page to stop reliably.",
                fg="yellow",
            )
        )

    try:
        from node.policy import NodePolicy

        node_policy = NodePolicy.from_env()
        click.echo(f"Policy     {node_policy.summary()} ({node_policy.source})")
        data_dir = Path(os.environ.get("OCC_NODE_DATA_DIR") or "./node-data")
        click.echo(f"Audit      {data_dir / 'audit.jsonl'}" if node_policy.audit else "Audit      disabled")
    except ConfigError as exc:
        click.echo(click.style(f"Policy     invalid — {exc}", fg="red"))

    token = read_secret_file(Path(os.environ.get("OCC_NODE_DATA_DIR") or "./node-data") / "node.id")
    if token:
        click.echo(f"Node id    {token}")
    click.echo("")


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


def _install_autostart(data_dir: Path, backend: Optional[str]) -> None:
    """Best-effort per-user auto-start: systemd, launchd, or a printed task."""
    import platform
    import shlex

    python = shlex.quote(sys.executable)
    directory = shlex.quote(str(data_dir))
    command = f"{python} -m node.cli run --data-dir {directory}"
    system = platform.system()
    if system == "Linux":
        unit_dir = Path.home() / ".config/systemd/user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit = unit_dir / "occ-node.service"
        unit.write_text(
            "[Unit]\n"
            "Description=Open Computer Use node\n"
            "After=graphical-session.target\n\n"
            "[Service]\n"
            f"WorkingDirectory={Path.home()}\n"
            f"ExecStart={command}\n"
            "Restart=on-failure\n"
            "RestartSec=10\n\n"
            "[Install]\n"
            "WantedBy=default.target\n",
            encoding="utf-8",
        )
        click.echo(f"Auto-start unit written to {unit}")
        click.echo("Enable it with: systemctl --user daemon-reload && systemctl --user enable --now occ-node")
        click.echo("Logs:            journalctl --user -u occ-node -f")
    elif system == "Darwin":
        plist_dir = Path.home() / "Library/LaunchAgents"
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist = plist_dir / "com.opencomputeruse.node.plist"
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            "  <key>Label</key><string>com.opencomputeruse.node</string>\n"
            "  <key>ProgramArguments</key><array>\n"
            f"    <string>{sys.executable}</string><string>-m</string>"
            "<string>node.cli</string><string>run</string>\n"
            f"    <string>--data-dir</string><string>{data_dir}</string>\n"
            "  </array>\n"
            "  <key>RunAtLoad</key><true/>\n"
            "  <key>KeepAlive</key><true/>\n"
            "</dict></plist>\n",
            encoding="utf-8",
        )
        click.echo(f"Auto-start agent written to {plist}")
        click.echo(f"Load it with: launchctl load -w {plist}")
        click.echo("macOS will ask for Screen Recording and Accessibility permission the first time.")
    else:
        click.echo("Auto-start on Windows: create a Task Scheduler task at logon that runs")
        click.echo(f"  {command}")
        click.echo("or run the node from a terminal in your desktop session.")


@cli.command()
@click.option("--server", required=True, help="Server URL, e.g. https://agent.example.com")
@click.option("--token", required=True, help="Node token shown once by My computers")
@click.option("--name", default=None, help="Name shown in My computers (default: hostname)")
@click.option("--data-dir", default=None, type=click.Path(file_okay=False))
@click.option("--backend", type=click.Choice(["desktop", "browser"]), default="desktop")
@click.option("--auto-start", is_flag=True, help="Install a per-user service that starts at login")
def connect(
    server: str,
    token: str,
    name: Optional[str],
    data_dir: Optional[str],
    backend: str,
    auto_start: bool,
) -> None:
    """Link this computer to your account. Run it once, then `occ-node run`."""
    try:
        websocket_url = normalise_server_url(server)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    token = (token or "").strip()
    if len(token) < 16:
        raise click.ClickException(
            "That token looks too short. Copy it again from My computers; it is shown once."
        )
    directory = Path(
        data_dir or os.environ.get("OCC_NODE_DATA_DIR") or "./node-data"
    ).expanduser()
    path = save_config(
        directory,
        {
            "server": server.strip(),
            "token": token,
            "name": name or socket.gethostname(),
            "backend": backend,
        },
    )
    click.echo(click.style("This computer is linked.", fg="green", bold=True))
    click.echo(f"Config   {path} (mode 600)")
    click.echo(f"Server   {websocket_url}")
    click.echo(f"Name     {name or socket.gethostname()}")
    click.echo("")
    click.echo("Start it now:  python -m node.cli run")
    click.echo("Check it:      python -m node.cli status")
    click.echo("Panic stop:    python -m node.cli stop")
    if auto_start:
        _install_autostart(directory, backend)


if __name__ == "__main__":
    cli()
