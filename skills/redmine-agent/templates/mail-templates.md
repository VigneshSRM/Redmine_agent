# Mail / Notification Templates

These texts are written into Redmine journal `notes`, so Redmine's own notification
engine emails every watcher (assignee, requester, approver). They double as the email
body and the permanent audit note on the ticket. A machine-readable copy used by
`scripts/redmine.py` is in `templates/templates.json`.

**Placeholders** (render all; unknown values become `_unknown_`, never silently drop):

| Token | Filled with |
|---|---|
| `{{issue_id}}` | issue number |
| `{{subject}}` | issue subject |
| `{{project}}` | project name |
| `{{tracker}}` | tracker name (Bug / Support / …) |
| `{{priority}}` | priority name |
| `{{status}}` | current status name |
| `{{assignee}}` | assignee display name |
| `{{approver}}` | approver display name |
| `{{requester}}` | issue author display name |
| `{{resolution_code}}` | resolution code (see resolution-templates.md) |
| `{{resolution_detail}}` | short resolution summary |
| `{{due_date}}` | issue due date |
| `{{sla_state}}` | OK / WARNING / BREACHED / ESCALATED |
| `{{sla_deadline}}` | applicable SLA deadline (UTC) |
| `{{sla_elapsed_pct}}` | percent of SLA window consumed |
| `{{reason}}` | rejection reason / breach cause |
| `{{url}}` | issue URL (`{REDMINE_URL}/issues/{{issue_id}}`) |
| `{{date}}` | UTC date of the event |
| `{{agent}}` | acting agent name (default `Redmine Agent`) |

---

## 1. `assignment` — ticket assigned

Subject line (first line): `[Assigned] #{{issue_id}} {{subject}}`

```
Hello {{assignee}},

you are now assigned to ticket #{{issue_id}} "{{subject}}" ({{tracker}} · {{priority}} · {{project}}).

Summary: see ticket description
Due date: {{due_date}}
Current status: {{status}}

Next steps:
  1. Review the ticket and its history.
  2. Update the status when you start (In Progress).
  3. Post progress notes on the ticket so everyone stays informed.

Ticket: {{url}}

— {{agent}}, {{date}}
```

## 2. `approval_request` — approval needed to close

Subject: `[Approval needed] #{{issue_id}} {{subject}}`

```
Hello {{approver}},

ticket #{{issue_id}} "{{subject}}" ({{project}} · {{priority}}) has been resolved and is
waiting for your approval to close.

What was done:
{{resolution_detail}}

Proposed resolution code: {{resolution_code}}

Please reply ON THIS TICKET with exactly one of:
  APPROVED            — the agent will close the ticket
  REJECTED: <reason>  — the ticket goes back to the assignee ({{assignee}})

Approval deadline: {{due_date}}

Ticket: {{url}}

— {{agent}}, {{date}}
```

## 3. `approval_granted` — decision recorded (then closure follows)

Subject: `[Approved] #{{issue_id}} {{subject}}`

```
Approval recorded for #{{issue_id}} "{{subject}}".

Approver : {{approver}}
Decision : APPROVED
Date     : {{date}}
Resolution code: {{resolution_code}}

The ticket is being closed now — see the closure notice below/next.

Ticket: {{url}}

— {{agent}}
```

## 4. `approval_rejected` — decision recorded, ticket reopened

Subject: `[Rejected] #{{issue_id}} {{subject}}`

```
Approval was DENIED for #{{issue_id}} "{{subject}}".

Approver : {{approver}}
Decision : REJECTED
Reason   : {{reason}}
Date     : {{date}}

The ticket returns to {{status}} and is back with {{assignee}} for rework.
Please address the reason above and resubmit for approval.

Ticket: {{url}}

— {{agent}}
```

## 5. `closure_notice` — ticket closed

Subject: `[Closed] #{{issue_id}} {{subject}}`

```
Ticket #{{issue_id}} "{{subject}}" ({{tracker}} · {{project}}) is now CLOSED.

Resolution : {{resolution_code}} — {{resolution_detail}}
Approved by: {{approver}}
Closed on  : {{date}}

Full resolution record is on the ticket (see the resolution note).

Thank you,
{{agent}}

Ticket: {{url}}
```

## 6. `sla_warning` — SLA window ≥75% consumed

Subject: `[SLA warning] #{{issue_id}} {{subject}} — {{sla_elapsed_pct}}% of time used`

```
SLA WARNING for #{{issue_id}} "{{subject}}" ({{priority}} · {{project}}).

Clock    : resolution SLA
Deadline : {{sla_deadline}} (UTC)
Consumed : {{sla_elapsed_pct}}%
Assignee : {{assignee}}

Please prioritise this ticket or renegotiate the deadline with the requester.

Ticket: {{url}}

— {{agent}}, {{date}}
```

## 7. `sla_breach` — deadline missed

Subject: `[SLA BREACH] #{{issue_id}} {{subject}}`

```
SLA BREACHED for #{{issue_id}} "{{subject}}" ({{priority}} · {{project}}).

Deadline : {{sla_deadline}} (UTC)
Assignee : {{assignee}}
Reason   : {{reason}}

Actions taken by the agent: SLA state set to Breached, priority raised one step,
project lead added as watcher. Immediate recovery expected — please post a plan
on the ticket within the next hour.

Ticket: {{url}}

— {{agent}}, {{date}}
```

## 8. `sla_escalation` — breach past 2× target

Subject: `[SLA ESCALATION] #{{issue_id}} {{subject}} — management attention required`

```
ESCALATION for #{{issue_id}} "{{subject}}" ({{priority}} · {{project}}).

The resolution SLA has been breached for more than twice its target:
Deadline was {{sla_deadline}} (UTC), currently {{sla_elapsed_pct}}%+ consumed.

Assignee : {{assignee}}
Reason   : {{reason}}

@project-lead please assign recovery ownership and update the ticket today.

Ticket: {{url}}

— {{agent}}, {{date}}
```

## 9. `daily_digest` — rolled-up summary

Subject: `[Digest] {{project}} ticket health — {{date}}`

```
Daily ticket digest — {{project}} ({{date}})

Open tickets        : {{open_total}}
New since yesterday : {{new_total}}
Due today           : {{due_today}}
SLA warning         : {{sla_warning_total}}
SLA breached        : {{sla_breach_total}}

Needs attention:
{{attention_list}}

Full SLA report is attached to the tracking ticket / news item.

— {{agent}}
```

## 10. `reassignment` — ticket moved between assignees

Subject: `[Reassigned] #{{issue_id}} {{subject}}`

```
Ticket #{{issue_id}} "{{subject}}" has been reassigned.

Previous assignee : {{previous_assignee}}
New assignee      : {{assignee}}
Reason            : {{reason}}

Ticket: {{url}}

— {{agent}}, {{date}}
```
