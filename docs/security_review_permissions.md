# Permissions review (Nov 22, 2026)

Owner, high priority: **deny by default, on Django's permission model**. This is what was there, what was open and
what changed. The policy is `attnd/permissions.py`; the tests are `attnd/tests_permissions.py`.

## Before

Access was decided in many places with their own rules:

- console event views: `console.views_events.event_view` → `Event.can_edit` (any team role), with
  `can_manage_team` / `role_for(...) == 'owner'` checks inside the views;
- Studio: `studio.access.studio_event_view` / `studio_required` / its own `staff_required`;
- staff areas: `diagnostics.views.staff_required` (also imported by Prospects and AI credit), `is_staff` checks in
  components and templates; the Platform admin: `platform_admin.views.superuser_required`;
- components: `Component.audience` strings (`staff`, `studio`, `superuser`) checked against `is_staff` /
  `is_superuser`; Ask Attnd tools: `registry.audience` from the URL namespace;
- everything else: `login_required` + "rows of `request.user`" inside the view.

No view had to say what it needed; a new view with only `@login_required` was open to every signed-in person.

## The policy now

- **Platform roles are Django Groups** with named permissions (`ROLES`; kept in the database by `sync_roles` after
  every migrate): **Staff** (`prospects.view_prospect` / `change_prospect`, `diagnostics.view_diagnosticsession` /
  `delete_diagnosticsession`, `credit.view_aispend` / `change_ailimits`, `studio.view_sitereport` /
  `change_sitereport`, `events.mark_event_paid`, `events.view_showcase`) and **Platform admins** (Staff +
  `platform_admin.change_platformsettings`, `events.assign_event_owner`, `events.view_all_usage`). `is_staff`
  accounts have the Staff role without being in the group (so nothing changed for today's staff); superusers have
  every permission (Django's rule). Anyone can now be given a role in the admin by adding them to the group.
- **Event roles are object permissions** answered by `PermissionMixin` (on the site's one auth backend) (`user.has_perm('events.change_location',
  event_or_location)`): contributor = view / change the event, add / change / delete its locations, schedule items
  and notifications, change its website (`studio.change_site`); event admin = + `events.manage_event` (status,
  listing, sources, team, upgrade requests…) and `studio.publish_site` (still also needs the Studio plan); owner = +
  `events.delete_event`, `events.transfer_event`. `Event.can_edit` / `can_manage_team` and
  `studio.access.can_publish` are now these permissions, so every existing call goes through the same backend.
- **Every view declares its permission**: `event_view` (default `events.change_event`, or `perm=`),
  `studio_event_view` (`studio.change_site`), `requires(perm)` for platform areas (404 for anyone without it),
  `signed_in` (any signed-in person, rows of their own), `attnd.public.public` for public pages, `declare(...)` for
  anything else (class views, `django.contrib.auth` views). `DeclaredPermissionMiddleware` refuses a view that
  declares nothing (404 when its URL names an object, else 403) and `tests_permissions.CoverageTests` fails CI on one.
- **Components** declare `permission` (event components default to `events.view_event`, checked by their data
  function; platform ones the area's permission; the person's own cards `signed_in`); `C.allowed` asks it.
- **Ask Attnd** tools and pages take their permission from their view (`Action.permission`, overrides for
  `assign_event_owner`, `admin_usage`, `event_paid`); `registry.allowed` asks `user.has_perm` for platform ones, so
  the tool lists follow the same roles as the pages.
- **Templates** ask `perms.<app>.<codename>` for the top bar's staff and admin links.
- **Querysets**: `Event.objects.for_user(user, perm)` and `Location / ScheduledItem / Notification.objects
  .for_user(user, perm)`; `events_for_user` is now `for_user`. `RawLookupLintTests` flags `get_object_or_404(Model,
  …)` / `Model.objects.get|filter(…)` of event-owned models in views without an event / owner filter (an allow-list
  with a reason for each safe one).

## What was open, and the fixes

| # | Where | What | Fix |
|---|-------|------|-----|
| 1 | `console:team` invite | An event **admin** could demote another admin (or change a pending invite's role) by "inviting" their email again: `update_or_create` reset the role to contributor | Admins' invites never change the role of someone already on the team or already invited; only the owner sets roles |
| 2 | Undo (`console/undo.py`) | Someone **demoted** to contributor (or an admin who lost the role) could still undo a team change they made earlier (re-adding a removed member, removing an invite): undo only checked `can_edit` | Undo of `EventMember` / `EventInvite` operations needs `events.manage_event` now |
| 3 | Ask Attnd confirm cards (`agent/summaries.py`) | The summaries of `discard_ai_changes` / `apply_ai_changes` (and `build_apply_changes`) looked up the AI review by id **without the event**: a crafted call could show another event's name and pending count in the confirm card (the view itself 404s, so nothing was applied) | Reviews are looked up through `Event.objects.for_user(user)`; another event's id gives no summary and no POST data |
| 4 | Staff areas | Access was "`is_staff` or superuser" copied into five decorators, components and templates, with no way to give one person one area | One role (`Staff`) with named permissions; components, tool lists, canvas tabs and the top bar ask the same permission |
| 5 | Undeclared views | A new view with `@login_required` only was open to every signed-in person | Deny by default (middleware + CI test) |
| 6 | `console/components/catalog_chat.py` | Three hand-written "my events" queries (a fourth copy of `events_for_user`) | `Event.objects.for_user(user)` |

Reviewed and fine (kept, now covered by tests): another event's location / item / notification ids through this
event's URLs (404 everywhere, `EventMatrixTests.test_another_events_rows_are_404_through_this_event`); jobs, task
retry, the task feed's `?ids=`, AI reviews, undo ids, agent runs, proposals of other people (`SignedInIdorTests`);
attachments (`AgentAttachment` filtered by `run.user` in `agent/internal.py`); `components/act` and `pick` (the run
is loaded for `request.user`, the component's data function checks the event); Studio preview tokens (signed,
per-site secret, revocable; `studio.hosting`), the sites host (no cookies, only site routes); upgrade requests (the
event's admins; approvals only in the Platform admin); claim requests (sign-in required, staff decide); CSV exports
(event views, formula-safe); email links (sign-in required, the views check the event again); the API (device ids
are unguessable UUIDs and only reach that device's rows; CI keys are limited to DEMO by `ci_scope`,
`events/tests_ci_keys.py`; preview tokens are signed per person + event and capped at 3 phones,
`events/tests_preview.py`; attestation JWTs carry no user and see only public / joined content).

Kept as they were (decisions for the owner):

- **Contributors may publish draft changes and send notifications** (both reach attendees). Publishing the event
  itself, listing it and changing its sources need an event admin. If contributors should not reach attendees,
  declare `event_publish_changes`, `event_discard_changes` and `notifications` with `perm='events.manage_event'`
  and move `events.add_notification` out of `CONTRIBUTOR`.
- **Staff are not on every team**: an `is_staff` account without a role on an event gets 404 on its pages (as
  before); only superusers act as owners everywhere.
- Role facts that are not permissions stay as they are: staff and superusers need a strong second factor and sign in
  once a day, staff AI spend has no monthly limit, the Studio import option is shown to staff.

## Matrix (what the tests check)

- every event URL × anonymous (sign-in), stranger / staff / Staff-group member / another event's owner (404),
  contributor / event admin / owner / superuser (allowed by role, else 404), and with another event's row ids (404,
  nothing deleted);
- contributor can't publish, change sources, invite, mark paid; admin can't delete or transfer; admin can't demote
  an admin;
- every platform URL (≥ 25) × organizer / contributor / stranger (404), staff and Staff-group members (Staff
  permissions only), Platform-admins members and superusers (all);
- every component × roles, event components for strangers and with another event's rows (404);
- Ask Attnd tools per role (`assign_event_owner` only for platform admins; nothing for anonymous);
- the role groups in the database match `ROLES`; every declared permission exists.
