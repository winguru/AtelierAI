FROM python:3.12-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    ffmpeg \
    libimage-exiftool-perl \
    gosu \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /tmp/atelier-deps/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/atelier-deps/requirements.txt

COPY . /workspace

RUN chmod +x /workspace/start.sh /workspace/app/entrypoint.sh && \
    useradd --create-home --shell /bin/bash app

ENV ATELIER_APP_ROOT=/workspace/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/workspace/app/entrypoint.sh"]
CMD ["/workspace/start.sh"]
