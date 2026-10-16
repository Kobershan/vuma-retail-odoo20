# Odoo 20 on Fly.io
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    libldap2-dev \
    libsasl2-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libopenjp2-7-dev \
    libtiff-dev \
    libwebp-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev \
    libpango1.0-dev \
    libcairo2-dev \
    libgdk-pixbuf-2.0-dev \
    libffi-dev \
    libssl-dev \
    wkhtmltopdf \
    fonts-dejavu-core \
    fonts-liberation \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create odoo user
RUN useradd -m -d /home/odoo -s /bin/bash odoo

# Set working directory
WORKDIR /home/odoo

# Copy Odoo source and custom addons/themes
COPY --chown=odoo:odoo odoo/ ./odoo/
COPY --chown=odoo:odoo custom-addons/ ./custom-addons/
COPY --chown=odoo:odoo themes/ ./themes/

# Install Python dependencies
RUN pip install --no-cache-dir -r odoo/requirements.txt

# Install Odoo in development mode
RUN pip install --no-cache-dir -e ./odoo

# Copy entrypoint and config template
COPY --chown=odoo:odoo entrypoint.sh /entrypoint.sh
COPY --chown=odoo:odoo odoo.conf.template /home/odoo/odoo.conf.template

RUN chmod +x /entrypoint.sh

USER odoo

EXPOSE 8069

ENTRYPOINT ["/entrypoint.sh"]
CMD ["odoo", "-c", "/home/odoo/odoo.conf"]