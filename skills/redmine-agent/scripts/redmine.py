#!/usr/bin/env python3
"""Redmine Agent CLI — helper for the redmine-agent skill.

Drives a Redmine instance over the REST API (JSON) using only the Python stdlib.

Environment:
    REDMINE_URL       base URL, e.g. https://redmine.example.com  (required)
    REDMINE_API_KEY   API key from /my/account                    (required)

Commands:
    list              search/list tickets
    show              show one ticket (optionally journals/allowed_statuses)
    assign            assign a ticket to a user (posts notification note)
    note              post a comment/journal note
    request-approval  move ticket to Pending Approval + notify approver
    approve           verify approval evidence, then close with resolution
    reject            verify rejection evidence, reopen with reason
    close             close with resolution (approval gate enforced)
    sla-report        SLA scan: OK / WARNING / BREACHED / ESCALATED
    digest            daily ticket digest
    render            render a mail/resolution template
    templates         list available templates / resolution codes

Exit codes: 0 ok · 1 API error · 2 usage error · 3 approval-gate violation
Docs: https://www.redmine.org/projects/redmine/wiki/Rest_api
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ----------------------------------------------------------------------------- config

AGENT_NAME = os.environ.get("REDMINE_AGENT_NAME", "Redmine Agent")
TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "templates" / "templates.json"

APPROVE_TOKENS = ("APPROVED", "APPROVE", "LGTM", "OK TO CLOSE", "OK-TO-CLOSE")
REJECT_TOKENS = ("REJECTED", "REJECT", "NOT APPROVED", "DENIED")
PENDING_APPROVAL_NAMES = ("pending approval", "awaiting approval", "approval pending", "approval")
RESOLUTION_FIELD_NAMES = ("resolution",)
APPROVAL_FIELD_NAMES = ("approval state", "approval_state", "approval")
APPROVER_FIELD_NAMES = ("approver",)
SLA_STATE_FIELD_NAMES = ("sla state", "sla_state")
SLA_RESP_DUE_NAMES = ("response due", "response_due")
SLA_RES_DUE_NAMES = ("resolution due", "resolution_due", "sla due", "sla_due")

PRIORITY_RAISE_ORDER = ["Low", "Normal", "High", "Critical", "Urgent"]


class ApiError(Exception):
    def __init__(self, status, messages):
        super().__init__("; ".join(messages) if messages else f"HTTP {status}")
        self.status = status
        self.messages = messages


class GateViolation(Exception):
    pass


# ----------------------------------------------------------------------------- helpers

def load_templates() -> dict:
    if not TEMPLATES_PATH.exists():
        die(f"templates file not found: {TEMPLATES_PATH}")
    with open(TEMPLATES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def render_string(text: str, ctx: dict) -> str:
    out = []
    i = 0
    while True:
        start = text.find("{{", i)
        if start < 0:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = text.find("}}", start)
        if end < 0:
            out.append(text[start:])
            break
        key = text[start + 2:end].strip()
        val = ctx.get(key)
        out.append("_unknown_" if val in (None, "") else str(val))
        i = end + 2
    return "".join(out)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_redmine_time(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "-"


def display_name(user) -> str:
    """Display name from a user object — /users.json has no 'name' field."""
    if not user:
        return "_unknown_"
    return (
        user.get("name")
        or " ".join(filter(None, [user.get("firstname"), user.get("lastname")])).strip()
        or user.get("login")
        or str(user.get("id"))
    )


def humanize_hours(hours: float) -> str:
    if hours < 0:
        return f"-{humanize_hours(-hours)}"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg):
    print(msg)


# ----------------------------------------------------------------------------- client

class Redmine:
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url.rstrip("/")
        self.key = api_key

    def _request(self, method: str, path: str, params=None, payload=None):
        url = f"{self.base}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        data = None
        headers = {
            "X-Redmine-API-Key": self.key,
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                if not body:
                    return {}
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read().decode("utf-8"))
            except Exception:
                parsed = {}
            messages = parsed.get("errors") or [parsed.get("error", f"HTTP {exc.code}")]
            raise ApiError(exc.code, [str(m) for m in messages]) from exc
        except urllib.error.URLError as exc:
            raise ApiError(0, [f"network error: {exc.reason}"]) from exc

    def get(self, path, **params):
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=clean or None)

    def post(self, path, payload):
        return self._request("POST", path, payload=payload)

    def put(self, path, payload):
        return self._request("PUT", path, payload=payload)

    # -- convenience ------------------------------------------------------------

    def current_user(self):
        return self.get("/users/current.json")["user"]

    def issue(self, issue_id, includes=()):
        params = {"include": ",".join(includes)} if includes else {}
        return self.get(f"/issues/{issue_id}.json", **params)["issue"]

    def update_issue(self, issue_id, **fields):
        return self.put(f"/issues/{issue_id}.json", {"issue": fields})

    def statuses(self):
        return self.get("/issue_statuses.json")["issue_statuses"]

    def find_user(self, name_or_login):
        """Resolve a login/name to an active user dict; exact login match wins."""
        data = self.get("/users.json", status="1", name=name_or_login, limit=100)
        users = data.get("users", [])
        exact = [u for u in users if u.get("login", "").lower() == name_or_login.lower()]
        if len(exact) == 1:
            return exact[0]
        if len(users) == 1:
            return users[0]
        return None, users  # ambiguous / not found

    def find_status(self, statuses, names, require_closed=None):
        for name in names:
            for st in statuses:
                if st["name"].lower() == name and (
                    require_closed is None or bool(st.get("is_closed")) == require_closed
                ):
                    return st
        return None

    def closed_status(self, statuses):
        st = self.find_status(statuses, ("closed",), require_closed=True)
        if st:
            return st
        for st in statuses:
            if st.get("is_closed"):
                return st
        return None

    def custom_field_id(self, issue, names):
        for cf in issue.get("custom_fields", []):
            if cf.get("name", "").lower() in names:
                return cf["id"]
        return None

    def custom_field_value(self, issue, names):
        for cf in issue.get("custom_fields", []):
            if cf.get("name", "").lower() in names:
                return cf.get("value")
        return None

    def set_custom_fields(self, fields, updates: dict):
        """Merge {id: value} updates into an issue's custom_fields array."""
        merged = {cf["id"]: cf.get("value") for cf in fields}
        merged.update(updates)
        return [{"id": k, "value": v} for k, v in merged.items()]

    def project_lead(self, project_id):
        proj = self.get(f"/projects/{project_id}.json").get("project", {})
        return proj.get("lead") or {}


