# -*- coding: utf-8 -*-
"""Shape checks on the management dashboards.

A dashboard is an o-spreadsheet document, and o-spreadsheet is
particular about shapes it accepts: it iterates ``globalFilters``
directly, so an empty object there is a browser crash rather than a
dashboard with no filters, and it parses every pivot and chart domain
as a Python expression it then evaluates in the browser. These checks
run against the records the module installs, so a bad payload is
caught before it reaches anyone.
"""
import ast
import json

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestManagementDashboards(TransactionCase):
    """The dashboard documents load through the same shape o-spreadsheet expects."""

    def _dashboards(self):
        return self.env['spreadsheet.dashboard'].search([])

    def test_every_dashboard_parses(self):
        """The payload is JSON, not just a blob that happens to be text."""
        dashboards = self._dashboards()
        self.assertTrue(dashboards, 'no dashboards installed')
        for dashboard in dashboards:
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                self.assertIn('sheets', data)

    def test_global_filters_is_an_array(self):
        """``globalFilters`` must be iterable, even when empty.

        o-spreadsheet imports the filters with ``for (const filter of
        data.globalFilters || [])``. An object does not satisfy that:
        the dashboard stops loading with "object is not iterable".
        """
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                self.assertIsInstance(
                    data.get('globalFilters'), list,
                    f'{dashboard.name}: globalFilters must be an array')

    def test_document_collections_are_objects(self):
        """Everything o-spreadsheet looks up by id is an object."""
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for key in ('pivots', 'lists', 'namedRanges', 'styles',
                            'formats', 'borders', 'customTableStyles'):
                    self.assertIsInstance(data.get(key), dict,
                                          f'{dashboard.name}: {key} must be an object')

    def test_sheets_have_array_and_object_shapes(self):
        """Per-sheet collections keep the shape the importer iterates."""
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for sheet in data['sheets']:
                    for key in ('figures', 'merges', 'conditionalFormats',
                                'dataValidationRules', 'tables'):
                        self.assertIsInstance(sheet.get(key), list,
                                              f'{dashboard.name}:{sheet["id"]}: {key} must be an array')
                    for key in ('cells', 'styles', 'formats', 'borders'):
                        self.assertIsInstance(sheet.get(key), dict,
                                              f'{dashboard.name}:{sheet["id"]}: {key} must be an object')

    def test_pivot_dimensions_are_objects_with_a_field_name(self):
        """A pivot dimension must be an object carrying a ``fieldName``.

        o-spreadsheet reads a dimension like this::

            createPivotDimension(fields, dimension) {
                const field = fields[dimension.fieldName];

        Given a bare string, ``dimension.fieldName`` is undefined, ``field`` is
        undefined, and the dimension comes back with no field, no display name
        and nothing to group by. Nothing throws at import: the document loads,
        the sheet renders, the KPI figures appear -- and every cell reading the
        pivot shows ``#ERROR``, because the pivot cannot be normalised. That is
        how all seven dashboards failed at once while every other check passed.
        """
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for pivot_id, pivot in (data.get('pivots') or {}).items():
                    for axis in ('rows', 'columns'):
                        for dimension in pivot.get(axis) or []:
                            with self.subTest(pivot=pivot_id, axis=axis):
                                self.assertIsInstance(
                                    dimension, dict,
                                    f'{dashboard.name}: pivot {pivot_id} '
                                    f'{axis} entry {dimension!r} is not an '
                                    'object -- its cells will render as '
                                    '#ERROR')
                                self.assertTrue(
                                    dimension.get('fieldName'),
                                    f'{dashboard.name}: pivot {pivot_id} '
                                    f'{axis} entry {dimension!r} has no '
                                    'fieldName')

    def test_pivot_filters_is_an_array(self):
        """Every pivot's ``filters`` must be a list, or simply absent.

        o-spreadsheet builds the pivot's runtime definition with

            (definition.filters ?? []).map((filter) => ...)

        The ``??`` only substitutes the empty list when the key is *absent*. An
        empty mapping is kept as a mapping, ``.map`` is not a function on it,
        and the whole runtime definition construction throws. That qualifies as
        an ordinary load failure in the browser: the document imports, the grid
        and figures appear, and every cell reading the pivot renders ``#ERROR``.

        Being absent is fine, which is what the dashboards Odoo ships itself
        do, so this is written to accept None as well as a list and to reject
        anything array-shaped it cannot iterate.
        """
        allowed = (list, type(None))
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for pivot_id, pivot in (data.get('pivots') or {}).items():
                    with self.subTest(pivot=pivot_id):
                        filters = pivot.get('filters')
                        self.assertIsInstance(
                            filters, allowed,
                            f'{dashboard.name}: pivot {pivot_id} filters is '
                            f'{type(filters).__name__}; o-spreadsheet maps it, '
                            'so only a list or nothing will do')

    def test_pivots_have_unique_measures(self):
        """A KPI reads its pivot by measure id, so ids must not repeat."""
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for pivot_id, pivot in data.get('pivots', {}).items():
                    measures = [measure['id'] for measure in pivot['measures']]
                    self.assertEqual(
                        len(measures), len(set(measures)),
                        f'{dashboard.name}: pivot {pivot_id} repeats a measure')

    def test_charts_use_registered_types(self):
        """An unknown chart type takes the whole dashboard down."""
        allowed = {'bar', 'combo', 'line', 'pie', 'scorecard', 'gauge',
                   'scatter', 'waterfall', 'pyramid', 'radar', 'geo',
                   'funnel', 'sunburst', 'treemap', 'calendar', 'bubble'}
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for sheet in data['sheets']:
                    for figure in sheet.get('figures', []):
                        if figure.get('tag') != 'chart':
                            continue
                        with self.subTest(figure=figure['id']):
                            self.assertIn(figure['data']['type'], allowed)

    def test_domains_are_evaluable_in_the_browser(self):
        """A pivot or chart reads Odoo through its domain.

        The browser parses a domain as a Python expression, checks every
        ``&`` and ``|`` has enough operands, and evaluates the calls
        against a fixed set of builtins. A domain that breaks either rule
        takes the whole dashboard down, so each one is checked here.
        """
        # The py interpreter's allowed builtins. date_trunc is notably
        # absent: a domain that uses it cannot be evaluated client-side.
        functions = {
            'context_today', 'relativedelta', 'strftime',
            'bool', 'set', 'min', 'max',
            'datetime', 'date', 'time', 'timedelta', 'today', 'now',
        }

        def check(domain, where):
            # Odoo stores a domain either as the string this test is here to
            # check, or already evaluated into a plain nested list -- which is
            # what the dashboards Odoo ships itself carry, and what
            # Domain#toJson() produces once there is nothing contextual left in
            # it. ast.parse() is only given the string case: handing it a list
            # raises TypeError, not SyntaxError, and that TypeError is not a
            # failure of the dashboard.
            if isinstance(domain, (list, tuple)):
                return
            if not isinstance(domain, str):
                self.fail(f'{where}: domain is neither a string nor a list '
                          f'({type(domain).__name__})')
            try:
                body = ast.parse(domain, mode='eval').body
            except SyntaxError as exc:
                self.fail(f'{where}: domain is not a valid expression ({exc})')
            if not isinstance(body, (ast.List, ast.Tuple)):
                self.fail(f'{where}: domain is not a list of conditions')
            if not body.elts:
                return
            # Mirror normalizeDomainAST: every & or | needs one more
            # operand, every (field, operator, value) triple satisfies one.
            expected = 1
            for element in body.elts:
                if (isinstance(element, ast.Constant)
                        and isinstance(element.value, str)):
                    if element.value in ('&', '|'):
                        expected += 1
                elif isinstance(element, (ast.List, ast.Tuple)) \
                        and len(element.elts) == 3:
                    expected -= 1
            self.assertLessEqual(
                expected, 0,
                f'{where}: domain is missing {expected} segment(s) -- '
                f'an & or | has too few conditions')
            for node in ast.walk(ast.parse(domain, mode='eval')):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.id if isinstance(func, ast.Name)
                        else func.attr if isinstance(func, ast.Attribute)
                        else None)
                self.assertIn(
                    name, functions,
                    f'{where}: domain calls {name}(), which the browser '
                    f'cannot evaluate')

        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for pivot_id, pivot in data.get('pivots', {}).items():
                    check(pivot.get('domain', '[]'),
                          f'{dashboard.name}: pivot {pivot_id}')
                for sheet in data['sheets']:
                    for figure in sheet.get('figures', []):
                        if figure.get('tag') != 'chart':
                            continue
                        domain = (figure.get('data', {})
                                  .get('dataSource', {})
                                  .get('searchParams', {})
                                  .get('domain', '[]'))
                        check(domain, f'{dashboard.name}: chart {figure["id"]}')

    def test_figures_have_visible_sizes(self):
        """A figure is measured in pixels, not cells.

        o-spreadsheet renders a figure onto a canvas of exactly
        ``width`` by ``height`` pixels. A figure sized in cells
        (a 1 or a 2) is a couple of pixels across and never
        shows up, so every figure is checked to be large enough
        to see.
        """
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for sheet in data['sheets']:
                    for figure in sheet.get('figures', []):
                        with self.subTest(figure=figure['id']):
                            self.assertGreaterEqual(
                                figure['width'], 50,
                                f'{dashboard.name}: figure {figure["id"]} '
                                f'is {figure["width"]}px wide -- too '
                                f'small to see')
                            self.assertGreaterEqual(
                                figure['height'], 50,
                                f'{dashboard.name}: figure {figure["id"]} '
                                f'is {figure["height"]}px tall -- too '
                                f'small to see')

    def test_grid_uses_size_objects(self):
        """o-spreadsheet reads a column or row size from a "size" key.

        A bare number is silently dropped, which resets the grid
        to its defaults and misplaces every figure on it, so both
        maps are checked to be objects carrying a "size" entry.
        """
        for dashboard in self._dashboards():
            with self.subTest(dashboard=dashboard.name):
                data = json.loads(dashboard.spreadsheet_binary_data.content)
                for sheet in data['sheets']:
                    for key in ('cols', 'rows'):
                        for index, entry in (sheet.get(key) or {}).items():
                            self.assertIsInstance(
                                entry, dict,
                                f'{dashboard.name}:{sheet["id"]}: '
                                f'{key}[{index}] must be an object')
                            self.assertIn(
                                'size', entry,
                                f'{dashboard.name}:{sheet["id"]}: '
                                f'{key}[{index}] is missing its "size"')
