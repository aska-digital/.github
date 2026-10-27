#!/usr/bin/env python3
"""Label triage: applies the labelling standard to every issue and pull request as it opens, changes and closes.

Labels come only from structured signals, never from words in a title:

  - the answers a person picked in an issue form or ticked in the pull request template: each label lists the
    exact answers that mean it (`form` in the manifests);
  - the files a pull request changes: a label's `paths`, and the manifests' `path_rules`;
  - the size of a pull request (lines added and removed);
  - who opened it: someone outside the team (source: report or source: contribution), or a GitHub app (ai: authored);
  - the issues a pull request closes: it inherits their type, area, concern and priority labels.

Then it keeps the set consistent, whoever changed it:

  - a dimension that allows one label keeps the newest: adding `type: feature` removes `type: bug`;
  - an item that nobody on the team has looked at carries `status: needs triage`, until a maintainer labels it;
  - closing removes `status:` labels, and GitHub's close reason becomes a `resolution:` label; reopening removes it;
  - a pull request without exactly one label in each dimension whose rule is "one" fails this check.

Outsiders cannot set labels on GitHub (it takes triage access), so a label on an item came from a form, from this
script or from a maintainer. Signals only ever add labels, and only into an empty single-label dimension: they
never override a maintainer's choice. Every change is written to the job summary with its reason.

    python3 .github/labels/triage.py          # .github/workflows/labels.yml runs it with the event's payload

Standard library only (Python 3.11 or later).
"""
import json
import os
import re
import sys
import tomllib
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sync  # noqa: E402  (the same directory: manifests, merge, GitHub client)

TEAM = {'OWNER', 'MEMBER', 'COLLABORATOR'}
TRIAGE = 'status: needs triage'
INHERIT = {'type', 'area', 'concern', 'priority'}
CLOSING = re.compile(r'\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b', re.I)  # GitHub's own keywords


# ---------------------------------------------------------------- signals

def glob_re(pattern):
    """A path glob: ** crosses folders, * and ? stay inside one, a trailing / means everything under it."""
    if pattern.endswith('/'):
        pattern += '**'
    out, i = '', 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out, i = out + '(?:.*/)?', i + 3
        elif pattern.startswith('**', i):
            out, i = out + '.*', i + 2
        elif pattern[i] == '*':
            out, i = out + '[^/]*', i + 1
        elif pattern[i] == '?':
            out, i = out + '[^/]', i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r'\Z')


def matches(path, patterns):
    return any(glob_re(p).match(path) for p in patterns)


def answers(body, known=()):
    """What the author picked: ticked boxes, and the value under each form heading. Lower-cased and trimmed.
    A multi-select value is cut back into the known answers it joins, even ones that hold a comma themselves.
    Free text is never searched for words."""
    out = set()
    if not body:
        return out
    for m in re.finditer(r'^\s*[-*]\s*\[[xX]\]\s*(.+?)\s*$', body, re.M):
        out.add(m.group(1).strip().lower())
    for m in re.finditer(r'^###\s+.+?\n\s*\n(.+?)\s*(?=\n###\s|\Z)', body, re.M | re.S):
        value = m.group(1).strip()
        if '\n' in value or value == '_No response_':
            continue
        parts = [v.strip().lower() for v in value.split(',')]
        out.add(value.lower())
        for i in range(len(parts)):
            for j in range(i + 1, len(parts) + 1):
                joined = ', '.join(parts[i:j])
                if joined in known:
                    out.add(joined)
    return out


def size_label(cfg, lines):
    for name, upto in cfg['size']:
        if lines <= upto:
            return name
    return cfg['size'][-1][0]


# ---------------------------------------------------------------- the decision

def config(shared, org, repo):
    dims, labels, _ = sync.merge(shared, org, repo)
    form, paths = {}, []
    for m in (shared, org, repo):
        for lab in m.get('labels', []):
            for a in lab.get('form', []):
                form.setdefault(a.strip().lower(), []).append(lab['name'])
            if lab.get('paths'):
                paths.append({'label': lab['name'], 'when': 'any', 'paths': lab['paths']})
        paths += m.get('path_rules', [])
    size = sorted(((k, v) for k, v in shared.get('size', {}).items()), key=lambda kv: kv[1])
    size = [(f'size: {k}', v) for k, v in size]
    return {'dims': dims, 'labels': labels, 'form': form, 'paths': paths, 'size': size}


def exclusive(cfg, prefix):
    d = cfg['dims'].get(prefix)
    return d is not None and d['rule'] in ('one', 'at-most-one')


