# Stay-An0n

A lightweight, self-hosted anonymous imageboard for a trusted home or school LAN. It includes boards, thread catalogs, anonymous OPs/replies, local image uploads, post references/backlinks, light/dark themes, SQLite persistence, basic rate limiting, and automatic retention pruning. It uses Python's standard library and SQLite; there is no frontend build step, external API, account system, or runtime internet dependency.

> **Privacy note:** “Anonymous” means no public account is required. It does not mean untraceable. The server sees connecting IP addresses, the host operator can read posts and files, and your Wi-Fi/router/firewall may log device activity. Do not publish private or identifying information. Keep this service on a trusted LAN; do not port-forward it to the public internet.

## Quick start (Docker)

Requirements: Docker Engine with the Compose plugin, or Docker Desktop. From this directory:

```sh
docker compose up -d --build
```

Open `http://localhost:8080`. To use it from another device on the same network, find the host's LAN IP and open `http://HOST-LAN-IP:8080` on that device. The server binds to `0.0.0.0` inside the container.

Find the host IP:

- Linux: `hostname -I` (choose the LAN address, often `192.168.x.x`)
- Windows: `ipconfig` (look for the active adapter's IPv4 Address)
- macOS: `ipconfig getifaddr en0` (Wi-Fi; interface can vary)

If needed, allow inbound TCP port 8080 on the host firewall **only for your private/local network**. Do not configure router port forwarding. Anyone on a reachable network who can connect can browse and post; restrict the firewall/Wi-Fi network if that is not desired.

## Native start (no Docker)

Requires Python 3.11+ and no pip packages:

```sh
python server.py
```

By default it listens on `0.0.0.0:8080`. Override `PORT`, `HOST`, `STAYANON_DATA`, `STAYANON_UPLOADS`, and `MAX_UPLOAD_BYTES` with environment variables as needed.

## Admin console

Open `http://localhost:8080/admin` (or `http://<server-LAN-IP>:8080/admin`) and enter the admin password/token. On first launch, a high-entropy token is generated in `data/admin-token.txt` (Docker: `./data/admin-token.txt`). In PowerShell, read it with `Get-Content .\data\admin-token.txt`; in Linux/macOS use `cat data/admin-token.txt`. If `STAYANON_ADMIN_TOKEN` is configured in the environment, use that value instead. The token is held in the current browser tab's session storage and sent only to this server in the `X-Admin-Token` header.

The console includes a dashboard, board create/rename/disable/delete controls, searchable post moderation, thread locking, reply/thread deletion, temporary or permanent IP bans and unbans, posting settings, the pruning tool, recent audit history, and downloadable backups of the database plus uploaded images. Deleting a board removes all threads, posts, and attachments belonging to it. Download a backup before destructive actions.

The admin page is available to visitors but all privileged APIs require the token. Keep the token private and use the console only on a trusted network. This built-in server is intended for a local network; do not expose it directly to the public internet without a stronger deployment setup and HTTPS. IP moderation records the address directly seen by the server starting with this version; previous posts cannot be assigned a historical address.

See [ADMIN_FEATURES.md](ADMIN_FEATURES.md) for the feature priorities and the follow-up roadmap. Example API calls from the project directory:

```sh
TOKEN="$(cat data/admin-token.txt)"
curl -H "X-Admin-Token: $TOKEN" http://localhost:8080/api/admin/status
curl -X POST -H "X-Admin-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"id":"games","title":"Games","description":"Games and play"}' \
  http://localhost:8080/api/admin/boards
```

## Using the board

- Choose a board, then **New thread** to create an OP.
- Open a thread to reply, optionally attaching an image.
- Use the **[quote]** link or type `>>PUBLICID` (the visible ID is ten hexadecimal characters) to reference a post.
- Images open at full size when clicked. JPEG, PNG, GIF and WebP uploads are accepted, with an 8 MiB default cap.
- Use **Theme** to switch light/dark mode.
- Image upload forms show a local preview before posting, support drag-and-drop, and check the file type and 8 MiB limit before sending. If the browser rejects an image, try saving it as JPEG, PNG, GIF, or WebP.

## Storage and backups

- SQLite database: `./data/stayanon.sqlite3`
- Admin token: `./data/admin-token.txt`
- Uploaded images: `./uploads/`

These are bind-mounted into the container, so they survive container replacement. Stop the service before a consistent file-level backup:

```sh
docker compose stop
 tar -czf stay-an0n-backup-$(date +%F).tar.gz data uploads
 docker compose start
```

Restore by stopping the service, preserving a copy of current `data/` and `uploads/`, extracting the backup over those directories, and starting again. Never delete `data/` or `uploads/` as part of an ordinary update. For a clean reset, stop the service and deliberately move those folders elsewhere before recreating empty ones.

## Automatic retention / pruning

Pruning runs on a five-minute background schedule and after successful posting. When a board exceeds its thread or post limit, it ranks old eligible **threads** using the sum of post interaction scores and incoming quote references, with oldest activity as a deterministic tie-breaker. Each pruning pass considers up to five lowest-scoring eligible posts. Eligible replies can be deleted individually; if an OP is selected, its entire thread is deleted so replies are never orphaned. A pass may remove more than five rows when it must remove an OP and its replies. Posts younger than the board's `grace_hours` are not eligible. The minimum-retention floor prevents pruning below the configured number of posts.

Board defaults are `max_threads=150`, `max_posts=1200`, `grace_hours=24`, `min_posts=30`, and `bump_limit=300`. Per-board values can be edited in the admin console. Effective global settings include `min_posts`, `max_text_chars`, `rate_seconds`, and `prune_batch`; per-board retention grace is configured on each board. Pruning and moderation actions are recorded in the audit log. Deleted post text is not copied into the audit log.

## API overview

- `GET /api/health`, `GET /api/boards`
- `GET /api/boards/{board}/catalog?page=1&limit=30`
- `POST /api/boards/{board}/threads` (multipart form: `subject`, `body`, optional `image`)
- `GET /api/threads/{threadPublicId}`
- `POST /api/threads/{threadPublicId}/replies` (multipart form: `body`, optional `image`)
- `GET /uploads/{randomFilename}`
- Admin: `GET /api/admin/status`, `GET /api/admin/posts`, `GET /api/admin/bans`, `GET /api/admin/backup`, `POST /api/admin/boards`, `PATCH /api/admin/boards/{id}`, `DELETE /api/admin/boards/{id}`, `PATCH /api/admin/threads/{publicId}`, `PATCH /api/admin/settings`, `POST /api/admin/bans`, `POST /api/admin/bans/remove`, `POST /api/admin/prune`, `DELETE /api/admin/posts/{publicId}`

## Frontend and image upload troubleshooting

- The image chooser accepts JPEG, PNG, GIF, and WebP. It displays a preview and file size before posting.
- If an upload returns `413`, the image or multipart request exceeds the configured limit. The default is 8 MiB (`MAX_UPLOAD_BYTES` / `MAX_UPLOAD_BYTES` environment setting); restart the server after changing it.
- If an upload returns `415`, the file signature does not match a supported image type. Renaming a file does not convert its format.
- If a post succeeds but the image does not appear, check that the `uploads/` directory (or Docker bind mount `./uploads:/uploads`) is writable and persistent.
- In Docker, rebuild after updating the project with `docker compose up -d --build`. For native Windows, stop `python server.py` with Ctrl+C and start it again so the updated backend is loaded. Refresh the page with Ctrl+F5 to load the updated frontend.

## Tests and troubleshooting

Run tests with:

```sh
python -m unittest discover -s tests -v
```

Check service logs: `docker compose logs -f`. Check health: `curl http://localhost:8080/api/health`. If another service uses port 8080, launch with `STAYANON_PORT=8081 docker compose up -d --build` and visit port 8081. The app stores UTC epoch timestamps and renders them in each visitor's local time zone.

## Current scope / limitations

This is a compact first implementation, not a hardened public imageboard. It has no user accounts, no external hosting, no WebSockets, and no full-featured moderation dashboard. The server stores IPs only transiently as keys in the rate-limit table (the current implementation keeps the last post time per IP; it does not write IPs into posts). Uploaded files are signature-checked and randomized, but image dimensions are not decoded or stripped, and server logs contain client IP addresses. For public deployment, add stronger authentication, CSRF protection, reverse-proxy TLS, configurable logging retention, and image re-encoding/dimension checks.

## Frontend design profile

The interface uses a charcoal-first palette with neon-green links and status indicators, monospace typography throughout, square borders, compact board rows, a split directory/sidebar homepage, and small catalog thumbnails. It intentionally avoids hero marketing copy, rounded cards, decorative illustrations, and social-feed styling. On narrow screens, the sidebar becomes a compact top directory and catalog cards remain dense. The thread creation form is visible on board pages; the “Start a thread” control scrolls to it rather than opening a modal. Theme switching defaults to charcoal and can switch to the light utility theme.
#   S t a y - A n 0 n  
 