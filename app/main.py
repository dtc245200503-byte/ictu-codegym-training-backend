from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

from app.api.routes.auth import router as auth_router
from app.api.routes.management import router as management_router
from app.api.routes.profile import router as profile_router
from app.api.routes.leads import router as leads_router
from app.api.routes.sprint4 import router as sprint4_router
from app.api.routes.academic import router as academic_router
from app.api.routes.surveys import router as surveys_router
from app.api.routes.dashboards import router as dashboards_router


app = FastAPI(
    title="ICTU × CodeGym Training Management API",
    description="Backend API for the training management system.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(management_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(leads_router, prefix="/api")
app.include_router(sprint4_router, prefix="/api")
app.include_router(academic_router, prefix="/api")
app.include_router(surveys_router, prefix="/api")
app.include_router(dashboards_router, prefix="/api")


@app.get("/")
def read_root():
    return {
        "message": "ICTU × CodeGym Training Management API",
        "status": "running",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}
