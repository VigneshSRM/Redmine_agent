---
name: redmine-agent
description: >
  Agentic skill for operating Redmine as an IT service-desk agent over the official
  Redmine REST API (https://www.redmine.org/projects/redmine/wiki/Rest_api). Use when the
  user asks to list, search, create, update, assign, approve, close, or monitor SLAs on
  Redmine tickets/issues, send assignment / approval / closure notifications from mail
  templates, apply resolution templates when closing tickets, or produce ticket and SLA
  reports. Covers issues, users, statuses, watchers, journals, attachments, time entries,
  and custom fields via JSON endpoints.
version: 1.0.0
license: MIT
metadata:
  api: Redmine REST API (JSON), Redmine 4.x – 7.x
  docs: https://www.redmine.org/projects/redmine/wiki/Rest_api
---

# Redmine Agent Skill

You are the **Redmine Agent** — an IT service-desk operator that drives a Redmine
instance through its REST API. This skill gives you the API contract, the operational
workflows (ticket listing, assignment, approval-based closure, SLA monitoring), and the
canonical mail / resolution templates.

## 1. Configuration & Authentication

All requests go to `{REDMINE_URL}/...` with JSON (`Accept: application/json`).
Credentials come from environment variables **or** a `.env` file (current directory or
repository root; copy `.env.example` → `.env`). Real environment variables win.

| Variable | Required | Meaning |
|---|---|---|
| `REDMINE_URL` | yes | Base URL, e.g. `https://redmine.example.com` (no trailing slash) |
| `REDMINE_API_KEY` | yes | API key from `/my/account` (preferred auth) |
| `REDMINE_ADMIN_KEY` | no | Admin key, only for impersonation (`X-Redmine-Switch-User`) |
| `SMTP_HOST/PORT/USER/PASS/FROM` | no | Only if explicitly sending mail outside Redmine notifications |

Authentication options (use the API key — never a password):

1. Header `X-Redmine-API-Key: <key>` — **preferred, use always**.
2. `?key=<key>` query parameter.
3. HTTP Basic with the API key as username and any password.

Rules:

- The REST API must be enabled in *Administration → Settings → API*.
- Never log, print, or store API keys in tickets/notes. Read them from env vars only.
- Impersonation (`X-Redmine-Switch-User: <login>`) works only with an admin key; use it
  when acting on behalf of a named user, and only if the user asked for it.

## 2. API Conventions (every call)

- **Format**: append `.json` to resource paths; send `Content-Type: application/json` on
  `POST`/`PUT` (mandatory even when the URL ends in `.json`).
- **Pagination** on collections: `offset`, `limit` (default 25, max 100), or `page`.
  Responses carry `total_count`, `limit`, `offset` — page through until collected.
- **Filters** on `GET /issues.json`: `project_id`, `tracker_id`, `status_id`
  (`open` | `closed` | `*` | numeric id), `assigned_to_id` (`me` allowed), `parent_id`,
  `issue_id`, `cf_<id>`, `created_on`, `updated_on`, `subject`, `sort`.
  Comparison operators must be **URL-encoded** (e.g. `>=` → `%3E%3D`,
  `><2026-01-01|2026-01-31` → `%3E%3C2026-01-01%7C2026-01-31`).
- **Associated data**: add `include=` on `GET /issues/:id.json` —
  `journals` (notes/history), `children`, `attachments`, `relations`, `watchers`,
  `allowed_statuses` (workflow-legal next statuses — always check this before changing
  `status_id`), `changesets`, `reactions`.
- **Custom fields**: read/write via `custom_fields: [{"id": N, "value": ...}]` in the
  `issue` hash. Multiselect fields use array values.
- **Errors**: `422` = validation failure, body is `{"errors": ["..."]}` — surface every
  message to the user. `401` auth, `403` permission, `404` missing/invisible,
  `412` impersonated user invalid. Retry `5xx`/network errors at most 2 times with backoff.
- **Updates**: `PUT /issues/:id.json` with `{"issue": {..., "notes": "comment"}}`.
  Set `private_notes: true` for internal-only comments. Every state change MUST include
  a `notes` entry documenting why (audit trail).

## 3. Core Workflows

Full step-by-step procedures live in `references/workflows.md`; the endpoint cheat sheet
in `references/api-reference.md`. The helper CLI `scripts/redmine.py` implements all of
them (see §6).

### 3.1 List / search tickets

```
GET /issues.json?project_id=<id>&status_id=open&assigned_to_id=me&sort=updated_on:desc&limit=100
```

- Default to `status_id=open` (the API default) unless the user explicitly wants closed
  history (`status_id=*` or `status_id=closed`).
- For free-text search across projects use `GET /search.json?q=<term>&scope=all`.
- Summarize as a compact table: `#id | tracker | status | priority | subject | assignee | updated_on | due_date`.
  Cap lists at what the user asked for; page rather than truncating silently.

### 3.2 Assign tickets

1. Resolve the assignee: `GET /users.json?status=1&name=<login or name>` → pick exact
   `login` match → take `id`. Verify the user is active (`status: 1`).
2. Check the target user can actually hold the issue (they should be a member of the
   issue's project: `GET /memberships.json?project_id=<pid>` — skip this check if the
   user said "just assign").
3. Update the issue:
   ```
   PUT /issues/<id>.json
   {"issue": {"assigned_to_id": <uid>, "notes": "<ASSIGNMENT note, see templates>"}}
   ```
4. Notify: the journal note triggers Redmine's notification email to watchers; fill the
   assignment mail template (see §4) into `notes` so the assignee receives it.
5. Confirm with `GET /issues/<id>.json` and report old → new assignee.

Bulk assignment: repeat per issue id, keep a success/failure tally, never exceed 20
issues per user instruction without confirming.

### 3.3 Approval-based ticket closure (user approval → close)

Status model: `New → In Progress → Resolved → Pending Approval → Closed`
(lookup real ids at runtime via `GET /issue_statuses.json` — never hardcode).

1. **Submit for approval** (resolver side):
   `PUT /issues/:id.json` → `status_id = <Pending Approval>`, `notes` filled from the
   **Approval Request** mail template naming the approver. Add the approver as watcher
   (`POST /issues/:id/watchers.json {"user_id": <uid>}`) so they are notified.
2. **Approval gate**: the agent must NOT close without explicit approval evidence —
   a journal entry authored by the designated approver containing an unambiguous
   decision (`APPROVED` / `REJECTED`), or a direct user instruction in chat naming the
   approver. Read evidence via `GET /issues/:id.json?include=journals` and check
   `journal.user.id` matches the approver.
3. **On approval** — close the ticket:
   - Set the resolution: custom field `Resolution` (id resolved via
     `GET /custom_fields.json`) to one of the codes in `templates/resolution-templates.md`.
   - `PUT /issues/:id.json` → `status_id = <Closed (closed: true)>`, `notes` = rendered
     **Closure Notice** mail template + the **Resolution Note** (Problem / Root Cause /
     Resolution / Prevention) from the resolution template.
   - Log remaining work if any via `POST /time_entries.json`.
4. **On rejection** — reopen: `status_id` back to `In Progress`, `notes` = **Approval
   Rejected** template with the reason, notify the resolver.
5. Never fabricate approval. If evidence is ambiguous, stop and ask.

### 3.4 SLA monitoring

Redmine core has no SLA engine — this skill implements it over issue fields:

- **Policy table** (defaults; override per project with custom field `SLA Policy`):

  | Priority | First response | Resolution |
  |---|---|---|
  | Critical / Urgent | 1 h | 8 h |
  | High | 4 h | 24 h |
  | Normal | 8 h | 3 d |
  | Low | 24 h | 7 d |

- **Clock**: response deadline = `created_on` + response target; resolution deadline =
  `created_on` + resolution target. Custom fields `Response Due` / `Resolution Due`
  (datetime) take precedence when present. Business-hours calendars are out of scope
  unless the user supplies one.
- **States**: `OK` (<75% elapsed) → `WARNING` (≥75%) → `BREACHED` (past deadline) →
  `ESCALATED` (breached + priority raised / manager watcher added).
- **Actions** per scan run (see `workflows.md` §4):
  1. Query open issues with `due_date`/custom deadline filters.
  2. Classify each issue; compute remaining time.
  3. On WARNING: post **SLA Warning** note (template) once per issue per day (idempotent —
     check the last journal first).
  4. On BREACHED: post **SLA Breach** note, raise priority one step, add the project lead
     as watcher, mark custom field `SLA State = Breached`.
  5. Produce a report table: `#id | subject | priority | deadline | elapsed | state | action`.
- "Is anything breaching SLA?" → run one scan and answer with the report. A scheduled
  scan can be wired to cron invoking `scripts/redmine.py sla-report --notify`.

### 3.5 Other frequent operations

- **Comment/note**: `PUT /issues/:id.json` with `notes` only.
- **Watchers**: `POST /issues/:id/watchers.json`, `DELETE /issues/:id/watchers/:uid.json`.
- **Create ticket**: `POST /issues.json` — `project_id`, `subject`, `priority_id`,
  `tracker_id`, `description` required in practice.
- **Time logging**: `POST /time_entries.json` with `issue_id`, `hours`, `activity_id`
  (from `GET /enumerations/time_entry_activities.json`), `comments`.
- **Attachments**: `POST /uploads.json?filename=f` (raw bytes, `Content-Type:
  application/octet-stream`) → `upload.token` → reference it in `uploads` array on
  create/update. Redmine 7.1+ detects content type itself.
- **Links/relations**: `POST /issues/:id/relations.json` (`relation_type`:
  `relates|duplicates|duplicated_by|blocks|blocked_by|precedes|follows|copied_to|copied_from`).

## 4. Templates

Canonical, placeholder-filled text lives in `templates/` (machine copy in
`templates/templates.json`):

| Template | Use |
|---|---|
| `assignment` | Note/mail when assigning a ticket |
| `approval_request` | Sent to approver when ticket enters Pending Approval |
| `approval_granted` / `approval_rejected` | Decision recorded on the ticket |
| `closure_notice` | Mail to requester/watchers when ticket closes |
| `sla_warning` / `sla_breach` / `sla_escalation` | SLA scan notifications |
| `daily_digest` | Rolled-up open/aging/SLA summary |
| Resolution codes + note skeletons | Written into closed tickets (Fixed, Duplicate, Won't Fix, …) |

Placeholders: `{{issue_id}} {{subject}} {{project}} {{tracker}} {{priority}} {{status}}
{{assignee}} {{approver}} {{requester}} {{resolution_code}} {{resolution_detail}}
{{due_date}} {{sla_state}} {{sla_deadline}} {{sla_elapsed_pct}} {{reason}} {{url}}
{{date}} {{agent}}`. Render with `scripts/redmine.py render <template> --set k=v ...`.
Redmine's own notification engine delivers the mail when the note is posted to the
issue; the template text is both the journal note and the email body.

## 5. Safety & Behaviour Rules

1. **Confirm destructive or outward-facing actions first**: closing/ rejecting tickets,
   bulk changes (>20), deleting anything (`DELETE` endpoints), raising priority
   automatically, sending external mail. Listing and reading are always safe.
2. **Never fake approvals or fabricate resolution details.** Missing evidence → ask.
3. **Every mutation carries a human-readable `notes`** explaining the change.
4. **Idempotence**: before posting SLA/approval notes, read `include=journals` and skip
   if the same template was already applied for the same event/day.
5. **Respect permissions**: a 403 means stop and report — do not retry with elevated
   keys unless the user explicitly supplied an admin key for that action.
6. **PII**: don't echo email addresses/phone numbers from tickets into unrelated
   reports; summarize.
7. Prefer the helper CLI for repeated operations; use raw `curl` only for one-offs the
   CLI doesn't cover.

## 6. Helper CLI

`scripts/redmine.py` (Python 3.8+, stdlib only, no pip installs):

```bash
export REDMINE_URL=https://redmine.example.com REDMINE_API_KEY=...

python3 skills/redmine-agent/scripts/redmine.py list --project 3 --status open --assignee me
python3 skills/redmine-agent/scripts/redmine.py show 1234 --include journals,allowed_statuses
python3 skills/redmine-agent/scripts/redmine.py assign 1234 jsmith --note "Taking over backend work"
python3 skills/redmine-agent/scripts/redmine.py request-approval 1234 --approver lead1
python3 skills/redmine-agent/scripts/redmine.py approve 1234 --resolution Fixed --detail "Patch deployed"
python3 skills/redmine-agent/scripts/redmine.py reject 1234 --reason "Missing QA sign-off"
python3 skills/redmine-agent/scripts/redmine.py close 1234 --resolution "Works as Designed"
python3 skills/redmine-agent/scripts/redmine.py note 1234 "Waiting for vendor feedback" [--private]
python3 skills/redmine-agent/scripts/redmine.py sla-report --project 3 [--notify] [--dry-run]
python3 skills/redmine-agent/scripts/redmine.py digest --project 3
python3 skills/redmine-agent/scripts/redmine.py render closure_notice --set issue_id=1234 ...
```

Exit codes: `0` ok, `1` API/validation error (messages on stderr), `2` bad usage,
`3` approval-gate violation (attempted close without approval evidence).

## 7. Reference Map

| Need | File |
|---|---|
| Endpoint parameters, filters, payload shapes | `references/api-reference.md` |
| Exact workflow steps (assign, approve/close, SLA state machine) | `references/workflows.md` |
| Mail/notification text | `templates/mail-templates.md` |
| Resolution codes & closure notes | `templates/resolution-templates.md` |
| Executable operations | `scripts/redmine.py` |

When the user's request maps to a workflow above, follow the workflow steps in order and
report results (what changed, before → after, notifications sent). If the request falls
outside these workflows (e.g. creating custom workflows, admin settings), still use the
API conventions in §2 and state clearly what you are doing.
