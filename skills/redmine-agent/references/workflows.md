# Redmine Agent — Operational Workflows

Step-by-step procedures the agent follows. All API shapes are in
`references/api-reference.md`; template text in `templates/`. Runtime values (status ids,
custom-field ids, user ids) are resolved dynamically — see §0.

---

## 0. Runtime resolution (do this first in every session)

```bash
GET /issue_statuses.json        → STATUS = {name → {id, is_closed}}
                                 "Pending Approval" id may not exist → see §3 fallback
GET /users/current.json         → confirm credentials work; record my id
GET /custom_fields.json         → CF = {name → id}  (find "Resolution", "SLA Policy",
                                 "Response Due", "Resolution Due", "SLA State")
GET /enumerations/issue_priorities.json → PRIORITY = {name → id}
```

Cache these for the session; re-query if a 422 hints a changed id.

---

## 1. List / triage tickets

1. Parse the request into filters: project, tracker, status, assignee, priority, dates,
   free text.
2. Build `GET /issues.json` query (encode date operators!).
3. Page through with `limit=100` until `total_count` covered or the user's cap reached.
4. Present a table sorted by the user's intent (default `updated_on:desc`):

   ```
   | #ID | Tracker | Status | Priority | Subject | Assignee | Updated | Due |
   ```

5. Offer next actions per row (assign / comment / approve / close / SLA check).
6. For triage of *unassigned* open issues: `status_id=open&assigned_to_id=!*`.

## 2. Assignment workflow

Goal: issue N is assigned to user U, U is notified, audit trail written.

1. **Resolve user**: `GET /users.json?status=1&name=<login-or-name>`.
   - Exactly one active match → proceed.
   - 0 or >1 matches → show candidates, ask the user to disambiguate. Never guess.
2. **Check project membership** (skip if the user waived it):
   `GET /projects/:project_id/memberships.json` → confirm U has a role allowing
   `assign_issues`. If U is not a member, report and offer to add them via
   `POST /projects/:id/memberships.json` (needs confirmation).
3. **Prefetch issue**: `GET /issues/:id.json` → capture current `assigned_to`, `status`,
   `subject` for the before/after report.
4. **Update + notify in one call** (Redmine emails watchers when a journal is added):

   ```json
   PUT /issues/:id.json
   {"issue": {
      "assigned_to_id": <uid>,
      "status_id": <In Progress id>,            // only if it's currently New and workflow allows
      "notes": "<assignment template rendered>"
   }}
   ```

   Render the `assignment` template (`templates/mail-templates.md`) with
   assignee/requester/issue fields and the ticket URL.
5. **Verify**: `GET /issues/:id.json` → `assigned_to.id == uid`.
6. **Report**:
   ```
   ✔ #1234 "Login page 500s" assigned: (unassigned) → jsmith
     Status: New → In Progress   · notification note posted (watchers mailed by Redmine)
   ```
7. **Bulk mode**: loop ids; per id run steps 1–5; final tally
   `assigned: N, failed: M [ids+reasons]`; abort on first 3 consecutive failures.

## 3. Approval-based closure workflow

State machine:

```
In Progress ──submit──▶ Pending Approval ──APPROVED──▶ Closed (resolution set)
      ▲                        │
      └───────REJECTED─────────┘
```

### 3.1 Submit for approval

1. Prefetch `GET /issues/:id.json?include=allowed_statuses,journals` — confirm
   `Pending Approval` ∈ `allowed_statuses`. If the instance has no such status, fall
   back to leaving status unchanged and flagging the issue with custom field
   `Approval State = Pending` (id from CF map), and say so in the report.
2. Identify the **approver**: explicit user instruction > custom field `Approver` >
   project lead from `/projects/:id.json` (`lead`). Ask if ambiguous.
