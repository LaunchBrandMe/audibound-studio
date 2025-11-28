from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
from datetime import datetime
import os
import uuid
import json

from src.core.director import ScriptDirector
from src.core.abml import SeriesBible, ScriptManifest, Scene
from src.core.voice_engine import get_voice_provider
from src.core.voice_mapper import VoiceMapper
from src.core.assembly import AudioAssembler
import asyncio
from src.worker import task_direct_script, task_produce_audio, get_project_from_db, update_project_in_db
from src.worker import persist_voice_overrides
from src.core.voice_library import get_voice_library
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from datetime import datetime
import time
from fastapi import UploadFile, File, Form
from pathlib import Path

app = FastAPI(title="Audibound Studio API")

# Mount static files
app.mount("/static", StaticFiles(directory="src/static"), name="static")
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

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

class ValidationSummary(BaseModel):
    score: int
    issues: List[str] = []
    warnings: List[str] = []
    timestamp: Optional[str] = None


class CharacterSettings(BaseModel):
    name: str
    description: Optional[str] = None
    default_voice: Optional[str] = None
    default_style: Optional[str] = None
    override_voice: Optional[str] = None
    override_style: Optional[str] = None


class RenderHistoryEntry(BaseModel):
    timestamp: str
    engine: str
    output_path: Optional[str] = None


class ProjectResponse(BaseModel):
    project_id: str
    title: str
    status: str
    output_path: Optional[str] = None
    last_engine: Optional[str] = None
    bible: Optional[SeriesBible] = None
    manifest: Optional[ScriptManifest] = None
    characters: Optional[List[CharacterSettings]] = None
    available_voices: Optional[Dict[str, str]] = None
    render_history: Optional[List[RenderHistoryEntry]] = None
    voice_overrides: Optional[Dict[str, Dict[str, Optional[str]]]] = None
    validation_summary: Optional[ValidationSummary] = None


class ProduceAudioRequest(BaseModel):
    engine: str = "kokoro"


class OverridesRequest(BaseModel):
    overrides: Dict[str, Dict[str, Optional[str]]]

