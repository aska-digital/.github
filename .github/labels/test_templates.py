#!/usr/bin/env python3
"""Template tests (no network): every answer a form or the pull request template offers in a section the triage
reads means exactly one label, every label a form attaches exists, and the template headings are ones the bot
parses. A typo in an option would otherwise silently stop labelling.

    python3 .github/labels/test_templates.py
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import sync  # noqa: E402

fails = []


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        fails.append(what)


shared, org = sync.load(sync.SHARED), sync.load(sync.ORG)
_, labels, _ = sync.merge(shared, org)
answers = {a.lower() for m in (shared, org) for lab in m.get('labels', []) for a in lab.get('form', [])}
# answers that deliberately mean no label
NEUTRAL = {'a person'}

forms = sorted(glob.glob(os.path.join(ROOT, '.github', 'ISSUE_TEMPLATE', '*.yml')))
for f in forms:
    if f.endswith('config.yml'):
        continue
    text = open(f, encoding='utf-8').read()
    name = os.path.basename(f)
    slips = [n for n, line in enumerate(text.split('\n'), 1)
             if (re.match(r'\s*(?:-\s+)?(?:name|description|label|placeholder|value):\s+[^"\'|>].*:\s', line)
                 or re.fullmatch(r'\s+-\s+(?i:yes|no|true|false|on|off)\s*', line))]
    check(not slips, f'{name}: no unquoted ": " or bare yes/no that YAML would misread (lines {slips or "none"})')
    m = re.search(r'^labels:\s*\[(.*)\]\s*$', text, re.M)
    attached = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    check(attached and all(a.lower() in labels for a in attached), f'{name}: attaches a type label from the set ({attached})')
    for section in re.split(r'\n  - type: ', text)[1:]:
        if not section.startswith(('checkboxes', 'dropdown')) or 'id: touches' not in section and 'id: ai' not in section \
                and 'id: review' not in section and 'id: bar' not in section and 'id: part' not in section:
            continue
        opts = re.findall(r'^\s+- (?:label: )?(.+?)\s*$', section.split('options:', 1)[1], re.M)
        bad = [o for o in opts if o.strip('"').lower() not in answers | NEUTRAL]
        check(not bad, f'{name}: every option in a triaged field means a label ({bad or "all do"})')

pr = os.path.join(ROOT, '.github', 'pull_request_template.md')
if os.path.exists(pr):
    boxes = re.findall(r'^- \[ \] (.+?)\s*$', open(pr, encoding='utf-8').read(), re.M)
    bad = [b for b in boxes if b.lower() not in answers]
    check(boxes and not bad, f'pull request template: every box means a label ({bad or "all do"})')

print(f'\n{"FAILED: " + str(len(fails)) if fails else "all passed"}')
sys.exit(1 if fails else 0)
