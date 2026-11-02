# Resolution Templates

Canonical resolution codes written to the `Resolution` custom field (id resolved via
`GET /custom_fields.json`) plus the note skeleton posted when a ticket is closed after
approval. A machine-readable copy used by `scripts/redmine.py` is in
`templates/templates.json`.

## 1. Resolution codes

| Code | Use when | Typical tracker | Follow-up |
|---|---|---|---|
| `Fixed` | Root cause found and removed (patch, config, data fix deployed and verified) | Bug | Link the changeset / release |
| `Works as Designed` | Behaviour is the documented/intended one | Bug, Support | Point to the doc; update docs if unclear |
| `Duplicate` | Same root cause as another ticket | Bug | Fill `{{duplicate_of}}` (issue id) and add a `duplicates` relation |
| `Invalid` | Not a real defect (wrong report, misconfiguration, user error) | Bug | Explain why in the note |
| `Incomplete` | Cannot act: missing info past the waiting period | Support | State exactly what was missing |
| `Cannot Reproduce` | Never observed despite stated attempts | Bug | List environments/attempts tried |
| `Won't Fix` | Real, but fix declined (cost/risk/obsolete) | Bug | Record the decision owner and rationale |
| `Cancelled` | Request withdrawn or no longer needed | Support, Task | Name who cancelled it |
| `External` | Fixed/resolved by a third party or outside dependency | any | Cite vendor ticket/reference |
| `Not Applicable` | Out of scope for this project | any | Point to where it belongs |

If the instance's `Resolution` field has a fixed value list, pick the closest allowed
value and map to it — report the mapping used.

## 2. Closure note skeleton (posted with the final `PUT`)

```markdown
== RESOLUTION RECORD · #{{issue_id}} {{subject}} ==

Resolution code : {{resolution_code}}
Approved by     : {{approver}} on {{date}}
Closed by       : {{agent}}

1) PROBLEM
{{problem_statement}}

2) ROOT CAUSE
{{root_cause}}

3) RESOLUTION
{{resolution_steps}}

4) VERIFICATION
{{verification}}

5) PREVENTION / FOLLOW-UP
{{prevention}}
```

Filling rules (strict):

- `problem_statement` — restate the request in one or two sentences from the original
  description. Never add symptoms that were not reported.
- `root_cause` — only what the journals/changesets prove; otherwise write
  `_Not recorded_`. **Never fabricate a root cause.**
- `resolution_steps` — concrete actions taken (with change ids, KB article ids, config
  keys) as found in the journal history.
- `verification` — how it was confirmed (test, customer confirmation, monitoring).
  If unverified, write `_Pending verification — closed on approval decision_`.
- `prevention` — monitoring, doc or process follow-ups; create linked follow-up tickets
  only when the user asks.

## 3. Code-specific note addenda (append to section 3)

**Fixed**
```
Fix deployed in: {{change_ref}}
Affected versions: {{affected_versions}}
Fixed version: {{fixed_version}}
```

**Duplicate**
```
Duplicate of #{{duplicate_of}} — all further work continues there.
Relation added: #{{duplicate_of}} duplicates #{{issue_id}}.
```

**Won't Fix**
```
Decision owner: {{decision_owner}}
Rationale: {{reason}}
Revisit condition: {{revisit_condition}}
```

**Cannot Reproduce**
```
Attempted: {{attempts}}
Environments: {{environments}}
Reopen with: exact steps, environment details, and timestamps.
```

**External**
```
Vendor/external reference: {{external_ref}}
Resolution received: {{resolution_detail}}
```

**Cancelled / Invalid / Incomplete / Not Applicable**
```
Reason: {{reason}}
Requested by: {{requester}}
```

## 4. Approval → closure record (audit line, always append)

```
Approval audit: approver={{approver}} (user #{{approver_id}}) · decision=APPROVED ·
journal #{{approval_journal_id}} at {{approval_date}} · closed={{date}}
```

This line links the closure to the exact approval evidence the agent relied on. If the
approval came from a chat instruction instead of a journal, write:
`approval source=user instruction in agent session {{session_ref}}`.
