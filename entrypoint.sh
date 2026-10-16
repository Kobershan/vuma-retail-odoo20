#!/bin/bash
set -e

# Generate odoo.conf from template with environment variables
envsubst < /home/odoo/odoo.conf.template > /home/odoo/odoo.conf

# Wait for database to be ready
echo "Waiting for database at $DB_HOST:$DB_PORT..."
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t 5; do
    echo "Database not ready, waiting..."
    sleep 2
done

# Run database initialization/migration if needed
if [ "$RUN_MIGRATION" = "true" ]; then
    echo "Running Odoo migration..."
    odoo -c /home/odoo/odoo.conf -d "$DB_NAME" --stop-after-init --no-http
fi

# Execute the main command
exec "$@"