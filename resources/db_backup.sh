#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Get the directory where the script is located
SCRIPT_DIR=$(dirname "$(realpath "$0")")
ENV_FILE="$SCRIPT_DIR/../.env" # Assumes .env is in the parent directory

# Check if .env file exists and source it early to get potential BACKUP_DIR
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE" >&2
    exit 1
fi
echo "Sourcing environment variables from $ENV_FILE"
source "$ENV_FILE"

# Determine the backup destination directory
# Priority: 1. Command-line argument, 2. BACKUP_DIR env var
if [ -n "$1" ]; then
  # Use command-line argument if provided
  BACKUP_DEST_DIR="$1"
  echo "Using backup destination from command-line argument: $BACKUP_DEST_DIR"
elif [ -n "$BACKUP_DIR" ]; then
  # Use BACKUP_DIR from .env file if command-line arg is missing
  BACKUP_DEST_DIR="$BACKUP_DIR"
  echo "Using backup destination from BACKUP_DIR environment variable: $BACKUP_DEST_DIR"
else
  # Error if neither is provided
  echo "Error: Backup destination directory not specified."
  echo "Usage: $0 <backup_destination_directory>"
  echo "Alternatively, set BACKUP_DIR in the $ENV_FILE file."
  exit 1
fi

# --- Configuration Validation ---
# Check if necessary database variables are set
if [ -z "$DB_USER" ] || [ -z "$DB_PASSWORD" ] || [ -z "$DB_NAME" ]; then
  echo "Error: DB_USER, DB_PASSWORD, and DB_NAME must be set in the .env file." >&2
  exit 1
fi

# Use 'localhost' and '5432' as defaults if DB_HOST/DB_PORT are not set
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}

# --- Backup Process ---
TIMESTAMP=$(date +%Y%m%d%H%M%S)
# Use .dump extension for custom format
BACKUP_FILENAME="${DB_NAME}_backup_${TIMESTAMP}.dump"
BACKUP_FILE_PATH="${BACKUP_DEST_DIR}/${BACKUP_FILENAME}"

# Create backup destination directory if it doesn't exist
echo "Creating backup directory (if it doesn't exist): $BACKUP_DEST_DIR"
mkdir -p "$BACKUP_DEST_DIR"
if [ $? -ne 0 ]; then
    echo "Error: Failed to create backup directory $BACKUP_DEST_DIR" >&2
    exit 1
fi

# Check if the directory is writable (basic check)
if [ ! -w "$BACKUP_DEST_DIR" ]; then
    echo "Error: Backup directory $BACKUP_DEST_DIR is not writable." >&2
    exit 1
fi

echo "Starting PostgreSQL backup..."
echo "  Database: $DB_NAME"
echo "  Host: $DB_HOST:$DB_PORT"
echo "  User: $DB_USER"
echo "  Destination: $BACKUP_FILE_PATH"

# Run pg_dump inside a temporary Docker container
# - Mounts the backup destination directory into the container
# - Uses custom-format (-F c) which is compressed and suitable for pg_restore
docker run --rm --network=host \
    -e PGPASSWORD="$DB_PASSWORD" \
    -v "$BACKUP_DEST_DIR":/backups \
    postgres:16 \
    pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -F c -b -v -f "/backups/$BACKUP_FILENAME" "$DB_NAME"

# Check if docker command was successful
if [ $? -ne 0 ]; then
    echo "Error: Docker pg_dump command failed." >&2
    # Consider removing the potentially incomplete backup file
    # rm -f "$BACKUP_FILE_PATH"
    exit 1
fi

echo "Backup completed successfully: $BACKUP_FILE_PATH"

exit 0
