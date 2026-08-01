FROM python:3.12-slim

ARG PYTORCH_VARIANT=auto

WORKDIR /workspace

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    ffmpeg \
    libimage-exiftool-perl \
    gosu \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt \
     app/requirements.torch.linux-cpu.txt \
     app/requirements.torch.linux-cuda.txt \
  app/requirements.torch.linux-rocm.txt \
     app/requirements.torch.raspberrypi.txt \
     /tmp/atelier-deps/
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
    pip install --no-cache-dir -r /tmp/atelier-deps/requirements.txt

COPY . /workspace

RUN chmod +x /workspace/start.sh /workspace/app/entrypoint.sh && \
    useradd --create-home --shell /bin/bash app

ENV ATELIER_APP_ROOT=/workspace/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/workspace/app/entrypoint.sh"]
CMD ["/workspace/start.sh"]
