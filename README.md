# NukeNER Review

A collaborative application for checking named entity predictions. Reviewers mark each model prediction **TP** or **FP**. The metrics page reports precision (`TP / (TP + FP)`) and review coverage. It does not estimate recall or capture missed entities.

## Repository layout

- `frontend/`: static HTML, CSS, and JavaScript. This is the Vercel project root.
- `backend/`: FastAPI, PostgreSQL models, Alembic migrations, and tests. Keep this service on Render for the initial Vercel move.

## Input

Upload CSV or JSON rows with document_id, filename, source, cleaned_title, sentence_id, sentence, entities. The three metadata fields may be blank or omitted in JSON. Source, when present, appears quietly beside the document ID. Each entity needs text and label. start_char and end_char are optional: the importer locates text in the sentence, using the next unused occurrence when text repeats. Explicit offsets are zero-based and end_char is exclusive. An entity whose text cannot be found causes a clear import error. Metadata must be consistent for every row of a document.

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

## Email and accounts

Set FRONTEND_URL to the Vercel origin so invitation and reset links open the correct site. On Render Free, add RESEND_API_KEY and EMAIL_FROM under the backend service's Environment settings. EMAIL_FROM must use a sender accepted by your email provider, usually on a verified domain. The backend sends through Resend's HTTPS API because Render Free blocks outbound SMTP ports 25, 465, and 587. Save the variables and redeploy. Keep API keys out of GitHub and frontend settings.

SMTP remains supported on hosts that allow it: set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, and SMTP_TLS. Invitations and password resets use the configured provider. If delivery is unavailable, the reset endpoint returns 503 and the button remains available for a retry. Existing pending invitations can be resent after the provider is configured. Password reset links expire after one hour and are single-use.

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