# ----------------------------------------------------------------------------- context

def build_ctx(client: Redmine, issue: dict, extra: dict) -> dict:
    assignee = issue.get("assigned_to") or {}
    author = issue.get("author") or {}
    project = issue.get("project") or {}
    ctx = {
        "issue_id": issue.get("id"),
        "subject": issue.get("subject"),
        "project": project.get("name"),
        "tracker": (issue.get("tracker") or {}).get("name"),
        "priority": (issue.get("priority") or {}).get("name"),
        "status": (issue.get("status") or {}).get("name"),
        "assignee": assignee.get("name"),
        "requester": author.get("name"),
        "due_date": issue.get("due_date") or "_none_",
        "url": f"{client.base}/issues/{issue.get('id')}",
        "date": utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "agent": AGENT_NAME,
    }
    ctx.update({k: v for k, v in extra.items() if v is not None})
    return ctx


def post_note(client: Redmine, issue_id, body: str, private=False, **extra):
    fields = {"notes": body}
    if private:
        fields["private_notes"] = True
    fields.update(extra)
    return client.update_issue(issue_id, **fields)


def render_template(tmpl_store: dict, name: str, ctx: dict):
    entry = tmpl_store["mail"].get(name)
    if not entry:
        die(f"unknown template '{name}'. available: {', '.join(tmpl_store['mail'])}")
    subject = render_string(entry["subject"], ctx)
    body = render_string(entry["body"], ctx)
    return subject, body


# ----------------------------------------------------------------------------- approval gate

def find_decision(issue_with_journals: dict, approver_uid: int, want: str):
    """Return the latest decision journal by approver_uid, or None.

    want: 'approve' or 'reject'. Without an approver_uid, decisions authored by the
    issue's own author or assignee are ignored (no self-approval).
    """
    tokens = APPROVE_TOKENS if want == "approve" else REJECT_TOKENS
    excluded = set()
    if not approver_uid:
        excluded = {
            (issue_with_journals.get("author") or {}).get("id"),
            (issue_with_journals.get("assigned_to") or {}).get("id"),
        }
    found = None
    for journal in issue_with_journals.get("journals", []):
        user = journal.get("user") or {}
        if approver_uid:
            if user.get("id") != approver_uid:
                continue
        elif user.get("id") in excluded:
            continue
        notes = (journal.get("notes") or "").upper()
        if any(tok in notes for tok in tokens):
            found = journal  # keep scanning: latest wins
    return found


def approval_gate(client: Redmine, issue: dict, approver_uid: int, want: str, attest: bool):
    """Return (evidence_dict, audit_line_parts). Raise GateViolation when blocked."""
    full = client.issue(issue["id"], includes=["journals"])
    journal = find_decision(full, approver_uid, want)
    if journal:
        return {
            "source": "journal",
            "journal_id": journal["id"],
            "date": journal.get("created_on"),
        }
    if attest:
        return {"source": "user instruction in agent session", "journal_id": None, "date": None}
    raise GateViolation(
        f"no '{want}' evidence found on #{issue['id']} from approver user #{approver_uid}. "
        f"Ask the approver to reply on the ticket, or re-run with --attest to record a "
        f"user-instruction approval source."
    )


