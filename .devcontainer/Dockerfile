FROM python:3.12-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    ffmpeg \
    libimage-exiftool-perl \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt app/dev-requirements.txt /tmp/atelier-deps/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/atelier-deps/requirements.txt && \
    pip install --no-cache-dir -r /tmp/atelier-deps/dev-requirements.txt

CMD ["tail", "-f", "/dev/null"]
