from fastapi import FastAPI
app = FastAPI(
    title="ICTU × CodeGym Training Management API",
    description="Backend API for the training management system.",
    version="0.1.0",
)
@app.get("/")
def read_root():
    return {
        "message": "ICTU × CodeGym Training Management API",
        "status": "running",
    }
@app.get("/health")
def health_check():
    return {"status": "healthy"}
