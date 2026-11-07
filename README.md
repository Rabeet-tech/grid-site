# G.R.I.D site

Your design (`static/index.html`) plus a small FastAPI backend. The page works without the API (it just shows the placeholder cards), and lights up when the API is there.

Dark mode: toggle in the nav (half-circle icon). It starts from the visitor's system setting and remembers their choice (`localStorage` key `grid-theme`). Colours live in the `:root[data-theme="dark"]` block at the top of the CSS.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env            # then set ADMIN_TOKEN (python -m app.cli token)
export ADMIN_TOKEN=...          # or load .env however you like
uvicorn app.main:create_app --factory --reload
# site: http://127.0.0.1:8000     API docs: http://127.0.0.1:8000/docs
python -m pytest                # tests
```

Data lives in `data/grid.db` (SQLite). Back that file up; that's the whole database.

## Load your real content

Copy `seed.example.json`, fill it in, then:

```bash
python -m app.cli seed my-seed.json            # add
python -m app.cli seed my-seed.json --replace  # wipe members + log first
```

Photos: `curl -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" --data-binary @sara.jpg http://127.0.0.1:8000/api/admin/members/1/photo` (JPEG/PNG/WebP, max 2 MB, square works best).

## Endpoints

Public

| | |
|---|---|
| `GET /api/site` | applications open/closed, join-section label, handle, contact email, department list |
| `GET /api/members?team=rnd\|software\|comms` | members, ordered by `sort` then id |
| `GET /api/log` | milestones, newest first |
| `POST /api/applications` | `{name, email, department}`. 201 ok, 409 already applied, 403 closed, 422 invalid, 429 too many (5 per 10 min per IP) |

Admin (`Authorization: Bearer <ADMIN_TOKEN>`; all return 503 if no token is set)

| | |
|---|---|
| `GET /api/admin/applications[?status=]`, `GET .../applications.csv` | review / export (CSV is formula-injection safe) |
| `PATCH /api/admin/applications/{id}` | `{status: new\|accepted\|waitlist\|rejected}` |
| `DELETE /api/admin/applications/{id}` | remove an applicant's data |
| `GET/POST /api/admin/members`, `PATCH/DELETE .../members/{id}`, `PUT .../members/{id}/photo` | manage members |
| `POST /api/admin/log`, `PATCH/DELETE .../log/{id}` | manage milestones (`kind`: Hackathon, Project, Research, Event) |
| `GET/PATCH /api/admin/settings` | `applications_open`, `applications_label`, `handle`, `contact_email`, `departments` |

Example: close applications and set the join-section text

```bash
curl -X PATCH http://127.0.0.1:8000/api/admin/settings \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"applications_label": "Applications close 30 Nov"}'
```

## Deploying

- Run behind HTTPS (Caddy/nginx, or a host like Render/Fly/Railway) with `uvicorn ... --factory --proxy-headers` so rate limiting sees real client IPs.
- Set `GRID_DOCS=0` to hide `/docs`.
- Keep `data/` and `static/photos/` on persistent disk. On hosts with ephemeral disks, point `GRID_DB` and `GRID_PHOTO_DIR` at a mounted volume.
- Rate limits are in-memory per process: fine for one worker, use one worker (or move to Redis later).
