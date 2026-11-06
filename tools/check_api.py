"""Check the add-on's Silo requests against Silo's published API contract.

Reads every call with a literal path out of the add-on's code (client.get,
post, pages and so on), then looks for its path, method, query parameters and
fixed values in Silo's OpenAPI file. Silo's API changes without warning: a
value it stops accepting fails a whole sync with HTTP 422 (group=box_set did).

Usage: python tools/check_api.py path/to/silo-server/contracts/api/v2/openapi.json
Exits 1 if anything doesn't match. A path built at run time can't be checked:
those are listed as skipped.
"""
import ast
import json
import os
import re
import sys

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'resources', 'lib', 'silokodi')
METHODS = {'get': 'get', 'post': 'post', 'put': 'put', 'patch': 'patch', 'delete': 'delete', 'pages': 'get'}
NOT_PARAMS = {'timeout', 'body', 'auth', 'profile', 'params'}
PREFIX = '/api/v2'


def literal_path(node):
    """A call's path as text with {} for what's filled in; None if it's all run-time."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        text = literal_path(node.left)
        return re.sub(r'%[sd]', '{}', text) if text else None
    if isinstance(node, ast.JoinedStr):
        parts = [p.value if isinstance(p, ast.Constant) else '{}' for p in node.values]
        return ''.join(parts)
    return None


def calls():
    """(file:line, method, path, {param: literal value or None})."""
    found, skipped = [], []
    for name in sorted(os.listdir(SOURCE)):
        if not name.endswith('.py'):
            continue
        tree = ast.parse(open(os.path.join(SOURCE, name), encoding='utf-8').read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
                continue
            where = '%s:%d' % (name, node.lineno)
            if node.func.attr == 'request' and len(node.args) > 1 and isinstance(node.args[0], ast.Constant):
                method, path = str(node.args[0].value).lower(), literal_path(node.args[1])  # request('POST', path)
            elif node.func.attr in METHODS:
                method, path = METHODS[node.func.attr], literal_path(node.args[0])
            else:
                continue
            if path is None:
                continue
            if not path.startswith('/'):
                continue
            params = {}
            unknown = False
            for keyword in node.keywords:
                if keyword.arg is None:
                    unknown = True
                else:
                    params[keyword.arg] = (keyword.value.value if isinstance(keyword.value, ast.Constant) else None)
            found.append((where, method, path.partition('?')[0], params))
            if unknown:
                skipped.append('%s: **arguments not checked' % where)
    return found, skipped


def same_path(path, route):
    """Segment by segment; what the add-on fills in may be a route's fixed word
    (browse/{}/{} is browse/genre/{slug}), and a route's {x} takes anything."""
    ours, theirs = path.strip('/').split('/'), route.strip('/').split('/')
    return len(ours) == len(theirs) and all(
        a == b or '{' in a or '{' in b for a, b in zip(ours, theirs))


def query_params(spec, operation, shared):
    result = {}
    for param in list(shared) + list(operation.get('parameters') or []):
        if '$ref' in param:
            param = spec['components']['parameters'][param['$ref'].rpartition('/')[2]]
        if param.get('in') == 'query':
            schema = param.get('schema') or {}
            if '$ref' in schema:
                schema = spec['components']['schemas'][schema['$ref'].rpartition('/')[2]]
            result[param['name']] = schema.get('enum')
    return result


def check(spec):
    routes = []
    for route, item in spec['paths'].items():
        short = route[len(PREFIX):] if route.startswith(PREFIX) else route
        routes.append((short, item))
    problems = []
    found, skipped = calls()
    for where, method, path, params in found:
        matches = [(short, item) for short, item in routes if same_path(path, short)]
        # A literal route (/catalog/box-sets) wins over a filled-in one (/catalog/{id}).
        matches.sort(key=lambda m: m[0].count('{'))
        if not matches:
            problems.append('%s: no such path: %s %s' % (where, method.upper(), path))
            continue
        with_method = [m for m in matches if method in m[1]]
        if not with_method:
            problems.append('%s: %s not allowed on %s' % (where, method.upper(), path))
            continue
        short, item = with_method[0]
        known = query_params(spec, item[method], item.get('parameters') or [])
        for name, value in params.items():
            if name in NOT_PARAMS:
                continue
            if name not in known:
                problems.append('%s: %s %s has no parameter %s' % (where, method.upper(), short, name))
            elif value is not None and known[name] and value not in known[name]:
                problems.append('%s: %s %s: %s=%s is not one of %s' % (where, method.upper(), short, name, value, known[name]))
    return len(found), skipped, problems


if __name__ == '__main__':
    with open(sys.argv[1], encoding='utf-8') as handle:
        total, not_checked, wrong = check(json.load(handle))
    print('%d requests checked' % total)
    for line in not_checked:
        print('skipped  ' + line)
    for line in wrong:
        print('PROBLEM  ' + line)
    sys.exit(1 if wrong else 0)
