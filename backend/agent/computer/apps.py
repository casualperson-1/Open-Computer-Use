"""Installed-app discovery and default-app launching.

The agent uses this module to behave like the Windows "How do you want to open
this file?" dialog: a file type has a saved app, and the first time a new type
is opened the human picks an app and can choose to always use it.
"""

from __future__ import annotations

import mimetypes
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

_FIELD_CODES = (
    "%f", "%F", "%u", "%U", "%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m",
)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:[/:?#].*)?$", re.I)
_LOCAL_RE = re.compile(
    r"^(?:localhost|(?:\d{1,3}\.){3}\d{1,3})(?::\d{1,5})?(?:[/?#].*)?$", re.I
)
_SPECIAL_KINDS = {"url", "folder", "file", "app"}
_KIND_LABELS = {"url": "web links", "folder": "folders", "file": "files"}

CACHE_SECONDS = 45.0
_cache: Dict[str, Any] = {"at": 0.0, "apps": []}


@dataclass
class InstalledApp:
    """One launchable application."""

    id: str
    name: str
    command: str
    categories: str = ""
    mime_types: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "categories": self.categories,
            "mime_types": list(self.mime_types[:12]),
        }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _desktop_dirs() -> List[Path]:
    home = Path.home()
    candidates = [
        home / ".local/share/applications",
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
        home / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ]
    seen = set()
    directories: List[Path] = []
    for path in candidates:
        resolved = str(path)
        if resolved in seen or not path.is_dir():
            continue
        seen.add(resolved)
        directories.append(path)
    return directories