# ----------------------------------------------------------------------------- SLA

def sla_policy_for(tmpl_store: dict, priority_name: str) -> dict:
    policy = tmpl_store.get("sla_policy", {})
    return policy.get(priority_name) or policy.get("Normal") or {"response_hours": 8, "resolution_hours": 72}


def sla_clocks(client: Redmine, tmpl_store: dict, issue: dict):
    """Compute response/resolution deadlines and states. Returns list of clock dicts."""
    created = parse_redmine_time(issue.get("created_on")) or utcnow()
    now = utcnow()
    closed = (issue.get("status") or {}).get("is_closed")
    policy = sla_policy_for(tmpl_store, (issue.get("priority") or {}).get("name") or "Normal")
    # custom field values first, then policy
    resp_due = parse_redmine_time(client.custom_field_value(issue, SLA_RESP_DUE_NAMES))
    res_due = parse_redmine_time(client.custom_field_value(issue, SLA_RES_DUE_NAMES))
    if resp_due is None:
        resp_due = created + timedelta(hours=policy["response_hours"])
    if res_due is None:
        res_due = created + timedelta(hours=policy["resolution_hours"])

    journals = issue.get("journals") or []
    author_id = (issue.get("author") or {}).get("id")
    closed_on = parse_redmine_time(issue.get("closed_on") or issue.get("updated_on")) if closed else None
    first_response = None
    for j in journals:
        if (j.get("user") or {}).get("id") != author_id and (j.get("notes") or "").strip():
            first_response = parse_redmine_time(j.get("created_on"))
            break
    if first_response is None and closed_on is not None:
        first_response = closed_on  # closed without a recorded response: stop the clock

    def classify(deadline, done_at):
        ref = done_at or now
        total = (deadline - created).total_seconds()
        elapsed = (ref - created).total_seconds()
        pct = (elapsed / total * 100.0) if total > 0 else 100.0
        if done_at is not None:
            state = "CLOSED-OK" if done_at <= deadline else "CLOSED-LATE"
        elif now >= deadline:
            over = (now - deadline).total_seconds() / 3600.0
            state = "ESCALATED" if total > 0 and (now - created).total_seconds() > 2 * total else "BREACHED"
            if state == "BREACHED" and over > 0:
                pass
        elif pct >= 75.0:
            state = "WARNING"
        else:
            state = "OK"
        return {
            "deadline": deadline,
            "elapsed_pct": round(pct, 1),
            "remaining_h": (deadline - now).total_seconds() / 3600.0,
            "state": state,
        }

    # response clock stops at the first responder note; classify marks it CLOSED-OK/CLOSED-LATE
    resp = classify(resp_due, first_response)
    res = classify(res_due, closed_on)
    return [
        {"clock": "first-response", **resp},
        {"clock": "resolution", **res},
    ]


# ----------------------------------------------------------------------------- commands

def resolve_project(client: Redmine, value):
    """Return (project_id, project_name) for an id or identifier."""
    if not value:
        return None, "all projects"
    if str(value).isdigit():
        proj = client.get(f"/projects/{value}.json").get("project", {})
        return proj.get("id", value), proj.get("name", str(value))
    proj = client.get(f"/projects/{value}.json").get("project", {})
    return proj["id"], proj.get("name", value)


def cmd_list(client, args):
    params = {}
    if args.project:
        pid, _ = resolve_project(client, args.project)
        params["project_id"] = pid
    if args.status:
        params["status_id"] = args.status
    if args.assignee:
        if args.assignee == "me":
            params["assigned_to_id"] = "me"
        elif str(args.assignee).isdigit():
            params["assigned_to_id"] = args.assignee
        else:
            found = client.find_user(args.assignee)
            user = found[0] if isinstance(found, tuple) else found
            if not user:
                if isinstance(found, tuple) and found[1]:
                    die("ambiguous assignee, candidates: " + ", ".join(f"{u['login']}({u['id']})" for u in found[1]))
                die(f"no active user matching '{args.assignee}'")
            params["assigned_to_id"] = user["id"]
    if args.tracker:
        params["tracker_id"] = args.tracker
    if args.subject:
        params["subject"] = args.subject
    params["sort"] = args.sort
    params["limit"] = args.limit

    rows = []
    offset = 0
    while True:
        data = client.get("/issues.json", offset=offset, **params)
        issues = data.get("issues", [])
        rows.extend(issues)
        total = data.get("total_count", len(rows))
        offset += len(issues)
        if not issues or offset >= total or offset >= args.limit:
            break

    if not rows:
        info("no tickets matched.")
        return
    header = f"{'ID':>6} | {'Tracker':<10} | {'Status':<14} | {'Priority':<8} | {'Assignee':<16} | {'Updated':<17} | Subject"
    info(header)
    info("-" * len(header))
    for it in rows:
        info(
            f"{it['id']:>6} | {(it.get('tracker') or {}).get('name',''):<10} | "
            f"{(it.get('status') or {}).get('name',''):<14} | {(it.get('priority') or {}).get('name',''):<8} | "
            f"{(it.get('assigned_to') or {}).get('name','-'):<16} | "
            f"{(it.get('updated_on') or '')[:16]:<17} | {it.get('subject','')[:60]}"
        )
    info(f"\n{len(rows)} ticket(s)" + (f" of {data.get('total_count', '?')}" if data.get("total_count") else ""))


