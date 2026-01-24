#!/bin/bash
# Exit immediately if a command exits with a non-zero status.
set -e

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
  echo "Error: DATABASE_URL environment variable not set."
  exit 1
fi

# Extract the directory path from the sqlite DATABASE_URL
# Example: "sqlite:///var/lib/atelierai/image_db.sqlite" -> "/var/lib/atelierai"
DB_PATH=${DATABASE_URL#sqlite:///}
DB_DIR=$(dirname "$DB_PATH")

# Create the directory if it doesn't exist
mkdir -p "$DB_DIR"

# Change ownership of the directory to the app user.
# This is safe to run every time the container starts.
chown -R app:app "$DB_DIR"
echo "✅ Ensured database directory is writable by 'app' user: $DB_DIR"

# Ensure the image library directory is writable by the 'app' user
IMAGE_LIBRARY_DIR="/var/lib/atelierai/image_library"
mkdir -p "$IMAGE_LIBRARY_DIR"
chown -R app:app "$IMAGE_LIBRARY_DIR"
echo "✅ Ensured image library directory is writable by 'app' user: $IMAGE_LIBRARY_DIR"

# Now, switch to the app user and execute the command passed to this script
# (which will be "/app/start.sh" from our CMD)
exec gosu app "$@"