@app.post("/projects", response_model=ProjectResponse)
async def create_project(request: CreateProjectRequest):
    project_id = str(uuid.uuid4())
    new_project = {
        "id": project_id,
        "title": request.title,
        "raw_text": request.text,
        "status": "created",
        "bible": None,
        "manifest": None,
        "voice_overrides": {},
        "render_history": []
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

    characters = []
    if project.get("manifest") and project.get("bible"):
        bible = SeriesBible(**project["bible"])
        voice_mapper = VoiceMapper(bible)
        overrides = project.get("voice_overrides") or {}
        
        # Add Narrator first (it's not in bible.characters but always exists)
        narrator_voice = overrides.get("Narrator", {}).get("voice") or voice_mapper.get_voice_for_speaker("Narrator")
        narrator_style = overrides.get("Narrator", {}).get("style") or None
        characters.append(CharacterSettings(
            name="Narrator",
            description="Story narrator",
            default_voice=voice_mapper.get_voice_for_speaker("Narrator"),
            default_style=None,
            override_voice=overrides.get("Narrator", {}).get("voice"),
            override_style=overrides.get("Narrator", {}).get("style")
        ))
        
        # Add other characters from bible
        for character in bible.characters:
            assigned_voice = overrides.get(character.name, {}).get("voice") or voice_mapper.get_voice_for_speaker(character.name)
            assigned_style = overrides.get(character.name, {}).get("style") or None
            characters.append(CharacterSettings(
                name=character.name,
                description=character.description,
                default_voice=voice_mapper.get_voice_for_speaker(character.name),
                default_style=None,
                override_voice=overrides.get(character.name, {}).get("voice"),
                override_style=overrides.get(character.name, {}).get("style")
            ))
        available_voices = VoiceMapper.get_all_available_voices()
    else:
        available_voices = VoiceMapper.get_all_available_voices()

    history = [RenderHistoryEntry(**entry) for entry in project.get("render_history") or []]

    return ProjectResponse(
        project_id=project["id"],
        title=project.get("title", "Untitled"),
        status=project["status"],
        output_path=project.get("output_path"),
        last_engine=project.get("last_engine"),
        bible=project.get("bible"),
        manifest=project.get("manifest"),
        characters=characters or None,
        available_voices=available_voices,
        render_history=history or None,
        voice_overrides=project.get("voice_overrides") or {},
        validation_summary=project.get("validation_summary")
    )

@app.post("/projects/{project_id}/produce")
async def produce_audio(project_id: str, request: ProduceAudioRequest | None = None):
    project = get_project_from_db(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if not project.get("manifest"):
        raise HTTPException(status_code=400, detail="Project has not been directed yet")

    engine = (request.engine if request else "kokoro_multi").lower()
    allowed_engines = {"kokoro", "kokoro_single", "kokoro_multi", "styletts2", "indextts2", "sesame", "mock"}
    if engine not in allowed_engines:
        raise HTTPException(status_code=400, detail=f"Unsupported engine '{engine}'")

    # Dispatch to Celery
    task_produce_audio.delay(project_id, engine)
    return {"message": "Production queued", "project_id": project_id, "engine": engine}


@app.post("/projects/{project_id}/overrides")
async def update_overrides(project_id: str, request: OverridesRequest):
    if not persist_voice_overrides(project_id, request.overrides):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"message": "Overrides saved"}


# ==================== VOICE LIBRARY ENDPOINTS ====================

@app.post("/voices/upload")
async def upload_voice(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    engine: str = Form("styletts2")
):
    """Upload reference audio for voice cloning"""
    try:
        print(f"[Upload] Received upload request - filename: {file.filename}, name: {name}, engine: {engine}")
        audio_bytes = await file.read()
        print(f"[Upload] Read {len(audio_bytes)} bytes")
        
        # Validate
        if len(audio_bytes) > 10 * 1024 * 1024:
            print(f"[Upload] ERROR: File too large ({len(audio_bytes)} bytes)")
            raise HTTPException(status_code=400, detail="File too large (max 10MB)")
        
        if not file.filename:
            print("[Upload] ERROR: No filename provided")
            raise HTTPException(status_code=400, detail="No filename provided")
            
        allowed_ext = {'.wav', '.mp3', '.m4a', '.flac'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        print(f"[Upload] File extension: {file_ext}")
        if file_ext not in allowed_ext:
            print(f"[Upload] ERROR: Invalid file type: {file_ext}")
            raise HTTPException(status_code=400, detail=f"Invalid file type: {file_ext}. Allowed: {', '.join(allowed_ext)}")
        
        # Add to library
        print("[Upload] Adding to voice library...")
        voice_lib = get_voice_library()
        tag_list = [t.strip() for t in tags.split(',')] if tags else []
        voice_entry = voice_lib.add_voice(
            name=name or os.path.splitext(file.filename)[0],
            audio_bytes=audio_bytes,
            filename=file.filename,
            engine=engine,
            tags=tag_list
        )
        print(f"[Upload] SUCCESS: Voice added with ID {voice_entry.get('id')}")
        
        return {"success": True, "voice": voice_entry}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Upload] EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voices/{voice_id}/samples")
async def get_voice_samples(voice_id: str):
    """Get list of generated samples for a voice"""
    try:
        output_dir = Path("outputs/voice_tests")
        if not output_dir.exists():
            return {"samples": []}
            
        samples = []
        # Pattern: test_{voice_id}_{timestamp}.wav
        prefix = f"test_{voice_id}_"
        
        for file_path in output_dir.glob(f"{prefix}*.wav"):
            try:
                # Extract timestamp from filename
                # filename format: test_VOICEID_TIMESTAMP.wav
                # We need to handle the fact that VOICEID might contain underscores? 
                # Actually voice_id is UUID so no underscores usually, but let's be safe
                # The prefix includes the voice_id, so the rest is TIMESTAMP.wav
                ts_part = file_path.name[len(prefix):-4] # remove .wav
                timestamp = int(ts_part)
                
                samples.append({
                    "filename": file_path.name,
                    "url": f"/outputs/voice_tests/{file_path.name}",
                    "timestamp": timestamp,
                    "date": datetime.fromtimestamp(timestamp).isoformat()
                })
            except ValueError:
                continue # Skip files that don't match expected format
                
        # Sort by timestamp descending (newest first)
        samples.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return {"samples": samples}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voices")
async def list_voices(query: str = None, tags: str = None):
    """List all voices"""
    try:
        voice_lib = get_voice_library()
        tag_list = [t.strip() for t in tags.split(',')] if tags else None
        
        if query or tag_list:
            voices = voice_lib.search_voices(query=query or "", tags=tag_list)
        else:
            voices = voice_lib.get_all_voices()
        
        return {"voices": voices, "count": len(voices)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voices/{voice_id}")
async def get_voice_detail(voice_id: str):
    """Get voice details"""
    voice = get_voice_library().get_voice(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    return {"voice": voice}


@app.delete("/voices/{voice_id}")
async def delete_voice_endpoint(voice_id: str):
    """Delete voice"""
    if not get_voice_library().delete_voice(voice_id):
        raise HTTPException(status_code=404, detail="Voice not found")
    return {"success": True}


@app.put("/voices/{voice_id}")
async def update_voice_endpoint(voice_id: str, updates: Dict):
    """Update voice metadata"""
    voice = get_voice_library().update_voice(voice_id, updates)
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    return {"success": True, "voice": voice}


@app.post("/voices/{voice_id}/test")
async def test_voice_endpoint(voice_id: str, request: Dict):
    """Test voice with sample generation"""
    try:
        voice_lib = get_voice_library()
        voice = voice_lib.get_voice(voice_id)
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        
        text = request.get("text", "This is a test of voice cloning.")
        style = request.get("style")
        
        # Get voice details
        engine = voice.get("engine", "styletts2")
        reference_path = voice.get("reference_file")
        
        if not os.path.exists(reference_path):
            raise HTTPException(status_code=404, detail="Reference audio file not found")
        
        # Generate audio with voice cloning
        from src.core.voice_engine import get_voice_provider
        
        modal_url = os.getenv(f"{engine.upper()}_MODAL_URL")
        if not modal_url:
            raise HTTPException(status_code=500, detail=f"Modal URL not configured for {engine}")
        
        provider = get_voice_provider(engine, modal_url=modal_url)
        
        # Generate with cloning!
        audio_bytes = await provider.generate_audio(
            text=text,
            voice_id="default",
            style=style,
            reference_audio_path=reference_path  # Voice cloning!
        )
        
        # Save test audio
        test_dir = os.path.join("outputs", "voice_tests")
        os.makedirs(test_dir, exist_ok=True)
        test_filename = f"test_{voice_id}_{int(datetime.now().timestamp())}.wav"
        test_path = os.path.join(test_dir, test_filename)
        
        with open(test_path, 'wb') as f:
            f.write(audio_bytes)
        
        return {
            "success": True,
            "audio_url": f"/outputs/voice_tests/{test_filename}",
            "text": text,
            "voice_name": voice['name'],
            "engine": engine
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error testing voice: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
