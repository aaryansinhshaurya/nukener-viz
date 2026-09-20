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

## Email and accounts

Set `FRONTEND_URL` to the Vercel origin so invitation and reset links open the correct site. **Render Free blocks SMTP ports 25, 465, and 587**, so Gmail SMTP cannot send from a Free backend. For a small private project, use the [Apps Script mail web app](backend/google_apps_script/Code.gs):

1. Create a Google Apps Script project and replace its entire `Code.gs` with that file. Save it.
2. In **Project Settings > Script Properties**, add `MAIL_SECRET` with a long random value. Do not include backslashes in the property name. Select `authorizeMail` in the editor, click **Run**, and grant the requested Google mail permission.
3. Choose **Deploy > New deployment > Web app**. Set **Execute as: Me** and **Who has access: Anyone**. Copy the deployed URL ending in `/exec`. If you later edit the script, use **Deploy > Manage deployments > Edit > New version > Deploy** so `/exec` runs the new code.
4. In the Render **backend service > Environment**, set `APPS_SCRIPT_MAIL_URL` to that full `/exec` URL and `APPS_SCRIPT_MAIL_SECRET` to the exact same value as `MAIL_SECRET`. Set `FRONTEND_URL` to the frontend origin. Save and redeploy. A Resend key and `EMAIL_FROM` are not needed when using Apps Script; remove placeholder values such as `value`.
5. Open the `/exec` URL in an incognito browser window. Its JSON response should say the mail endpoint is running. This checks `doGet` and anonymous access only; an actual password reset for an existing account or a project invitation checks `doPost` and `MailApp`. If sending fails, check **Executions** in Apps Script and the Render backend log. Resend previously saved invitations from the project's **Team** tab.

The backend sends JSON containing `secret`, `to`, `subject`, and `body`, and accepts success only after the script returns `{"ok": true}`. Keep the shared secret out of GitHub and the frontend. Deployment ID and Script ID are not needed by the backend. Apps Script takes priority over the providers below. An incomplete Apps Script configuration is reported as unavailable instead of falling back to another provider.

Apps Script sends from the Google account that deployed the web app. The script's `name: "NukeNER-Viz"` changes the visible sender name, not the sender email address. If an invitation fails, inspect the matching `doPost` execution and its logs in Apps Script, then the Render backend log. The text `Apps Script email delivery failed` comes from an older backend version; deploy the current backend to get a more specific error. The invitation remains pending, so use **Team > Resend** after fixing delivery. Editing `Code.gs` also requires deploying a new Apps Script version.

Alternatively, this project supports Resend's HTTPS API:

1. In [Resend](https://resend.com/docs/dashboard/domains/introduction), add a domain you control and complete its DNS verification. Create an API key in Resend's **API Keys** page; its value starts with `re_`.
2. In the Render **backend service → Environment**, replace the placeholder `value`: set `RESEND_API_KEY` to that generated key, `EMAIL_FROM` to a sender on the verified domain (for example, `NukeNER Review <review@your-domain.example>`), and `FRONTEND_URL` to your exact Vercel origin. Keep the key on the backend only.
3. Save the environment and deploy the current backend code. Test **Forgot password** with an existing account. In a project's **Team** tab, use **Resend** for invitations that were saved while email was unavailable.

Resend's test sender is limited; use a verified domain to send invitations to other people. An alternative is a paid Render service with SMTP enabled and a Gmail app password, which requires Google 2-Step Verification. Do not use your normal Gmail password as `SMTP_PASSWORD`.

SMTP remains supported on hosts that allow it: set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, and SMTP_TLS. Invitations and password resets use the configured provider. If delivery is unavailable, the reset endpoint returns 503 and the button remains available for a retry. Existing pending invitations can be resent after the provider is configured. Password reset links expire after one hour and are single-use.

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
