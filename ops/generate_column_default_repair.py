"""Emit the SQL repair for NOT NULL columns that carry no database default.

Background
----------
Odoo declares many fields `required=True` with a Python default and then makes
the column NOT NULL. Whenever the ORM omits such a field from an INSERT, the
database rejects the row. Odoo 20.0's own test helpers do exactly that for
`res.users`/`res.partner`, `res.company` and `product.template`, so the stock
and purchase_stock test suites die in `setUpClass`. This reproduces on a
pristine, freshly initialised Odoo 20 database, so it is an upstream defect
rather than local drift.

The fix
-------
Give each affected column a database-level DEFAULT equal to the value that
field's own Python default produces. This is safe by construction: when Odoo
supplies the value (which it always does for normal application code) the
default is never consulted, so the only behaviour that changes is the case that
currently raises. A crash becomes the value Odoo intended.

Safety rules applied here
-------------------------
* Only scalar columns are touched; M2M/O2M/relational defaults are skipped.
* Only literal defaults are baked in. A default whose value depends on runtime
  context is skipped rather than guessed at.
* `today()`/`now()`-style defaults become `CURRENT_DATE`/`now()` rather than a
  frozen literal, so they keep advancing.

Prints a reviewable SQL script. It does not touch the database.
"""
import datetime

# Column types that can carry a meaningful SQL DEFAULT.
SCALAR_TYPES = ('char', 'text', 'integer', 'float', 'boolean',
                'date', 'datetime', 'selection', 'binary', 'monetary')

LITERAL_TYPES = (bool, int, float, str, datetime.date, datetime.datetime, datetime.time)

# Python default callables that mean "right now" and must not be frozen.
NOW_NAMES = ('now', 'today', 'context_today', 'context_timestamp')

# Physical SQL types we are willing to give a default to. Anything else is left
# alone: in particular Odoo 20 stores translated Char/Text as `jsonb`, and a
# bare string is not a valid JSON document.
SAFE_SQL_TYPES = {
    'character varying', 'text', 'character',
    'integer', 'bigint', 'smallint',
    'numeric', 'double precision', 'real',
    'boolean', 'date', 'timestamp without time zone',
}


def db_column_types():
    """Map (table, column) -> physical SQL data type, straight from the catalog."""
    types = {}
    env.cr.execute("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """)
    for table, column, data_type in env.cr.fetchall():
        types[(table, column)] = data_type
    return types


def is_now_default(default):
    name = getattr(default, '__name__', '') or ''
    return name in NOW_NAMES


def sql_literal(value, field_type):
    if field_type == 'datetime':
        return 'now()'
    if field_type == 'date':
        return 'CURRENT_DATE'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return "'%s'" % value.replace("'", "''")
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime.datetime):
        return "'%s'::timestamp" % value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, datetime.date):
        return "'%s'::date" % value.strftime('%Y-%m-%d')
    if isinstance(value, datetime.time):
        return "'%s'::time" % value.strftime('%H:%M:%S')
    return None


rows = []
skipped = []
COLUMN_TYPES = db_column_types()
for model_name in env['ir.model'].search([]).mapped('model'):
    if model_name.startswith('ir.'):
        continue
    model = env[model_name]
    for field in model._fields.values():
        if not field.store or not getattr(field, 'required', False):
            continue
        if field.type not in SCALAR_TYPES:
            continue
        if not getattr(field, 'column_type', None) or len(field.column_type) != 2:
            continue
        sql_type = COLUMN_TYPES.get((model._table, field.name))
        if sql_type is None:
            skipped.append((model_name, field.name, 'column not found'))
            continue
        if sql_type not in SAFE_SQL_TYPES:
            skipped.append((model_name, field.name, 'unsafe sql type %s' % sql_type))
            continue
        default = field.default
        if not callable(default):
            skipped.append((model_name, field.name, 'no callable default'))
            continue
        if is_now_default(default):
            literal = sql_literal(None, field.type)
            if literal:
                rows.append((model._table, field.name, literal, model_name, field.name))
                continue
        try:
            value = default(model)
        except Exception as exc:
            skipped.append((model_name, field.name, 'default raised %s' % type(exc).__name__))
            continue
        if not isinstance(value, LITERAL_TYPES):
            skipped.append((model_name, field.name, 'non-literal default %r' % (value,)))
            continue
        literal = sql_literal(value, field.type)
        if literal is None:
            skipped.append((model_name, field.name, 'no literal form'))
            continue
        rows.append((model._table, field.name, literal, model_name, field.name))

rows = sorted(set(rows), key=lambda r: (r[0], r[1]))
print('-- %d statements' % len(rows))
for table, column, literal, model_name, field_name in rows:
    print("ALTER TABLE %-38s ALTER COLUMN %-30s SET DEFAULT %s;"
          "  -- %s.%s" % (table, column, literal, model_name, field_name))
print()
print('-- skipped: %d' % len(skipped))
for model_name, column, why in sorted(skipped, key=lambda r: (str(r[0]), str(r[1]))):
    print('--   %s.%s -> %s' % (model_name, column, why))
env.cr.rollback()