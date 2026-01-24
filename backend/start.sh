#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Print a message to indicate the server is starting
echo "Starting Uvicorn server for AtelierAI..."

# Run the uvicorn command
# We use --host 0.0.0.0 to make it accessible from outside the container
# We use --port 8000 as our standard port
# We use --reload for development, so the server restarts on code changes
uvicorn main:app --host 0.0.0.0 --port 8000 --reload