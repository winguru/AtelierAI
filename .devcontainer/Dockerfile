# Use a slim, official Python image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies, including gosu
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy the backend requirements file and install Python dependencies
COPY ./backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend application code and scripts
COPY ./backend/ /app/

# Make the shell scripts executable
RUN chmod +x /app/start.sh /app/entrypoint.sh

# Create the non-root user
RUN useradd --create-home --shell /bin/bash app

# Set the entrypoint. This script will run as root on container start.
ENTRYPOINT ["/app/entrypoint.sh"]

# Set the default command. This is passed as an argument to the entrypoint.
CMD ["/app/start.sh"]