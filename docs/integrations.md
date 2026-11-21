# Integrations

The app can send escalation notifications to Slack, create Jira issues from cases, and publish case reports to Confluence. Only the platform operator can create connections. Each one lives in the `CASEMGR_CONNECTIONS` deployment secret and names the tenants that may use it. Tenant admins then choose among their tenant's connections in Settings.

## What leaves the app

| Destination | Sent | Never sent |
|---|---|---|
| Slack | Case number, severity, trigger, tenant, link. The title is included unless the case is restricted | Description, notes, observables, evidence |
| Jira | `[#N] title`, severity, priority, category, state, opened time, link; labels `casemgr` and `severity-<level>` | Description, notes, observables, evidence |
| Confluence | The full report (executive, technical, or post-incident review), with its TLP marking and privilege notice at the top | Evidence files |

Restricted cases and TLP:RED cases are never sent to Jira or Confluence. A case that is privileged, or a report that contains a privileged note, can be sent only by a user with `legal:manage` (admins, IR leads, legal). Every send is written to the audit log (`jira.create`, `report.publish`) and recorded on the case timeline.

## The `CASEMGR_CONNECTIONS` secret

The secret is a JSON object that maps a connection name (lowercase letters, digits, `-`, `_`; at most 40 characters) to a connection. Every URL must use https. The app refuses to start if the value is invalid, and the error message never includes the values themselves.

```json
{
  "slack-soc": {
    "type": "slack",
    "webhook_url": "https://hooks.slack.com/services/T000/B000/XXXX",
    "tenants": ["detect-respond"]
  },
  "jira": {
    "type": "jira",
    "base_url": "https://acme.atlassian.net",
    "auth": "basic",
    "user": "svc-casemgr@acme.com",
    "token": "<Atlassian API token>",
    "tenants": ["detect-respond", "it-ops"]
  },
  "wiki": {
    "type": "confluence",
    "base_url": "https://acme.atlassian.net/wiki",
    "auth": "basic",
    "user": "svc-casemgr@acme.com",
    "token": "<Atlassian API token>",
    "tenants": ["detect-respond"]
  }
}
```

| Field | Notes |
|---|---|
| `type` | `slack`, `jira`, or `confluence` |
| `webhook_url` | Slack only |
| `base_url` | Jira or Confluence only. Cloud Jira: `https://<site>.atlassian.net`. Cloud Confluence: `https://<site>.atlassian.net/wiki`. Data Center: the base URL, including any context path |
| `auth` | `basic` (Cloud: account email and API token) or `bearer` (Data Center personal access token) |
| `user` | Required with `basic` |
| `token` | API token or personal access token |
| `tenants` | Tenant slugs that may use the connection |

Add the secret to the `casemgr` Secret as the key `CASEMGR_CONNECTIONS` (see [deployment.md](deployment.md#3-secrets)). The backend and jobs read it at startup.

The backend and jobs reach these services on port 443, which the chart's network policy allows by default. If a Data Center server listens on another port, add that port to `networkPolicy.egressPorts`.

## Slack

1. In Slack, create an app for the workspace (api.slack.com/apps > Create New App > From scratch).
2. Under Incoming Webhooks, turn webhooks on, choose Add New Webhook to Workspace, and pick the channel.
3. Copy the webhook URL into a `slack` connection.
4. In Settings > Escalation rules, a tenant admin ticks the connection on each rule that should post to it.

The `sla` and `clocks` jobs post the messages. A failed post is retried on the job's next run, and the rule's last error is kept without the webhook URL.

## Jira

1. Create a service account, and give it Browse Projects and Create Issues permissions in the target project. Make sure the project has the issue type you will use (default `Task`) and that its create screen has the Labels field.
2. Create a token for that account:
   - Cloud: id.atlassian.com > Security > API tokens. Use `auth: basic` with the account's email as `user`.
   - Data Center: Profile > Personal Access Tokens. Use `auth: bearer`.
3. Add a `jira` connection, then in Settings > Integrations set the connection, the project key (for example `SOC`), and the issue type.
4. Users with `cases:write` see Create Jira issue on the case summary. Each case gets one issue: asking again returns the existing issue. When cases are merged, the issue moves to the target case unless the target already has one.

The app uses REST API v2 (`POST /rest/api/2/issue`) because it takes a plain-text description on both Cloud and Data Center.

## Confluence

1. Give the service account permission to view and add pages in the target space.
2. Create a token as for Jira. One Atlassian Cloud token works for both services, but each needs its own connection because the base URLs differ.
3. Add a `confluence` connection, then in Settings > Integrations set the connection, the space key, and optionally a parent page id (the number in the page's URL). Pages go under the parent page, or at the top of the space if no parent is set.
4. Users with `cases:write` see Publish ... in Confluence beside each report. The page title is `Case #N - <report name> (<tenant>)`. Publishing again updates the same page as a new version. If the page was deleted in Confluence, a new page is created.

The app uses REST API v1 (`/rest/api/content`) with storage format, which both Cloud and Data Center support. All case text is HTML-escaped before it is sent.

## Rotation

1. Create the new token or webhook. Leave the old one active.
2. Update `CASEMGR_CONNECTIONS` in the secret store. Keep the connection names the same: rules and Settings refer to connections by name.
3. Restart the backend (`kubectl rollout restart deployment/<release>-backend`). The jobs pick up the new value on their next run.
4. Run the relevant checks below, then revoke the old token or delete the old webhook.

Renaming or removing a connection does not change tenant settings. A Slack rule that names a missing connection records an error on its notifications. Jira and Confluence actions return an error that names the connection.

## Manual test checklist

Run these checks on a system that can reach the services, using a test tenant.

Setup
- [ ] The backend starts with the new secret. A malformed value (for example a missing `tenants`) stops startup with an error that names the field and doesn't include the token.
- [ ] Settings > Integrations lists only the tenant's own Jira and Confluence connections. Escalation rules list only its Slack connections.

Slack
- [ ] A rule with trigger "Case created" and a Slack connection: create a case, run the `sla` job, and the message appears in the channel with a working link.
- [ ] A restricted case posts without its title.
- [ ] A wrong webhook URL leaves the rule's last error on the notification, without the URL, and the next job run retries it.

Jira
- [ ] Create Jira issue creates the issue in the project with the summary, description, and labels, and the case shows the key as a link.
- [ ] Clicking again does not create a second issue. The timeline shows "Jira issue created: KEY", and the audit log has `jira.create`.
- [ ] A wrong project key or a missing issue type shows Jira's reason in the app.
- [ ] Restricted and TLP:RED cases are refused. For a privileged case, an analyst is refused and an IR lead succeeds.
- [ ] Data Center only: a bearer personal access token works.

Confluence
- [ ] Publishing each report creates a page under the parent page. The TLP marking (and the privilege notice, when there is one) shows in a note box at the top, and the tables render.
- [ ] Publishing again adds a version to the same page, and the app shows the new version number.
- [ ] After the page is deleted in Confluence, publishing creates a new page.
- [ ] A case title containing `<script>` shows as text on the page.
- [ ] Restricted and TLP:RED cases are refused. A report with a privileged note needs `legal:manage`.
