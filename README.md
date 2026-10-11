# Stay-An0n

## 📖 Overview

**Stay-An0n** is a minimal, self-contained imageboard built using Python's standard library and SQLite. It features boards, thread catalogs, anonymous OPs and replies, local image uploads, post references/backlinks, light/dark themes, SQLite persistence, basic rate limiting, and automatic retention pruning.

**Zero Overhead:** There is no frontend build step, external API, user account system, or runtime internet dependency.

> 🔒 **Privacy Note:** "Anonymous" means no public account is required. It does *not* mean untraceable. The server sees connecting IP addresses, the host operator can read posts and files, and your Wi-Fi/router/firewall may log device activity. Do not publish private or identifying information. Keep this service on a trusted LAN; **do not port-forward it to the public internet**.

## ✨ Features

* **Boards & Catalogs:** Create, rename, disable, or delete boards with searchable thread catalogs.

* **Anonymous Interaction:** Simple anonymous posting for OPs and replies with optional image attachments.

* **Backlinks & References:** Use the `[quote]` link or type `>>PUBLICID` (10-character hex ID) to reference other posts.

* **Theming:** Built-in charcoal dark theme and a clean light utility theme.

* **Robust Storage:** SQLite-backed persistence with automatic retention pruning and easy backup tools.

* **Admin Console:** Full moderation controls (IP bans, thread locking, post deletion, board management, audit logs, and backups).

## 🚀 Quick Start

### Option 1: Docker (Recommended)

**Requirements:** Docker Engine with the Compose plugin, or Docker Desktop.

From the project directory, run:

```
docker compose up -d --build

```

