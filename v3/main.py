# App entry point (`uvicorn main:app`); all endpoints live in api/routes.py.
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app_db
from api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# Runs once at startup: marks runs a previous process left "running" as failed.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    orphaned = app_db.mark_orphaned_runs()
    if orphaned:
        logging.getLogger(__name__).warning("Marked %s run(s) from a previous process as failed.", orphaned)
    yield


app = FastAPI(title="terminautomator", lifespan=lifespan)

# Mount the router at the root path
app.include_router(router)
