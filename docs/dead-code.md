# Dead-code cleanup

The approved cleanup has been applied. This file records what was removed and
what is intentionally kept.

## Removed

- `server/agent/loop.py` — legacy browser-agent loop, superseded by
  `server/agent/open_computer_use/loop.py`.
- `server/agent/browser/` — legacy Playwright/PyAutoGUI browser-agent stack and
  its `agent=` console entry.
- `server/agent/actions/` and `server/agent/perception/` — legacy action
  resolver and perception extractor.
- `server/agent/providers/mock.py` — scripted providers. The only part still
  needed by current tests (`ScriptedProvider`) now lives in
  `tests/scripted_provider.py`; `mock` is no longer a selectable provider.
- `mock-shop/` — fixture used only by the removed browser-agent tests.
- `tests/test_m2_actions.py`, `test_m3_agent_loop.py`, `test_m5_deals.py`,
  `test_m6_scheduler.py` — milestone tests for the removed stack.
- `frontend/` — empty directory.
- `deploy/gcp/setup.sh` — legacy single-VM bootstrap.
- `deploy/Caddyfile` — host-install config; `deploy/Caddyfile.compose` is the
  stack's proxy config and remains.
- `docs/install/{ubuntu,fedora,arch}.md` — pre-Docker host-install notes.
- The `rich` dependency, which only the removed browser CLI imported.

## Intentionally retained

- `server/scheduler/` (`TaskSchedulerManager`) and
  `server/agent/skills/deal_finder.py` — unwired today, kept for a later
  scheduler/deals feature. `apscheduler` stays because the scheduler imports it.
- `browser/` (Xvfb + noVNC image) — the optional `desktop` compose profile
  documented in `docs/docker.md`.
- `playwright` — used by the live browser node backend.
- `pyperclip` — used by `node/drivers/desktop.py`.
- `server/requirements.txt` (ranges) and `server/requirements.lock.txt` (image
  pins) — intentional pair.
