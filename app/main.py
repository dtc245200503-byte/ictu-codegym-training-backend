from fastapi import FastAPI

from app.api.routes.auth import router as auth_router


app = FastAPI(
    title="ICTU × CodeGym Training Management API",
    description="Backend API for the training management system.",
    version="0.1.0",
)

app.include_router(auth_router, prefix="/api")


@app.get("/")
def read_root():
    return {
        "message": "ICTU × CodeGym Training Management API",
        "status": "running",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}
