#!/usr/bin/env python3
"""Labels and repository properties under the labelling standard (LABELS-STANDARD.md in each organisation's
.github repository).

Two systems, kept apart:

  - Repositories are classified by GitHub topics and the organisation's custom properties (kind, stage, board,
    owner), listed for every repository in .github/labels/repos.toml.
  - Issues and pull requests share one label set: the shared dimensions in labels.shared.toml (the same file in
    every organisation), the organisation's own in labels.toml, and a repository's `area:` labels in that
    repository's own .github/labels.toml.

    python3 .github/labels/sync.py check                       # the manifests are sound (no network)
    GH_TOKEN=... python3 .github/labels/sync.py plan  OWNER/REPO   # what apply would change; changes nothing
    GH_TOKEN=... python3 .github/labels/sync.py apply OWNER/REPO   # rename, update and create labels
    GH_TOKEN=... python3 .github/labels/sync.py apply --org        # every repository listed in repos.toml
    GH_TOKEN=... python3 .github/labels/sync.py lint  OWNER/REPO   # open issues and PRs against the rules
    GH_TOKEN=... python3 .github/labels/sync.py repos plan|apply   # custom properties and topics
    ... --repos PATH                     # another repos.toml: private repositories are listed in a private one

A label is never deleted. An old label is renamed to its new name, so every issue that carried it keeps it;
a label the standard doesn't know is reported and left alone. Topics are only ever added. Standard library
only (Python 3.11 or later, for tomllib).
"""
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

VERSION = '1.0'
HERE = os.path.dirname(os.path.abspath(__file__))
SHARED, ORG, REPOS = (os.path.join(HERE, f) for f in ('labels.shared.toml', 'labels.toml', 'repos.toml'))
RULES = {'one', 'at-most-one', 'any'}
APPLIES = {'issue', 'pr'}
COLOR = re.compile(r'[0-9a-f]{6}')
PREFIX = re.compile(r'[a-z]+')
TOPIC = re.compile(r'[a-z0-9][a-z0-9-]{0,49}')
BOARDS = {'main', 'internal', 'project', 'none'}
NAME_MAX, DESC_MAX = 50, 100          # GitHub's limits for a label


def load(path):
    with open(path, 'rb') as f:
        return tomllib.load(f)


def prefix_of(name):
    return name.split(': ', 1)[0] if ': ' in name else ''


# ---------------------------------------------------------------- the label set

def merge(*manifests):
    """One label set from the shared, organisation and repository manifests, in that order. Returns
    (dimensions by prefix, labels by lower-case name, renames old -> new)."""
    dims, labels, renames = {}, {}, {}
    for m in manifests:
        for d in m.get('dimensions', []):
            dims[d['prefix']] = d
        for lab in m.get('labels', []):
            d = dims.get(prefix_of(lab['name']), {})
            labels[lab['name'].lower()] = {'name': lab['name'], 'color': lab.get('color', d.get('color', 'ededed')),
                                           'description': lab.get('description', '')}
        renames.update(m.get('renames', {}))
    return dims, labels, renames


