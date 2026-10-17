# -*- coding: utf-8 -*-
{
    'name': 'Vuma Store Front',
    'version': '1.0.0',
    'category': 'Website/Website',
    'summary': 'The online shop lists everything in inventory, at its selling price.',
    'description': """
Vuma Store Front
================

The website shop is the shop window of the whole business: it shows what is
actually on the shelves, priced at what the shop charges, without anyone
having to remember to tick *Published* on every product.

How it behaves
--------------
* **Everything in stock is listed.** Every sellable storable product is
  published to the website while it has stock, whichever company owns it --
  Trade Rite and Siyaya DC included.
* **Out of stock disappears, restock reappears.** Odoo's own *Unpublish
  Out-of-Stock Products* machinery drives the switch from the stock move
  itself: no cron, no delay.
* **Availability counts every company's shelves.** The "in stock" badge,
  the quantity the cart accepts and the auto-publish decision are measured
  with every company active, so a stocked Trade Rite or Siyaya DC product
  reads as available -- not *Sold out* -- even to a visitor whose session
  holds only the website's own company.
* **Another company's goods are catalogue-only.** Trade Rite and Siyaya DC
  products are displayed in full -- price, stock badge, page -- but the
  shop offers the native *Contact Us* action where the cart button would
  be, and the cart refuses them: its order belongs to the website company,
  and Odoo forbids company crossover on an order line. Those orders are
  the business's to place, through the staff's mixed-basket quotes.
* **The selling price is what the shop quotes.** No price list is applied to
  the website, so the shop shows the product's Sales Price, displayed as the
  website is configured (tax included).
* **"Sell when Out-of-Stock" is the escape hatch.** A product that must stay
  visible while empty (made to order, drop-shipped, pre-order) can be flagged
  *Sell when Out-of-Stock*, and the automation leaves it alone.

What installing does
--------------------
* enables the *Unpublish Out-of-Stock Products* website setting;
* adopts the catalogue that already exists: every sellable storable product
  joins the automation, and everything currently in stock is published;
* widens the shop's product domain and the product multi-company read rule,
  so a visitor sees the whole catalogue and not only the website company's
  products.
""",
    'author': 'Vuma Retail',
    'license': 'LGPL-3',
    'depends': [
        'website_sale',
        'stock',
    ],
    'data': [
        'data/storefront_data.xml',
    ],
    'post_init_hook': 'vuma_storefront_post_init',
    'installable': True,
}
