# MT5 Client Portal Backend

Step 21 development build.

## What is included

- FastAPI backend
- SQLite local persistence
- Client/admin login sessions
- Groups, clients, memberships, deposits, withdrawals, expenses, internal transfers, commissions, daily close finalization, MT5 account records, manual MT5 snapshots, and CSV exports
- Neutral compact UI with collapsible admin tools
- Clearer client portfolio dashboard with group names instead of raw IDs
- Duplicate memberships are blocked going forward
- Existing duplicate memberships are merged visually in the client dashboard

## Run locally

```bat
cd "C:\Users\halla\OneDrive\Desktop\mt5_portal_backend"
python -m pytest
python -m uvicorn app.api.app:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

## Notes

This is still a local development build. Do not expose it publicly until deployment hardening, security review, and legal/compliance review are complete.

## Step 22 - Client profile, password, admin access, and audit controls

Step 22 adds account-security workflow improvements:

- Client About/Profile tab in the client dashboard.
- Client can add/update email and email report opt-in.
- Client can change password while logged in.
- Client can request an email password-reset token in local development.
- Client username remains fixed and cannot be changed.
- Client can save a 2FA preference placeholder.
- Admin can view a client dashboard through a protected admin endpoint.
- Admin can reset a client password.
- Admin can disable/reset client 2FA preference.
- Admin view/reset actions are recorded in an audit_events table.
- Client dashboard now uses left-side navigation tabs.

Note: the password-reset endpoint returns a development-only reset token until real email sending is added. Full authenticator-code 2FA verification is still a later production-security step.

## Step 23 - Existing Group Import Wizard foundation

This version adds the first foundation for importing an already-running MT5 group into the portal.

New protected admin endpoints:

- `POST /api/admin/groups/{group_id}/import-wizard/review`
- `POST /api/admin/groups/{group_id}/import-wizard/finalize`

The wizard lets an admin manually queue detected MT5 money movements and classify them before they are saved to the ledger. This is meant for historical deposits, withdrawals, commission withdrawals, expenses, and transfers that happened before the portal was live.

Supported import modes:

- `percentage_import`
- `current_balance_import`
- `historical_reconstruction`

Supported classifications include:

- client deposit
- deposit split equally
- deposit split by percentage
- client withdrawal
- shared group expense
- external commission withdrawal
- partner commission withdrawal
- mixed commission withdrawal
- transfer to new MT5 account
- transfer to existing MT5 account
- broker fee/correction
- manual adjustment
- ignore/already handled

The web UI now includes an **Existing group import wizard** admin section. Real MT5 history scanning is still a later step; this version lets the admin manually simulate/import detected history safely first.


## Step 24 - Admin UI rebuild

This version focuses on usability and clarity instead of adding more financial logic.

Added:

- Premium left-side admin navigation
- Toggleable light/dark mode
- Compact professional layout
- Smaller forms, tables, and controls
- Clearer Existing Group Import Wizard guidance
- Help `!` icons on navigation, common fields, and key actions
- Search inside admin navigation without expanding the whole page

The import wizard is still a foundation: it manually simulates detected MT5 cash movements. Live MT5 history scanning comes later.

## Step 26 - Premium admin UI cleanup

Step 26 focuses on making the portal feel more like a professional fintech/admin product instead of a prototype.

### Added / improved

- Professional line-style sidebar icons instead of emoji icons.
- Stronger premium light theme and a cleaner, less muddy dark theme.
- Cleaner import wizard queue with cards, summaries, and movement effects.
- One-click removal of individual queued import movements before finalizing.
- Import review now shows a readable preview instead of raw JSON by default.
- Raw technical JSON is hidden under Advanced technical details.
- Group dashboard now shows financial summary cards and clean member balance rows instead of raw IDs.
- Raw group/member/client IDs are hidden under Advanced technical IDs.
- Improved empty states for MT5 accounts and import wizard sections.
- Higher-priority tooltip styling to prevent help popovers from being hidden behind panels.

### Test status

Expected local test result:

```bash
85 passed
```


## Step 26

Fixes tooltip layering by rendering all help text in a single fixed floating tooltip layer above the sidebar, cards, tables, and overlays.

## Step 27 notes

### Persistent local data
The running portal now stores its default SQLite database outside the project folder at:

- Windows: `C:\Users\<you>\mt5_portal_data\mt5_portal.db`
- macOS/Linux: `~/mt5_portal_data/mt5_portal.db`

This means replacing the `mt5_portal_backend` code folder during future upgrades should not reset users, clients, groups, deposits, withdrawals, and ledger history.

If an older project folder already has `mt5_portal.db`, the app copies it into the persistent data folder the first time it starts, as long as the persistent database does not already exist.

Advanced override:

```bat
set MT5_PORTAL_DB_PATH=C:\path\to\custom\mt5_portal.db
```

### Dark-mode interaction polish
Dark-mode button hover states were adjusted to use a subtle glow instead of turning too bright and unreadable.
