from . import models
from . import report


def post_init_hook(env):
    """Set the confirmed AVCO cost method for every product category."""
    categories = env['product.category'].search([])
    for company in env['res.company'].search([]):
        categories.with_company(company).write(
            {'property_cost_method': 'average'})
    # A fresh installation has not run the upgrade migrations, so seed the
    # stock departments here as well.
    env['vuma.department']._vuma_seed_from_catalogue()
    # Every internal user starts with every Vuma function granted.
    env['vuma.function']._vuma_sync_access()
