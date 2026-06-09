FROM python:3.12-slim

# Copy uv and uvx binaries from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /workspace

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    ffmpeg \
    libimage-exiftool-perl \
    git \
    curl \
    vim \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt app/dev-requirements.txt /tmp/atelier-deps/

ENV PIP_ROOT_USER_ACTION=ignore
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/atelier-deps/requirements.txt && \
    pip install --no-cache-dir -r /tmp/atelier-deps/dev-requirements.txt

# RUN uvx --from git+https://github.com/oraios/serena serena-mcp-server
RUN uv tool install -p 3.13 serena-agent@latest --prerelease=allow

CMD ["tail", "-f", "/dev/null"]
