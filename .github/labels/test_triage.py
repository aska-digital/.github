#!/usr/bin/env python3
"""Triage tests (no network): every rule the triage bot applies, against fake events, including the cases a
busy public repository produces: no title, no body, a body edited to tick more boxes, two types at once.

    python3 .github/labels/test_triage.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('triage', os.path.join(HERE, 'triage.py'))
triage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(triage)
sync = triage.sync
fails = []


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        fails.append(what)


SHARED = sync.load(os.path.join(HERE, 'labels.shared.toml'))
REPO = {'version': 1, 'labels': [{'name': 'area: relay', 'description': 'The relay', 'paths': ['relay/']},
                                 {'name': 'area: gates', 'description': 'Gates', 'paths': ['gates/**/*.py'],
                                  'form': ['The send gates']}]}
CFG = triage.config(SHARED, {'version': 1}, REPO)


def pr(action='opened', labels=(), body='', assoc='CONTRIBUTOR', bot=False, add=10, rm=2, title='x', **kw):
    item = {'number': 7, 'title': title, 'body': body, 'author_association': assoc, 'state': 'open',
            'user': {'login': 'someone', 'type': 'Bot' if bot else 'User'}, 'additions': add, 'deletions': rm,
            'labels': [{'name': x} for x in labels]}
    return dict({'action': action, 'pull_request': item}, **kw)


def issue(action='opened', labels=(), body=None, assoc='NONE', reason=None, **kw):
    item = {'number': 9, 'title': '', 'body': body, 'author_association': assoc, 'state_reason': reason,
            'user': {'login': 'someone', 'type': 'User'}, 'labels': [{'name': x} for x in labels]}
    return dict({'action': action, 'issue': item}, **kw)


def d(event, ev, files=(), linked=()):
    return triage.decide(CFG, event, ev, files, linked)


# 1. globs
check(triage.matches('relay/a/b.py', ['relay/']) and not triage.matches('relayx/a.py', ['relay/']), 'a folder glob covers what is under it, only')
check(triage.matches('README.md', ['**/*.md']) and triage.matches('a/b/c.md', ['**/*.md']), '**/ matches at the root and below')
check(triage.matches('gates/x/y.py', ['gates/**/*.py']) and not triage.matches('gates/y.txt', ['gates/**/*.py']), '** inside a pattern')
check(sync.validate(SHARED, {'version': 1}, REPO) == [], 'the shared set, with form answers, sizes and path rules, is sound')

# 2. a pull request with no title and no body still gets labelled from what it changes
add, rm, probs = d('pull_request_target', pr(title='', body=None), files=['relay/main.py', 'relay/README.md'])
check('area: relay' in add and 'size: s' in add and 'source: contribution' in add and 'status: needs triage' in add,
      'no title, no body: area from paths, size, outside source and needs triage')
check(probs == ['needs exactly one type: label, has 0'], 'and the check asks a maintainer for its type')
add, _, probs = d('pull_request_target', pr(body=None), files=['docs/a.md', 'README.md'])
check('type: docs' in add and probs == [], 'only documentation changed: type: docs, and the check passes')
add, _, _ = d('pull_request_target', pr(body=None), files=['docs/a.md', 'relay/x.py'])
check('type: docs' not in add and 'area: relay' in add, 'one code file means it is not docs only')
add, _, _ = d('pull_request_target', pr(body=None), files=['.github/workflows/ci.yml'])
check('type: chore' in add and 'concern: security' in add, 'a workflow change is upkeep, and gets a security look')

# 3. structured answers, never words
body = '## Kind of change\n\n- [x] Fixes something that does not work\n- [ ] Adds or changes behaviour\n\n## AI\n\n- [X] An AI agent wrote it, and a person reviewed it\n'
add, _, probs = d('pull_request_target', pr(body=body), files=['relay/a.py'])
check('type: bug' in add and 'ai: authored' in add and probs == [], 'ticked boxes in the template become labels')
add, _, _ = d('pull_request_target', pr(body='This fixes a security bug in the relay, a breaking feature'), files=['x.py'])
check(not any(x.startswith(('type:', 'concern:')) for x in add), 'words in free text are never read as labels')
ib = '### Which part\n\nThe send gates\n\n### What else it touches\n\nSecurity, Accessibility\n\n### Details\n\n_No response_\n'
add, _, _ = d('issues', issue(body=ib))
check({'area: gates', 'concern: security', 'concern: accessibility', 'source: report', 'status: needs triage'} <= set(add),
      'issue form answers, multi-select included, become labels')

ib2 = '### What else it touches\n\nSecurity, Privacy, personal data or secrets\n'
add, _, _ = d('issues', issue(body=ib2))
check({'concern: security', 'concern: privacy'} <= set(add), 'a multi-select answer holding a comma is still read whole')

# 4. signals never override a maintainer
body2 = '- [x] Fixes something that does not work\n- [x] Adds or changes behaviour\n'
add, _, _ = d('pull_request_target', pr(body=body2), files=['x.py'])
check(sum(1 for x in add if x.startswith('type:')) == 1, 'two ticked types: only one is taken')
add, _, _ = d('pull_request_target', pr(action='edited', labels=['type: feature', 'size: s'], body=body), files=[])
check('type: bug' not in add, 'an edited body cannot replace a type a maintainer set')

# 5. one label per single-label dimension, newest wins
add, rm, _ = d('issues', issue(action='labeled', labels=['type: bug', 'type: feature', 'status: needs triage'], label={'name': 'type: feature'}))
check(rm.get('type: bug') and rm.get('status: needs triage'), 'a new type replaces the old one, and the item is triaged')
add, rm, _ = d('issues', issue(action='labeled', labels=['concern: security', 'concern: privacy'], label={'name': 'concern: privacy'}))
check(rm == {}, 'a dimension that allows many keeps them all')

# 6. size follows every push
add, rm, _ = d('pull_request_target', pr(action='synchronize', labels=['type: bug', 'size: xs'], add=400, rm=20), files=['x.py'])
check(add.get('size: l') and rm.get('size: xs'), 'a push that grows it moves it to its new size')

# 7. close and reopen
add, rm, _ = d('issues', issue(action='closed', labels=['type: bug', 'status: blocked'], reason='not_planned'))
check(rm.get('status: blocked') and add.get('resolution: wontfix'), 'closed as not planned: status removed, resolution: wontfix')
add, _, _ = d('issues', issue(action='closed', labels=['resolution: duplicate'], reason='not_planned'))
check(add == {}, "a maintainer's resolution label is kept")
add, rm, _ = d('issues', issue(action='reopened', labels=['type: bug', 'resolution: wontfix'], assoc='MEMBER'))
check(rm.get('resolution: wontfix') and 'status: needs triage' not in add, 'reopened: resolution removed; a typed team issue is not re-triaged')

# 8. who opened it
add, _, _ = d('pull_request_target', pr(bot=True, assoc='NONE'), files=['x.py'])
check('ai: authored' in add, 'a GitHub app opening a pull request is labelled ai: authored')
add, _, _ = d('issues', issue(assoc='MEMBER', body=None))
check('source: report' not in add and 'status: needs triage' in add, 'a team member is not an outside report, but an untyped issue still needs triage')

# 9. inherited from the issue it closes
add, _, probs = d('pull_request_target', pr(body='Fixes #3'), files=['x.py'], linked={'type: bug', 'priority: p1', 'status: blocked'})
check('type: bug' in add and 'priority: p1' in add and 'status: blocked' not in add and probs == [],
      'type and priority come from the issue it closes, status does not')
check(triage.CLOSING.findall('closes #4, Fixes: #5 and resolved #6; see #7') == ['4', '5', '6'], "GitHub's closing keywords only")

print(f'\n{"FAILED: " + str(len(fails)) if fails else "all passed"}')
sys.exit(1 if fails else 0)
