FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements.txt requirements-computer.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY neobots ./neobots
COPY web ./web

FROM base AS app
RUN useradd -m -u 1000 agent && mkdir /data && chown agent:agent /data
USER agent
ENV NEOBOTS_DATA_DIR=/data
CMD ["uvicorn", "neobots.app:app", "--host", "0.0.0.0", "--port", "8787"]

FROM base AS computer
RUN pip install --no-cache-dir -r requirements-computer.txt \
    && playwright install --with-deps chromium \
    && mkdir -p /opt/browsers && cp -a /root/.cache/ms-playwright/. /opt/browsers/ \
    && useradd -m -u 1000 agent && mkdir /workspace /browser-profile \
    && chown -R agent:agent /workspace /browser-profile /opt/browsers
USER agent
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
CMD ["uvicorn", "neobots.computer:create_app", "--factory", "--host", "0.0.0.0", "--port", "8788"]