def cmd_show(client, args):
    includes = [s.strip() for s in args.include.split(",") if s.strip()] if args.include else []
    issue = client.issue(args.id, includes=includes)
    assignee = issue.get("assigned_to") or {}
    info(f"#{issue['id']} {issue.get('subject')}")
    info(f"  project   : {(issue.get('project') or {}).get('name')}")
    info(f"  tracker   : {(issue.get('tracker') or {}).get('name')}   status: {(issue.get('status') or {}).get('name')}"
         f"   priority: {(issue.get('priority') or {}).get('name')}")
    info(f"  author    : {(issue.get('author') or {}).get('name')}   assignee: {assignee.get('name', '-')}")
    info(f"  dates     : created {issue.get('created_on')}  due {issue.get('due_date') or '-'}  updated {issue.get('updated_on')}")
    info(f"  progress  : {issue.get('done_ratio')}%   spent {issue.get('spent_hours', 0)}h / est {issue.get('estimated_hours') or '-'}h")
    if issue.get("custom_fields"):
        cf_str = ", ".join(f"{cf['name']}={cf.get('value')}" for cf in issue["custom_fields"] if cf.get("value"))
        if cf_str:
            info(f"  custom    : {cf_str}")
    if "allowed_statuses" in includes and issue.get("allowed_statuses"):
        info("  next statuses: " + ", ".join(f"{s['name']}({s['id']})" for s in issue["allowed_statuses"]))
    if "journals" in includes:
        info("\n  --- journals ---")
        for j in issue.get("journals", []):
            user = (j.get("user") or {}).get("name", "?")
            note = (j.get("notes") or "").strip()
            info(f"  [{j.get('created_on')}] {user}: {note[:300] if note else '(field changes only)'}")
    if "watchers" in includes and issue.get("watchers"):
        info("  watchers  : " + ", ".join(w.get("name", str(w.get("id"))) for w in issue["watchers"]))


def cmd_assign(client, args):
    found = client.find_user(args.login)
    user = found[0] if isinstance(found, tuple) else found
    if not user:
        if isinstance(found, tuple) and found[1]:
            die("ambiguous user, candidates: " + ", ".join(f"{u['login']}({u['id']})" for u in found[1]))
        die(f"no active user matching '{args.login}'")
    issue = client.issue(args.id)
    tmpl = load_templates()
    ctx = build_ctx(client, issue, {"assignee": display_name(user), "reason": args.reason})
    subject, body = render_template(tmpl, "assignment", ctx)
    note = args.note or body
    client.update_issue(args.id, assigned_to_id=user["id"], notes=note)
    after = client.issue(args.id)
    prev = display_name(issue.get("assigned_to")) if issue.get("assigned_to") else "(unassigned)"
    new = display_name(after.get("assigned_to")) if after.get("assigned_to") else "?"
    info(f"✔ #{args.id} assigned: {prev} → {new}  (user id {user['id']})")
    info(f"  notification note posted (Redmine mails watchers)")
    info(f"  subject: {subject}")


def cmd_note(client, args):
    body = args.text if args.text != "-" else sys.stdin.read()
    post_note(client, args.id, body, private=args.private)
    info(f"✔ note posted on #{args.id}" + (" (private)" if args.private else ""))


