#!/usr/bin/env python3
"""Label sync tests (no network): the manifest checks, the plan against a fake repository (renames keep issues
labelled, nothing is ever deleted), the lint, and the repository properties. The last test checks this
organisation's own manifests.

    python3 .github/labels/test_sync.py
"""
import copy
import importlib.util
import os
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('sync', os.path.join(HERE, 'sync.py'))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
fails = []


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        fails.append(what)


SHARED = {'version': 1,
          'dimensions': [{'prefix': 'type', 'rule': 'one', 'applies': ['issue', 'pr'], 'color': '1d76db', 'description': 'Kind.'},
                         {'prefix': 'status', 'rule': 'at-most-one', 'applies': ['issue'], 'color': 'fbca04', 'description': 'Why waiting.'},
                         {'prefix': 'area', 'rule': 'any', 'applies': ['issue', 'pr'], 'color': 'c2e0c6', 'description': 'Where.'}],
          'labels': [{'name': 'type: bug', 'description': 'Broken'}, {'name': 'type: feature', 'description': 'New'},
                     {'name': 'status: blocked', 'description': 'Blocked'},
                     {'name': 'help wanted', 'fixed': True, 'color': '008672', 'description': 'Help'}],
          'renames': {'bug': 'type: bug', 'enhancement': 'type: feature'}}
ORG = {'version': 1,
       'dimensions': [{'prefix': 'claim', 'rule': 'at-most-one', 'applies': ['pr'], 'color': 'f9d0c4', 'description': 'Label.'}],
       'labels': [{'name': 'claim: proven', 'description': 'Proved'}]}
REPO = {'version': 1, 'labels': [{'name': 'area: relay', 'description': 'The relay'}]}


class Fake:
    def __init__(self, labels, issues=(), repo=REPO, schema=(), values=(), topics=()):
        self.labels, self.issues, self.repo = labels, list(issues), repo
        self.schema, self.values, self.topics, self.calls = list(schema), list(values), list(topics), []

    def call(self, method, path, body=None, accept=None):
        self.calls.append((method, path, body))
        if path.endswith('/contents/.github/forum/board.toml'):
            return '[[labels]]\nname = "project: f0"\ncolor = "1d76db"' if getattr(self, 'board', False) else None
        if path.endswith('/contents/.github/labels.toml'):
            if not self.repo:
                return None
            return '\n'.join(['version = 1'] + [f'[[labels]]\nname = "{x["name"]}"\ndescription = "{x["description"]}"' for x in self.repo['labels']])
        if path.endswith('/properties/schema'):
            return self.schema
        if path.endswith('/topics'):
            return {'names': self.topics}
        return {}

    def pages(self, path):
        if path.endswith('/labels'):
            return copy.deepcopy(self.labels)
        if '/issues' in path:
            return self.issues
        if path.endswith('/properties/values'):
            return self.values
        return []


# 1. the manifests
check(sync.validate(SHARED, ORG, REPO) == [], 'shared, organisation and repository manifests together are sound')
bad = copy.deepcopy(ORG)
bad['dimensions'].append(dict(SHARED['dimensions'][0]))
check(any('defined twice' in e for e in sync.validate(SHARED, bad)), 'an organisation may not redefine a shared dimension')
check(any("only area: labels" in e for e in sync.validate(SHARED, ORG, {'version': 1, 'labels': [{'name': 'type: x', 'description': 'x'}]})),
      'a repository may add only area: labels')
bad = copy.deepcopy(SHARED)
bad['labels'].append({'name': 'wip', 'description': 'x'})
check(any('has no prefix' in e for e in sync.validate(bad, ORG)), 'a label without a prefix is refused unless it is one of GitHub\'s fixed names')
bad = copy.deepcopy(SHARED)
bad['labels'].append({'name': 'kind: x', 'description': 'x'})
check(any("prefix 'kind' is not a dimension" in e for e in sync.validate(bad, ORG)), 'a label in an unknown dimension is refused')
bad = copy.deepcopy(SHARED)
bad['renames']['wip'] = 'status: wip'
check(any('the new name is not a label' in e for e in sync.validate(bad, ORG)), 'a rename to a label outside the set is refused')
bad = copy.deepcopy(SHARED)
bad['labels'][0]['description'] = 'x' * 101
check(any('1 to 100 characters' in e for e in sync.validate(bad, ORG)), "GitHub's 100-character description limit is kept")

# 2. the plan
have = [{'name': 'bug', 'color': 'd73a4a', 'description': "Something isn't working"},
        {'name': 'type: feature', 'color': '1d76db', 'description': 'New'},
        {'name': 'enhancement', 'color': 'a2eeef', 'description': 'x'},
        {'name': 'help wanted', 'color': '008672', 'description': 'Extra attention is needed'},
        {'name': 'wip', 'color': 'ffffff', 'description': ''}]
api = Fake(have)
steps = sync.plan(api, 'o/r', SHARED, ORG)
acts = {(a, n) for a, n, _ in steps}
check(('rename', 'bug') in acts and not any(a == 'update' and n == 'type: bug' for a, n in acts),
      'an old label is renamed (issues keep it), and not updated again after')
