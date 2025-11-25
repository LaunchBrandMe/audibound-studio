from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
import uuid
import json

from src.core.director import ScriptDirector
from src.core.abml import SeriesBible, ScriptManifest, Scene
from src.core.voice_engine import get_voice_provider
from src.core.assembly import AudioAssembler
import asyncio
from src.worker import task_direct_script, task_produce_audio, get_project_from_db, update_project_in_db
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="Audibound Studio API")

# Mount static files
app.mount("/static", StaticFiles(directory="src/static"), name="static")

@app.get("/")
async def read_root():
    return FileResponse("src/static/index.html")

# Helper to init DB if needed
if not os.path.exists("projects_db.json"):
    with open("projects_db.json", "w") as f:
        json.dump({}, f)

class CreateProjectRequest(BaseModel):
    title: str
    text: str

class ProjectResponse(BaseModel):
    project_id: str
    title: str
    status: str
    output_path: Optional[str] = None
    last_engine: Optional[str] = None
    bible: Optional[SeriesBible] = None
    manifest: Optional[ScriptManifest] = None


class ProduceAudioRequest(BaseModel):
    engine: str = "kokoro"

@app.post("/projects", response_model=ProjectResponse)
async def create_project(request: CreateProjectRequest):
    project_id = str(uuid.uuid4())
    new_project = {
        "id": project_id,
        "title": request.title,
        "raw_text": request.text,
        "status": "created",
        "bible": None,
        "manifest": None
    }
    update_project_in_db(project_id, new_project)
    return ProjectResponse(
        project_id=project_id,
        title=new_project["title"],
        status="created",
        output_path=None,
        last_engine=None
    )

@app.post("/projects/{project_id}/direct")
async def direct_script(project_id: str):
    project = get_project_from_db(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Dispatch to Celery
    task_direct_script.delay(project_id)
    
    return {"message": "Direction queued", "project_id": project_id}



@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    project = get_project_from_db(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ProjectResponse(
        project_id=project["id"],
        title=project.get("title", "Untitled"),
        status=project["status"],
        output_path=project.get("output_path"),
        last_engine=project.get("last_engine"),
        bible=project.get("bible"),
        manifest=project.get("manifest")
    )

@app.post("/projects/{project_id}/produce")
async def produce_audio(project_id: str, request: ProduceAudioRequest | None = None):
    project = get_project_from_db(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if not project.get("manifest"):
        raise HTTPException(status_code=400, detail="Project has not been directed yet")

    engine = (request.engine if request else "kokoro").lower()
    allowed_engines = {"kokoro", "styletts2", "indextts2", "mock"}
    if engine not in allowed_engines:
        raise HTTPException(status_code=400, detail=f"Unsupported engine '{engine}'")

    # Dispatch to Celery
    task_produce_audio.delay(project_id, engine)
    return {"message": "Production queued", "project_id": project_id, "engine": engine}


