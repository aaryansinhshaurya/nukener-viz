from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, projects, documents, reviews, metrics, locks, versions

app = FastAPI(title="NER Review Platform API", version="0.1.0")

# Frontend is served from a different origin during dev (Vite on :5173,
# or a static file:// / different port in prod) — the API has to allow
# it explicitly since browsers block cross-origin requests by default.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://nukenerviz.netlify.app"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(reviews.router)
app.include_router(metrics.router)
app.include_router(locks.router)
app.include_router(versions.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
