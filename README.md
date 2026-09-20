# NukeNER Review

A collaborative application for checking named entity predictions. Reviewers mark each model prediction **TP** or **FP**. The metrics page reports precision (`TP / (TP + FP)`) and review coverage. It does not estimate recall or capture missed entities.

## Repository layout

- `frontend/`: static HTML, CSS, and JavaScript. This is the Vercel project root.
- `backend/`: FastAPI, PostgreSQL models, Alembic migrations, and tests. Keep this service on Render for the initial Vercel move.

## Input

Upload CSV or JSON rows with document_id, filename, source, cleaned_title, sentence_id, sentence, entities. The three metadata fields may be blank or omitted in JSON. Source, when present, appears quietly beside the document ID. Each entity needs text and label. start_char and end_char are optional: the importer locates text in the sentence, using the next unused occurrence when text repeats. Explicit offsets are zero-based and end_char is exclusive. An entity whose text cannot be found in its sentence is skipped and reported in the upload summary; valid entities still import. Explicit offsets that point to different text remain an error. Metadata must be consistent for every row of a document.

## Local backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set DATABASE_URL and JWT_SECRET_KEY in .env.
.venv\Scripts\python.exe -m alembic upgrade head
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

If a dataset was uploaded before text-only entities were supported, select that project in **Projects** and upload the original CSV or JSON again. The backend checks that the document IDs, sentence IDs, and sentence text match the stored dataset, then adds only missing predictions. It keeps existing reviews and does not duplicate entities on a repeat upload. A different dataset is rejected; create a new project for different text. After repairing, save a new version if you use checkpoints, since earlier versions may predate the added entities.

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
.venv\Scripts\python.exe -m alembic heads
cd ..
node --check frontend/app.js
```
