from fastapi import FastAPI

from api.routes import router

app = FastAPI(title="Data Entry Automation Agent")

app.include_router(router)


@app.get("/")
def root():
    return {"status": "ok", "service": "data-entry-automation-agent"}
