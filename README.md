# Self-Hosted Browser Agent with Visible Human-Like Mouse Control

A production-ready self-hosted browser automation platform modeled after Meta's Muse-style agents. Driven by multi-model AI (Gemini, Claude, OpenAI, DeepSeek, and local Ollama), it moves a visible cursor along natural Bezier curves, executes multi-step web interactions, detects deals, flags price drops, and automates recurring tasks on a calendar schedule.

```
+-------------------------------------------------------------+
|                      Caddy Reverse Proxy                    |
|                      (HTTP/HTTPS, Auth)                     |
+------------------------------+------------------------------+
                               |
       +-----------------------+-----------------------+
       |                                               |
+------v--------------------+              +-----------v--------------+
|   FastAPI App & Scheduler |              |  Browser Runner (Xvfb)   |
|   - Agent Loop (Observe,  |              |  - Headed Chromium       |
|     Reason, Act, Verify)  |              |  - Injected SVG Cursor   |
|   - Multi-Model Providers |              |  - x11vnc & noVNC        |
|   - Price Drop Tracker    |<------------>|  - Bezier Curve Mouse    |
|   - SQLite & APScheduler  |  Playwright  |  - xdotool real_mouse    |
+---------------------------+              +--------------------------+
```

---

## Key Features

1. **Visibly Human-like Browser Control**:
   - Injected SVG cursor overlay with click ripples that follows Playwright pointer movements and persists across navigations and iframes.
   - Natural cubic Bezier trajectory generation with ease-in/out timing and micro-jitter (`generate_bezier_path`).
   - Smooth stepped wheel scrolling and variable per-keystroke typing latency.
   - Optional OS-level `real_mouse` using `xdotool` against virtual X11 display.
2. **Perception & Action Resolver**:
   - Extract interactive accessibility tree and DOM elements with numeric IDs and Set-of-Marks labels.
   - Anti-SSRF validation preventing model access to private IP subnets and cloud metadata endpoints (`169.254.169.254`).
3. **Pluggable Model Providers**:
   - Built-in adapters for Anthropic (Claude 3.5 Sonnet), Google Gemini (Gemini 1.5 Flash/Pro), OpenAI (GPT-4o), DeepSeek (deepseek-chat), and local Ollama.
   - Text perception fallback for models without vision capabilities.
4. **Deal-Finding Engine**:
   - Structured deal schema extraction, deduplication, historical price tracking, drop detection, and CSV export.
5. **Scheduler & Calendar**:
   - APScheduler with SQLite job store, strict 1-browser concurrency locking, and ntfy/webhook alerts.
6. **Self-Hosted & Distro Independent**:
   - Runs cleanly inside Ubuntu 24.04 LTS container base with Xvfb + Openbox + Chromium + noVNC.
   - Low-memory profile designed specifically for Google Cloud free-tier `e2-micro` instances.

---

## Quickstart

### 1. Requirements
- Docker Engine (or Podman) and Compose.
- No Python, Chromium, or X server required on the host.

### 2. Launch
```bash
git clone https://github.com/user/browser-agent.git
cd browser-agent
cp .env.example .env
docker compose up -d
```

Open your browser at `http://localhost:6080` to view the live desktop stream, or `http://localhost:8000` for the dashboard and API.

### 3. Run the M1 Acceptance Mouse Demo
To witness the visible mouse navigate, click, type, and scroll:
```bash
docker exec -e DISPLAY=:99 browser-agent-runner agent mouse-demo
```

---

## Google Cloud Platform (e2-micro Free Tier)

Google Cloud's free-tier `e2-micro` instance provides 1 GB of memory. To run smoothly:
1. SSH into your GCP VM instance.
2. Run the automated setup helper:
   ```bash
   sudo ./deploy/gcp/setup.sh
   ```
   This will:
   - Configure a 4GB swapfile.
   - Set optimal kernel swappiness.
   - Launch the stack using the `low-memory` profile with lean Chromium arguments and single-browser context limits.

> [!NOTE]
> Please verify your region's current free-tier limits on the Google Cloud Compute Engine pricing documentation. Larger machine shapes (e.g. `e2-small` or `e2-medium`) will provide higher concurrency and smoother video rendering.

---

## Acceptance Test Suite

Run the full automated test suite inside the container environment:

```bash
# M1: Bezier curve & mouse mechanics
docker exec -e DISPLAY=:99 browser-agent-runner python3 -m unittest discover -s /app/backend/agent/browser

# M2: Perception & DOM actions on Mock Shop
docker exec -e DISPLAY=:99 browser-agent-runner python3 /app/test_m2_actions.py

# M3: Multi-model agent loop & cost limits
docker exec -e DISPLAY=:99 browser-agent-runner python3 /app/test_m3_agent_loop.py

# M5: Deal finding & price drop detection
docker exec browser-agent-runner python3 /app/tests/test_m5_deals.py

# M6: Scheduler & concurrency locks
docker exec -e DISPLAY=:99 browser-agent-runner python3 /app/test_m6_scheduler.py
```