def cmd_request_approval(client, args):
    tmpl = load_templates()
    statuses = client.statuses()
    pending = client.find_status(statuses, PENDING_APPROVAL_NAMES)
    found = client.find_user(args.approver)
    approver = found[0] if isinstance(found, tuple) else found
    if not approver:
        die(f"no active approver matching '{args.approver}'")
    issue = client.issue(args.id, includes=["allowed_statuses"])
    allowed = {s["id"] for s in issue.get("allowed_statuses", [])}

    ctx = build_ctx(client, issue, {
        "approver": display_name(approver),
        "resolution_detail": args.detail or "_not recorded_",
        "resolution_code": args.resolution_code or "_undecided_",
    })
    subject, body = render_template(tmpl, "approval_request", ctx)

    # watcher first (idempotent: 422 'already watched' tolerated)
    try:
        client.post(f"/issues/{args.id}/watchers.json", {"user_id": approver["id"]})
        info(f"✔ approver {display_name(approver)} (id {approver['id']}) added as watcher")
    except ApiError as exc:
        if exc.status == 422:
            info(f"· approver already a watcher (or not addable: {'; '.join(exc.messages)})")
        else:
            raise

    fields = {"notes": body}
    if pending and (not allowed or pending["id"] in allowed):
        fields["status_id"] = pending["id"]
        info(f"✔ status → {pending['name']} (id {pending['id']})")
    elif pending:
        info(f"! 'Pending Approval' (id {pending['id']}) not in allowed_statuses for this workflow; "
             f"status unchanged. Note posted requesting approval anyway.")
    else:
        info("! this instance has no 'Pending Approval'-like status; status unchanged. Note posted.")
    client.update_issue(args.id, **fields)
    info(f"✔ approval request note posted on #{args.id}")
    info(f"  subject: {subject}")
    info(f"  decision tokens the approver may reply with: APPROVED | REJECTED: <reason>")


def _close_with_resolution(client, tmpl, issue, args, evidence, approver):
    statuses = client.statuses()
    closed = client.closed_status(statuses)
    if not closed:
        die("no closed status (is_closed=true) found in this instance", 1)
    code = args.resolution
    if code not in tmpl["resolution_codes"]:
        info(f"! '{code}' is not a standard code; standard: {', '.join(tmpl['resolution_codes'])}")
    ctx = build_ctx(client, issue, {
        "approver": display_name(approver) if approver else (getattr(args, "approver", None) or "_unknown_"),
        "approver_id": approver.get("id") if approver else "_unknown_",
        "resolution_code": code,
        "resolution_detail": args.detail or tmpl["resolution_codes"].get(code, code),
        "problem_statement": args.problem or "_Not recorded_",
        "root_cause": args.root_cause or "_Not recorded_",
        "resolution_steps": args.detail or tmpl["resolution_codes"].get(code, code),
        "verification": args.verification or "_Pending verification — closed on approval decision_",
        "prevention": args.prevention or "_None_",
        "approval_journal_id": evidence.get("journal_id") or "n/a",
        "approval_date": evidence.get("date") or "n/a",
        "session_ref": evidence.get("source"),
    })
    addendum = tmpl["resolution_addenda"].get(code, "")
    if addendum:
        ctx.setdefault("reason", args.reason or "_not recorded_")
        ctx.setdefault("duplicate_of", getattr(args, "duplicate_of", None) or "_unknown_")
        ctx.setdefault("external_ref", getattr(args, "external_ref", None) or "_unknown_")
        ctx.setdefault("change_ref", getattr(args, "change_ref", None) or "_unknown_")
        ctx["resolution_steps"] += "\n\n" + render_string(addendum, ctx)
    if evidence.get("source", "").startswith("user instruction"):
        ctx["resolution_steps"] += (
            f"\n\nApproval audit: source={evidence['source']} · closed={ctx['date']}"
        )
    _, res_note = render_string(tmpl["resolution_note"]["subject"], ctx), render_string(tmpl["resolution_note"]["body"], ctx)
    _, closure = render_template(tmpl, "closure_notice", ctx)
    _, granted = render_template(tmpl, "approval_granted", ctx)

    cf_updates = {}
    res_cf = client.custom_field_id(issue, RESOLUTION_FIELD_NAMES)
    if res_cf:
        cf_updates[res_cf] = code
    state_cf = client.custom_field_id(issue, APPROVAL_FIELD_NAMES)
    if state_cf:
        cf_updates[state_cf] = "Approved"

    fields = {
        "status_id": closed["id"],
        "done_ratio": 100,
        "notes": granted + "\n\n" + closure + "\n\n" + res_note,
    }
    if cf_updates:
        fields["custom_fields"] = client.set_custom_fields(issue.get("custom_fields", []), cf_updates)
    client.update_issue(args.id, **fields)
    after = client.issue(args.id)
    ok = (after.get("status") or {}).get("is_closed")
    info(f"✔ #{args.id} closed as '{code}' → status {after['status']['name']} (closed={bool(ok)})")
    info("  notes posted: approval record + closure notice + resolution record")
    if res_cf:
        info(f"  custom field 'Resolution' = {code}")
    else:
        info("  ! no 'Resolution' custom field found on this issue — code kept in the note only")


