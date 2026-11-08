# Dead-code candidates (review only — nothing deleted)

Nothing in this list has been removed. Each item is a candidate with the reason
it looks dead, so the owner can approve or reject it. Deletions, when approved,
will happen in a separate commit.

## Unwired product code

| Candidate | Why it looks dead |
| --- | --- |
| `server/scheduler/` (`TaskSchedulerManager`) | Never imported by `server/app.py`, `server/cli.py` or any live module. Only `tests/test_m6_scheduler.py` imports it. There is no scheduler UI, API route, migration wiring or startup hook. |
| `server/agent/skills/deal_finder.py` | Only imported by the unwired scheduler and `tests/test_m5_deals.py`. No live route, agent tool or provider references it. |
| `server/agent/loop.py` | Legacy browser-agent loop. The live agent uses `agent/open_computer_use/loop.py`; only old acceptance tests import this file. |
| `server/agent/browser/` (`cli.py`, `session.py`, `mouse.py`, `cursor.js`, `demo_page.html`) | Legacy Playwright/PyAutoGUI browser-agent stack. The only live browser control is `node/drivers/browser.py`; `server/setup.py` still exposes an `agent=` console entry pointing here. |
| `server/agent/actions/` and `server/agent/perception/` | Legacy action resolver and perception extractor. Not imported by `server/app.py` or the live agent loop. |
| `server/agent/providers/mock.py` | Scripted provider used only by legacy browser acceptance tests; not in the live provider registry for production unless explicitly selected. |
| `browser/` (Xvfb + noVNC all-in-one image) | Still referenced by the `desktop` compose profile, so it is **not** dead, but it is legacy and doubles the maintenance surface. Candidate for demotion to a dev-only profile. |
| `mock-shop/` | HTML fixture used only by `tests/test_m2_actions.py` and `tests/test_m3_agent_loop.py`. |
| `tests/test_m2_actions.py`, `test_m3_agent_loop.py`, `test_m5_deals.py`, `test_m6_scheduler.py` | Legacy milestone acceptance tests for the browser-agent/scheduler/deals stack. They are kept for history but do not exercise the current server/node product. |

## Repository leftovers

| Candidate | Why it looks dead |
| --- | --- |
| `frontend/` | Empty directory (not tracked by git). No build step or reference. |
| `backend/` | Referenced in an old commit and in some docs/comments, but the directory does not exist in the working tree. Comments mentioning `backend/agent/browser` are stale. |
| `deploy/gcp/setup.sh` | Legacy single-VM bootstrap for the old all-in-one install. The current deployment is the compose stack in `docs/docker.md`. |
| `deploy/Caddyfile` | Host-install Caddyfile; `deploy/Caddyfile.compose` is the one used by the stack. Check whether `docs/install/*` still references it before deleting. |
| `docs/install/{ubuntu,fedora,arch}.md` | Pre-Docker installation notes for the legacy desktop install. `docs/node.md` is the current node guide. |

## Dependencies

Removed in this phase (no imports anywhere; all were transitive already or
unused): `numpy`, `aiohttp`, `python-dateutil`, `python-dotenv`, `pyscreeze`.

Retained pending the decision above:

- `apscheduler` — only the unwired scheduler imports it.
- `rich` — only `server/agent/browser/cli.py` (legacy console entry) imports it.
- `playwright` — still used by the live browser node backend, keep.
- `pyscreeze`/`pyperclip` — `pyscreeze` comes with PyAutoGUI; `pyperclip` is
  used by `node/drivers/desktop.py`, keep `pyperclip`.

## Duplicate / overlapping modules

- `server/agent/loop.py` vs `server/agent/open_computer_use/loop.py`: two agent
  loops; only the latter is live.
- `server/agent/computer/actions.py` (tool schema for the legacy loop) vs
  `server/agent/open_computer_use/loop.py` + provider tool specs: overlapping
  responsibility, keep the live path.
- `deploy/Caddyfile` vs `deploy/Caddyfile.compose`: two proxy configs.
- `server/requirements.txt` vs `server/requirements.lock.txt`: intentional
  (ranges vs image pins), not dead.
