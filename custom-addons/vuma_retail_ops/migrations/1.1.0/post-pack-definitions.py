# -*- coding: utf-8 -*-
"""Fill free-form pack definitions from the earlier shared-pack rows.

Version 1.0.x stored only ``pack_size_id`` and a base quantity.  Version 1.1
stores the name, the quantity as entered and the unit it was entered in, so
packs such as 50ml or 1L can keep their own unit.  Existing rows are copied
across without touching their stock or variants.
"""


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        UPDATE vuma_product_pack_line AS line
           SET name = pack.name->>'en_US',
               pack_qty = line.units_per_pack,
               pack_uom_id = template.uom_id
          FROM vuma_pack_size AS pack,
               product_template AS template
         WHERE line.pack_size_id = pack.id
           AND line.template_id = template.id
           AND (line.name IS NULL OR line.name = '')
    """)