def cmd_approve(client, args):
    tmpl = load_templates()
    issue = client.issue(args.id)
    approver = None
    if args.approver:
        found = client.find_user(args.approver)
        approver = found[0] if isinstance(found, tuple) else found
        if not approver:
            die(f"no active approver matching '{args.approver}'")
    else:
        approver = None  # derived below from the approval journal or project lead
    evidence = approval_gate(client, issue, approver["id"] if approver else 0, "approve", args.attest)
    if not approver:
        # resolve approver from journals (whoever approved) or project lead
        full = client.issue(args.id, includes=["journals"])
        j = find_decision(full, 0, "approve")
        approver = (j.get("user") if j else None) or client.project_lead(issue["project"]["id"]) or {}
    info(f"✔ approval evidence: {evidence['source']}"
         + (f" (journal #{evidence['journal_id']}, {evidence['date']})" if evidence.get("journal_id") else ""))
    _close_with_resolution(client, tmpl, issue, args, evidence, approver)


def cmd_reject(client, args):
    tmpl = load_templates()
    issue = client.issue(args.id)
    approver = None
    if args.approver:
        found = client.find_user(args.approver)
        approver = found[0] if isinstance(found, tuple) else found
        if not approver:
            die(f"no active approver matching '{args.approver}'")
    evidence = approval_gate(client, issue, approver["id"] if approver else 0, "reject", args.attest)
    if not approver:
        full = client.issue(args.id, includes=["journals"])
        j = find_decision(full, 0, "reject")
        approver = (j.get("user") if j else None) or {}

    statuses = client.statuses()
    in_progress = client.find_status(statuses, ("in progress", "assigned"))
    ctx = build_ctx(client, issue, {
        "approver": display_name(approver) if approver else "_unknown_",
        "reason": args.reason,
    })
    subject, body = render_template(tmpl, "approval_rejected", ctx)
    fields = {"notes": body}
    if in_progress:
        fields["status_id"] = in_progress["id"]
    state_cf = client.custom_field_id(issue, APPROVAL_FIELD_NAMES)
    if state_cf:
        fields["custom_fields"] = client.set_custom_fields(issue.get("custom_fields", []), {state_cf: "Rejected"})
    client.update_issue(args.id, **fields)
    info(f"✔ #{args.id} approval REJECTED — ticket reopened"
         + (f" → {in_progress['name']}" if in_progress else " (status unchanged: no 'In Progress' status found)"))
    info(f"  subject: {subject}")


def cmd_close(client, args):
    if args.skip_gate:
        args.attest = True
    cmd_approve(client, args)


def cmd_sla_report(client, args):
    tmpl = load_templates()
    params = {"status_id": "open", "limit": 100, "sort": "priority:desc,updated_on:asc"}
    if args.project:
        pid, _ = resolve_project(client, args.project)
        params["project_id"] = pid
    rows, offset = [], 0
    while True:
        data = client.get("/issues.json", offset=offset, **params)
        batch = data.get("issues", [])
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= data.get("total_count", 0):
            break

    now = utcnow()
    counts = {"OK": 0, "WARNING": 0, "BREACHED": 0, "ESCALATED": 0}
    report, notifications = [], []
    for it in rows:
        full = client.issue(it["id"], includes=["journals"]) if not args.fast else it
        for clock in sla_clocks(client, tmpl, full):
            state = clock["state"]
            if state in counts:
                counts[state] += 1
            if state in ("WARNING", "BREACHED", "ESCALATED"):
                report.append((it, clock))
                if args.notify and not args.dry_run:
                    notifications.append((it, clock, full))

    info(f"SLA scan — {len(rows)} open ticket(s) — {fmt_dt(now)}")
    info(f"  OK: {counts['OK']} clock(s) · WARNING: {counts['WARNING']} · BREACHED: {counts['BREACHED']} · ESCALATED: {counts['ESCALATED']}")
    if not report:
        info("\n✔ no SLA warnings or breaches.")
        return
    header = f"\n{'ID':>6} | {'Clock':<14} | {'Priority':<8} | {'Deadline (UTC)':<18} | {'Elapsed':>8} | {'State':<10} | Subject"
    info(header)
    info("-" * len(header))
    for it, clock in report:
        info(
            f"{it['id']:>6} | {clock['clock']:<14} | {(it.get('priority') or {}).get('name',''):<8} | "
            f"{fmt_dt(clock['deadline']):<18} | {clock['elapsed_pct']:>7.1f}% | {clock['state']:<10} | {it.get('subject','')[:40]}"
        )

    applied = 0
    for it, clock, full in notifications:
        if _apply_sla_action(client, tmpl, it, clock, full, escalate=(clock["state"] == "ESCALATED")):
            applied += 1
    if args.notify:
        info(f"\n✔ SLA notifications applied for {applied} clock(s) "
             f"({len(notifications) - applied} skipped as already posted)"
             + (" (dry-run: none sent)" if args.dry_run else ""))
    else:
        info("\nrun with --notify to post warning/breach notes and apply escalation actions.")


