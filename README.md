# Redmine_agent

Agentic AI skill that turns an agent harness into a **Redmine service-desk operator**.
Built on the official [Redmine REST API](https://www.redmine.org/projects/redmine/wiki/Rest_api)
(JSON, Redmine 4.x–7.x).

## What the Redmine Agent does

| Capability | Description |
|---|---|
| **List / search tickets** | Filter by project, tracker, status, assignee, dates, custom fields; free-text search; triage tables |
| **Assign tickets** | Resolve user → verify project membership → assign + post a templated notification note (Redmine mails watchers) |
| **Approval-based closure** | Resolved tickets move to *Pending Approval*; the approver replies `APPROVED` / `REJECTED` on the ticket; only then does the agent close — with a **mail template** (closure notice) and a **resolution template** (code + Problem/Root cause/Resolution/Verification record) |
| **SLA monitoring** | Response & resolution clocks per priority (1h/8h Critical … 24h/7d Low), `OK → WARNING → BREACHED → ESCALATED` state machine, idempotent warning/breach notes, priority raise, lead escalation, daily digest |
| **Everything else** | Notes/journals, watchers, create/update tickets, time logging, attachments, relations, custom fields, users |

## Repository layout

```
skills/redmine-agent/
├── SKILL.md                      # the agent skill file (start here)
├── references/
│   ├── api-reference.md          # REST endpoint cheat sheet (auth, filters, payloads)
│   └── workflows.md              # step-by-step assign / approve-close / SLA procedures
├── templates/
│   ├── mail-templates.md         # assignment, approval, closure, SLA, digest mails
│   ├── resolution-templates.md   # resolution codes + closure note skeletons
│   └── templates.json            # machine-readable copy used by the CLI
└── scripts/
    └── redmine.py                # stdlib-only CLI implementing all workflows
```

## Install into an agent harness

Agent Skills are folders with a `SKILL.md` (YAML front-matter + instructions). Copy or
symlink `skills/redmine-agent/` into your harness's skill directory, e.g.:

```bash
# Claude Code / compatible harnesses
cp -r skills/redmine-agent ~/.claude/skills/redmine-agent

# or reference it in place
ln -s "$PWD/skills/redmine-agent" ~/.agents/skills/redmine-agent
```

The harness discovers the skill from the `name`/`description` front-matter and loads the
reference files on demand.

## Configure

```bash
export REDMINE_URL=https://redmine.example.com   # no trailing slash
export REDMINE_API_KEY=<key from /my/account>    # Administration → Settings → API must be enabled
```

Never hardcode keys. An optional `REDMINE_ADMIN_KEY` supports impersonation
(`X-Redmine-Switch-User`) for admin accounts only.

## CLI quick start

```bash
CLI=skills/redmine-agent/scripts/redmine.py

python3 $CLI list --project web --status open --assignee me
python3 $CLI show 1234 --include journals,allowed_statuses

python3 $CLI assign 1234 jsmith
python3 $CLI request-approval 1234 --approver lead1 --resolution-code Fixed --detail "Cache fix deployed"
python3 $CLI approve  1234 --resolution Fixed --approver lead1 --detail "..." --root-cause "..."   # verifies approver's APPROVED reply
python3 $CLI reject   1234 --reason "Missing QA sign-off" --approver lead1
python3 $CLI close    1234 --resolution "Works as Designed" --skip-gate     # attested approval (user instruction)

python3 $CLI sla-report --project web --notify        # scan + post warnings/breaches (idempotent)
python3 $CLI digest --project web                     # daily health digest
python3 $CLI render closure_notice --set issue_id=1 ... 
```

Exit codes: `0` ok · `1` API error · `2` usage · `3` approval-gate violation
(the agent refuses to close tickets without approval evidence — journal reply by the
approver, or an explicit user attestation via `--attest`/`--skip-gate`).

## Safety model

- Reads are always safe; closes, rejects, bulk changes and escalations require user
  confirmation or explicit approval evidence.
- Every mutation carries a human-readable journal note (audit trail).
- SLA/notifications are idempotent — one warning per issue per day.
- Approval evidence is verified by user id and decision token; self-approval is rejected.

Docs: [Redmine REST API](https://www.redmine.org/projects/redmine/wiki/Rest_api) ·
[Issues](https://www.redmine.org/projects/redmine/wiki/Rest_Issues) ·
[Users](https://www.redmine.org/projects/redmine/wiki/Rest_Users)
