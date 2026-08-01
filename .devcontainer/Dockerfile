FROM python:3.12-slim

ARG PYTORCH_VARIANT=auto

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

COPY app/requirements.txt \
         app/dev-requirements.txt \
         app/requirements.torch.linux-cpu.txt \
         app/requirements.torch.linux-cuda.txt \
    app/requirements.torch.linux-rocm.txt \
         app/requirements.torch.raspberrypi.txt \
         /tmp/atelier-deps/

ENV PIP_ROOT_USER_ACTION=ignore
RUN pip install --no-cache-dir --upgrade pip && \
        ARCH=$(uname -m) && \
        PT_VARIANT=${PYTORCH_VARIANT} && \
        if [ "$PT_VARIANT" = "cuda" ]; then \
            TORCH_FILE=/tmp/atelier-deps/requirements.torch.linux-cuda.txt; \
        elif [ "$PT_VARIANT" = "rocm" ]; then \
            TORCH_FILE=/tmp/atelier-deps/requirements.torch.linux-rocm.txt; \
        elif [ "$PT_VARIANT" = "cpu" ]; then \
            TORCH_FILE=/tmp/atelier-deps/requirements.torch.linux-cpu.txt; \
        elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
            TORCH_FILE=/tmp/atelier-deps/requirements.torch.raspberrypi.txt; \
        else \
            TORCH_FILE=/tmp/atelier-deps/requirements.torch.linux-cpu.txt; \
        fi && \
        echo "Installing PyTorch from: $TORCH_FILE (arch=$ARCH variant=$PT_VARIANT)" && \
        pip install --no-cache-dir -r "$TORCH_FILE" && \
    pip install --no-cache-dir -r /tmp/atelier-deps/requirements.txt && \
    pip install --no-cache-dir -r /tmp/atelier-deps/dev-requirements.txt

# RUN uvx --from git+https://github.com/oraios/serena serena-mcp-server
RUN uv tool install -p 3.13 serena-agent@latest --prerelease=allow

CMD ["tail", "-f", "/dev/null"]
