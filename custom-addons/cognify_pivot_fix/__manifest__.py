{
    'name': 'Pivot Percentage Fix',
    'version': '1.0.1',
    'category': 'Hidden',
    'summary': 'Hardens pivot views against percentage measures with no aggregate function in metadata.',
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['web'],
    'assets': {
        # PivotModel is lazy-loaded: it only exists in the *_lazy bundle, so the
        # patch has to be declared there or `patch()` runs against a prototype
        # that does not exist yet and silently does nothing.
        'web.assets_backend_lazy': [
            'cognify_pivot_fix/static/src/js/pivot_fix.js',
        ],
    },
    'installable': True,
}