def validate(shared, org, repo=None, where='labels'):
    """Every problem in these manifests together; an empty list means the set is sound."""
    errs = []
    for name, m in (('labels.shared.toml', shared), ('labels.toml', org), ('the repository file', repo or {})):
        if m and m.get('version') != 1:
            errs.append(f'{name}: version must be 1')
    if repo:
        for d in repo.get('dimensions', []):
            errs.append(f'the repository file may not add dimensions ({d.get("prefix")!r}); only area: labels')
        for lab in repo.get('labels', []):
            if prefix_of(lab.get('name', '')) != 'area':
                errs.append(f'the repository file may hold only area: labels, not {lab.get("name")!r}')
    seen = set()
    for m in (shared, org):
        for d in m.get('dimensions', []):
            p = d.get('prefix')
            if not isinstance(p, str) or not PREFIX.fullmatch(p):
                errs.append(f'dimension {p!r}: prefix must be a lower-case word')
            if p in seen:
                errs.append(f'dimension {p}: defined twice (an organisation may not redefine a shared dimension)')
            seen.add(p)
            if d.get('rule') not in RULES:
                errs.append(f'dimension {p}: rule must be one of {sorted(RULES)}')
            if not set(d.get('applies', [])) or not set(d.get('applies', [])) <= APPLIES:
                errs.append(f'dimension {p}: applies must list issue and/or pr')
            if not COLOR.fullmatch(str(d.get('color', ''))):
                errs.append(f'dimension {p}: color must be six lower-case hex digits')
            if not d.get('description'):
                errs.append(f'dimension {p}: description is required')
    dims, _, _ = merge(shared, org, repo or {})
    names = {}
    for m in (shared, org, repo or {}):
        for lab in m.get('labels', []):
            n = lab.get('name', '')
            if not n or len(n) > NAME_MAX:
                errs.append(f'label {n!r}: name must be 1 to {NAME_MAX} characters')
            if n.lower() in names:
                errs.append(f'label {n!r}: defined twice')
            names[n.lower()] = n
            p = prefix_of(n)
            if p and p not in dims:
                errs.append(f'label {n!r}: prefix {p!r} is not a dimension')
            if not p and not lab.get('fixed'):
                errs.append(f'label {n!r}: has no prefix; only GitHub\'s fixed names may (set fixed = true)')
            if 'color' in lab and not COLOR.fullmatch(str(lab['color'])):
                errs.append(f'label {n!r}: color must be six lower-case hex digits')
            if not lab.get('description') or len(lab['description']) > DESC_MAX:
                errs.append(f'label {n!r}: description must be 1 to {DESC_MAX} characters')
    answers = {}
    for m in (shared, org, repo or {}):
        for lab in m.get('labels', []):
            for k in ('form', 'paths'):
                if k in lab and (not isinstance(lab[k], list) or not all(isinstance(x, str) and x for x in lab[k])):
                    errs.append(f'label {lab.get("name")!r}: {k} must be a list of strings')
            for a in lab.get('form', []):
                other = answers.get(a.lower())
                if other and prefix_of(other) == prefix_of(lab['name']):
                    errs.append(f'form answer {a!r} means both {other!r} and {lab["name"]!r}')
                answers[a.lower()] = lab['name']
        for r in m.get('path_rules', []):
            if r.get('label', '').lower() not in names:
                errs.append(f'path rule for {r.get("label")!r}: not a label in the set')
            if r.get('when', 'any') not in ('any', 'all') or not r.get('paths'):
                errs.append(f'path rule for {r.get("label")!r}: needs paths, and when = "any" or "all"')
    size = shared.get('size', {})
    for k, v in size.items():
        if f'size: {k}' not in names or not isinstance(v, int) or v < 1:
            errs.append(f'size {k!r}: needs a size: {k} label and a whole number of lines')
    for old, new in {**shared.get('renames', {}), **org.get('renames', {}), **(repo or {}).get('renames', {})}.items():
        if new.lower() not in names:
            errs.append(f'rename {old!r} -> {new!r}: the new name is not a label in the set')
        if old.lower() in names:
            errs.append(f'rename {old!r}: the old name is itself a label in the set')
    return [f'{where}: {e}' for e in errs]


def validate_repos(r, dims):
    errs = []
    if r.get('version') != 1 or not r.get('org'):
        errs.append('repos.toml: needs version = 1 and org')
    props = r.get('properties', {})
    for k in ('kind', 'stage'):
        if not props.get(k, {}).get('values'):
            errs.append(f'repos.toml: [properties.{k}] needs values')
    for k, spec in props.items():
        if spec.get('type', 'single_select') not in ('single_select', 'multi_select', 'string'):
            errs.append(f'repos.toml: [properties.{k}] type must be single_select, multi_select or string')
    if set(props.get('board', {}).get('values', BOARDS)) != BOARDS:
        errs.append(f'repos.toml: board values are fixed by the forum standard: {sorted(BOARDS)}')
    seen = set()
    for rep in r.get('repos', []):
        n = rep.get('name')
        if not n or n in seen:
            errs.append(f'repos.toml: repository {n!r} missing a name or listed twice')
        seen.add(n)
        for k, spec in props.items():
            v = rep.get(k)
            if v is None:
                if k in ('kind', 'board'):
                    errs.append(f'repos.toml: {n}: {k} is required')
                continue
            allowed = spec.get('values', sorted(BOARDS) if k == 'board' else None)
            kind = spec.get('type', 'single_select')
            if kind == 'multi_select':
                if not isinstance(v, list) or not v or not set(v) <= set(allowed):
                    errs.append(f'repos.toml: {n}: {k} = {v!r} must be a list drawn from {allowed}')
            elif kind == 'single_select' and v not in allowed:
                errs.append(f'repos.toml: {n}: {k} = {v!r} is not one of {allowed}')
            elif kind == 'string' and not isinstance(v, str):
                errs.append(f'repos.toml: {n}: {k} must be text')
        for t in rep.get('topics', []) + r.get('required_topics', []):
            if not TOPIC.fullmatch(t):
                errs.append(f'repos.toml: {n}: topic {t!r} must be lower-case letters, digits and hyphens')
    return errs


