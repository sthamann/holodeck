"""Record native MyNotes requests and replay the compiled title PATCH offline."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
from time import time_ns
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
HOST = '127.0.0.1'


def millis(value):
    delta = datetime.fromisoformat(value.strip('"')) - EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def stamp(value):
    return (EPOCH + timedelta(milliseconds=value)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def http(origin, route, method='GET', data=None, **headers):
    request = Request(origin + route, None if data is None else json.dumps(data).encode(),
                      {'Origin': origin, 'Content-Type': 'application/json', **headers}, method=method)
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers.get('ETag'), json.loads(response.read())


def read_row(db, slug):
    return list(db.execute('SELECT title, content, updated_at FROM notes WHERE slug=?', (slug,)).fetchone())


def capture(port, native_db, destination):
    origin = f'http://{HOST}:{port}'
    rows = []
    with sqlite3.connect(native_db) as db:
        for clock_mode in ('native', 'ahead'):
            for profile in ('write', 'noop', 'stale', 'stale-noop'):
                for sample in range(3):
                    title = f'Reference {profile} {sample}'
                    body = {'title': title, 'content': 'Synthetic test body', 'slug': 'deck-' + uuid4().hex}
                    created, _, record = http(origin, '/api/v1/notes', 'POST', body)
                    assert created == 201, record
                    slug = record['slug']
                    route = '/api/v1/notes/' + slug
                    if clock_mode == 'ahead':
                        # This is a starting fixture, not a patched teacher clock.
                        with db:
                            db.execute('UPDATE notes SET updated_at=? WHERE slug=?',
                                       ('2090-01-01T00:00:00.000Z', slug))
                    before_status, etag, before = http(origin, route)
                    assert before_status == 200
                    assert etag == '"' + before['updated_at'] + '"'
                    before_row = read_row(db, slug)
                    request = {'title': title if profile.endswith('noop') else title + ' revised'}
                    if_match = ('"' + stamp(millis(etag) - 1) + '"') if profile.startswith('stale') else etag
                    start = time_ns() // 1_000_000
                    status, returned_tag, returned = http(origin, route, 'PATCH', request, **{'If-Match': if_match})
                    end = time_ns() // 1_000_000
                    assert end >= start
                    after_status, after_tag, after = http(origin, route)
                    assert after_status == 200 and after_tag == '"' + after['updated_at'] + '"'
                    if status == 200:
                        assert returned == after and returned_tag == after_tag
                    after_row = read_row(db, slug)
                    assert before_row == [before[field] for field in ('title', 'content', 'updated_at')]
                    assert after_row == [after[field] for field in ('title', 'content', 'updated_at')]
                    rows.append({'clock_mode': clock_mode, 'profile': profile, 'route': route, 'request': request,
                                 'if_match': if_match, 'clock_interval': [start, end], 'before': before,
                                 'native_patch_status': status, 'native_after': after})
    destination.write_text(json.dumps(rows, indent=2) + '\n')
    print(f'{len(rows)} native requests with independent GET and SQLite readback')


def circuit_outputs(binary, checkpoint, inputs):
    stdout = subprocess.run([binary, checkpoint], input=''.join(json.dumps(row) + '\n' for row in inputs),
                            text=True, capture_output=True, check=True, env={}).stdout
    return [json.loads(line) for line in stdout.splitlines()]


def inputs_for(row):
    before = row['before']
    request = row['request']
    if (not request.keys() <= {'title', 'content', 'tags'}
            or request.get('content', before['content']) != before['content']
            or request.get('tags', []) != [] or before['tags'] != []
            or not request['title'] or request['title'] != request['title'].strip()
            or len(request['title']) > 200):
        raise ValueError('request is outside the title-edit slice')
    titles = list(dict.fromkeys((before['title'], row['request']['title'])))
    old_title, new_title = (titles.index(title) for title in (before['title'], row['request']['title']))
    revision = millis(before['updated_at'])
    inputs = [[old_title, new_title, revision, millis(row['if_match']), clock, revision, 0, 1, 412]
              for clock in row['clock_interval']]
    return titles, inputs


def replay(port, traces, binary, checkpoint, database, report):
    try:
        with socket.create_connection((HOST, port), timeout=0.5):
            raise RuntimeError('the native MyNotes reference must be off')
    except ConnectionRefusedError:
        pass
    rows = json.loads(traces.read_text())
    titles_and_inputs = [inputs_for(row) for row in rows]
    outputs = iter(circuit_outputs(binary, checkpoint, [value for _, pair in titles_and_inputs for value in pair]))
    report_rows = []
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE IF NOT EXISTS replayed (slug TEXT PRIMARY KEY, record TEXT)')
        for row, (titles, _) in zip(rows, titles_and_inputs, strict=True):
            lo, hi = next(outputs), next(outputs)
            status, write, min_revision, title = lo
            assert [status, write, title] == [hi[0], hi[1], hi[3]]
            assert status in (0, 412) and write in (0, 1)
            before, after = row['before'], row['native_after']
            assert (status or 200) == row['native_patch_status']
            assert titles[title] == after['title']
            assert bool(write) == (before != after)
            # The independent local clock is only observed as a request interval.
            # Future-dated native fixtures force exact +1 without revealing the output.
            native_revision = millis(after['updated_at'])
            assert min_revision <= native_revision <= hi[2]
            predicted = before.copy()
            if write:
                predicted.update(title=titles[title], updated_at=stamp(min_revision))
            assert {k:v for k,v in predicted.items() if k != 'updated_at'} == {
                k:v for k,v in after.items() if k != 'updated_at'}
            if min_revision == hi[2]:
                assert predicted == after
            with db:
                db.execute('INSERT INTO replayed VALUES (?,?)', (before['slug'], json.dumps(predicted)))
            report_rows.append({'profile': row['profile'], 'clock_mode': row['clock_mode'],
                                'status': status or 200, 'write': bool(write), 'slug': before['slug'],
                                'exact_revision': min_revision == hi[2], 'expected_local': predicted})
    with sqlite3.connect(database) as db:
        for row in report_rows:
            restored, = db.execute('SELECT record FROM replayed WHERE slug=?', (row['slug'],)).fetchone()
            assert json.loads(restored) == row.pop('expected_local')
    result = {'cases': len(report_rows), 'exact_versions': sum(row['exact_revision'] for row in report_rows),
              'teacher_off': True, 'sqlite_reopen': True, 'results': report_rows}
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(f"{result['cases']}/{result['cases']} native status and persisted fields, "
          f"{result['exact_versions']} exact timestamps, others within observed clock interval")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=55766)
    modes = parser.add_subparsers(dest='mode', required=True)
    record = modes.add_parser('capture')
    for name in ('native_db', 'traces'):
        record.add_argument(name, type=Path)
    check = modes.add_parser('replay')
    for name in ('traces', 'binary', 'checkpoint', 'database', 'report'):
        check.add_argument(name, type=Path)
    args = parser.parse_args()
    if args.mode == 'capture':
        capture(args.port, args.native_db, args.traces)
    else:
        replay(args.port, args.traces, args.binary, args.checkpoint, args.database, args.report)


if __name__ == '__main__':
    main()
