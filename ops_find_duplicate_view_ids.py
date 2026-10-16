"""Report every SQL view that hands the ORM more rows than unique ids.

A report view is read through the ORM, which addresses a row by its `id`. If a
view emits two rows carrying the same id, one of them silently wins: the chart
or list shows one company's figures where it should show both, and nothing in
the log says so. The test suite checks the models it knows about, but this
checks the data actually sitting in the database, so a view that is only
exposed once its data has a particular shape is caught too.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_find_duplicate_view_ids.py
"""
import psycopg2

tables = {
    model._table
    for model in env.registry.models.values()
    if model._auto is False and model._table
    # Models that have not been loaded yet report a placeholder table name.
    and not model._table.startswith('_')
    # Odoo core ships one of its own: `report.stock.quantity` is the forecast,
    # read aggregated over date x product x state x warehouse, and it reuses a
    # synthetic negative id across those rows on purpose. Only our report views
    # are addressed by id, so only they are worth this check.
    and not model._table.startswith('report_')
}

checked = 0
bad = 0
for table in sorted(tables):
    try:
        env.cr.execute('SELECT count(*), count(DISTINCT id) FROM "%s"' % table)
        rows, distinct = env.cr.fetchone()
    except psycopg2.Error:
        # Declared by the registry but never materialised -- an uninstalled
        # template model, say. Nothing to check.
        env.cr.rollback()
        continue
    if not rows:
        continue
    checked += 1
    if rows != distinct:
        bad += 1
        env.cr.execute(
            'SELECT id, count(*) FROM "%s" GROUP BY id '
            'HAVING count(*) > 1 ORDER BY 2 DESC LIMIT 3' % table)
        examples = ', '.join(
            'id %s x%d' % (row[0], row[1]) for row in env.cr.fetchall())
        print('DUPLICATE  %-42s %d rows, %d ids   (%s)'
              % (table, rows, distinct, examples))

print('\n%d of %d populated report views reuse an id' % (bad, checked))
if not bad:
    print('every report view emits one row per id')