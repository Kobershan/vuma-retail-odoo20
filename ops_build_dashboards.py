"""Build the Cognify management dashboard JSON files.

The dashboards are live: each chart reads Odoo through a named action, and each
KPI reads a pivot. Nothing is a snapshot, so a dashboard cannot drift from the
books.

Running this regenerates ``data/files/*.json``. It is not part of the module at
run time -- the generated files are what Odoo loads -- but keeping the source as
a script is what makes the dashboards editable at all, since the raw format is
an o-spreadsheet document rather than anything you would hand-edit.

    ./venv/bin/python ops_build_dashboards.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'custom-addons', 'cognify_management_reports', 'data', 'files')

SHEET_VERSION = '19.3.10'
DASHBOARD_SHEET = 'dashboard_sheet_id'
DATA_SHEET = 'data_sheet_id'

ACCOUNTANT = '#01666B'
GOOD = '#00A04A'
BAD = '#DC6965'
NEUTRAL = '#875A7B'

# A pivot's `rows` and `columns` hold objects, not strings: each entry is
# {"fieldName": ...}. o-spreadsheet reads a dimension as `fields[dimension.fieldName]`,
# so a bare string gives fields[undefined] and the dimension comes back with no
# field, no display name and nothing to group by. A pivot's `filters` must be a
# list for the same reason: the runtime definition is
# `(definition.filters ?? []).map(...)`, and ?? only falls through for an
# *absent* key, so a mapping is kept as one and .map is not a function on it.
# Neither throws at import -- the document loads, the figures appear, and every
# cell reading that pivot shows #ERROR. That is how all seven dashboards failed
# at once while every other check passed. _dimension() builds the rows, the
# filter list is emitted as [], and validate() rejects the string and the
# mapping here, where it is cheap, rather than leaving it to the browser.

# A dashboard is a floating-figure layout over a cell grid. o-spreadsheet
# measures a figure in pixels, not cells, so the builder lays its content
# out on a pixel grid: every dashboard column is COL_W wide, every layout
# row carries its own height, and a figure is anchored to a cell with a
# pixel offset. These are the values the dashboards are drawn on.
COL_W = 250            # pixels per dashboard column
FIGURE_GAP = 10        # pixels between neighbouring figures
SCORECARD_H = 100      # scorecard height in pixels
CHART_H = 335          # chart height in pixels (o-spreadsheet's default)
HEADING_H = 24         # section heading row height
SUBHEADING_H = 18      # section subheading row height

# Chart types o-spreadsheet knows about. ChartPlugin.import() runs every figure
# tagged "chart" through the chart type registry and throws on anything else,
# which takes the whole dashboard down with it.
CHART_TYPES = {
    'bar', 'combo', 'line', 'pie', 'scorecard', 'gauge', 'scatter',
    'waterfall', 'pyramid', 'radar', 'geo', 'funnel', 'sunburst',
    'treemap', 'calendar', 'bubble',
}

# A domain is evaluated in the browser, not on the server, so it can only
# call the functions the py interpreter exposes. context_today(),
# relativedelta() and strftime() are there; date_trunc() is not, so a
# domain that uses it takes the whole dashboard down. This is the set of
# callable names a domain is allowed to reach for.
DOMAIN_FUNCTIONS = {
    'context_today', 'relativedelta', 'strftime',
    'bool', 'set', 'min', 'max',
    'datetime', 'date', 'time', 'timedelta', 'today', 'now',
}


def _domain_problems(domain, where):
    """Check a domain the way o-spreadsheet's Domain class does on load.

    The browser parses a domain as a Python expression, then walks the
    top-level list counting operands: every "&" or "|" needs one more,
    every (field, operator, value) triple satisfies one. It then evaluates
    any function call against a fixed set of builtins. Both rules are
    checked here so a bad domain fails the build, not the dashboard.
    """
    import ast

    try:
        tree = ast.parse(domain, mode='eval')
    except SyntaxError as exc:
        return [f'{where}: domain is not a valid expression ({exc})']

    if not isinstance(tree.body, (ast.List, ast.Tuple)):
        return [f'{where}: domain is not a list of conditions']

    # An empty domain filters nothing, which the browser accepts.
    if len(tree.body.elts) == 0:
        return []

    problems = []
    expected = 1
    for element in tree.body.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            if element.value in ('&', '|'):
                expected += 1
            elif element.value != '!':
                problems.append(f'{where}: unknown operator {element.value!r}')
        elif isinstance(element, (ast.List, ast.Tuple)) and len(element.elts) == 3:
            expected -= 1
        else:
            problems.append(
                f'{where}: condition is not a (field, operator, value) triple')
    if expected > 0:
        problems.append(
            f'{where}: domain is missing {expected} segment(s) -- an & or | '
            f'has too few conditions')

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None)
        if name and name not in DOMAIN_FUNCTIONS:
            problems.append(
                f'{where}: domain calls {name}(), which the browser cannot '
                f'evaluate')

    return problems


def _dimension(field):
    """One entry of a pivot's ``rows``/``columns``.

    o-spreadsheet reads a dimension as an *object*::

        createPivotDimension(fields, dimension) {
            const field = fields[dimension.fieldName];

    A bare string there gives ``fields[undefined]``, so the dimension comes back
    with no field, no display name and no name to group by. Nothing throws on
    the spot: the pivot simply cannot be normalised, its load rejects, and every
    cell reading it renders as ``#ERROR``. That is why the shape has to be
    right here rather than being left to the browser to complain about.

    A dimension may also carry a granularity (``"date:month"``) and an order.
    """
    if isinstance(field, dict):
        return field
    name, _, granularity = str(field).partition(':')
    dimension = {'fieldName': name}
    if granularity:
        dimension['granularity'] = granularity
    return dimension


def validate(document):
    """Fail the build on anything o-spreadsheet would refuse to import.

    Both of these crash the dashboard on load rather than degrading, so it is
    much cheaper to catch them here than in the browser.
    """
    import re

    problems = []
    style_ids = set(document.get('styles') or {})

    # o-spreadsheet imports the filters with
    # `for (const filter of data.globalFilters || [])`. An empty object is
    # not iterable, so a dashboard with no filters still has to carry an
    # empty array.
    if not isinstance(document.get('globalFilters'), list):
        problems.append('globalFilters is not an array')

    for sheet in document['sheets']:
        # Sheet-level "styles" maps cell position -> style id. o-spreadsheet
        # feeds every key to toZone(), which only understands XC ranges.
        for key, style_id in (sheet.get('styles') or {}).items():
            if not re.fullmatch(r'[A-Z]+\d+(:[A-Z]+\d+)?', key):
                problems.append(
                    f'{sheet["id"]}: styles key {key!r} is not a cell range')
            if str(style_id) not in style_ids:
                problems.append(
                    f'{sheet["id"]}: styles[{key!r}] points at undefined '
                    f'style {style_id!r}')

        for merge in sheet.get('merges') or []:
            if not re.fullmatch(r'[A-Z]+\d+:[A-Z]+\d+', merge):
                problems.append(f'{sheet["id"]}: bad merge {merge!r}')

        for figure in sheet.get('figures') or []:
            if figure.get('tag') != 'chart':
                continue
            chart_type = figure.get('data', {}).get('type')
            if chart_type not in CHART_TYPES:
                problems.append(
                    f'{sheet["id"]}: figure {figure.get("id")} has unknown '
                    f'chart type {chart_type!r}')

    for pivot in document.get('pivots', {}).values():
        measures = [measure['id'] for measure in pivot.get('measures', [])]
        if len(measures) != len(set(measures)):
            problems.append(
                f'pivot {pivot.get("id")}: duplicate measure ids')
        # Every dimension must be an object carrying a fieldName. A bare string
        # imports cleanly and then fails to load in the browser, so it has to be
        # caught here: the build is the only place it is cheap to catch.
        for axis in ('rows', 'columns'):
            for dimension in pivot.get(axis) or []:
                if not isinstance(dimension, dict) or not dimension.get('fieldName'):
                    problems.append(
                        f'pivot {pivot.get("id")}: {axis} entry {dimension!r} is '
                        'not an object with a fieldName -- the pivot will not '
                        'load and its cells will render as #ERROR')
        # o-spreadsheet builds a pivot's runtime definition with
        # ``(definition.filters ?? []).map(...)``, which only falls through to
        # an empty array for the truly absent. A mapping there is kept as a
        # mapping and .map is not a function on it, so every KPI cell reading
        # that pivot comes back as #ERROR.
        # o-spreadsheet builds a pivot's runtime definition with
        # ``(definition.filters ?? []).map(...)``, which only falls through to
        # an empty array for the truly absent. A mapping there is kept as a
        # mapping and .map is not a function on it, so every KPI cell reading
        # that pivot comes back as #ERROR.
        if not isinstance(pivot.get('filters'), list):
            problems.append(
                f'pivot {pivot.get("id")}: filters is '
                f'{type(pivot.get("filters")).__name__}, not a list')
        for problem in _domain_problems(
                pivot.get('domain', '[]'), f'pivot {pivot.get("id")}'):
            problems.append(problem)

    for sheet in document['sheets']:
        for figure in sheet.get('figures') or []:
            if figure.get('tag') != 'chart':
                continue
            domain = (figure.get('data', {})
                      .get('dataSource', {})
                      .get('searchParams', {})
                      .get('domain', '[]'))
            for problem in _domain_problems(
                    domain, f'chart {figure.get("id")}'):
                problems.append(problem)

    return problems


class Dashboard:
    """Collects figures and KPI cells for one dashboard."""

    def __init__(self, name, columns=6):
        self.name = name
        self.columns = columns
        self.figures = []
        self.pivots = {}
        self.cells = {}
        self._next_pivot = 1
        self._kpi_row = 1
        # Section headings are plain cells on the dashboard sheet, not figures.
        # o-spreadsheet's ChartPlugin turns *every* figure tagged "chart" into a
        # chart definition and resolves its "type" against the chart type
        # registry, which has no "text" entry -- a heading declared that way
        # makes the whole dashboard fail to load.
        self.dash_cells = {}
        self.dash_styles = {}
        self.dash_merges = []
        # Style definitions belong to the document-level registry, keyed by id.
        self.doc_styles = {}
        # Layout cursor. Content is placed on layout rows, each with its
        # own pixel height, so figures never overlap the headings or each
        # other.
        self._row = 0              # next layout row to place content on
        self._row_heights = {}     # layout row -> height in pixels
        self._figure_row = None    # row-group id of the current figure row
        self._figure_row_height = 0

    # -- layout -------------------------------------------------------
    def _cell_ref(self, col, row):
        """Spreadsheet coordinate for a dashboard column and layout row."""
        letters = ''
        col += 1
        while col:
            col, rem = divmod(col - 1, 26)
            letters = chr(ord('A') + rem) + letters
        return f'{letters}{row + 1}'

    def _close_figure_row(self):
        """Record the height of the figure row just finished."""
        if self._figure_row is not None:
            self._row_heights[self._row] = self._figure_row_height
            self._row += 1
            self._figure_row = None
            self._figure_row_height = 0

    def _figure_row_at(self, row_group, height):
        """Layout row for a figure, starting a new row once the caller
        moves on to another row group."""
        if self._figure_row != row_group:
            self._close_figure_row()
            self._figure_row = row_group
        self._figure_row_height = max(self._figure_row_height, height)
        return self._row

    # -- pivots and KPI cells ------------------------------------------
    def pivot(self, name, model, domain, measures, rows=(), columns=()):
        """Register a pivot and return the id to use in PIVOT.VALUE formulas.
        
        measures: list of (key, fieldName) or (key, fieldName, aggregator)
        """
        pivot_id = str(self._next_pivot)
        self._next_pivot += 1
        measure_list = []
        for m in measures:
            measure = {'id': m[0], 'fieldName': m[1]}
            if len(m) > 2:
                measure['aggregator'] = m[2]
            measure_list.append(measure)
        self.pivots[pivot_id] = {
            'type': 'ODOO',
            'id': pivot_id,
            'formulaId': pivot_id,
            'name': name,
            'model': model,
            'domain': domain,
            'measures': measure_list,
            'columns': [_dimension(d) for d in columns],
            'rows': [_dimension(d) for d in rows],
            'context': {},
            # o-spreadsheet reads these with .map(), so an object is a crash:
            # ``(definition.filters ?? []).map(...)`` keeps an object as-is
            # instead of falling through to the empty array the ?? implies.
            'filters': [],
            'sortedColumn': None,
            # Only populated when a global filter points at this pivot.
            'fieldMatching': {},
        }
        return pivot_id

    def kpi(self, label, pivot_id, measure, comparison=None, col_offset=0):
        """Reserve a KPI row; returns the cell holding the value."""
        row = self._kpi_row
        self._kpi_row += 1
        base = 2 + col_offset
        value_col = chr(ord('D') + col_offset * 2)
        prev_col = chr(ord('E') + col_offset * 2)
        label_col = chr(ord('C') + col_offset * 2)
        self.cells[f'{label_col}{row}'] = f'=_t("{label}")'
        self.cells[f'{value_col}{row}'] = f'=PIVOT.VALUE({pivot_id},"{measure}")'
        if comparison:
            self.cells[f'{prev_col}{row}'] = comparison
        return f'Data!{value_col}{row}', (f'Data!{prev_col}{row}' if comparison else None)

    # -- figures --------------------------------------------------------
    def chart(self, col, row, width, height, title, chart_type, action,
              measure, group_by, domain='', context=None, extra=None,
              field_matching=None):
        """An Odoo-backed chart. Reads live through the named action."""
        layout_row = self._figure_row_at(row, CHART_H)
        chart_id = f'f{len(self.figures)}'
        data = {
            'type': chart_type,
            'chartId': chart_id,
            'title': {'text': title, 'bold': True, 'color': ACCOUNTANT},
            'background': '',
            'dataSource': {
                'type': 'odoo',
                'actionXmlId': action,
                'metaData': {
                    'resModel': extra or 'sale.report',
                    'groupBy': group_by,
                    'measure': measure,
                    'order': None,
                    'mode': chart_type,
                },
                'searchParams': {
                    'context': context or {'group_by': []},
                    'domain': domain or '[]',
                    'groupBy': group_by,
                    'orderBy': [],
                },
            },
            'humanize': True,
            'dataSetStyles': {},
        }
        # A chart with no fieldMatching still loads; it just ignores the
        # dashboard filter bar. This is left as an explicit parameter so
        # the matching can be set without touching the builder again.
        if field_matching is not None:
            data['fieldMatching'] = field_matching
        if chart_type in ('bar', 'line', 'pie', 'combo'):
            data['stacked'] = False
            data['legendPosition'] = 'top'
            data['aggregated'] = False
        self.figures.append({
            'id': chart_id,
            # Anchored to the top-left cell and shifted by a pixel
            # offset: o-spreadsheet sizes a figure in pixels, so the
            # dashboard column the chart sits in becomes its x offset.
            'col': 0, 'row': layout_row,
            'width': width * COL_W - FIGURE_GAP, 'height': CHART_H,
            'offset': {'x': col * COL_W, 'y': 0},
            'tag': 'chart',
            'data': data,
        })
        return data

    def scorecard(self, col, row, width, height, title, key_value,
                  baseline=None, description=''):
        """A single KPI, drawn as a scorecard figure."""
        layout_row = self._figure_row_at(row, SCORECARD_H)
        data = {
            'type': 'scorecard',
            'chartId': f'f{len(self.figures)}',   # mirrors Odoo's own dashboards
            'title': {'text': title, 'bold': True, 'color': ACCOUNTANT},
            'background': '',
            'keyValue': key_value,
            'humanize': True,
            'baselineColorUp': GOOD,
            'baselineColorDown': BAD,
            'baselineMode': 'percentage',
        }
        if baseline:
            data['baseline'] = baseline
            data['baselineDescr'] = {'text': description or 'vs previous'}
        self.figures.append({
            'id': f'f{len(self.figures)}',
            # Anchored to the top-left cell and shifted by a pixel
            # offset: o-spreadsheet sizes a figure in pixels, so the
            # dashboard column the scorecard sits in becomes its x offset.
            'col': 0, 'row': layout_row,
            'width': width * COL_W - FIGURE_GAP, 'height': SCORECARD_H,
            'offset': {'x': col * COL_W, 'y': 0},
            'tag': 'chart',
            'data': data,
        })

    def text(self, col, row, width, height, title, subtitle=''):
        """A section heading, rendered as merged, styled cells.

        A figure tagged "chart" is always read as a chart definition
        by o-spreadsheet, and "text" is not a registered chart type,
        so a heading has to be cells rather than a figure.
        """
        # Finish any figure row so the heading starts below it.
        self._close_figure_row()
        # The heading spans every dashboard column so it reads as a
        # divider across the full width.
        last_col = self.columns - 1

        # Style *definitions* live in the document-level "styles"
        # registry, keyed by id. The sheet-level "styles" map is
        # position -> id, and its keys are XC ranges: o-spreadsheet
        # runs every key through toZone().
        title_style = len(self.doc_styles) + 1
        self.doc_styles[str(title_style)] = {
            'bold': True, 'fontSize': 15, 'textColor': ACCOUNTANT,
        }
        ref = self._cell_ref(col, self._row)
        self.dash_styles[ref] = title_style
        self.dash_cells[ref] = title
        self._row_heights[self._row] = HEADING_H
        self.dash_merges.append(f'{ref}:{self._cell_ref(last_col, self._row)}')
        self._row += 1

        if subtitle:
            subtitle_style = len(self.doc_styles) + 1
            self.doc_styles[str(subtitle_style)] = {
                'fontSize': 11, 'textColor': '#6B7280',
            }
            ref = self._cell_ref(col, self._row)
            self.dash_styles[ref] = subtitle_style
            self.dash_cells[ref] = subtitle
            self._row_heights[self._row] = SUBHEADING_H
            self.dash_merges.append(f'{ref}:{self._cell_ref(last_col, self._row)}')
            self._row += 1

    # -- document -------------------------------------------------------
    def build(self):
        # Close any figure row still open so its height is recorded,
        # then emit the grid. o-spreadsheet reads a column or row size
        # from the "size" key, so both maps are objects keyed by index.
        self._close_figure_row()
        cols = {str(i): {'size': COL_W} for i in range(self.columns)}
        rows = {str(r): {'size': h} for r, h in self._row_heights.items()}
        return {
            'version': SHEET_VERSION,
            'revisionId': 'START_REVISION',
            'settings': {'locale': {}},
            'sheets': [
                {
                    'id': DASHBOARD_SHEET,
                    'name': 'Dashboard',
                    'colNumber': self.columns,
                    'rowNumber': self._row + 1,
                    'rows': rows, 'cols': cols,
                    'merges': self.dash_merges, 'cells': self.dash_cells,
                    'styles': self.dash_styles, 'formats': {},
                    'borders': {}, 'conditionalFormats': [],
                    'dataValidationRules': [], 'tables': [],
                    'areGridLinesVisible': True,
                    'isVisible': True,
                    'backgroundColor': '#F9FAFB',
                    'figures': self.figures,
                    'headerGroups': {'ROW': [], 'COL': []},
                    'comments': {},
                },
                {
                    'id': DATA_SHEET,
                    'name': 'Data',
                    'colNumber': 10, 'rowNumber': 60,
                    'rows': {}, 'cols': {}, 'merges': [],
                    'cells': self.cells, 'styles': {}, 'formats': {},
                    'borders': {}, 'conditionalFormats': [],
                    'dataValidationRules': [], 'tables': [],
                    'areGridLinesVisible': False,
                    'isVisible': False,
                    'figures': [],
                    'headerGroups': {'ROW': [], 'COL': []},
                    'comments': {},
                },
            ],
            'pivots': self.pivots,
            'globalFilters': [],
            'lists': {},
            'styles': self.doc_styles, 'formats': {}, 'borders': {},
            'odooLinkReferences': {},
            # The loader treats this as a flag: it only repairs figure ids
            # when it is absent. A list here would be ignored, but a flag
            # is what the current format uses.
            'uniqueFigureIds': True,
            'listNextId': 1,
            'pivotNextId': self._next_pivot,
            'customTableStyles': {},
            'namedRanges': {},
        }


# A domain with a single condition carries no "&": the operator combines
# two conditions, so "&" followed by one is rejected by the browser's
# domain parser as "missing 1 segment(s)".
CONFIRMED = '[("state", "not in", ("draft", "sent", "cancel"))]'
QUOTATIONS = '[("state", "in", ("draft", "sent"))]'


def build_overview():
    d = Dashboard('Management Overview')

    # Date domains are evaluated in the browser, so they may only use the
    # functions the py interpreter exposes: context_today(), relativedelta()
    # and strftime(). date_trunc() is not among them, so the first day of a
    # month is found by subtracting a relativedelta and formatting it.
    this_month = ("[\"&\", (\"state\", \"not in\", (\"draft\", \"sent\", \"cancel\")), "
                  "(\"date\", \">=\", context_today().strftime(\"%Y-%m-01\")), "
                  "(\"date\", \"<=\", context_today().strftime(\"%Y-%m-%d\"))]")
    last_month = ("[\"&\", (\"state\", \"not in\", (\"draft\", \"sent\", \"cancel\")), "
                  "(\"date\", \">=\", (context_today() - relativedelta(months=1)).strftime(\"%Y-%m-01\")), "
                  "(\"date\", \"<\", context_today().strftime(\"%Y-%m-01\"))]")
    today_domain = ("[\"&\", (\"state\", \"not in\", (\"draft\", \"sent\", \"cancel\")), "
                    "(\"date\", \">=\", context_today().strftime(\"%Y-%m-%d\")), "
                    "(\"date\", \"<=\", context_today().strftime(\"%Y-%m-%d\"))]")

    # Add company_id as row groupby so the pivot can compute grand totals correctly
    # (sale.report SQL view is pre-grouped, so empty groupby returns detail rows)
    SCALAR_ROWS = ['company_id']
    
    sales_now = d.pivot('sales this month', 'sale.report', this_month,
                        [('revenue', 'price_subtotal', 'sum'), ('cost', 'purchase_price', 'sum'),
                         ('margin', 'margin', 'sum')],
                        rows=SCALAR_ROWS)
    sales_prev = d.pivot('sales last month', 'sale.report', last_month,
                         [('revenue', 'price_subtotal', 'sum')],
                         rows=SCALAR_ROWS)
    sales_today = d.pivot('sales today', 'sale.report', today_domain,
                          [('revenue', 'price_subtotal', 'sum')],
                          rows=SCALAR_ROWS)
    pipeline = d.pivot('open quotations', 'sale.report', QUOTATIONS,
                       [('value', 'price_subtotal', 'sum')],
                       rows=SCALAR_ROWS)

    d.text(0, 0, 3, 24, 'Trading', 'This month against last month')
    d.scorecard(0, 2, 1, 1, 'Sales this month', *d.kpi(
        'Sales this month', sales_now, 'revenue',
        comparison=f'=PIVOT.VALUE({sales_prev},"revenue")'))
    d.scorecard(1, 2, 1, 1, 'Cost of sales', *d.kpi(
        'Cost of sales', sales_now, 'cost'))
    d.scorecard(2, 2, 1, 1, 'Gross profit', *d.kpi(
        'Gross profit', sales_now, 'margin'))
    d.scorecard(3, 2, 1, 1, 'Sales today', *d.kpi(
        'Sales today', sales_today, 'revenue'))
    d.scorecard(4, 2, 1, 1, 'Open quotations', *d.kpi(
        'Open quotations', pipeline, 'value'))

    d.text(0, 11, 6, 24, 'Trend', 'Monthly, last 12 months')
    d.chart(0, 13, 3, 2, 'Sales by month', 'bar',
            'cognify_management_reports.action_sales_by_month', 'price_subtotal',
            ['date:month'], CONFIRMED)
    d.chart(3, 13, 3, 2, 'Gross margin by month', 'bar',
            'cognify_management_reports.action_cost_of_sales', 'margin',
            ['date:month'], CONFIRMED)
    d.chart(0, 26, 3, 2, 'Sales by category', 'pie',
            'cognify_management_reports.action_sales_by_category', 'price_subtotal',
            ['categ_id'], CONFIRMED)
    d.chart(3, 26, 3, 2, 'Sales by channel', 'bar',
            'cognify_management_reports.action_sales_by_channel', 'price_subtotal',
            ['team_id'], CONFIRMED)
    return d


def build_customers():
    d = Dashboard('Customers')
    owing = '[("outstanding", ">", 0)]'
    overdue = '[("overdue", ">", 0)]'
    SCALAR_ROWS = ['company_id']

    totals = d.pivot('customer totals', 'cognify.customer.credit', '[]',
                     [('billed', 'billed', 'sum'), ('outstanding', 'outstanding', 'sum'),
                      ('overdue', 'overdue', 'sum'), ('available', 'credit_available', 'sum')],
                     rows=SCALAR_ROWS)
    owing_p = d.pivot('customers owing', 'cognify.customer.credit', owing,
                      [('outstanding', 'outstanding', 'sum')],
                      rows=SCALAR_ROWS)
    over_p = d.pivot('customers over limit', 'cognify.customer.credit',
                     '[("over_limit", "=", true)]', [('over', 'outstanding', 'sum')],
                     rows=SCALAR_ROWS)

    d.text(0, 0, 6, 24, 'Money owed', 'Across every customer')
    d.scorecard(0, 2, 1, 1, 'Invoiced', *d.kpi('Invoiced', totals, 'billed'))
    d.scorecard(1, 2, 1, 1, 'Outstanding', *d.kpi('Outstanding', totals, 'outstanding'))
    d.scorecard(2, 2, 1, 1, 'Overdue', *d.kpi('Overdue', totals, 'overdue'))
    d.scorecard(3, 2, 1, 1, 'Credit available', *d.kpi('Credit available', totals, 'available'))

    d.text(0, 11, 6, 24, 'Who to chase', '')
    d.chart(0, 13, 3, 2, 'Largest balances', 'bar',
            'cognify_management_reports.action_customer_credit', 'outstanding',
            ['partner_name'], owing, extra='cognify.customer.credit')
    d.chart(3, 13, 3, 2, 'Overdue by customer', 'bar',
            'cognify_management_reports.action_customer_credit', 'overdue',
            ['partner_name'], overdue, extra='cognify.customer.credit')
    d.chart(0, 26, 3, 2, 'Sales by customer', 'bar',
            'cognify_management_reports.action_sales_by_customer', 'price_subtotal',
            ['partner_id'], CONFIRMED)
    d.chart(3, 26, 3, 2, 'Balances by sales team', 'bar',
            'cognify_management_reports.action_customer_credit', 'outstanding',
            ['team_id'], owing, extra='cognify.customer.credit')
    return d


def build_staff():
    d = Dashboard('Staff')
    year = "[('month', '>=', context_today().strftime('%Y-01-01'))]"
    SCALAR_ROWS = ['company_id']

    totals = d.pivot('staff totals', 'cognify.staff.report', year,
                     [('hours', 'hours_worked', 'sum'), ('overtime', 'overtime_hours', 'sum'),
                      ('late', 'late_arrivals', 'sum'), ('leave', 'leave_taken', 'sum'),
                      ('available', 'leave_available', 'sum')],
                     rows=SCALAR_ROWS)

    d.text(0, 0, 6, 24, 'This year', 'Hours, overtime and time off')
    d.scorecard(0, 2, 1, 1, 'Hours worked', *d.kpi('Hours worked', totals, 'hours'))
    d.scorecard(1, 2, 1, 1, 'Overtime', *d.kpi('Overtime', totals, 'overtime'))
    d.scorecard(2, 2, 1, 1, 'Late arrivals', *d.kpi('Late arrivals', totals, 'late'))
    d.scorecard(3, 2, 1, 1, 'Time off taken', *d.kpi('Time off taken', totals, 'leave'))
    d.scorecard(4, 2, 1, 1, 'Leave available', *d.kpi('Leave available', totals, 'available'))

    d.text(0, 11, 6, 24, 'By person', '')
    d.chart(0, 13, 3, 2, 'Hours by employee', 'bar',
            'cognify_management_reports.action_staff_hours', 'hours_worked',
            ['employee_name'], year, extra='cognify.staff.report')
    d.chart(3, 13, 3, 2, 'Overtime by employee', 'bar',
            'cognify_management_reports.action_staff_hours', 'overtime_hours',
            ['employee_name'], year, extra='cognify.staff.report')
    d.chart(0, 26, 3, 2, 'Time off by month', 'bar',
            'cognify_management_reports.action_staff_leave', 'leave_taken',
            ['month:month'], year, extra='cognify.staff.report')
    d.chart(3, 26, 3, 2, 'Leave still available', 'bar',
            'cognify_management_reports.action_staff_leave', 'leave_available',
            ['employee_name'], year, extra='cognify.staff.report')
    return d


def build_products():
    d = Dashboard('Products')

    # Add company_id as row groupby so the pivot can compute grand totals correctly
    SCALAR_ROWS = ['company_id']
    totals = d.pivot('product totals', 'sale.report', CONFIRMED,
                     [('revenue', 'price_subtotal', 'sum'), ('qty', 'product_uom_qty', 'sum'),
                      ('margin', 'margin', 'sum'), ('cost', 'purchase_price', 'sum')],
                     rows=SCALAR_ROWS)

    d.text(0, 0, 6, 24, 'What is moving', '')
    d.scorecard(0, 2, 1, 1, 'Revenue', *d.kpi('Revenue', totals, 'revenue'))
    d.scorecard(1, 2, 1, 1, 'Units sold', *d.kpi('Units sold', totals, 'qty'))
    d.scorecard(2, 2, 1, 1, 'Cost of sales', *d.kpi('Cost of sales', totals, 'cost'))
    d.scorecard(3, 2, 1, 1, 'Gross profit', *d.kpi('Gross profit', totals, 'margin'))

    d.text(0, 11, 6, 24, 'Turnover', '')
    d.chart(0, 13, 3, 2, 'Best sellers by revenue', 'bar',
            'cognify_management_reports.action_product_turnover', 'price_subtotal',
            ['product_id'], CONFIRMED)
    d.chart(3, 13, 3, 2, 'Slowest movers', 'bar',
            'cognify_management_reports.action_product_turnover_lowest', 'price_subtotal',
            ['product_id'], CONFIRMED)
    d.chart(0, 26, 3, 2, 'Revenue by category', 'bar',
            'cognify_management_reports.action_sales_by_category', 'price_subtotal',
            ['categ_id'], CONFIRMED)
    d.chart(3, 26, 3, 2, 'Salesperson by month', 'bar',
            'cognify_management_reports.action_salesperson_performance', 'price_subtotal',
            ['user_id'], CONFIRMED)
    return d


def build_finance():
    """Money in, money out, and what is owed and how badly."""
    d = Dashboard('Finance and Cash')
    SCALAR_ROWS = ['company_id']

    totals = d.pivot('cash totals', 'cognify.cash.position', '[]',
                     [('in', 'receipts'), ('out', 'payments'), ('net', 'net'),
                      ('unreconciled', 'unreconciled')],
                     rows=SCALAR_ROWS)
    ageing = d.pivot('debt ageing', 'cognify.customer.ageing', '[]',
                     [('current', 'bucket_current'), ('d30', 'bucket_30'),
                      ('d60', 'bucket_60'), ('d90', 'bucket_90'),
                      ('d90plus', 'bucket_90_plus'), ('overdue', 'overdue')],
                     rows=SCALAR_ROWS)
    credit = d.pivot('credit', 'cognify.customer.credit', '[]',
                     [('limit', 'credit_limit'), ('available', 'credit_available'),
                      ('outstanding', 'outstanding')],
                     rows=SCALAR_ROWS)

    d.text(0, 0, 6, 24, 'Cash and bank', 'From the general ledger')
    d.scorecard(0, 2, 1, 1, 'Money in', *d.kpi('Money in', totals, 'in'))
    d.scorecard(1, 2, 1, 1, 'Money out', *d.kpi('Money out', totals, 'out'))
    d.scorecard(2, 2, 1, 1, 'Net movement', *d.kpi('Net movement', totals, 'net'))
    d.scorecard(3, 2, 1, 1, 'Unreconciled', *d.kpi('Unreconciled', totals, 'unreconciled'))

    d.text(0, 11, 6, 24, 'Debt', 'How old the money owed is')
    d.scorecard(0, 13, 1, 1, 'Not due yet', *d.kpi('Not due yet', ageing, 'current'))
    d.scorecard(1, 13, 1, 1, '1 to 30 days', *d.kpi('1 to 30 days', ageing, 'd30'))
    d.scorecard(2, 13, 1, 1, '31 to 60 days', *d.kpi('31 to 60 days', ageing, 'd60'))
    d.scorecard(3, 13, 1, 1, '61 to 90 days', *d.kpi('61 to 90 days', ageing, 'd90'))
    d.scorecard(4, 13, 1, 1, 'Over 90 days', *d.kpi('Over 90 days', ageing, 'd90plus'))

    d.text(0, 22, 6, 24, 'Trends', '')
    d.chart(0, 24, 3, 2, 'Money in and out by month', 'bar',
            'cognify_management_reports.action_cash_position', 'receipts',
            ['month:month'], extra='cognify.cash.position')
    d.chart(3, 24, 3, 2, 'Closing balance by month', 'line',
            'cognify_management_reports.action_cash_position', 'closing',
            ['month:month'], extra='cognify.cash.position')
    d.chart(0, 37, 3, 2, 'Ageing bands', 'bar',
            'cognify_management_reports.action_customer_ageing', 'overdue',
            ['age_band'], extra='cognify.customer.ageing')
    d.chart(3, 37, 3, 2, 'Largest balances by customer', 'bar',
            'cognify_management_reports.action_customer_credit', 'outstanding',
            ['partner_name'], extra='cognify.customer.credit')
    return d


def build_stock():
    """What the stock is worth, and what is not moving."""
    d = Dashboard('Stock')
    SCALAR_ROWS = ['company_id']

    totals = d.pivot('stock totals', 'cognify.stock.health', '[]',
                     [('cost', 'value_cost', 'sum'), ('retail', 'value_retail', 'sum'),
                      ('margin', 'margin_value', 'sum'), ('onhand', 'qty_on_hand', 'sum')],
                     rows=SCALAR_ROWS)
    problems = d.pivot('stock problems', 'cognify.stock.health',
                       '[("stock_status", "in", ("dead", "low", "slow", "out"))]',
                       [('value', 'value_cost', 'sum')],
                       rows=SCALAR_ROWS)

    d.text(0, 0, 6, 24, 'What the stock is worth', '')
    d.scorecard(0, 2, 1, 1, 'Value at cost', *d.kpi('Value at cost', totals, 'cost'))
    d.scorecard(1, 2, 1, 1, 'Value at retail', *d.kpi('Value at retail', totals, 'retail'))
    d.scorecard(2, 2, 1, 1, 'Margin in stock', *d.kpi('Margin in stock', totals, 'margin'))
    d.scorecard(3, 2, 1, 1, 'Units on hand', *d.kpi('Units on hand', totals, 'onhand'))
    d.scorecard(4, 2, 1, 1, 'Value in problem lines',
                *d.kpi('Value in problem lines', problems, 'value'))

    d.text(0, 11, 6, 24, 'What is not moving', '')
    d.chart(0, 13, 3, 2, 'Value at cost by category', 'bar',
            'cognify_management_reports.action_stock_health', 'value_cost',
            ['categ_id'], extra='cognify.stock.health')
    d.chart(3, 13, 3, 2, 'Stock status', 'pie',
            'cognify_management_reports.action_stock_health', 'value_cost',
            ['stock_status'], extra='cognify.stock.health')
    d.chart(0, 26, 3, 2, 'Dead and slow movers', 'bar',
            'cognify_management_reports.action_stock_dead', 'value_cost',
            ['product_name'], extra='cognify.stock.health')
    d.chart(3, 26, 3, 2, 'Low and out of stock', 'bar',
            'cognify_management_reports.action_stock_low', 'qty_available',
            ['product_name'], extra='cognify.stock.health')
    return d


def build_people():
    """Targets against actual, expenses and time."""
    d = Dashboard('Targets and People')
    SCALAR_ROWS = ['company_id']

    year = "[('month', '>=', context_today().strftime('%Y-01-01'))]"
    attainment = d.pivot('attainment', 'cognify.rep.achievement', year,
                         [('revenue', 'revenue', 'sum'), ('target', 'target_revenue', 'sum'),
                          ('gap', 'gap', 'sum'), ('attainment', 'attainment', 'avg')],
                         rows=SCALAR_ROWS)
    expenses = d.pivot('expenses', 'cognify.expense.report', year,
                       [('spend', 'expense_total', 'sum'), ('claims', 'expense_count', 'sum'),
                        ('ratio', 'expense_ratio', 'avg')],
                       rows=SCALAR_ROWS)
    utilisation = d.pivot('utilisation', 'cognify.utilisation.report', year,
                          [('hours', 'hours', 'sum'), ('billable', 'billable_hours', 'sum'),
                           ('cost', 'cost', 'sum'), ('revenue', 'revenue', 'sum'),
                           ('coverage', 'timesheet_coverage', 'avg')],
                          rows=SCALAR_ROWS)

    d.text(0, 0, 6, 24, 'Sales team', 'This year, against target')
    d.scorecard(0, 2, 1, 1, 'Revenue', *d.kpi('Revenue', attainment, 'revenue'))
    d.scorecard(1, 2, 1, 1, 'Target', *d.kpi('Target', attainment, 'target'))
    d.scorecard(2, 2, 1, 1, 'Still to go', *d.kpi('Still to go', attainment, 'gap'))
    d.scorecard(3, 2, 1, 1, 'Attainment %',
                *d.kpi('Attainment %', attainment, 'attainment'))

    d.text(0, 11, 6, 24, 'Expenses and time', 'This year')
    d.scorecard(0, 13, 1, 1, 'Expenses claimed', *d.kpi('Expenses', expenses, 'spend'))
    d.scorecard(1, 13, 1, 1, 'Claims', *d.kpi('Claims', expenses, 'claims'))
    d.scorecard(2, 13, 1, 1, 'Hours recorded', *d.kpi('Hours', utilisation, 'hours'))
    d.scorecard(3, 13, 1, 1, 'Billable hours', *d.kpi('Billable', utilisation, 'billable'))
    d.scorecard(4, 13, 1, 1, 'Timesheet coverage',
                *d.kpi('Coverage', utilisation, 'coverage'))

    d.text(0, 22, 6, 24, 'By person', '')
    d.chart(0, 24, 3, 2, 'Revenue against target', 'bar',
            'cognify_management_reports.action_rep_achievement', 'revenue',
            ['user_name'], year, extra='cognify.rep.achievement')
    d.chart(3, 24, 3, 2, 'Attainment by person', 'bar',
            'cognify_management_reports.action_rep_achievement', 'attainment',
            ['user_name'], year, extra='cognify.rep.achievement')
    d.chart(0, 37, 3, 2, 'Expenses by person', 'bar',
            'cognify_management_reports.action_expense_report', 'expense_total',
            ['employee_name'], year, extra='cognify.expense.report')
    d.chart(3, 37, 3, 2, 'Billable share by person', 'bar',
            'cognify_management_reports.action_utilisation_report', 'billable_percent',
            ['employee_name'], year, extra='cognify.utilisation.report')
    return d


DASHBOARDS = [
    ('management_overview.json', build_overview),
    ('sales_products.json', build_products),
    ('customers.json', build_customers),
    ('staff.json', build_staff),
    ('finance.json', build_finance),
    ('stock.json', build_stock),
    ('targets_people.json', build_people),
]

if __name__ == '__main__':
    os.makedirs(OUT_DIR, exist_ok=True)
    built = []
    for filename, builder in DASHBOARDS:
        dashboard = builder()
        built.append((filename, dashboard.build(), dashboard))

    # Collect every problem first and refuse to write anything while the
    # output is invalid. Writing then raising would still leave the broken
    # document on disk for an upgrade to pick up, which is exactly the outcome
    # the check exists to prevent.
    problems = []
    for filename, document, _ in built:
        problems.extend(validate(document))
    if problems:
        raise SystemExit(
            'no dashboard file was written; invalid dashboards:\n  '
            + '\n  '.join(problems))

    for filename, document, dashboard in built:
        path = os.path.join(OUT_DIR, filename)
        with open(path, 'w') as handle:
            json.dump(document, handle, indent=1)
        print(f'{filename:26s} {len(dashboard.figures):2d} figures, '
              f'{len(dashboard.pivots):2d} pivots, {len(dashboard.cells):2d} cells')
    print(f'\nwritten to {OUT_DIR}')