def parse_desktop_entry(path: Path) -> Optional[InstalledApp]:
    """Parse a freedesktop ``.desktop`` file into an app, or None if hidden."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    data: Dict[str, str] = {}
    in_entry = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data.setdefault(key.strip(), value.strip())

    if data.get("Type", "Application") != "Application":
        return None
    if data.get("Hidden", "").lower() == "true" or data.get("NoDisplay", "").lower() == "true":
        return None
    name = data.get("Name", "").strip()
    command = data.get("Exec", "").strip()
    if not name or not command:
        return None

    mimes = [item.strip() for item in data.get("MimeType", "").split(";") if item.strip()]
    return InstalledApp(
        id=path.stem,
        name=name,
        command=command,
        categories=data.get("Categories", ""),
        mime_types=mimes,
    )


def _mac_apps(directory: Path) -> List[InstalledApp]:
    apps: List[InstalledApp] = []
    try:
        bundles = sorted(directory.glob("*.app"))
    except OSError:
        return apps
    for bundle in bundles:
        name = bundle.stem
        apps.append(
            InstalledApp(
                id=f"mac:{name}",
                name=name,
                command=f"open -a {shlex.quote(name)}",
            )
        )
    return apps


def installed_apps(refresh: bool = False) -> List[InstalledApp]:
    """Return the launchable apps on this machine (cached briefly)."""
    now = time.monotonic()
    cached: List[InstalledApp] = _cache["apps"]
    if not refresh and cached and now - float(_cache["at"]) < CACHE_SECONDS:
        return list(cached)

    apps: List[InstalledApp] = []
    seen = set()

    if platform.system() == "Darwin":
        for directory in (
            Path("/Applications"),
            Path.home() / "Applications",
            Path("/System/Applications"),
        ):
            for app in _mac_apps(directory):
                if app.id not in seen:
                    seen.add(app.id)
                    apps.append(app)
    else:
        for directory in _desktop_dirs():
            try:
                paths = sorted(directory.glob("*.desktop"))
            except OSError:
                continue
            for path in paths:
                app = parse_desktop_entry(path)
                if app is None or app.id in seen:
                    continue
                seen.add(app.id)
                apps.append(app)

    apps.sort(key=lambda item: item.name.lower())
    _cache["apps"] = apps
    _cache["at"] = now
    return list(apps)


def find_app(identifier: str) -> Optional[InstalledApp]:
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    apps = installed_apps()
    for app in apps:
        if app.id == identifier:
            return app
    low = identifier.lower()
    for app in apps:
        if app.name.lower() == low:
            return app
    for app in apps:
        if low in app.name.lower() or low in app.id.lower():
            return app
    return None


def apps_for_mime(mime: Optional[str]) -> List[InstalledApp]:
    if not mime:
        return []
    return [app for app in installed_apps() if mime in app.mime_types]


# ---------------------------------------------------------------------------
# Kinds and labels
# ---------------------------------------------------------------------------


def is_url(target: str) -> bool:
    text = (target or "").strip()
    if not text or " " in text:
        return False
    if _SCHEME_RE.match(text) or text.lower().startswith("www."):
        return True
    if _LOCAL_RE.match(text):
        return True
    if mimetypes.guess_type(text)[0]:
        return False
    return bool(_DOMAIN_RE.match(text))


def with_scheme(target: str) -> str:
    text = target.strip()
    return text if _SCHEME_RE.match(text) else f"https://{text}"


def search_url(query: str) -> str:
    return "https://duckduckgo.com/?q=" + quote_plus(query.strip())


def kind_for_target(target: str) -> str:
    text = (target or "").strip()
    if is_url(text):
        return "url"
    path = Path(text).expanduser()
    if text.endswith(("/", os.sep)) or path.is_dir():
        return "folder"
    if path.suffix:
        return path.suffix.lower()
    guess = mimetypes.guess_type(text)[0]
    if guess:
        return "." + guess.split("/")[-1]
    return "file"


def kind_label(kind: str) -> str:
    kind = (kind or "").strip()
    if kind in _KIND_LABELS:
        return _KIND_LABELS[kind]
    if kind.startswith("."):
        suffix = kind[1:]
        return f"{suffix.upper()} files" if suffix else "files"
    return kind.replace("-", " ")


def mime_for_kind(kind: str) -> Optional[str]:
    kind = (kind or "").strip().lower()
    if kind == "url":
        return "x-scheme-handler/http"
    if kind == "folder":
        return "inode/directory"
    if kind.startswith("."):
        return mimetypes.guess_type("file" + kind)[0]
    return None


def normalise_kind(value: str) -> str:
    """Turn ``pdf`` into ``.pdf`` while keeping url/folder/file/app roles."""
    kind = (value or "").strip().lower()
    if not kind:
        return ""
    if kind in _SPECIAL_KINDS or kind.startswith(".") or ":" in kind:
        return kind
    if re.fullmatch(r"[a-z0-9]{1,8}", kind):
        return f".{kind}"
    return kind


# ---------------------------------------------------------------------------
# System defaults
# ---------------------------------------------------------------------------


def system_default_app_id(kind: str) -> str:
    """Best-effort system default desktop id for a kind (used to preselect)."""
    mime = mime_for_kind(kind)
    if not mime:
        return ""

    if platform.system() == "Linux" and shutil.which("xdg-mime"):
        try:
            result = subprocess.run(
                ["xdg-mime", "query", "default", mime],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().splitlines()[0]
        except Exception:
            pass

    home = Path.home()
    for path in (
        home / ".config/mimeapps.list",
        home / ".local/share/applications/mimeapps.list",
        Path("/usr/share/applications/mimeapps.list"),
    ):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        section = ""
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line
                continue
            if section != "[Default Applications]" or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != mime:
                continue
            first = value.split(";")[0].strip()
            if first:
                return first
    return ""


# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------


def strip_field_codes(command: str) -> str:
    cleaned = command or ""
    for code in _FIELD_CODES:
        cleaned = cleaned.replace(code, "")
    return cleaned.strip()


def build_command(template: str, target: Optional[str] = None) -> str:
    template = (template or "").strip()
    if not target:
        return strip_field_codes(template)
    quoted = shlex.quote(target)
    for code in ("%f", "%F", "%u", "%U"):
        if code in template:
            return template.replace(code, quoted)
    return f"{strip_field_codes(template)} {quoted}".strip()


def launch_app(app: InstalledApp, target: Optional[str] = None) -> str:
    """Start an app, optionally with a file/URL argument, and return a summary."""
    command = build_command(app.command, target)
    if not command:
        raise OSError(f"{app.name} has no launch command.")

    if platform.system() == "Windows":  # pragma: no cover - not the target platform
        try:
            os.startfile(target or "")  # type: ignore[attr-defined]
        except OSError as exc:
            raise OSError(f"Could not launch {app.name}: {exc}") from exc
        return f"Opened {target or app.name} with {app.name}."

    try:
        subprocess.Popen(
            command,
            shell=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise OSError(f"Could not launch {app.name}: {exc}") from exc

    if target:
        return f"Opened {target} with {app.name}."
    return f"Launched {app.name}."


def app_from_saved(data: Dict[str, Any]) -> InstalledApp:
    """Rebuild an app from a stored default-app row."""
    return InstalledApp(
        id=str(data.get("app_id") or ""),
        name=str(data.get("app_name") or data.get("app_id") or "the saved app"),
        command=str(data.get("command") or ""),
    )


__all__ = [
    "InstalledApp",
    "app_from_saved",
    "apps_for_mime",
    "build_command",
    "find_app",
    "installed_apps",
    "is_url",
    "kind_for_target",
    "kind_label",
    "launch_app",
    "mime_for_kind",
    "normalise_kind",
    "parse_desktop_entry",
    "search_url",
    "strip_field_codes",
    "system_default_app_id",
    "with_scheme",
]