3. `POST /issues/:id/watchers.json {"user_id": <approver_uid>}` (ignore 422 "already
   watcher").
4. `PUT /issues/:id.json`:
   ```json
   {"issue": {
      "status_id": <Pending Approval id>,
      "notes": "<approval_request template: summary, what was done, resolution draft, how to decide, deadline>"
   }}
   ```
   Include in the note the exact decision tokens the approver may reply with:
   `APPROVED` or `REJECTED: <reason>`.
5. Report the request and the deadline (default 2 business days from the note).

### 3.2 Approval gate (hard rule)

Before any close/reject action, gather **approval evidence**:

1. `GET /issues/:id.json?include=journals`.
2. Valid evidence = a journal entry where:
   - `journal.user.id == approver_uid` (identity), AND
   - the note contains an unambiguous decision token:
     `APPROVED` / `APPROVE` / `LGTM` / `OK TO CLOSE` (approve side) or
     `REJECTED` / `REJECT` / `NOT APPROVED` (reject side), AND
   - it is the **latest** decision from that approver (later decisions override).
3. Alternatively, the user in chat directly instructs "approver X approved ticket N" —
   record that as evidence and cite it in the notes.
4. **No evidence → do not close.** Reply with the pending state and how to approve.
   The CLI enforces this too (exit code 3).

### 3.3 On APPROVED — close with resolution

1. Determine `resolution_code` (see `templates/resolution-templates.md`):
   explicit instruction > draft submitted at 3.1 > `Fixed` for merged fixes > ask.
2. Compose the closure note = `closure_notice` template + `resolution_note` skeleton
   (Problem / Root Cause / Resolution / Verification / Prevention), fully filled.
   **Never invent root-cause facts** — only use what the journals contain; mark
   unknowns as `_Not recorded_`.
3. One `PUT /issues/:id.json`:
   ```json
   {"issue": {
      "status_id": <id where is_closed == true>,   // e.g. "Closed"
      "done_ratio": 100,
      "custom_fields": [
        {"id": <Resolution cf id>, "value": "Fixed"},
        {"id": <Approval State cf id>, "value": "Approved"}      // if used
      ],
      "notes": "<rendered closure note>"
   }}
   ```
4. Verify: `GET /issues/:id.json` → `status.is_closed == true`, resolution set.
5. Optionally log time: `POST /time_entries.json` (only with real hours, never fake).
6. Report: resolution code, who approved (name + journal id + date), notifications sent.

### 3.4 On REJECTED — reopen

```json
PUT /issues/:id.json
{"issue": {
   "status_id": <In Progress id>,
   "custom_fields": [{"id": <Approval State cf id>, "value": "Rejected"}],
   "notes": "<approval_rejected template with reason>"
}}
```

Remove nothing else. Report the reason and the expected rework.

---

## 4. SLA monitoring workflow

### 4.1 Policy & clocks

Default policy (override via custom field `SLA Policy` or user instruction):

| Priority | First response | Resolution |
|---|---|---|
| Critical/Urgent | 1 h | 8 h |
| High | 4 h | 24 h |
| Normal | 8 h | 72 h |
| Low | 24 h | 168 h |

- `response_deadline  = created_on + response_target`
- `resolution_deadline = created_on + resolution_target`
- If custom fields `Response Due` / `Resolution Due` exist and are set, they win.
- First response = first journal entry with `notes` authored by someone other than the
  author. Resolution clock stops when status `is_closed`.
- All timestamps compared in UTC. `created_on` from the API is UTC (`...Z`).

### 4.2 States

```
elapsed% = (now - created_on) / (deadline - created_on)

OK        : elapsed% < 75
WARNING   : elapsed% >= 75 and now < deadline
BREACHED  : now >= deadline
ESCALATED : BREACHED and escalation action applied
RESPONDED : first-response deadline met (for the response clock)
CLOSED-OK / CLOSED-LATE : terminal, from history
```

### 4.3 Scan run (command: `sla-report [--project P] [--notify]`)

1. Pull open issues: `GET /issues.json?status_id=open&project_id=P&limit=100` (page).
   Include anything with a deadline inside 48 h plus every open Critical/High.
2. For each issue fetch `include=journals,allowed_statuses` only when needed
   (warning/breach candidates).
3. Compute both clocks; classify; build rows:

   ```
   | #ID | Subject | Priority | Clock | Deadline (UTC) | Elapsed | State | Action |
   ```
4. **Actions (idempotent — check last journal before writing):**
   - `WARNING` → post `sla_warning` note (once per issue per day), tag assignee.
   - `BREACHED` → post `sla_breach` note; set `SLA State = Breached`; raise priority one
     step (Low→Normal→High→Critical; never above Critical); add project lead as watcher.
     These three mutations need prior user confirmation the **first** time per project,
     then run automatically within the same session.
   - `ESCALATED` (older than 2× the resolution target) → post `sla_escalation` note
     adding the project lead + a mention in the note body.
5. Emit the report table + summary:
   `open: N · ok: n1 · warning: n2 · breached: n3 · worst: #id`.
6. With `--notify`, also render the `daily_digest` template and post it as a news item
   (`POST /projects/:id/news.json`) or as an issue note to the team's tracking issue
   (configurable); never send external mail unless SMTP env is configured AND the user
   asked.

### 4.4 Answering SLA questions quickly

- "Are we within SLA on X?" → fetch X, compute both clocks, answer with state + margin.
- "Which tickets are at risk today?" → scan with horizon = end of today.
- "SLA report for October" → filter `closed_on`/`created_on` range, include closed
  (`status_id=*`), report met/missed ratio from `CLOSED-OK` vs `CLOSED-LATE`.

---

## 5. Comment / update notes

1. Read the issue first (context, current status).
2. `PUT /issues/:id.json {"issue": {"notes": "..."}}` — `private_notes: true` for
   internal commentary.
3. For templated notes (assignment/SLA/closure) render from `templates/` rather than
   free-styling.

## 6. Reporting output style

- Lead with the outcome sentence ("3 tickets assigned to jsmith, 1 failed").
- Table for anything ≥ 3 items; short bullets otherwise.
- Always end mutations with verification result (the confirm GET).
- Surface `422 errors[]` verbatim — they explain instance-specific validation
  (workflow transitions, required custom fields).
