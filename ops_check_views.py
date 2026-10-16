#!/usr/bin/env python3
"""Check every SQL view this module defines by running it against the database.

Odoo reports a broken view by refusing to start, with the error buried in a log
file. Building the view by hand first turns a failed module update into a
one-line Postgres error, which is a lot quicker to work through.

Run from the project root:

    ./venv/bin/python ops_check_views.py
"""
import re
import sys
from pathlib import Path

import psycopg2

CONFIG = Path(__file__).resolve().parent
MODELS = CONFIG / 'custom-addons' / 'cognify_management_reports' / 'models'


def db_params():
    """Read the connection details out of odoo.conf and .dbpass."""
    params = {}
    for line in (CONFIG / 'odoo.conf').read_text().splitlines():
        line = line.strip()
        if '=' not in line or line.startswith('#'):
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        # odoo.conf spells the connection settings db_host, db_port, db_user;
        # psycopg2 wants them without the prefix. dbfilter has no prefix and
        # is kept under its own name, because it is what names the database.
        if key.startswith('db_'):
            params[key[3:]] = value.strip()
        elif key == 'dbfilter':
            params[key] = value.strip()
    params['password'] = (CONFIG / '.dbpass').read_text().strip()
    # dbfilter is the only place the database is named, and it is a regular
    # expression: strip the anchors and alternation to get the name itself.
    # A list of alternatives is not something psycopg2 can open, so the first
    # one is taken and the rest ignored.
    db_filter = params.pop('dbfilter', '').strip().lstrip('^').rstrip('$')
    params['dbname'] = db_filter.split('|')[0]
    return params


def views_from(path):
    """Pull every (model, sql, params) out of the init() methods in a file.

    The SQL is the first argument to execute, so the file is read for those
    calls rather than imported: importing would need a live Odoo registry, and
    this script is here to avoid needing one.
    """
    source = path.read_text()
    for match in re.finditer(
        r'def init\(self\):(.*?)(?=\n    def |\Z)', source, re.S
    ):
        body = match.group(1)
        name = re.search(r"_name = '([^']+)'", source)
        sql_match = re.search(
            r'self\.env\.cr\.execute\(\s*"""(.*?)"""(.*?)\)\s*\n', body, re.S)
        if not sql_match:
            continue
        sql = sql_match.group(1).strip()
        rest = sql_match.group(2).strip()
        params = []
        if rest.startswith(','):
            params = re.findall(r'self\._(\w+)\(\)', rest)

        # The SQL in the module creates its own view; here it only selects.
        create = re.match(
            r'CREATE OR REPLACE VIEW \w+ AS \(', sql, re.I)
        if create:
            sql = sql[create.end():]
            sql = sql[:sql.rindex(')')].strip()

        # psycopg2 reads every % as the start of a placeholder, so a LIKE '%x%'
        # in the query breaks it. Mark the real placeholders, escape everything
        # else, then put them back.
        for index, param in enumerate(params):
            sql = sql.replace('%s', f'\x00{index}\x00')
        sql = sql.replace('%', '%%')
        sql = re.sub(r'\x00(\d+)\x00', r'%s', sql)
        yield (name.group(1) if name else path.stem, sql, params)


def defaults():
    """Settings the views read, with the values the module falls back to."""
    return {
        'dead_days': 90,
        'slow_qty': 5,
    }


# A view that reads another model in the same module cannot be built before
# that model exists in the database. A stand-in table carrying the columns
# the view names is enough to prove the SQL is valid; it is only created
# (and dropped again) when the real table is not there.
STAND_INS = {
    'cognify_sales_target': """
        id SERIAL PRIMARY KEY,
        date DATE,
        user_id INTEGER,
        team_id INTEGER,
        company_id INTEGER,
        target_revenue NUMERIC,
        target_units FLOAT,
        target_margin NUMERIC
    """,
}


def main():
    conn = psycopg2.connect(**db_params())
    conn.autocommit = True
    settings = defaults()
    failures = 0
    checked = 0
    created = set()

    # Only a table the script had to create may be dropped again. An
    # installed module has the real table, and dropping that would take
    # the module's data with it.
    with conn.cursor() as cur:
        for table, columns in STAND_INS.items():
            cur.execute('SELECT to_regclass(%s) IS NOT NULL', (table,))
            if cur.fetchone()[0]:
                continue
            cur.execute(f'CREATE TABLE {table} ({columns})')
            created.add(table)

    for path in sorted(MODELS.glob('*.py')):
        for model, sql, params in views_from(path):
            checked += 1
            values = [settings[p] for p in params]
            name = f'check_{path.stem}'
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP VIEW IF EXISTS {name}')
                    cur.execute(f'CREATE VIEW {name} AS ({sql})', values)
                    # A view can build and still be nonsense, so read a row from
                    # it rather than trusting that it compiled.
                    cur.execute(f'SELECT count(*) FROM {name}')
                    rows = cur.fetchone()[0]
                    cur.execute(f'DROP VIEW {name}')
                print(f'  ok   {model:32} {rows:>8} rows')
            except psycopg2.Error as error:
                failures += 1
                message = str(error).strip().splitlines()
                print(f'  FAIL {model:32} {message[0]}')
                for detail in message[1:4]:
                    print(f'       {detail}')
                with conn.cursor() as cur:
                    cur.execute(f'DROP VIEW IF EXISTS {name}')

    # CASCADE because a check view built over a stand-in is still standing when
    # this runs, and Postgres refuses to drop a table that something depends on.
    with conn.cursor() as cur:
        for table in created:
            cur.execute(f'DROP TABLE IF EXISTS {table} CASCADE')

    conn.close()
    print(f'\n{checked - failures}/{checked} views build')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())