{
    "name": "Vuma Field App",
    "version": "1.0.0",
    "category": "Operations/Field Service",
    "summary": "Installable PWA for drivers and sales representatives: POD, "
               "deliveries, returns, collections and trip expenses.",
    "description": """
Vuma Field App
==============

A mobile-first, offline-capable progressive web app for Vuma drivers and
sales representatives. It uses the Vuma Retail Operations backend for prices,
stock, credit and approvals. Field actions are queued locally and replayed
idempotently when the device reconnects.
""",
    "author": "Vuma Retail",
    "license": "LGPL-3",
    "depends": ["vuma_retail_ops", "web"],
    "data": ["views/field_templates.xml"],
    "installable": True,
    "application": False,
}
