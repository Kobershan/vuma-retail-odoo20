# -*- coding: utf-8 -*-
"""Repair pack names copied from the translated pack-size field.

The 1.1.0 migration copied the raw JSONB translation value into the line's
plain text name when the pack-size library was still translatable.  This
step replaces those values with the English label (the base language used by
the library) and also fills any remaining missing names.
"""


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        UPDATE vuma_product_pack_line AS line
           SET name = pack.name->>'en_US'
          FROM vuma_pack_size AS pack
         WHERE line.pack_size_id = pack.id
           AND (
                line.name IS NULL
                OR line.name = ''
                OR (line.name LIKE '{%' AND line.name LIKE '%en_US%')
           )
    """)