check(('rename', 'enhancement') not in acts and ('unknown', 'enhancement') in acts,
      'a rename whose new name already exists is left for a person, and reported')
check(('update', 'help wanted') in acts and ('create', 'status: blocked') in acts and ('create', 'area: relay') in acts
      and ('create', 'claim: proven') in acts, 'shared, organisation and repository labels are all planned')
check(('unknown', 'wip') in acts, 'a label outside the standard is reported')
api2 = Fake(have + [{'name': 'project: f0', 'color': '1d76db', 'description': ''}])
api2.board = True
check(not any(n == 'project: f0' for _, n, _ in sync.plan(api2, 'o/.github', SHARED, ORG)),
      "a forum board's own labels are left to the forum gate, not reported")
sync.apply(api, 'o/r', [s for s in steps if s[0] != 'unknown'])
check(not any(m == 'DELETE' for m, _, _ in api.calls), 'nothing is ever deleted')
patch = [b for m, p, b in api.calls if m == 'PATCH' and p.endswith('/labels/bug')]
check(patch and patch[0] == {'new_name': 'type: bug', 'color': '1d76db', 'description': 'Broken'},
      'the rename sets the new name, the dimension colour and the description in one call')
check(any(m == 'POST' and b['name'] == 'area: relay' for m, _, b in api.calls), 'a missing label is created')
sp = [p for m, p, _ in api.calls if m == 'PATCH' and 'help%20wanted' in p]
check(sp != [], 'a label name is escaped in the URL')

# 3. the lint
issues = [{'number': 1, 'labels': [{'name': 'type: bug'}, {'name': 'type: feature'}]},
          {'number': 2, 'labels': []},
          {'number': 3, 'labels': [{'name': 'type: bug'}, {'name': 'status: blocked'}]},
          {'number': 4, 'pull_request': {}, 'labels': [{'name': 'type: chore'}, {'name': 'claim: proven'}]}]
out = sync.lint(Fake([], issues), 'o/r', SHARED, ORG)
check(any(x.startswith('#1 ') for x in out) and any(x.startswith('#2 ') for x in out) and not any(x.startswith('#3 ') for x in out),
      'two types, or none, break the rule; one type and one status pass')
check(not any(x.startswith('#4 ') for x in out), 'a dimension that applies only to issues or PRs is checked only there')

# 4. repository properties and topics
REPOS = {'version': 1, 'org': 'o', 'required_topics': ['o-org'],
         'properties': {'kind': {'type': 'multi_select', 'values': ['core', 'library', 'site']}, 'stage': {'values': ['started', 'verified']},
                        'board': {'values': ['main', 'internal', 'project', 'none']}, 'owner': {'type': 'string'}},
         'repos': [{'name': 'core', 'kind': ['core', 'site'], 'board': 'internal', 'topics': ['governance']},
                   {'name': 'lib', 'kind': ['library'], 'stage': 'started', 'board': 'none'}]}
check(sync.validate_repos(REPOS, {}) == [], 'a sound repos.toml')
bad = copy.deepcopy(REPOS)
bad['repos'][1]['kind'] = ['library', 'tool']
check(any("kind = ['library', 'tool']" in e for e in sync.validate_repos(bad, {})), 'a multi-select value outside its list is refused')
bad = copy.deepcopy(REPOS)
bad['repos'][1]['stage'] = 'done'
check(any("stage = 'done'" in e for e in sync.validate_repos(bad, {})), 'a single-select value outside its list is refused')
bad = copy.deepcopy(REPOS)
bad['properties']['board']['values'] = ['main', 'none']
check(any('fixed by the forum standard' in e for e in sync.validate_repos(bad, {})), 'board values are the forum standard\'s')
api = Fake([], values=[{'repository_name': 'lib', 'properties': [{'property_name': 'kind', 'value': ['library']}]}],
           topics=['lean'])
steps = sync.repos_plan(api, REPOS)
check(sum(1 for a, *_ in steps if a == 'schema') == 4, 'the property schema is planned when the organisation lacks it')
vals = {n: d for a, n, d in steps if a == 'values'}
check(vals['lib'] == {'stage': 'started', 'board': 'none'} and vals['core'] == {'kind': ['core', 'site'], 'board': 'internal'},
      'only the values that differ are set')
tops = {n: d for a, n, d in steps if a == 'topics'}
check(tops['lib'] == ['lean', 'o-org'] and tops['core'] == ['governance', 'lean', 'o-org'], 'topics are added, never removed')

# 5. this organisation's manifests
if os.path.exists(sync.ORG):
    shared, org = sync.load(sync.SHARED), sync.load(sync.ORG)
    errs = sync.validate(shared, org)
    if os.path.exists(sync.REPOS):
        errs += sync.validate_repos(sync.load(sync.REPOS), {})
    for e in errs:
        print('   ', e)
    check(errs == [], "this organisation's label and repository manifests pass the check")

print(f'\n{"FAILED: " + str(len(fails)) if fails else "all passed"}')
sys.exit(1 if fails else 0)
