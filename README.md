# NukeNER Review

A collaborative application for checking named entity predictions. Reviewers mark each model prediction **TP** or **FP**. The metrics page reports precision (`TP / (TP + FP)`) and review coverage both overall and separately for every entity class. It does not estimate recall or capture missed entities.

## Repository layout

- `frontend/`: static HTML, CSS, and JavaScript. This is the Vercel project root.
- `backend/`: FastAPI, PostgreSQL models, Alembic migrations, and tests. Keep this service on Render for the initial Vercel move.

## Input

Upload CSV rows with `source, document_id, cleaned_title, chunk_id, chunk_text, entities, triplets`, or JSON objects with the same fields. Each row is one chunk; input row order is preserved within each document. `entities` is a JSON array containing `entity_text`, `entity_class`, and optionally `entity_confidence_score`, `start_char`, and `end_char`. Offsets are zero-based and `end_char` is exclusive. During upload, the backend locates entities in chunk text, including LaTeX and OCR spacing variants. It loads and highlights only entities with valid, non-overlapping spans; the upload response reports how many predictions were skipped. Confidence is displayed with the entity and does not affect reviews or metrics. `triplets` is accepted but ignored for now.

Upload the original JSON or CSV file directly; no preprocessing script is required. JSON files named with the common `.josn` extension typo are also accepted and validated as JSON. Repeated mentions are assigned to distinct occurrences where possible; inspect ambiguous cases if the model intended a later occurrence.

## Local backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set DATABASE_URL and JWT_SECRET_KEY in .env.
.venv\Scripts\alembic.exe upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Run the frontend using any static file server. The default API URL in `frontend/index.html` points to Render; change it to `http://localhost:8000` for local end-to-end testing. Add the frontend origin to `FRONTEND_ORIGINS` in the backend environment.

## Sharing projects

Owners share the existing project from its **Team** tab. Enter a coworker's email and role, choose **Create share link**, then use **Copy link** and send it yourself. No invitation email or Google Apps Script is used.

A saved pending invitation from the earlier email flow remains available. Choose **Generate link** beside it to issue a fresh link for the same invitation record. The old token cannot be displayed because only its hash is stored. Each new link expires after seven days and replaces any previous link for that invitation. **Revoke** stops an unused link. The coworker opens the link, signs up or signs in with the invited email, reviews the project name and role, and explicitly accepts. The same project then appears on their dashboard; no new project or dataset upload is required.

Set `FRONTEND_URL` to the frontend origin so copied links point to the deployed app. Owners receive the raw link only when they create or regenerate it. The backend stores only its hash.

## Password reset email

Password reset still requires an email provider. Configure `RESEND_API_KEY` and `EMAIL_FROM` for Resend, or the SMTP settings in [backend/.env.example](backend/.env.example) on a host that permits SMTP. If no provider is configured, the password reset endpoint returns 503. Password reset links expire after one hour and are single-use. Invitations do not depend on this email configuration.

The Google Apps Script integration has been removed from the backend. Remove `APPS_SCRIPT_MAIL_URL` and `APPS_SCRIPT_MAIL_SECRET` from Render; you can delete the script from your Google account.

## Repairing entities in an existing project

Re-uploading the same chunk dataset adds only missing entity predictions and preserves existing reviews. A different set of documents or chunks is rejected; create a new project for different text. Existing sentence-based projects are retained by the database migration as chunks, and old saved versions remain inspectable and restorable.

Roles belong to projects, not accounts. Creating a project makes the creator an owner. Owners can invite owners, reviewers, or viewers. Owners manage invitations, members, and deletion; owners and reviewers may review and save versions; viewers may inspect and export.

## Versions and deletion

Saved versions can be inspected document by document. Comparisons show changed TP/FP verdicts, added predictions, and removed predictions. Revert requires a fresh comparison with the current state and an owner. The API saves a backup snapshot before reverting. Active document locks block a revert.

Owner deletion moves a project to Recently deleted. Owners may restore it for 30 days. Schedule `python -m app.maintenance` once daily on the backend host to permanently purge projects whose recovery window has expired. The maintenance command also removes old password reset records.

## Frontend on Vercel

Create a Vercel project with **Root Directory** set to `frontend` and **Framework Preset** set to **Other**. No build is needed. Keep the API on Render. Once the Vercel URL is known, set `FRONTEND_URL` and `FRONTEND_ORIGINS` on Render to include that exact origin, then redeploy the backend. The API URL in `frontend/index.html` stays pointed at Render. Run database migrations on Render before using the new frontend. Uploads go directly to Render; `MAX_UPLOAD_BYTES` defaults to 20 MiB and can be adjusted on that service.

## Checks

```powershell
cd backend
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\alembic.exe heads
cd ..
node --check frontend/app.js
```