# ---------------------------------------------------------------- GitHub

class GitHub:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None, accept='application/vnd.github+json'):
        url = path if path.startswith('https://') else 'https://api.github.com' + path
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={'Authorization': f'bearer {self.token}', 'Accept': accept,
                                              'Content-Type': 'application/json', 'User-Agent': f'labels-sync/{VERSION}',
                                              'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if accept.endswith('.raw'):
                    return raw.decode('utf-8')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == 'GET':
                return None
            raise SystemExit(f'labels: GitHub answered HTTP {e.code} to {method} {url.split("?")[0]}')

    def pages(self, path):
        out, page = [], 1
        while True:
            sep = '&' if '?' in path else '?'
            got = self.call('GET', f'{path}{sep}per_page=100&page={page}') or []
            out += got
            if len(got) < 100:
                return out
            page += 1


def repo_manifest(api, full):
    text = api.call('GET', f'/repos/{full}/contents/.github/labels.toml', accept='application/vnd.github.raw')
    return tomllib.loads(text) if text else {}


def plan(api, full, shared, org):
    """The changes that bring one repository's labels to the standard: a list of (action, name, detail)."""
    repo = repo_manifest(api, full)
    errs = validate(shared, org, repo, where=full)
    if errs:
        raise SystemExit('\n'.join(errs))
    _, want, renames = merge(shared, org, repo)
    have = {x['name'].lower(): x for x in api.pages(f'/repos/{full}/labels')}
    steps = []
    for old, new in renames.items():
        if old.lower() in have and new.lower() not in have:
            steps.append(('rename', old, want[new.lower()]))
            have.pop(old.lower())
            have[new.lower()] = dict(want[new.lower()])     # the rename sets colour and description too
    for key, lab in want.items():
        cur = have.get(key)
        if cur is None:
            steps.append(('create', lab['name'], lab))
        elif (cur['color'].lower(), cur.get('description') or '', cur['name']) != (lab['color'], lab['description'], lab['name']):
            steps.append(('update', cur['name'], lab))
    board = api.call('GET', f'/repos/{full}/contents/.github/forum/board.toml', accept='application/vnd.github.raw')
    forum = {x['name'].lower() for x in (tomllib.loads(board).get('labels', []) if board else [])}
    for key, cur in sorted(have.items()):
        if key not in want and key not in forum:      # a forum board's own labels are the forum gate's to keep
            steps.append(('unknown', cur['name'], None))
    return steps


def apply(api, full, steps):
    for action, name, lab in steps:
        q = urllib.parse.quote(name, safe='')
        if action in ('rename', 'update'):
            api.call('PATCH', f'/repos/{full}/labels/{q}',
                     {'new_name': lab['name'], 'color': lab['color'], 'description': lab['description']})
        elif action == 'create':
            api.call('POST', f'/repos/{full}/labels', lab)


def lint(api, full, shared, org):
    """Open issues and pull requests that break a dimension's rule (one type, at most one status, ...)."""
    dims, _, _ = merge(shared, org, repo_manifest(api, full))
    out = []
    for it in api.pages(f'/repos/{full}/issues?state=open'):
        kind = 'pr' if 'pull_request' in it else 'issue'
        names = [x['name'] for x in it['labels']]
        for p, d in dims.items():
            if kind not in d['applies']:
                continue
            n = sum(1 for x in names if prefix_of(x) == p)
            if d['rule'] == 'one' and n != 1 or d['rule'] == 'at-most-one' and n > 1:
                out.append(f'#{it["number"]} ({kind}): {n} {p}: label{"" if n == 1 else "s"}, the rule is {d["rule"]}')
    return out


def repos_plan(api, r):
    """Custom property schema and values, and topics to add, for every repository listed."""
    org, steps = r['org'], []
    schema = {p['property_name']: p for p in (api.call('GET', f'/orgs/{org}/properties/schema') or [])}
    for name, spec in r['properties'].items():
        want = {'value_type': spec.get('type', 'single_select'), 'description': spec.get('description', ''),
                'required': False}
        if want['value_type'] in ('single_select', 'multi_select'):
            want['allowed_values'] = spec['values']
        cur = schema.get(name)
        if cur is None or cur.get('allowed_values') != want.get('allowed_values') or cur.get('value_type') != want['value_type']:
            steps.append(('schema', name, want))
    values = {v['repository_name']: {p['property_name']: p['value'] for p in v['properties']}
              for v in api.pages(f'/orgs/{org}/properties/values')}
    for rep in r['repos']:
        want = {k: rep[k] for k in r['properties'] if rep.get(k) is not None}
        cur = values.get(rep['name'], {})
        same = lambda a, b: sorted(a) == sorted(b) if isinstance(a, list) and isinstance(b, list) else a == b  # noqa: E731
        diff = {k: v for k, v in want.items() if not same(cur.get(k), v)}
        if diff:
            steps.append(('values', rep['name'], diff))
        topics = (api.call('GET', f'/repos/{org}/{rep["name"]}/topics') or {}).get('names', [])
        add = [t for t in r.get('required_topics', []) + rep.get('topics', []) if t not in topics]
        if add:
            steps.append(('topics', rep['name'], sorted(set(topics) | set(add))))
    return steps


def repos_apply(api, r, steps):
    org = r['org']
    for action, name, detail in steps:
        if action == 'schema':
            api.call('PUT', f'/orgs/{org}/properties/schema/{urllib.parse.quote(name)}', detail)
        elif action == 'values':
            api.call('PATCH', f'/orgs/{org}/properties/values', {
                'repository_names': [name],
                'properties': [{'property_name': k, 'value': v} for k, v in detail.items()]})
        elif action == 'topics':
            api.call('PUT', f'/repos/{org}/{name}/topics', {'names': detail})


# ---------------------------------------------------------------- commands

def show(steps):
    for action, name, lab in steps:
        if action == 'unknown':
            print(f'  keep     {name!r} (not in the standard; left as it is)')
        elif action == 'rename':
            print(f'  rename   {name!r} -> {lab["name"]!r}')
        elif action in ('create', 'update'):
            print(f'  {action:8} {lab["name"]!r} #{lab["color"]} {lab["description"]}')
        else:
            print(f'  {action:8} {name}: {lab}')


def main(argv):
    global REPOS
    if '--repos' in argv:
        i = argv.index('--repos')
        REPOS = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    shared, org = load(SHARED), load(ORG)
    cmd = argv[1] if len(argv) > 1 else 'check'
    if cmd == 'check':
        errs = validate(shared, org)
        if os.path.exists(REPOS):
            errs += validate_repos(load(REPOS), merge(shared, org)[0])
        for e in errs:
            print('FAIL:', e)
        n = len(merge(shared, org)[1])
        print(f'labels {VERSION}: ' + (f'{len(errs)} problem(s)' if errs else f'{n} labels, manifests sound'))
        return 1 if errs else 0
    token = os.environ.get('GH_TOKEN') or sys.exit('labels: set GH_TOKEN')
    api = GitHub(token)
    if cmd == 'repos':
        r = load(REPOS)
        steps = repos_plan(api, r)
        show(steps)
        if argv[2:3] == ['apply']:
            repos_apply(api, r, steps)
        print(f'{len(steps)} change(s)' + (' applied' if argv[2:3] == ['apply'] else ' planned'))
        return 0
    targets = argv[2:]
    if targets == ['--org']:
        r = load(REPOS)
        targets = [f'{r["org"]}/{x["name"]}' for x in r['repos'] if x.get('labels', True)]
    if not targets:
        sys.exit(f'labels: {cmd} needs OWNER/REPO or --org')
    for full in targets:
        print(full)
        if cmd == 'lint':
            for line in lint(api, full, shared, org):
                print('  ' + line)
            continue
        steps = plan(api, full, shared, org)
        show(steps)
        if cmd == 'apply':
            apply(api, full, [s for s in steps if s[0] != 'unknown'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
