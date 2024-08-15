#!/bin/bash

SCRIPT_DIR=$(dirname "$(realpath "$0")")
source "$SCRIPT_DIR/../.env"

TIMESTAMP=$(date +\%Y\%m\%d\%H\%M\%S)
BACKUP_DIR="/mnt/backups/substiify"
BACKUP_FILE="${DB_NAME}_backup_$TIMESTAMP.sql"

# Create backup directory
mkdir -p $BACKUP_DIR

docker run --rm --network=host \
    -e PGPASSWORD=$DB_PASSWORD \
    -v $BACKUP_DIR:/backups \
    postgres:16 \
    pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER -F c -b -v -f /backups/$BACKUP_FILE $DB_NAME
