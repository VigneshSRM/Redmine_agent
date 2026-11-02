# Redmine REST API — Reference Cheat Sheet

Source of truth: [Redmine REST API](https://www.redmine.org/projects/redmine/wiki/Rest_api)
(Developer's Guide). All examples use JSON. Replace `{R}` with `REDMINE_URL`.

## Request envelope

```http
GET  {R}/issues.json
X-Redmine-API-Key: <key>
Accept: application/json

POST/PUT additionally:
Content-Type: application/json      # mandatory on POST/PUT
```

Impersonation (admin key only): `X-Redmine-Switch-User: <login>` — 412 if login invalid.

## Pagination & response meta

```
GET /issues.json?offset=0&limit=100        # limit default 25, max 100
GET /issues.json?page=3&limit=10           # page 3 of 10s == offset 20
```

```json
{ "issues": [ ... ], "total_count": 2595, "limit": 25, "offset": 0 }
```

`?nometa=1` / header `X-Redmine-Nometa: 1` strips the meta keys.

## Issues

### List — `GET /issues.json`

Open issues only by default.

| Filter | Values / notes |
|---|---|
| `issue_id` | single id or `1,2,3` |
| `project_id` | numeric id (not identifier) |
| `subproject_id` | `project_id=X&subproject_id=!*` excludes subprojects |
| `tracker_id` | numeric id |
| `status_id` | `open` \| `closed` \| `*` \| numeric id |
| `assigned_to_id` | user id or `me` |
| `author_id` | user id |
| `parent_id` | parent issue id |
| `cf_x` | custom field filter (field must have "used as a filter"); `~foo` = contains |
| `created_on`, `updated_on` | operators **URL-encoded**: `>=`→`%3E%3D`, `><a|b`→`%3E%3Ca%7Cb`, `<=`→`%3C%3D` |
| `due_date` | same operator encoding |
| `subject` | substring match |
| `sort` | `field:desc,other_field` e.g. `sort=updated_on:desc` |
| `include` | `attachments`, `relations` |

```bash
# open issues of project 2, tracker 1, newest first
GET /issues.json?project_id=2&tracker_id=1&status_id=open&sort=updated_on:desc&limit=100
# my open issues
GET /issues.json?assigned_to_id=me
# closed last month
GET /issues.json?status_id=closed&closed_on=%3E%3C2026-10-01%7C2026-10-31
# created after a timestamp
GET /issues.json?created_on=%3E%3D2026-01-02T08:12:32Z
```

### Show — `GET /issues/:id.json?include=...`

`include` values: `children`, `attachments`, `relations`, `changesets`, `journals`,
`watchers` (2.3+), `allowed_statuses` (5.0+ — workflow-legal next statuses, respects
open subtasks / blockers / closed parent), `reactions` (7.1+).

```bash
GET /issues/296.json?include=journals,allowed_statuses
```

Issue object keys: `id`, `project`, `tracker`, `status`, `priority`, `author`,
`assigned_to`, `category`, `subject`, `description`, `start_date`, `due_date`,
`done_ratio`, `estimated_hours`, `spent_hours`, `custom_fields`, `created_on`,
`updated_on`, `closed_on`.

### Create — `POST /issues.json`

```json
{
  "issue": {
    "project_id": 1,
    "tracker_id": 2,
    "status_id": 1,
    "priority_id": 4,
    "subject": "Example",
    "description": "Full description",
    "assigned_to_id": 6,
    "category_id": 9,
    "fixed_version_id": 3,
    "parent_issue_id": 100,
    "estimated_hours": 4,
    "is_private": false,
    "watcher_user_ids": [7, 8],
    "custom_fields": [{"id": 2, "value": "Fixed"}],
    "uploads": [{"token": "7167.abc...", "filename": "log.png"}]
  }
}
```

→ `201` with the created issue; `422` with `{"errors": [...]}` on validation failure.

### Update — `PUT /issues/:id.json`

Any create attribute, plus:

```json
{
  "issue": {
    "status_id": 5,
    "assigned_to_id": 6,
    "done_ratio": 80,
    "notes": "Comment posted as a journal entry (triggers email to watchers)",
    "private_notes": false,
    "custom_fields": [{"id": 2, "value": "Duplicate"}]
  }
}
```

### Delete — `DELETE /issues/:id.json` → `204`. Irreversible. Confirm with the user first.

### Watchers

```
POST   /issues/:id/watchers.json       {"user_id": 7}     # 2.4+
DELETE /issues/:id/watchers/:user_id.json
```

### Journals (history/notes) — 5.0+

Read with `GET /issues/:id.json?include=journals`. Each journal: `id`, `user`,
`notes`, `created_on`, `private_notes`, `details[]` (field changes).

## Users

```
GET /users.json?status=1&name=jsmith&group_id=20&limit=100   # status 1=active, 3=registered, 2=locked(?)
GET /users/:id.json                                          # visibility-limited for non-admins
GET /users/current.json                                      # who am I (incl. own api_key)
POST /users.json / PUT /users/:id.json / DELETE /users/:id.json   # admin only
```

User object: `id`, `login`, `firstname`, `lastname`, `mail`, `status`, `admin`,
`created_on`, `last_login_on`, `groups`, `memberships`.

## Memberships / project roles

```
GET    /projects/:id/memberships.json          # who can be assigned in this project
POST   /projects/:id/memberships.json          {"membership": {"user_id": 7, "role_ids": [1]}}
PUT    /memberships/:id.json                   {"membership": {"role_ids": [1, 2]}}
DELETE /memberships/:id.json
```

## Reference data (resolve names → ids at runtime)

```
GET /issue_statuses.json                     # [{id, name, is_closed}] — find "Closed", "Pending Approval", ...
GET /trackers.json                           # [{id, name}]
GET /enumerations/issue_priorities.json      # [{id, name, is_default}]
GET /enumerations/time_entry_activities.json # [{id, name}]
GET /projects.json?limit=100                 # [{id, identifier, name, status}]
GET /custom_fields.json                      # find "Resolution", "SLA Policy", ... field ids
GET /queries.json?project_id=1               # saved queries (use via /issues.json?query_id=N)
```

**Status ids are instance-specific. Always look them up; never hardcode.**
A status is closed when `is_closed: true` — use that id for closing tickets.

## Time entries

```json
POST /time_entries.json
{ "time_entry": { "issue_id": 1234, "hours": 1.5, "activity_id": 9, "comments": "Investigation" } }
```

List: `GET /time_entries.json?issue_id=1234` or `?user_id=me&spent_on=%3E%3D2026-10-01`.

## Attachments

```
POST /uploads.json?filename=report.pdf        # body = raw bytes
     Content-Type: application/octet-stream
→ 201 {"upload": {"token": "7167.ed1cc..."}}
```

Attach via `uploads: [{"token": "...", "filename": "report.pdf", "description": "..."}]`
on `POST /issues.json` or `PUT /issues/:id.json`. Redmine 7.1+ ignores client
`content_type` (detected from content). Oversized file → `422`.

Also: `GET /attachments/:id.json`, `GET /attachments/:id/download`, `DELETE /attachments/:id.json`.

## Issue relations

```json
POST /issues/:id/relations.json
{"relation": {"issue_to_id": 999, "relation_type": "blocks", "delay": 3}}
```

Types: `relates`, `duplicates`, `duplicated_by`, `blocks`, `blocked_by`, `precedes`,
`follows`, `copied_to`, `copied_from`. Delete: `DELETE /issues/:id/relations/:rid.json`.

## Search, news, wiki, versions, categories

```
GET /search.json?q=login+error&scope=all&all_words=1
GET /projects/:id/news.json
GET /projects/:id/wiki/index.json
GET /projects/:id/versions.json        # plan work against Target Versions
GET /projects/:id/issue_categories.json
```

## Error catalogue

| Code | Meaning | Agent action |
|---|---|---|
| 200/201/204 | OK / created / deleted | continue |
| 401 | bad/missing API key | stop, ask user to fix credentials |
| 403 | not permitted | stop, report required role |
| 404 | not found or invisible | confirm id; note visibility rules |
| 412 | impersonated login invalid | fix `X-Redmine-Switch-User` |
| 406 | wrong `Content-Type` on upload | use `application/octet-stream` |
| 422 | validation failed | relay every string in `errors[]` |
| 5xx / timeout | server error | retry ≤2 with backoff, then report |

## Filter operator encoding quick table

| Human | URL-encoded |
|---|---|
| `>=X` | `%3E%3DX` |
| `<=X` | `%3C%3DX` |
| `><A\|B` | `%3E%3CA%7CB` |
| `!*` (none) | `%21*` |
