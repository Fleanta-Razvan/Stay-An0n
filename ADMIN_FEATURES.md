# Stay-An0n Admin Features — Priority Plan

The admin console is intentionally divided into priorities so access protection and essential moderation exist before optional automation is added.

## P0 — Access, safety, and recovery (implemented)

- Password/token-gated console and token-protected admin API routes.
- Session-only browser storage for the admin token and an explicit logout action.
- Administrative audit entries for board changes, content deletion, IP bans, settings, and backup downloads.
- Downloadable ZIP backup containing a consistent SQLite snapshot and all valid uploaded image files.
- Clear warnings before destructive actions and warnings about the sensitivity of IP data.

## P1 — Core site control (implemented)

- Dashboard counts for boards, threads, posts, attachments, and active IP bans.
- Create boards; rename their display names; edit descriptions, per-board retention and capacity settings; enable/disable boards; delete a board with all its threads, posts, and images.
- Search moderation records by post text, subject, poster name, or public post ID; filter by board.
- Lock/unlock threads; delete individual replies; delete a thread and all replies; open a post attachment for review.
- Permanent or time-limited IPv4/IPv6 bans, recorded reasons, expiry display, and unban controls.
- Configure effective global minimum-retention, message length, and per-IP posting interval; manually run the existing pruning policy.
- View recent administrative actions.

## P2 — Abuse prevention (recommended next)

1. **User reports and moderation queue:** public report action on posts, report reason, duplicate-report throttling, resolve/dismiss controls.
2. **Spam controls:** configurable phrase/URL filters, repeat-post detection, per-board rate limits, upload hash duplicate detection, and an optional first-post cooldown.
3. **Board notices and rules:** announcement banner, board-specific rules, and a read-only maintenance-mode notice.
4. **Ban workflow:** optional subnet bans with safeguards, expiry notifications, and a preview showing how many recent posts match the address before banning.

## P3 — Operational tools (recommended after P2)

1. **Restore workflow:** validate a backup ZIP before any restore, require a server-side confirmation step, preserve a pre-restore backup, and restart safely.
2. **Moderation analytics:** post volume over time, upload/storage usage, active-thread trends, and ban/report statistics.
3. **Retention automation:** dry-run pruning preview, scheduled retention by board, orphan-file scanner, and cleanup report.
4. **Roles and hardening:** multiple moderator credentials with scoped permissions, optional LAN allowlist, login-attempt throttling, and deployment guidance for HTTPS/reverse proxies.

## Important behavior and limits

- A display-name rename does not change the board URL/ID, preserving existing links.
- IP bans only block posting from the IP address the server sees. They are not account bans: networks can be shared and IP addresses can change.
- IPs are stored for new posts going forward. Earlier records have no reliable IP history.
- Backup downloads contain post data and moderation records. Store them privately.
- Board deletion and content deletion are permanent. A backup is the recovery mechanism in this release; guided restoration is a P3 feature.