* Open [**http://localhost:8080**](http://localhost:8080) in your browser.

* To access it from another device on the same network, find your host's LAN IP and navigate to `http://HOST-LAN-IP:8080`.

#### Finding your Host LAN IP:

* **Linux:** `hostname -I` (choose your LAN address, e.g., `192.168.x.x`)

* **Windows:** `ipconfig` (look for the active adapter's IPv4 Address)

* **macOS:** `ipconfig getifaddr en0` (Wi-Fi; interface can vary)

*Note: Allow inbound TCP port 8080 on your host firewall **only for your private/local network**. Do not configure router port forwarding.*

### Option 2: Native Start (No Docker)

**Requirements:** Python 3.11+ (no external `pip` packages required).

```
python server.py

```

By default, the server listens on `0.0.0.0:8080`. Override settings using environment variables (`PORT`, `HOST`, `STAYANON_DATA`, `STAYANON_UPLOADS`, `MAX_UPLOAD_BYTES`).

## 🛠️ Admin Console

Access the admin panel at **`http://localhost:8080/admin`** and enter your admin password/token.

### Admin Token

* On first launch, a high-entropy token is generated at `data/admin-token.txt` (Docker: `./data/admin-token.txt`).

* **Retrieve Token:**

  * **Linux / macOS:** `cat data/admin-token.txt`

  * **Windows (PowerShell):** `Get-Content .\data\admin-token.txt`

* Alternatively, set the `STAYANON_ADMIN_TOKEN` environment variable.

* The token is stored securely in the current browser tab's session storage and sent exclusively via the `X-Admin-Token` header.

### Admin Capabilities

* Dashboard overview and system status.

* Board creation, renaming, disabling, and deletion (deletes all associated threads, posts, and attachments).

* Searchable post moderation and thread locking.

* Reply and thread deletion.

* Temporary or permanent IP bans and unbans.

* Posting settings and pruning tools.

* Recent audit history and downloadable backups (database + uploaded images).

## 💻 Usage Guide

* **Posting:** Choose a board, click **New thread** to create an OP, or open an existing thread to reply.

* **Images:** JPEG, PNG, GIF, and WebP uploads are accepted (8 MiB default cap). Click images to view them at full size. Upload forms feature local previews, drag-and-drop support, and pre-flight file checks.

* **References:** Use the `[quote]` link or type `>>PUBLICID` to link to other posts.

* **Theme:** Switch between light and dark modes using the theme toggle.

## 🗄️ Storage & Backups

* **SQLite Database:** `./data/stayanon.sqlite3`

* **Admin Token:** `./data/admin-token.txt`

* **Uploaded Images:** `./uploads/`

These directories are bind-mounted into the Docker container, ensuring data persists across container updates.

### Creating a Backup

Stop the service before performing a consistent file-level backup:

```
docker compose stop
tar -czf stay-an0n-backup-$(date +%F).tar.gz data uploads
docker compose start

```

*To restore:* Stop the service, preserve your current `data/` and `uploads/` directories, extract the backup over them, and restart. Never delete `data/` or `uploads/` during standard updates.

## ⏱️ Automatic Retention & Pruning

Pruning runs automatically on a 5-minute background schedule and after successful posts:

1. When a board exceeds its thread or post limit, eligible old **threads** are ranked using post interaction scores and incoming quote references (oldest activity acts as a deterministic tie-breaker).

2. Each pass considers up to 5 lowest-scoring eligible posts. Individual replies can be deleted; if an OP is selected, its entire thread is removed to prevent orphaned replies.

3. Posts younger than a board's `grace_hours` are exempt. The minimum-retention floor prevents pruning below the configured post count.

**Default Board Settings:**

* `max_threads = 150`

* `max_posts = 1200`

* `grace_hours = 24`

* `min_posts = 30`

* `bump_limit = 300`
  *(All values are customizable per board in the admin console).*

## 🔌 API Overview

### Public Endpoints

* `GET /api/health`

* `GET /api/boards`

* `GET /api/boards/{board}/catalog?page=1&limit=30`

* `POST /api/boards/{board}/threads` (Multipart form: `subject`, `body`, optional `image`)

* `GET /api/threads/{threadPublicId}`

* `POST /api/threads/{threadPublicId}/replies` (Multipart form: `body`, optional `image`)

* `GET /uploads/{randomFilename}`

### Admin Endpoints *(Requires `X-Admin-Token` header)*

* `GET /api/admin/status`

* `GET /api/admin/posts`

* `GET /api/admin/bans`

* `GET /api/admin/backup`

* `POST /api/admin/boards`

* `PATCH /api/admin/boards/{id}`

* `DELETE /api/admin/boards/{id}`

* `PATCH /api/admin/threads/{publicId}`

* `PATCH /api/admin/settings`

* `POST /api/admin/bans`

* `POST /api/admin/bans/remove`

* `POST /api/admin/prune`

* `DELETE /api/admin/posts/{publicId}`

#### Example API Call:

```
TOKEN="$(cat data/admin-token.txt)"
curl -H "X-Admin-Token: $TOKEN" http://localhost:8080/api/admin/status

```

## 🔍 Troubleshooting & FAQs

* **Image Upload Issues (`413` error):** The file or request exceeds `MAX_UPLOAD_BYTES` (default 8 MiB). Restart the server after updating the limit.

* **Image Upload Issues (`415` error):** File signature does not match a supported image type (JPEG, PNG, GIF, WebP). Renaming file extensions will not convert formats.

* **Missing Images:** Verify that the `uploads/` directory (or Docker bind mount `./uploads:/uploads`) is writable and persistent.

* **Port Conflicts:** If port 8080 is in use, run Docker with a custom port:

  ```
  STAYANON_PORT=8081 docker compose up -d --build
  
  ```

  Then visit `http://localhost:8081`.

* **Updates not showing:**

  * *Docker:* Rebuild using `docker compose up -d --build`.

  * *Native:* Restart `python server.py` and hard-refresh your browser (`Ctrl + F5`).

## 🧪 Tests

Run the test suite using Python's `unittest`:

```
python -m unittest discover -s tests -v

```

Check health status: `curl http://localhost:8080/api/health`

View container logs: `docker compose logs -f`

## 🛡️ Scope & Limitations

Stay-An0n is engineered as a compact, self-contained solution for trusted local networks, not as a hardened public imageboard:

* No user accounts or external hosting requirements.

* No WebSockets or complex heavy frontend frameworks.

* IP addresses are handled transiently for rate-limiting and active moderation; historical posts do not store permanent IP columns by default.

* For public/production deployments, ensure you add reverse-proxy TLS termination, robust authentication, CSRF protection, and log retention policies.
