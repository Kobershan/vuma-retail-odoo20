# -*- coding: utf-8 -*-
"""
Vuma Multi-Tenant Retail: dual-company (Trade Rite / Siyaya DC) operation.

* Mixed-basket quotes that auto-split per company on confirm.
* Stock-source company visible everywhere a product is picked.
* Cash collected for both companies in one session.
* Invoices credited across both companies from one wizard.
"""
from . import models
from . import controllers

from .hooks import vuma_post_init  # noqa: F401
