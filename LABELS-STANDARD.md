# The labelling standard

**Version 1, 27 October 2026.** One way to classify repositories, and one way to label issues and pull requests,
across [Foundation-0](https://github.com/Foundation-0), [Proven-Commons](https://github.com/Proven-Commons) and
[aska-digital](https://github.com/aska-digital). This file is the same, word for word, in each organisation's
`.github` repository, as are `labels.shared.toml`, `sync.py`, `triage.py` and their tests. A change is made in all
three at once and raises the version. Each organisation adds its own dimensions in `.github/labels/labels.toml`,
and lists its repositories in `.github/labels/repos.toml`.

It is built for a repository that gets a lot of public participation, from people and agents who don't read
this file, don't write titles and leave bodies empty. Three things make it hold up:

1. **Outsiders can't set labels at all.** On GitHub that takes triage access. So every label on an item came from
   a form, from the triage bot, or from a maintainer.
2. **The bot labels from structured signals, never from words.** It reads the answers picked in a form, the files a
   pull request changes, its size, who opened it, and the issues it closes. It never guesses from a title.
3. **The bot keeps the set consistent, whoever changed it.** A single-label dimension keeps the newest label, an
   untriaged item is marked, and a pull request without a type fails a check.

## Two systems

| | Repositories | Issues and pull requests |
|---|---|---|
| With | GitHub **topics** (public, searchable) and the organisation's **custom properties** (typed; an admin's settings, usable in rulesets) | **labels**, in dimensions |
| Listed in | `.github/labels/repos.toml` (private repositories in a private repository's copy) | `labels.shared.toml` + `labels.toml` + each repository's `.github/labels.toml` |
| Applied by | `sync.py repos apply`, by an owner | `triage.py` on every event, `sync.py apply` for the label set itself |

A repository, an issue or a pull request can carry many values at once. Each dimension says how many of its own
it allows.

## Repositories

**Topics.** Every repository carries its organisation's topic (`foundation-0`, `proven-commons`, `aska-digital`),
plus any subject topics people would search for (`mathematics`, `lean`, `ai-agents`). `sync.py` only adds topics;
it never removes one someone set by hand.

**Custom properties.** Four, with the same names in every organisation. The values are each organisation's own:

| Property | Type | Meaning |
|---|---|---|
| `kind` | multi-select | What the repository is: `core`, `research`, `library`, `product`, `site`, … A repository can be several. |
| `domain` | multi-select | What it is about: a field, a product family. |
| `stage` | single-select | How far along it is, on the organisation's own scale (Foundation0's Seed, Active and Published tiers, Proven Commons' path to the verified bar). |
| `board` | single-select | Its discussion board under the forum standard: `main`, `internal`, `project` or `none`. |
| `owner` | text | The person or team who answers for it. |

Properties are set by organisation owners only, so a repository can't reclassify itself. Rulesets can target
them, for example "every repository with `kind` research requires the verify check".

## Issues and pull requests: the dimensions

A label is `<dimension>: <value>`. The rule says how many labels from one dimension a single item may carry.

| Dimension | Rule | On | Set by | What it answers |
|---|---|---|---|---|
| `type:` | exactly one | issues, PRs | forms, path rules, the closed issue, maintainers | What kind of work: `bug`, `feature`, `docs`, `chore`, `research`, `question`, `tracking` |
| `status:` | at most one | issues, PRs | the bot (`needs triage`), maintainers | Why it is waiting: `needs triage`, `needs info`, `needs decision`, `needs admin`, `blocked`, `ready`, `in progress`. None means it is moving. |
| `priority:` | at most one | issues, PRs | maintainers, the closed issue | How soon: `p0` (harm now) to `p3` (when there is time). Most items carry none. |
| `area:` | any | issues, PRs | the repository's path rules, its forms, maintainers | Which part of the repository. Each repository defines its own. |
| `concern:` | any | issues, PRs | forms, path rules, maintainers | What needs a second look: `security`, `privacy`, `accessibility`, `performance`, `compliance`, `breaking` |
| `source:` | at most one | issues, PRs | the bot, maintainers | Where it came from: `report` and `contribution` (outside the team), `audit`, `review`, `forum`, `incident` |
| `ai:` | at most one | issues, PRs | forms, the bot (GitHub apps), maintainers | What an AI agent did: `authored` or `assisted` |
| `size:` | at most one | PRs | the bot, on every push | Lines changed: `xs` up to 10, `s` 50, `m` 250, `l` 1000, `xl` beyond |
| `resolution:` | at most one | issues, PRs | the bot (from GitHub's close reason), maintainers | Why it closed undone: `duplicate`, `invalid`, `wontfix`, `superseded` |

Plus GitHub's two names, kept exactly, because GitHub lists them to newcomers: `good first issue` and
`help wanted`.

**The organisations' own dimensions** (`labels.toml`):

- Foundation-0: `claim:` (`proven`, `verified`, `known`, `conjecture`: the Repository Standard's labels for a
  result), `evidence:` (the strongest check a claim has passed, charter 0.4), `review:` (what a research-review
  issue reports).
- Proven-Commons: `bar:` (which item of the verified bar), `level:` (the academy level a task suits),
  `upstream:` (`bug found`, `lemma`, `fix offered`).
- aska-digital: `team:` (`protean`, `amp`, `owner`).

**A repository's own:** `area:` labels only, in its `.github/labels.toml`, each with the paths it covers:

```toml
version = 1

[[labels]]
name = "area: relay"
description = "The approval relay"
paths = ["relay/"]                     # a pull request touching these gets the label
form = ["The approval relay"]          # an issue form answer that means it (optional)
```

## How the triage bot labels

`triage.py` runs on every issue and pull request event, through the organisation's reusable workflow
(`.github/workflows/labels.yml`, called from each repository by `workflow-templates/labels.yml`).

| Signal | Gives | Works without a title or body |
|---|---|---|
| The issue form used | `type:`: blank issues are off, so every issue comes from a form, and each form attaches its type | yes |
| Answers picked in a form, boxes ticked in the PR template | `type:`, `concern:`, `ai:`, `area:`, and any label listing that exact answer in `form` | no: the next rows cover it |
| The files a PR changes | `area:` from the repository's paths; `type: docs` when every file is documentation; `type: chore` when every file is CI, ignore files or dependencies; `concern: security` when it touches workflows, `CODEOWNERS` or a security policy; the organisations' own (Foundation0's `results/` are `type: research`) | yes |
| Lines changed | `size:` | yes |
| Who opened it | `source: report` or `source: contribution` when outside the team; `ai: authored` when a GitHub app; `status: needs triage` for every outside item and every untyped one | yes |
| The issues a PR closes (GitHub's own "Fixes #12" keywords) | their `type:`, `area:`, `concern:` and `priority:` | yes, if it links one |

Two rules keep a signal from doing harm. A signal only ever **adds**. And it adds to a single-label dimension
only when that dimension is **empty**, so it can never override a maintainer. Words in free text are never
read.

**Keeping the set consistent:**

- Adding a label in a single-label dimension removes the other one: `type: feature` replaces `type: bug`.
- `status: needs triage` comes off as soon as a maintainer sets a type, area, priority, status or resolution.
  The open items still carrying it are the triage queue:
  `is:open label:"status: needs triage"`.
- Closing removes `status:` labels. GitHub's close reason becomes `resolution: duplicate` or
  `resolution: wontfix` (not planned), unless a maintainer set another. Reopening removes the resolution.
- `size:` is recomputed on every push.
- A pull request without exactly one `type:` fails the triage check, and the run's summary says what is missing.
  Labels the bot adds don't start a new run (GitHub's rule for its own token), so the check is a signal for
  maintainers. Don't make it a required status check.

Every change the bot makes is listed in the run's summary, with its reason.

## Triage, for maintainers

1. Work the queue: `is:open label:"status: needs triage"`.
2. Set `type:` if it is missing (one), and `area:` (any).
3. Set `priority:` only when it is not "when someone gets to it".
4. Set `status:` only when it is waiting on something; take it off when it moves again.
5. Close with GitHub's reason. Set `resolution: invalid` or `resolution: superseded` by hand when one fits.

An agent with triage access follows the same steps, and the same rules hold.

## Changing a label set

- **Shared** (`labels.shared.toml`, `sync.py`, `triage.py`, the tests): one pull request in each `.github`
  repository, identical, raising `VERSION`.
- **An organisation's dimensions** (`labels.toml`): a pull request to its `.github` repository, approved by its
  owners (`CODEOWNERS`).
- **A repository's areas** (`.github/labels.toml` in the repository): a pull request there.
- **Renaming** is done with `[renames]`, never by deleting and re-creating, so issues keep their labels. The
  `[renames]` table in the shared set already moves GitHub's defaults (`bug`, `enhancement`, `documentation`, …) and
  the labels in use before this standard (`needs decision`, `needs admin`, `audit`, `security`) to their new names.

`sync.py check` runs in CI on every change to the label set. `test_templates.py` fails if a form option or a PR
template box doesn't mean exactly one label, so a typo can't silently stop the bot labelling.

## Rolling it out in an organisation

An owner, or an agent with an owner's token:

1. Merge the organisation's `.github` change: the label set, the reusable workflow, the workflow template, and
   the default issue forms and PR template (repositories without their own get these).
2. `GH_TOKEN=… python3 .github/labels/sync.py plan --org`. Read it: renames, updates, creations, and labels it
   doesn't know (it keeps those).
3. `GH_TOKEN=… python3 .github/labels/sync.py apply --org`.
4. `GH_TOKEN=… python3 .github/labels/sync.py repos plan`, then `repos apply`: the property schema, each
   repository's values, and topics. aska-digital also runs it with `--repos` pointing at the private list in
   `internal-stoa`.
5. In each repository, add `.github/workflows/labels.yml` from the workflow template (Actions → New workflow →
   "Labels"), and a `.github/labels.toml` with its areas.
6. Organisation settings → Repository → Repository labels: replace GitHub's defaults with the shared set, so
   new repositories start with it.
7. `sync.py lint OWNER/REPO` lists open items that break a rule. Run it once after the first apply to see the
   backlog to triage.
