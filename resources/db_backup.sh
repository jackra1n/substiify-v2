#!/bin/bash

source ../.env

TIMESTAMP=$(date +\%Y\%m\%d\%H\%M\%S)
BACKUP_DIR="/mnt/backups/substiify"
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_backup_$TIMESTAMP.sql"

# Create backup directory
mkdir -p $BACKUP_DIR

# Perform the backup using pg_dump
PGPASSWORD="$DB_PASSWORD" pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER -F c -b -v -f $BACKUP_FILE $DB_NAME
