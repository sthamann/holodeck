"""Fit view bindings from native browser observations correlated with HTTP effects."""
from collections import defaultdict
from urllib.parse import urlsplit

from learn import induce, leaves, route_pattern


def bind_input(expression):
    op, *args = expression
    if op == 'ref' and args[0] == 'input':
        return ['literal', {'$field': args[1]}]
    if op == 'record':
        return ['record', {key: bind_input(value) for key, value in args[0].items()}]
    if op == 'vector':
        return ['vector', *(bind_input(value) for value in args)]
    return expression


def fit_views(observations, operations):
    groups = defaultdict(list)
    for row in observations:
        identity = row['record']['id']
        if row['route'].count(identity) != 1:
            raise ValueError('Native view does not identify the selected object')
        groups[row['route'].replace(identity, '{record}')].append(row)
    views = []
    for route, rows in groups.items():
        if len(rows) < 2:
            raise ValueError('Each learned view needs distinct native records')
        first = rows[0]
        headings = [key for key in first['record'] if all(row['heading'] == row['record'][key] for row in rows)]
        if len(headings) != 1:
            raise ValueError('Ambiguous heading binding')
        view = {'route': route, 'surface': first['surface'], 'heading': headings[0], 'controls': []}
        if 'action' in first:
            actions = [row['action'] for row in rows]
            observed = actions[0]
            matching = [op for op in operations if op['method'] == observed['method'] and op['writes'] is not None
                        and all(route_pattern(op['path']).fullmatch(urlsplit(item['path']).path) for item in actions)]
            if len(matching) != 1:
                raise ValueError('Native UI action does not resolve to a unique compiled API operation')
            op = matching[0]
            candidates = [path[-1] for path, value in leaves(observed['body']) if path and path[-1] in first['record']
                          and all(dict(leaves(action['body'])).get(path) == action['value'] == action['persisted']
                                  for action in actions)]
            if len(candidates) != 1:
                raise ValueError('Ambiguous native input binding')
            field = candidates[0]
            if len({action['label'] for action in actions}) != 1 or len({action['button'] for action in actions}) != 1:
                raise ValueError('Inconsistent UI labels in the observed locale')
            view['controls'].append({'kind': 'number', 'label': observed['label'], 'field': field})
            inputs = [{'record': row['record'], 'input': {field: row['action']['value']}} for row in rows]
            body = bind_input(induce(inputs, [action['body'] for action in actions]))
            view['action'] = {'label': observed['button'], 'method': op['method'], 'route': op['path'], 'body': body}
        views.append(view)
    return views