def _apply_sla_action(client, tmpl, issue, clock, full, escalate=False) -> bool:
    name = "sla_escalation" if escalate else ("sla_breach" if clock["state"] in ("BREACHED", "ESCALATED") else "sla_warning")
    stamp = utcnow().strftime("%Y-%m-%d")
    for j in full.get("journals", []):
        notes = j.get("notes") or ""
        if name.split("_")[1].upper() in notes.upper() and stamp in notes:
            info(f"  · #{issue['id']} {name}: already posted today — skipped (idempotent)")
            return False
    ctx = build_ctx(client, full, {
        "sla_state": clock["state"],
        "sla_deadline": fmt_dt(clock["deadline"]),
        "sla_elapsed_pct": clock["elapsed_pct"],
        "reason": f"{clock['clock']} SLA {'escalated' if escalate else clock['state'].lower()}",
    })
    subject, body = render_template(tmpl, name, ctx)

    fields = {"notes": body}
    if clock["state"] in ("BREACHED", "ESCALATED"):
        state_cf = client.custom_field_id(full, SLA_STATE_FIELD_NAMES)
        if state_cf:
            fields["custom_fields"] = client.set_custom_fields(full.get("custom_fields", []), {state_cf: "Breached"})
        # raise priority one step
        prio = (full.get("priority") or {}).get("name")
        if prio in PRIORITY_RAISE_ORDER and prio != "Urgent":
            nxt = PRIORITY_RAISE_ORDER[min(PRIORITY_RAISE_ORDER.index(prio) + 1, len(PRIORITY_RAISE_ORDER) - 1)]
            for p in client.get("/enumerations/issue_priorities.json").get("issue_priorities", []):
                if p["name"] == nxt:
                    fields["priority_id"] = p["id"]
                    info(f"  · #{issue['id']} priority {prio} → {nxt}")
                    break
    client.update_issue(issue["id"], **fields)
    info(f"  ✔ #{issue['id']} posted {name} note — {subject}")
    return True


def cmd_digest(client, args):
    tmpl = load_templates()
    params = {"status_id": "open", "limit": 100}
    pid, project_label = resolve_project(client, args.project) if args.project else (None, "all projects")
    if pid:
        params["project_id"] = pid
    rows, offset = [], 0
    while True:
        data = client.get("/issues.json", offset=offset, **params)
        batch = data.get("issues", [])
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= data.get("total_count", 0):
            break
    today = utcnow().date()
    new_total = sum(1 for it in rows if (parse_redmine_time(it.get("created_on")) or utcnow()).date() == today)
    due_today = sum(1 for it in rows if it.get("due_date") == today.isoformat())
    warn = breach = 0
    attention = []
    for it in rows:
        for clock in sla_clocks(client, tmpl, client.issue(it["id"])):
            if clock["state"] == "WARNING":
                warn += 1
                attention.append(f"  ⚠ #{it['id']} {it['subject'][:50]} (SLA {clock['clock']} warning)")
            elif clock["state"] in ("BREACHED", "ESCALATED"):
                breach += 1
                attention.append(f"  ✖ #{it['id']} {it['subject'][:50]} (SLA breached)")
    ctx = {
        "project": project_label,
        "date": utcnow().strftime("%Y-%m-%d"),
        "open_total": len(rows),
        "new_total": new_total,
        "due_today": due_today,
        "sla_warning_total": warn,
        "sla_breach_total": breach,
        "attention_list": "\n".join(attention) or "  — none —",
        "agent": AGENT_NAME,
    }
    subject, body = render_template(tmpl, "daily_digest", ctx)
    info(subject)
    info("")
    info(body)


