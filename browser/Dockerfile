FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99
ENV RESOLUTION=1280x720x24
ENV OPEN_COMPUTR_USE_DATA_DIR=/app/data

# Xvfb virtual desktop + VNC + Python + the libraries PyAutoGUI needs
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    openbox \
    x11vnc \
    novnc \
    websockify \
    xdotool \
    x11-xserver-utils \
    xterm \
    net-tools \
    curl \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    python3-tk \
    fonts-liberation \
    fonts-dejavu-core \
    libnss3 \
    libatk-bridge2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libasound2t64 \
    libx11-6 \
    libxtst6 \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Symlink noVNC index so /vnc.html works out of the box
RUN ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# Ubuntu 24.04 ships a default 'ubuntu' user with UID 1000; reuse it as 'agent'
RUN id -u ubuntu >/dev/null 2>&1 && usermod -l agent -d /home/agent -m ubuntu && groupmod -n agent ubuntu || \
    useradd -m -s /bin/bash -u 1000 agent
RUN mkdir -p /home/agent/.vnc /app/data && chown -R agent:agent /home/agent /app

WORKDIR /app

# Python environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright playwright install --with-deps chromium && \
    chmod -R 777 /opt/ms-playwright

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

COPY backend/ /app/backend/
ENV PYTHONPATH="/app/backend:$PATH"
RUN pip install --no-cache-dir -e /app/backend/

COPY browser/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY browser/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8765 6080 5900

USER agent

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
