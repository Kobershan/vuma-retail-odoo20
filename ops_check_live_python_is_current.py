"""Prove the running server is executing the current addon Python.

An addon's Python is imported when the worker process starts, so a change to a
``models/*.py`` file is on disk long before it is in use: a module upgrade
reloads views, actions and menus, but the workers keep the class object they
imported at boot. That is exactly how a fix can look finished and still not show
up on screen.

This asks the live server whether the methods the fix added exist *on its
registry*, which is the question that matters. An unknown method comes back as
"Invalid method"; a known one that needs a record comes back with the error its
own guard raises, which proves the server resolved the name to real code.

    ./venv/bin/python ops_check_live_python_is_current.py
"""
import requests

BASE = 'http://127.0.0.1:8069'
DB = 'vuma_retail'
LOGIN = 'pictures.tester'
PASSWORD = open('/home/kob/Documents/vuma-retail/.picturetesterpass').read().strip()

session = requests.Session()
auth = session.post(f'{BASE}/web/session/authenticate', timeout=120, json={
    'jsonrpc': '2.0', 'method': 'call',
    'params': {'db': DB, 'login': LOGIN, 'password': PASSWORD}}).json()
if not auth.get('result', {}).get('uid'):
    raise SystemExit('could not log in')
print(f"authenticated as {LOGIN}")


def try_call(model, method, args=None, kwargs=None):
    """Returns (resolved, message): resolved means the name is real code."""
    response = session.post(f'{BASE}/web/dataset/call_kw', timeout=120, json={
        'jsonrpc': '2.0', 'method': 'call',
        'params': {'model': model, 'method': method, 'args': args or [],
                   'kwargs': kwargs or {'context': {}}}})
    body = response.json()
    if 'error' not in body:
        return True, 'called without error'
    data = body['error'].get('data', {})
    message = data.get('message') or json_text(body)
    resolved = 'Invalid method' not in message
    return resolved, message


def json_text(body):
    import json
    return json.dumps(body['error'])[:200]


# Methods the fixes added. If the server still runs the old Python these are
# simply not there, which is how a completed fix shows nothing on screen.
CHECKS = [
    ('customer.goods.pickup', 'action_reload_invoice_lines',
     'reload the invoice lines on demand'),
    ('customer.goods.pickup', '_sync_lines_for_invoice',
     'build the line list from the invoice'),
    ('customer.goods.pickup', '_drop_untouched_lines',
     'drop lines the rep left at zero'),
    ('customer.goods.pickup', '_check_something_was_collected',
     'refuse a collection with nothing on it'),
]

print()
stale = []
for model, method, what in CHECKS:
    resolved, message = try_call(model, method)
    verdict = 'present' if resolved else 'MISSING'
    print(f"  {verdict:<8} {model}.{method}  ({what})")
    if not resolved:
        stale.append(method)
    else:
        print(f"           server said: {message[:110]}")

if stale:
    print(f"\n{len(stale)} method(s) the server does not have: {stale}")
    print("The workers are running the addon as it was when they started.")
    print("Restart the service, then run this again.")
    raise SystemExit(1)

print("\nevery method the fixes added is on the running server")