def cmd_render(client, args):
    tmpl = load_templates()
    ctx = dict(kv.split("=", 1) for kv in args.set)
    ctx.setdefault("agent", AGENT_NAME)
    ctx.setdefault("date", utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    if args.template in tmpl["mail"]:
        subject, body = render_template(tmpl, args.template, ctx)
        info(f"Subject: {subject}\n")
        info(body)
    elif args.template == "resolution_note":
        info(render_string(tmpl["resolution_note"]["body"], ctx))
    else:
        die(f"unknown template '{args.template}'. mail: {', '.join(tmpl['mail'])}; plus resolution_note")


def cmd_templates(client, args):
    tmpl = load_templates()
    info("mail templates: " + ", ".join(tmpl["mail"]))
    info("resolution codes:")
    for code, desc in tmpl["resolution_codes"].items():
        info(f"  {code:<20} {desc}")


# ----------------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="redmine.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="list/search tickets")
    sp.add_argument("--project", help="project id or identifier")
    sp.add_argument("--status", default="open", help="open | closed | * | <status id>")
    sp.add_argument("--assignee", help="login | me | user id")
    sp.add_argument("--tracker", help="tracker id")
    sp.add_argument("--subject", help="subject substring filter")
    sp.add_argument("--sort", default="updated_on:desc")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="show one ticket")
    sp.add_argument("id", type=int)
    sp.add_argument("--include", help="comma list: journals,allowed_statuses,watchers,children,relations,attachments")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("assign", help="assign ticket and post notification note")
    sp.add_argument("id", type=int)
    sp.add_argument("login", help="assignee login or name")
    sp.add_argument("--note", help="override the assignment note body")
    sp.add_argument("--reason", help="reassignment reason (for template)")
    sp.set_defaults(func=cmd_assign)

    sp = sub.add_parser("note", help="post a note/comment")
    sp.add_argument("id", type=int)
    sp.add_argument("text", help="note text, or '-' to read stdin")
    sp.add_argument("--private", action="store_true")
    sp.set_defaults(func=cmd_note)

    sp = sub.add_parser("request-approval", help="submit ticket for user approval")
    sp.add_argument("id", type=int)
    sp.add_argument("--approver", required=True, help="approver login or name")
    sp.add_argument("--resolution-code", help="proposed resolution code")
    sp.add_argument("--detail", help="what was done (goes into the request)")
    sp.set_defaults(func=cmd_request_approval)

    def add_gate_args(sp2):
        sp2.add_argument("--approver", help="approver login (evidence must come from this user)")
        sp2.add_argument("--attest", action="store_true",
                         help="attest that the user instructed approval in the agent session")

    sp = sub.add_parser("approve", help="verify approval evidence, close with resolution")
    sp.add_argument("id", type=int)
    sp.add_argument("--resolution", required=True, help="resolution code (see templates)")
    sp.add_argument("--detail", help="resolution summary")
    sp.add_argument("--problem")
    sp.add_argument("--root-cause")
    sp.add_argument("--verification")
    sp.add_argument("--prevention")
    sp.add_argument("--reason", help="used by some resolution addenda")
    sp.add_argument("--duplicate-of")
    sp.add_argument("--external-ref")
    sp.add_argument("--change-ref")
    add_gate_args(sp)
    sp.set_defaults(func=cmd_approve)

    sp = sub.add_parser("reject", help="verify rejection evidence, reopen ticket")
    sp.add_argument("id", type=int)
    sp.add_argument("--reason", required=True)
    add_gate_args(sp)
    sp.set_defaults(func=cmd_reject)

    sp = sub.add_parser("close", help="close ticket with resolution (alias of approve)")
    sp.add_argument("id", type=int)
    sp.add_argument("--resolution", required=True)
    sp.add_argument("--detail")
    sp.add_argument("--problem")
    sp.add_argument("--root-cause")
    sp.add_argument("--verification")
    sp.add_argument("--prevention")
    sp.add_argument("--reason")
    sp.add_argument("--duplicate-of")
    sp.add_argument("--external-ref")
    sp.add_argument("--change-ref")
    sp.add_argument("--skip-gate", action="store_true", help="attest approval instead of checking journals")
    add_gate_args(sp)
    sp.set_defaults(func=cmd_close)

    sp = sub.add_parser("sla-report", help="SLA scan over open tickets")
    sp.add_argument("--project", help="project id or identifier")
    sp.add_argument("--notify", action="store_true", help="post warning/breach notes and escalate")
    sp.add_argument("--dry-run", action="store_true", help="report only, never mutate")
    sp.add_argument("--fast", action="store_true", help="skip journal fetch (less accurate)")
    sp.set_defaults(func=cmd_sla_report)

    sp = sub.add_parser("digest", help="daily ticket digest")
    sp.add_argument("--project", help="project id or identifier")
    sp.set_defaults(func=cmd_digest)

    sp = sub.add_parser("render", help="render a template")
    sp.add_argument("template")
    sp.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("templates", help="list templates and resolution codes")
    sp.set_defaults(func=cmd_templates)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd in ("render", "templates"):
        args.func(None, args)  # offline commands need no credentials
        return
    url = os.environ.get("REDMINE_URL")
    key = os.environ.get("REDMINE_API_KEY")
    if not url or not key:
        die("set REDMINE_URL and REDMINE_API_KEY environment variables", 2)
    client = Redmine(url, key)
    try:
        args.func(client, args)
    except GateViolation as exc:
        print(f"approval-gate: {exc}", file=sys.stderr)
        sys.exit(3)
    except ApiError as exc:
        print(f"redmine API error ({exc.status}): {'; '.join(exc.messages)}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
