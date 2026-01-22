# Use a slim, official Python image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies needed for some Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the backend requirements file and install Python dependencies
# This is done first to leverage Docker's layer caching
COPY ./backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend application code
COPY ./backend/ /app/

# Switch to a non-root user for security
RUN useradd --create-home --shell /bin/bash app
USER app

# Command to run the FastAPI server using Uvicorn
# This will be used by the devcontainer.json
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
