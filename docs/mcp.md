# MCP tools

Open Computer Use can attach [Model Context Protocol](https://modelcontextprotocol.io)
servers to a run. Their tools become extra actions for the model, alongside the
screen, mouse, keyboard and browser actions it already has.

## Where MCP runs

MCP servers are configured **on the server, per account**, and are connected by
the server process at the start of every run:

- **stdio** — the server spawns `command` + `args` as a child process. That
  process runs on the server host, or inside the server container for a Docker
  install, with that filesystem and `PATH`.
- **http** — the server connects to `url` with the configured headers. The MCP
  server can be anywhere the server can reach.

The node that drives the mouse is not involved. Configure MCP from a desktop
browser: the phone view deliberately has no MCP settings, although MCP tools do
apply to runs started from a phone.

## Add a server (desktop UI)

1. Open **Settings → MCP** in a desktop browser.
2. Fill in a name and pick the transport.
   - stdio: `Command` and space-separated `Arguments` (quotes work).
   - http: `URL`, plus optional `Headers` JSON.
3. Optional `Environment` JSON for stdio servers. It is encrypted at rest and is
   never shown again; leave the field blank while editing to keep the saved
   values.
4. **Test** connects once and lists the tools. **Add server** saves it.
5. Enable it with the checkbox, then start a run. The model sees the tools as
   `mcp__<server>__<tool>`.

A server that fails to start does not abort the run: the failure appears as an
`MCP:` observation and the run continues without those tools.

## Examples

### Filesystem server (no-Docker install, stdio)

Install Node.js on the server host (`sudo pacman -S nodejs npm` on Arch), create
the directory the agent may touch, then add:

| Field | Value |
| --- | --- |
| Name | `Files` |
| Transport | `stdio` |
| Command | `npx` |
| Arguments | `-y @modelcontextprotocol/server-filesystem /srv/agent-files` |

```bash
sudo install -d -o "$USER" /srv/agent-files
```

The path is resolved **on the server**, not on the computer the agent controls.

### Docker install

The server image has no Node.js and only sees its own volumes, so a stdio server
that depends on host tools or host paths will fail with `Command not found` or a
missing directory. Either:

- run the MCP server as an HTTP service and add it with transport `http`
  (recommended for Docker), or
- build your own image with the runtime installed and mount the data the agent
  should see into it.

### Remote HTTP server

| Field | Value |
| --- | --- |
| Transport | `http` |
| URL | `https://mcp.example.com/mcp` |
| Headers | `{"Authorization":"Bearer <token>"}` |

The URL must be reachable from the server. `localhost` means the server's own
loopback, not your laptop.

## Security

- MCP tools run with the server's privileges and often expose files, shells or
  network access. Give each server the narrowest scope it needs, and do not point
  a filesystem server at `/` or your home directory.
- Saved environment and header values are encrypted with the instance Fernet
  key. Losing `deploy/secrets/fernet.key` (or `data/.fernet.key`) makes them
  unreadable.
- MCP servers are per account: other users cannot see or call yours.
- With `SIGNUP_MODE=open` (the default) anyone can create an account on a public
  server — and their MCP servers would run on your host. Use `SIGNUP_MODE=closed`
  if that is not acceptable.
- The full-screen/mouse actions are still governed by the node policy and
  `NODE_APPROVAL_MODE`; MCP tools are not, so review what you attach.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `Command not found: npx` | The runtime is missing from the server's `PATH` — inside the container for Docker. Use HTTP transport instead. |
| **Test** returns an error | Run the same command with the same args and env manually on the server host; the error text is passed through. |
| Test succeeds but lists no tools | The server started but exposes none. Check its own logs. |
| Tools missing during a run | The server row is disabled, or it failed to start for that run — look for the `MCP:` observation. |
| Phone has no MCP page | Expected. Configure MCP from a desktop browser. |

## Prompt: install and configure an MCP server

Paste this into an agent that has shell access to the machine running the
server. Replace the placeholders before sending.

```text
Install and configure an MCP server for Open Computer Use on this machine.

Context:
- Repository: <path to Open Computer Use>
- The app runs: <docker compose | python -m server.cli serve from .venv>
- Server data directory: <./data | the ocu-data container volume>
- The agent should be able to work with: <directory or service>
- Preferred transport: <stdio | http>

Do this:
1. Detect whether the app runs in Docker or from a venv, and what PATH and
   filesystem its server process actually sees. Say which transport will work;
   for Docker, prefer http unless the image already has the runtime.
2. Install the runtime the server needs (Node.js/npm for npx servers, uv/uvx or
   a venv for Python servers) with this machine's package manager.
3. Create and lock down only the directory or credentials the server needs. Do
   not grant broader access than the stated directory.
4. Start the MCP server once by hand and verify the MCP handshake lists its
   tools.
5. Add it in Settings → MCP with the exact command/arguments or URL/headers,
   press Test, enable it, and start a short run that calls one of its tools.
6. Report the exact values you entered and anything the user must do (package
   approvals, firewall changes, tokens). Never print secrets.

Stop and ask before exposing a shell, the home directory or the whole
filesystem.
```