def decide(cfg, event, ev, files=(), linked=()):
    """What to add and remove, with a reason for each, and the rule problems left after. Pure: no network.
    `files` are the changed paths of a pull request; `linked` the label names of the issues it closes."""
    kind = 'pr' if event.startswith('pull_request') else 'issue'
    item = ev['pull_request'] if kind == 'pr' else ev['issue']
    action = ev['action']
    have = {x['name'] for x in item.get('labels', [])}
    add, remove = {}, {}
    known = {k for k in cfg['labels']}

    def now():
        return (have | set(add)) - set(remove)

    def dim_full(prefix):
        return any(sync.prefix_of(x) == prefix for x in now())

    def offer(name, why):
        """A signal suggests a label: added unless unknown, already there, or its single-label dimension is taken."""
        if name.lower() not in known or name in now():
            return
        p = sync.prefix_of(name)
        if p and exclusive(cfg, p) and dim_full(p):
            return
        if kind not in cfg['dims'].get(p, {'applies': ['issue', 'pr']})['applies']:
            return
        add[cfg['labels'][name.lower()]['name']] = why

    def drop(name, why):
        if name in now():
            add.pop(name, None)
            if name in have:
                remove[name] = why

    author = item.get('user') or {}
    outsider = item.get('author_association', 'NONE') not in TEAM
    if action in ('opened', 'reopened', 'edited', 'synchronize'):
        if action == 'reopened':
            for x in list(now()):
                if sync.prefix_of(x) == 'resolution':
                    drop(x, 'reopened')
        if action != 'synchronize':
            for a in sorted(answers(item.get('body'), cfg['form'])):
                for name in cfg['form'].get(a, []):
                    offer(name, f'the author chose "{a}"')
            for name in sorted(linked):
                if sync.prefix_of(name) in INHERIT:
                    offer(name, 'from the issue it closes')
        if kind == 'pr' and action != 'edited':
            for rule in cfg['paths']:
                hit = [f for f in files if matches(f, rule['paths'])]
                if hit and (rule.get('when', 'any') == 'any' or len(hit) == len(files)):
                    offer(rule['label'], f'it changes {hit[0]}' + (f' and {len(hit) - 1} more' if len(hit) > 1 else ''))
            if cfg['size']:
                want = size_label(cfg, int(item.get('additions', 0)) + int(item.get('deletions', 0)))
                for x in list(now()):
                    if sync.prefix_of(x) == 'size' and x != want:
                        drop(x, 'the size changed')
                offer(want, f'{int(item.get("additions", 0)) + int(item.get("deletions", 0))} lines changed')
        if action == 'opened':
            if author.get('type') == 'Bot':
                offer('ai: authored', 'a GitHub app opened it')
            if outsider:
                offer('source: contribution' if kind == 'pr' else 'source: report', 'opened by someone outside the team')
            if (outsider or not dim_full('type')) and not dim_full('status'):
                offer(TRIAGE, 'nobody on the team has looked at it yet')

    elif action == 'labeled':
        new = (ev.get('label') or {}).get('name', '')
        p = sync.prefix_of(new)
        if exclusive(cfg, p):
            for x in list(now()):
                if sync.prefix_of(x) == p and x != new:
                    drop(x, f'{new} replaces it')
        if new != TRIAGE and p in ('type', 'area', 'priority', 'status', 'resolution') and TRIAGE in now():
            drop(TRIAGE, f'labelled {new} by the team')

    elif action == 'closed':
        for x in list(now()):
            if sync.prefix_of(x) == 'status':
                drop(x, 'closed')
        reason = item.get('state_reason')
        if kind == 'issue' and not dim_full('resolution'):
            if reason == 'duplicate':
                offer('resolution: duplicate', 'closed as a duplicate')
            elif reason == 'not_planned':
                offer('resolution: wontfix', 'closed as not planned')

    problems = []
    if kind == 'pr' and item.get('state', 'open') == 'open' and action != 'closed':
        for p, d in cfg['dims'].items():
            if d['rule'] == 'one' and 'pr' in d['applies']:
                n = sum(1 for x in now() if sync.prefix_of(x) == p)
                if n != 1:
                    problems.append(f'needs exactly one {p}: label, has {n}')
    return add, remove, problems


# ---------------------------------------------------------------- the event

def run(api, full, event, ev, shared, org, summary=print):
    repo = sync.repo_manifest(api, full)
    cfg = config(shared, org, repo)
    kind = 'pr' if event.startswith('pull_request') else 'issue'
    item = ev['pull_request'] if kind == 'pr' else ev['issue']
    n = item['number']
    files, linked = [], set()
    if kind == 'pr' and ev['action'] in ('opened', 'reopened', 'synchronize'):
        files = [f['filename'] for f in api.pages(f'/repos/{full}/pulls/{n}/files')][:3000]
    if kind == 'pr' and ev['action'] in ('opened', 'reopened', 'edited'):
        for m in sorted(set(CLOSING.findall(item.get('body') or '')))[:10]:
            issue = api.call('GET', f'/repos/{full}/issues/{m}')
            if issue and 'pull_request' not in issue:
                linked.update(x['name'] for x in issue.get('labels', []))
    add, remove, problems = decide(cfg, event, ev, files, linked)
    if add:
        have = {x['name'].lower() for x in api.pages(f'/repos/{full}/labels')}
        for name in add:
            if name.lower() not in have:        # create it as the standard says, not with GitHub's grey default
                api.call('POST', f'/repos/{full}/labels', cfg['labels'][name.lower()])
        api.call('POST', f'/repos/{full}/issues/{n}/labels', {'labels': sorted(add)})
    for name in remove:
        api.call('DELETE', f'/repos/{full}/issues/{n}/labels/{urllib.parse.quote(name, safe="")}')
    lines = [f'### Labels on #{n}', '']
    lines += [f'- added `{k}`: {v}' for k, v in sorted(add.items())]
    lines += [f'- removed `{k}`: {v}' for k, v in sorted(remove.items())]
    lines += [f'- **{p}**' for p in problems] or []
    if len(lines) == 2:
        lines.append('- no change')
    summary('\n'.join(lines))
    return add, remove, problems


def main():
    with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as f:
        ev = json.load(f)
    shared, org = sync.load(sync.SHARED), sync.load(sync.ORG)
    api = sync.GitHub(os.environ.get('GH_TOKEN') or sys.exit('triage: set GH_TOKEN'))
    out = os.environ.get('GITHUB_STEP_SUMMARY')

    def summary(text):
        print(text)
        if out:
            with open(out, 'a', encoding='utf-8') as f:
                f.write(text + '\n')

    _, _, problems = run(api, os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_EVENT_NAME'], ev, shared, org, summary)
    if problems:
        print('::error::' + '; '.join(problems) + '. A maintainer adds the missing label (see LABELS-STANDARD.md).')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
