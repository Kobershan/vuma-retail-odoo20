# -*- coding: utf-8 -*-
"""Seed the per-user function access table on upgrade to 2.0.0.

Every existing internal user starts with every function granted, which is what
"nobody is locked out on the day this ships" means in practice.  The seeding is
idempotent, so running it twice is harmless.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    created = env['vuma.function']._vuma_sync_access()
    _logger.info(
        'Vuma function access: seeded %s rows during the 2.0.0 upgrade', created)
