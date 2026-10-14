# App entry point (`uvicorn main:app`); all endpoints live in api/routes.py.
from fastapi import FastAPI

from api.routes import router

app = FastAPI(title="Data Entry Automation Agent")

# Mount the router at the root path
app.include_router(router)
