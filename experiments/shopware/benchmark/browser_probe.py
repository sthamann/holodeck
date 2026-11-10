"""Run a model-emitted binding through a real browser and the durable VM."""
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time

from graph_contract import Deck, Store, config, graph, initial, root_of
from lessons import expected, request_for


SCOPE_EVENTS = {'storefront': 'cart.add', 'checkout': 'checkout.place', 'orders': 'order_delivery.ship'}


def page(model_view, values, root):
    columns = model_view['columns']
    return f"""<!doctype html><html lang="de"><meta charset="utf-8"><title>Holodeck browser probe</title>
    <table><thead><tr>{''.join(f'<th>{escape(field)}</th>' for field in columns)}</tr></thead>
    <tbody><tr>{''.join(f'<td>{escape(str(values[field]))}</td>' for field in columns)}</tr></tbody></table>
    <button type="button" data-root="{escape(root, quote=True)}">{escape(model_view['action'])}</button>
    <output id="result">pending</output>
    <script>
    const control = document.querySelector('button');
    control.onclick = async () => {{
      const response = await fetch('/command', {{method: 'POST', body: control.dataset.root}});
      document.querySelector('#result').textContent = await response.text();
    }};
    window.addEventListener('load', () => control.click(), {{once: true}});
    </script></html>"""


def click(chrome, html, root, apply):
    class BrowserHandler(BaseHTTPRequestHandler):
        def send(self, code, body, mime='text/plain'):
            self.send_response(code)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            self.send(200, html, 'text/html') if self.path == '/' else self.send(404, 'unknown')

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if self.path != '/command' or not 0 < size < 100 or self.rfile.read(size).decode() != root:
                self.send(400, 'Invalid row binding')
                return
            try:
                apply()
            except (KeyError, ValueError, TypeError) as error:
                self.send(409, str(error))
                return
            self.send(200, 'effect-committed')

        def log_message(self, *_):
            pass

    with HTTPServer(('127.0.0.1', 0), BrowserHandler) as server, TemporaryDirectory() as temp:
        server.timeout = 0.1
        output = Path(temp) / 'dom.html'
        log = Path(temp) / 'chrome.log'
        with output.open('w') as rendered, log.open('w') as errors:
            process = subprocess.Popen([str(chrome), '--headless', '--disable-gpu', '--no-first-run',
                                        f'--user-data-dir={temp}/profile', '--dump-dom',
                                        '--virtual-time-budget=6500',
                                        f'http://127.0.0.1:{server.server_port}/'],
                                       stdout=rendered, stderr=errors)
            timeout = time.monotonic() + 25
            while process.poll() is None and time.monotonic() < timeout:
                server.handle_request()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
                raise RuntimeError('Browser did not finish the binding probe')
        if process.returncode != 0 or '<output id="result">effect-committed</output>' not in output.read_text():
            raise RuntimeError('Chrome did not observe the committed effect')
        return output.read_text()


def browser_step(chrome, model, store, row, i, scope, ui_cases, directory):
    tenant, root = row['channel'], row['product']
    state = store.read(tenant, root)
    prompt = {'id': 'project', 'kind': 'project', 'root': root, 'args': {'text': ui_cases[scope][0]}}
    projected = model.forward(state, prompt, tenant)['writes']['holo.view']
    if projected != ui_cases[scope][1] or projected['binding'] != 'row.root':
        raise AssertionError('The new live projection diverged from the verified binding')
    fields = root_of(state)['fields']
    step = row['events'][i]

    def apply():
        event = {'id': f'{root}:{i}:browser', 'kind': projected['action'], 'root': root, 'args': step['args']}
        store.apply(tenant, event, model, i)

    rendered = click(chrome, page(projected, fields, root), root, apply)
    target = expected(step)
    after = root_of(store.read(tenant, root))['fields']
    if after != {**fields, **target}:
        raise AssertionError('Browser action did not match the reference effect')
    (directory / f'{scope}.html').write_text(rendered)
    return {'verified': True, 'shop': row['shop'], 'action': projected['action'],
            'columns': projected['columns'], 'effect': target}


def verify_chain(chrome, model, row, ui_cases, directory):
    tenant, root = row['channel'], row['product']
    by_event = {event: scope for scope, event in SCOPE_EVENTS.items()}
    store = Store(directory / 'same-journey.sqlite')
    proof = {}
    try:
        store.seed(graph(tenant, root, initial(row), config(row)))
        for i, step in enumerate(row['events']):
            scope = by_event.get(step['event'])
            if scope and scope not in proof:
                proof[scope] = browser_step(chrome, model, store, row, i, scope, ui_cases, directory)
            else:
                before = root_of(store.read(tenant, root))['fields']
                store.apply(tenant, request_for(step, root, i), model, i)
                after = root_of(store.read(tenant, root))['fields']
                if after != {**before, **expected(step)}:
                    raise AssertionError(f"Browser journey diverged at {step['event']}")
        proof['same_journey'] = {
            'verified': True, 'shop': row['shop'], 'root': root, 'events': len(row['events']),
            'final_fields': root_of(store.read(tenant, root))['fields'],
        }
        return proof
    finally:
        store.close()


def verify(chrome, predict, name, rows, result, ui_cases, directory):
    model = Deck(predict, name)
    directory.mkdir(exist_ok=False)
    correct_roots = {scope: {entry.get('root') for entry in result['ui']
                            if entry['scope'] == scope and entry['bound_action_correct']}
                     for scope in SCOPE_EVENTS}
    complete = {(row['shop'], row['episode']) for row in result['journeys'] if row['correct']}
    full = {}
    for row in rows:
        if (row['shop'], row['episode']) in complete and all(
                row['product'] in roots for roots in correct_roots.values()):
            full.setdefault(row['shop'], row)
    if full:
        trials = []
        for index, row in enumerate(full.values(), start=1):
            target = directory / f'journey-{index}'
            target.mkdir()
            trials.append(verify_chain(chrome, model, row, ui_cases, target))
        proof = {**trials[0], 'verified_journeys': trials}
        (directory / 'browser.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
        return proof

    proof = {}
    for scope, target_event in SCOPE_EVENTS.items():
        correct_views = correct_roots[scope]
        source = None
        for row in rows:
            before = next(i for i, step in enumerate(row['events']) if step['event'] == target_event)
            if row['product'] not in correct_views:
                continue
            if all(step['correct'] for step in result['steps'] if step['shop'] == row['shop']
                   and step['episode'] == row['episode'] and step['step'] <= before):
                source = row, before
                break
        if source is None:
            proof[scope] = {'verified': False, 'reason': 'No correct model-generated prefix and binding'}
            continue
        row, i = source
        tenant, root = row['channel'], row['product']
        store = Store(directory / f'{scope}.sqlite')
        try:
            store.seed(graph(tenant, root, initial(row), config(row)))
            for ordinal, step in enumerate(row['events'][:i]):
                store.apply(tenant, request_for(step, root, ordinal), model, ordinal)
            proof[scope] = browser_step(chrome, model, store, row, i, scope, ui_cases, directory)
        finally:
            store.close()
    (directory / 'browser.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    return proof
