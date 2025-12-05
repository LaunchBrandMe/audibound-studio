from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
from datetime import datetime
import os
import uuid
import json
import base64
import re
import io

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
from datetime import datetime
import time
from fastapi import UploadFile, File, Form
from pathlib import Path

app = FastAPI(title="Audibound Studio API")

# Mount static files
app.mount("/static", StaticFiles(directory="src/static"), name="static")
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")
app.mount("/references", StaticFiles(directory="references"), name="references")

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

class UpdateProjectRequest(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None

class ValidationSummary(BaseModel):
    score: int
    issues: List[str] = []
    warnings: List[str] = []
    clarifications: List[Dict] = []
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
    layers: Optional[List[str]] = None
    notes: Optional[List[str]] = None


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
    raw_text: Optional[str] = None
    created_at: Optional[str] = None


class CostTrackingEntry(BaseModel):
    id: str
    timestamp: str
    project_id: str
    project_title: str
    character_count: int
    word_count: int
    engines_used: Dict[str, str]  # character_name -> engine
    layers: List[str]
    estimated_cost: float
    estimated_breakdown: Dict[str, Any]
    actual_cost: Optional[float] = None
    actual_cost_updated_at: Optional[str] = None
    notes: str = ""


class LogCostRequest(BaseModel):
    project_id: str
    project_title: str
    character_count: int
    word_count: int
    engines_used: Dict[str, str]
    layers: List[str]
    estimated_cost: float
    estimated_breakdown: Dict[str, Any]


class UpdateActualCostRequest(BaseModel):
    actual_cost: float
    notes: str = ""



class SesamePlaygroundRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None


def _split_text_into_chunks(text: str) -> List[str]:
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = [s.strip() for s in sentences if s.strip()]
    if not chunks:
        return [text]
    return chunks

class ProduceAudioRequest(BaseModel):
    engine: str = "kokoro"
    include_voice: bool = True
    include_sfx: bool = True
    include_music: bool = True
    reuse_voice_cache: bool = True


class OverridesRequest(BaseModel):
    overrides: Dict[str, Dict[str, Optional[str]]]


@app.get("/projects")
async def list_projects():
    # 1. Load current DB
    if os.path.exists("projects_db.json"):
        with open("projects_db.json", "r") as f:
            projects_db = json.load(f)
    else:
        projects_db = {}
        
    # 2. Discover from disk
    projects_db, updates_made = _discover_projects_from_disk(projects_db)
    
    # 3. Save if updated
    if updates_made:
        with open("projects_db.json", "w") as f:
            json.dump(projects_db, f, indent=2, default=str)
            
    # 4. Return list
    project_list = []
    for p in projects_db.values():
        # Ensure project_id is present for frontend
        p_out = p.copy()
        if "id" in p_out and "project_id" not in p_out:
            p_out["project_id"] = p_out["id"]
        project_list.append(p_out)
        
    # Sort by created_at (newest first), fallback to title
    project_list.sort(key=lambda x: (x.get("created_at", ""), x.get("title", "")), reverse=True)
    
    return {"projects": project_list}

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
        last_engine=None,
        raw_text=new_project["raw_text"]
    )

@app.put("/projects/{project_id}")
async def update_project(project_id: str, request: UpdateProjectRequest):
    project = get_project_from_db(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if request.title is not None:
        project["title"] = request.title
    if request.text is not None:
        project["raw_text"] = request.text
        
    update_project_in_db(project_id, project)
    return {"message": "Project updated", "project_id": project_id}

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

    # --- Auto-scan for outputs on disk ---
    # This ensures that if the server restarted or DB was cleared, we recover state from disk
    project = _scan_and_update_project_outputs(project_id, project)

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
        render_history=history,
        voice_overrides=project.get("voice_overrides"),
        validation_summary=project.get("validation_summary"),
        raw_text=project.get("raw_text")
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
    
    include_voice = True if request is None else bool(request.include_voice)
    include_sfx = True if request is None else bool(request.include_sfx)
    include_music = True if request is None else bool(request.include_music)
    reuse_voice_cache = True if request is None else bool(request.reuse_voice_cache)

    # Update status to queued immediately to prevent UI race condition
    update_project_in_db(project_id, {"status": "queued"})

    # Dispatch to Celery
    task_produce_audio.delay(
        project_id,
        engine,
        include_voice=include_voice,
        include_sfx=include_sfx,
        include_music=include_music,
        reuse_voice_cache=reuse_voice_cache,
    )
    return {
        "message": "Production queued",
        "project_id": project_id,
        "engine": engine,
        "include_voice": include_voice,
        "include_sfx": include_sfx,
        "include_music": include_music,
        "reuse_voice_cache": reuse_voice_cache,
    }


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
    engine: str = Form("styletts2"),
    bio: Optional[str] = Form(None),
    gender: Optional[str] = Form("neutral"),
    profile_image: Optional[UploadFile] = File(None)
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

        # Handle profile image if provided
        profile_image_bytes = None
        if profile_image and profile_image.filename:
            profile_image_bytes = await profile_image.read()
            if len(profile_image_bytes) > 2 * 1024 * 1024:  # 2MB limit for images
                raise HTTPException(status_code=400, detail="Profile image too large (max 2MB)")

        # Add to library
        print("[Upload] Adding to voice library...")
        voice_lib = get_voice_library()
        tag_list = [t.strip() for t in tags.split(',')] if tags else []
        voice_entry = voice_lib.add_voice(
            name=name or os.path.splitext(file.filename)[0],
            audio_bytes=audio_bytes,
            filename=file.filename,
            engine=engine,
            tags=tag_list,
            bio=bio,
            gender=gender,
            profile_image=profile_image_bytes
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


@app.post("/voices/populate-defaults")
async def populate_default_voices():
    """Add all 14 default voices (10 Kokoro + 4 engine defaults) to the voice library"""
    from src.core.voice_mapper import VoiceMapper

    # Define all default voices with clean metadata
    DEFAULT_VOICES = [
        # Kokoro Voices
        {"id": "af_sarah", "name": "Sarah", "engine": "kokoro", "bio": "Young, energetic American female", "gender": "female", "tags": ["american", "female", "young", "energetic"]},
        {"id": "af_bella", "name": "Bella", "engine": "kokoro", "bio": "Warm, mature American female", "gender": "female", "tags": ["american", "female", "mature", "warm"]},
        {"id": "af_nicole", "name": "Nicole", "engine": "kokoro", "bio": "Neutral, professional American female", "gender": "female", "tags": ["american", "female", "professional", "neutral"]},
        {"id": "af_sky", "name": "Sky", "engine": "kokoro", "bio": "Bright, enthusiastic American female", "gender": "female", "tags": ["american", "female", "bright", "enthusiastic"]},
        {"id": "am_adam", "name": "Adam", "engine": "kokoro", "bio": "Mature, authoritative American male", "gender": "male", "tags": ["american", "male", "mature", "authoritative"]},
        {"id": "am_michael", "name": "Michael", "engine": "kokoro", "bio": "Strong, confident American male", "gender": "male", "tags": ["american", "male", "strong", "confident"]},
        {"id": "bf_emma", "name": "Emma", "engine": "kokoro", "bio": "Refined British female", "gender": "female", "tags": ["british", "female", "refined"]},
        {"id": "bf_isabella", "name": "Isabella", "engine": "kokoro", "bio": "Elegant British female", "gender": "female", "tags": ["british", "female", "elegant"]},
        {"id": "bm_george", "name": "George", "engine": "kokoro", "bio": "Distinguished British male", "gender": "male", "tags": ["british", "male", "distinguished"]},
        {"id": "bm_lewis", "name": "Lewis", "engine": "kokoro", "bio": "Warm British male", "gender": "male", "tags": ["british", "male", "warm"]},
        # Other Engine Defaults
        {"id": "default", "name": "StyleTTS2 Default", "engine": "styletts2", "bio": "Highly expressive with style control", "gender": "neutral", "tags": ["expressive", "versatile"]},
        {"id": "default", "name": "Sesame Default", "engine": "sesame", "bio": "Expressive neutral voice", "gender": "neutral", "tags": ["expressive", "neutral"]},
        {"id": "default", "name": "IndexTTS2 Default", "engine": "indextts2", "bio": "Emotion vector control (8 emotions)", "gender": "neutral", "tags": ["emotional", "versatile"]},
        {"id": "default", "name": "Dia Default", "engine": "dia", "bio": "Expressive multi-speaker", "gender": "neutral", "tags": ["expressive", "multi-speaker"]},
    ]

    voice_lib = get_voice_library()
    added = []
    skipped = []

    # Create a minimal 1-second silence WAV file for reference (required by library)
    import wave
    import struct
    os.makedirs("references/default_samples", exist_ok=True)
    silence_path = "references/default_samples/silence_1s.wav"
    if not os.path.exists(silence_path):
        with wave.open(silence_path, 'w') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            silence_data = struct.pack('<' + ('h' * 24000), *([0] * 24000))
            wav_file.writeframes(silence_data)

    with open(silence_path, 'rb') as f:
        silence_bytes = f.read()

    for voice_data in DEFAULT_VOICES:
        voice_id_internal = voice_data["id"]
        engine = voice_data["engine"]
        name = voice_data["name"]

        # Check if this default voice already exists
        existing = None
        for v in voice_lib.get_all_voices():
            if (v.get("metadata", {}).get("is_default") and
                v.get("engine") == engine and
                v.get("metadata", {}).get("default_voice_id") == voice_id_internal):
                existing = v
                break

        if existing:
            skipped.append(name)
            continue

        try:
            # Add to library (using silence as placeholder reference)
            voice_lib.add_voice(
                name=name,
                audio_bytes=silence_bytes,
                filename=f"{engine}_{voice_id_internal}_ref.wav",
                engine=engine,
                tags=voice_data["tags"],
                metadata={
                    "is_default": True,
                    "default_voice_id": voice_id_internal
                },
                bio=voice_data["bio"],
                gender=voice_data["gender"]
            )
            added.append(name)
        except Exception as e:
            print(f"Error adding {name}: {e}")

    return {
        "success": True,
        "added": added,
        "skipped": skipped,
        "total_voices": len(voice_lib.get_all_voices())
    }


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


@app.post("/voices/{voice_id}/stream")
async def stream_voice_chunks(voice_id: str, request: Dict):
    text = (request or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    voice_lib = get_voice_library()
    voice = voice_lib.get_voice(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    engine = voice.get("engine", "styletts2")
    modal_url = os.getenv(f"{engine.upper()}_MODAL_URL")
    if engine in {"kokoro", "styletts2", "indextts2", "sesame"} and not modal_url:
        raise HTTPException(status_code=500, detail=f"Modal URL not configured for {engine}")

    provider = get_voice_provider(engine, modal_url=modal_url)
    reference_path = voice.get("reference_file")

    chunks = _split_text_into_chunks(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="Unable to split text into chunks")

    async def event_generator():
        for idx, chunk in enumerate(chunks):
            try:
                audio_bytes = await provider.generate_audio(
                    text=chunk,
                    voice_id="default",
                    style=None,
                    reference_audio_path=reference_path
                )
                encoded = base64.b64encode(audio_bytes).decode('ascii')
                payload = {
                    "index": idx,
                    "text": chunk,
                    "audio": encoded,
                    "mime": "audio/wav"
                }
                yield f"event: chunk\ndata: {json.dumps(payload)}\n\n"
            except Exception as exc:
                error_payload = {"index": idx, "error": str(exc)}
                yield f"event: error\ndata: {json.dumps(error_payload)}\n\n"
                break
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/playground/sesame")
async def sesame_playground_page():
    return FileResponse("src/static/sesame_playground.html")


@app.post("/playground/sesame/generate")
async def sesame_playground_generate(request: SesamePlaygroundRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    reference_path = None
    if request.voice_id:
        voice = get_voice_library().get_voice(request.voice_id)
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        reference_path = voice.get("reference_file")

    modal_url = os.getenv("SESAME_MODAL_URL")
    if not modal_url:
        raise HTTPException(status_code=500, detail="SESAME_MODAL_URL not configured")

    provider = get_voice_provider("sesame", modal_url=modal_url)

    try:
        audio_bytes = await provider.generate_audio(
            text=text,
            voice_id="default",
            style=None,
            reference_audio_path=reference_path
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=sesame_playground.wav"}
    )


@app.get("/playground/dia")
async def dia_playground_page():
    return FileResponse("src/static/dia_playground.html")


@app.post("/playground/dia/generate")
async def dia_playground_generate(request: SesamePlaygroundRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    reference_path = None
    if request.voice_id:
        voice = get_voice_library().get_voice(request.voice_id)
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        reference_path = voice.get("reference_file")

    modal_url = os.getenv("DIA_MODAL_URL")
    if not modal_url:
        raise HTTPException(status_code=500, detail="DIA_MODAL_URL not configured")

    provider = get_voice_provider("dia", modal_url=modal_url)

    try:
        audio_bytes = await provider.generate_audio(
            text=text,
            voice_id="default",
            reference_audio_path=reference_path
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=dia_playground.wav"}
    )


@app.post("/playground/kokoro/generate")
async def kokoro_playground_generate(request: SesamePlaygroundRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    reference_path = None
    if request.voice_id:
        voice = get_voice_library().get_voice(request.voice_id)
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        reference_path = voice.get("reference_file")

    modal_url = os.getenv("KOKORO_MODAL_URL")
    if not modal_url:
        raise HTTPException(status_code=500, detail="KOKORO_MODAL_URL not configured")

    provider = get_voice_provider("kokoro", modal_url=modal_url)

    try:
        audio_bytes = await provider.generate_audio(
            text=text,
            voice_id=request.voice_id or "default",
            style=None,
            reference_audio_path=reference_path
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=kokoro_playground.wav"}
    )


@app.post("/playground/styletts2/generate")
async def styletts2_playground_generate(request: SesamePlaygroundRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    reference_path = None
    if request.voice_id:
        voice = get_voice_library().get_voice(request.voice_id)
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        reference_path = voice.get("reference_file")

    modal_url = os.getenv("STYLETTS2_MODAL_URL")
    if not modal_url:
        raise HTTPException(status_code=500, detail="STYLETTS2_MODAL_URL not configured")

    provider = get_voice_provider("styletts2", modal_url=modal_url)

    try:
        audio_bytes = await provider.generate_audio(
            text=text,
            voice_id="default",
            style=None,
            reference_audio_path=reference_path
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=styletts2_playground.wav"}
    )


@app.post("/playground/indextts2/generate")
async def indextts2_playground_generate(request: SesamePlaygroundRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    reference_path = None
    if request.voice_id:
        voice = get_voice_library().get_voice(request.voice_id)
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        reference_path = voice.get("reference_file")

    modal_url = os.getenv("INDEXTTS2_MODAL_URL")
    if not modal_url:
        raise HTTPException(status_code=500, detail="INDEXTTS2_MODAL_URL not configured")

    provider = get_voice_provider("indextts2", modal_url=modal_url)

    try:
        audio_bytes = await provider.generate_audio(
            text=text,
            voice_id=request.voice_id or "default",
            style=None,
            reference_audio_path=reference_path
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(audio_bytes),
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=indextts2_playground.wav"}
    )


# --- SFX & Music Playground ---

class SfxPlaygroundRequest(BaseModel):
    description: str
    duration: float = 5.0

class MusicPlaygroundRequest(BaseModel):
    style_description: str
    duration: float = 10.0

@app.get("/playground/sfx")
async def sfx_playground_page():
    return FileResponse("src/static/sfx_playground.html")

@app.post("/playground/sfx/generate")
async def sfx_playground_generate(request: SfxPlaygroundRequest):
    from src.core.sfx_engine import get_sfx_provider
    
    description = request.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Description is required")

    # Use AudioGen provider
    try:
        provider = get_sfx_provider("audiogen", duration=request.duration)
        file_path = await provider.get_sfx(description, "playground")
        
        # Save to permanent location for history
        history_dir = os.path.join("outputs", "playground_history", "sfx")
        os.makedirs(history_dir, exist_ok=True)
        timestamp = int(time.time())
        filename = f"sfx_{timestamp}.wav"
        permanent_path = os.path.join(history_dir, filename)
        
        with open(file_path, "rb") as f:
            audio_bytes = f.read()
        
        with open(permanent_path, "wb") as f:
            f.write(audio_bytes)
            
        # Clean up temp file
        os.unlink(file_path)
        
        # Save to history JSON
        _save_playground_history("sfx", {
            "description": description,
            "duration": request.duration,
            "timestamp": datetime.now().isoformat(),
            "file_path": f"/outputs/playground_history/sfx/{filename}"
        })
        
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as exc:
        print(f"SFX Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/playground/music/generate")
async def music_playground_generate(request: MusicPlaygroundRequest):
    from src.core.music_engine import get_music_provider
    
    description = request.style_description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Style description is required")

    # Use MusicGen provider
    try:
        provider = get_music_provider("musicgen")
        file_path = await provider.get_music(description, request.duration)
        
        # Save to permanent location for history
        history_dir = os.path.join("outputs", "playground_history", "music")
        os.makedirs(history_dir, exist_ok=True)
        timestamp = int(time.time())
        filename = f"music_{timestamp}.wav"
        permanent_path = os.path.join(history_dir, filename)
        
        with open(file_path, "rb") as f:
            audio_bytes = f.read()
        
        with open(permanent_path, "wb") as f:
            f.write(audio_bytes)
            
        # Clean up temp file
        os.unlink(file_path)
        
        # Save to history JSON
        _save_playground_history("music", {
            "description": description,
            "duration": request.duration,
            "timestamp": datetime.now().isoformat(),
            "file_path": f"/outputs/playground_history/music/{filename}"
        })
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as exc:
        print(f"Music Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# --- Cost Tracking Endpoints ---

@app.post("/api/cost-tracking/log")
async def log_cost_estimate(request: LogCostRequest):
    """Log a production request with estimated costs"""
    data = _load_cost_tracking_data()
    
    # Generate unique ID
    prod_id = f"prod_{int(time.time() * 1000)}"
    
    # Create entry
    entry = CostTrackingEntry(
        id=prod_id,
        timestamp=datetime.utcnow().isoformat() + "Z",
        project_id=request.project_id,
        project_title=request.project_title,
        character_count=request.character_count,
        word_count=request.word_count,
        engines_used=request.engines_used,
        layers=request.layers,
        estimated_cost=request.estimated_cost,
        estimated_breakdown=request.estimated_breakdown
    )
    
    # Add to data
    data["productions"].append(entry.dict())
    
    # Save
    _save_cost_tracking_data(data)
    
    return {"success": True, "production_id": prod_id}


@app.get("/api/cost-tracking")
async def get_cost_tracking():
    """Get all cost tracking data"""
    data = _load_cost_tracking_data()
    return data


@app.put("/api/cost-tracking/{production_id}/actual-cost")
async def update_actual_cost(production_id: str, request: UpdateActualCostRequest):
    """Update actual cost for a production"""
    data = _load_cost_tracking_data()
    
    # Find the production
    for prod in data["productions"]:
        if prod["id"] == production_id:
            prod["actual_cost"] = request.actual_cost
            prod["actual_cost_updated_at"] = datetime.utcnow().isoformat() + "Z"
            prod["notes"] = request.notes
            _save_cost_tracking_data(data)
            return {"success": True}
    
    raise HTTPException(status_code=404, detail="Production not found")





def _scan_and_update_project_outputs(project_id: str, project_data: Dict) -> Dict:
    """
    Scans the project output directory for:
    1. abml.json -> to restore bible and manifest if missing
    2. *.m4b -> to populate render_history
    """
    output_dir = os.path.join("outputs", project_id)
    if not os.path.exists(output_dir):
        return project_data

    updates = {}
    has_updates = False
    
    # 1. Check for ABML (restore direction)
    abml_path = os.path.join(output_dir, "abml.json")
    if os.path.exists(abml_path) and (not project_data.get("manifest") or not project_data.get("bible")):
        try:
            print(f"[Scan] Found abml.json for {project_id}, restoring manifest...")
            with open(abml_path, 'r') as f:
                manifest_data = json.load(f)
            
            updates["manifest"] = manifest_data
            if "bible" in manifest_data:
                updates["bible"] = manifest_data["bible"]
            
            if project_data.get("status") == "created":
                updates["status"] = "directed"
            
            has_updates = True
        except Exception as e:
            print(f"[Scan] Failed to load abml.json: {e}")

    # 2. Check for Render History
    existing_history = project_data.get("render_history") or []
    existing_paths = {entry.get("output_path") for entry in existing_history if entry.get("output_path")}
    
    new_entries = []
    
    for filename in os.listdir(output_dir):
        if not filename.endswith(".m4b"):
            continue
            
        # Use relative path as expected by frontend/API
        rel_path = os.path.join("outputs", project_id, filename)
        
        if rel_path in existing_paths:
            continue
            
        file_path = os.path.join(output_dir, filename)
        try:
            mod_time = os.path.getmtime(file_path)
            timestamp = datetime.fromtimestamp(mod_time).isoformat() + 'Z'
            
            # Parse filename for layers
            layers = []
            
            # Pattern: ..._layer1_layer2__XX.m4b
            match = re.search(r"(_([a-z_]+))?__\d+\.m4b$", filename)
            if match and match.group(2):
                layers_part = match.group(2) # "voice_sfx"
                possible_layers = layers_part.split('_')
                valid_layers = {'voice', 'sfx', 'music'}
                parsed_layers = [l for l in possible_layers if l in valid_layers]
                if parsed_layers:
                    layers = parsed_layers
            
            if not layers:
                layers = ["voice"] # Default assumption
                
            new_entries.append({
                "timestamp": timestamp,
                "engine": "Detected",
                "output_path": rel_path,
                "layers": layers,
                "notes": ["Detected from disk"]
            })
        except Exception as e:
            print(f"[Scan] Error processing file {filename}: {e}")
    
    if new_entries:
        print(f"[Scan] Found {len(new_entries)} new render history entries")
        new_entries.sort(key=lambda x: x["timestamp"])
        updated_history = existing_history + new_entries
        updated_history.sort(key=lambda x: x["timestamp"], reverse=True)
        updates["render_history"] = updated_history
        
        # Update last_engine/output_path if we found new files and current ones are empty
        if new_entries and not project_data.get("output_path"):
            latest = new_entries[-1]
            updates["output_path"] = latest["output_path"]
            updates["status"] = "produced"
            
        has_updates = True

    if has_updates:
        # Apply updates to local dict
        project_data.update(updates)
        # Persist to DB
        update_project_in_db(project_id, project_data)
        
    return project_data


# ---- Cost Tracking Helpers ----

COST_TRACKING_FILE = "cost_tracking.json"

def _load_cost_tracking_data():
    """Load cost tracking data from JSON file"""
    if not os.path.exists(COST_TRACKING_FILE):
        return {"productions": [], "last_updated": datetime.utcnow().isoformat() + "Z"}
    
    try:
        with open(COST_TRACKING_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading cost tracking data: {e}")
        return {"productions": [], "last_updated": datetime.utcnow().isoformat() + "Z"}


def _save_cost_tracking_data(data):
    """Save cost tracking data to JSON file"""
    data["last_updated"] = datetime.utcnow().isoformat() + "Z"
    try:
        with open(COST_TRACKING_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving cost tracking data: {e}")



def _discover_projects_from_disk(current_db: Dict) -> tuple[Dict, bool]:
    """
    Scans outputs/ directory for folders containing abml.json.
    If a folder exists but is not in current_db, adds it.
    Also ensures all projects have a 'created_at' timestamp (inferred from disk if missing).
    Returns (updated_db, updates_made).
    """
    outputs_dir = "outputs"
    if not os.path.exists(outputs_dir):
        return current_db, False
        
    updates_made = False
    
    # 1. Scan for new projects and update timestamps for all
    for project_id in os.listdir(outputs_dir):
        project_path = os.path.join(outputs_dir, project_id)
        if not os.path.isdir(project_path):
            continue
            
        # Skip system folders
        if project_id in ["cache", "playground_history", "voice_tests", "voice_cloning_tests"]:
            continue
            
        # Get timestamp from most recent audio file (m4b/mp3), fallback to folder modification time
        # Also determine if project has been produced
        has_audio = False
        try:
            # Find all audio files in this project
            audio_files = [os.path.join(project_path, f) for f in os.listdir(project_path) 
                          if f.endswith(('.m4b', '.mp3'))]
            if audio_files:
                # Use the most recent audio file's modification time
                latest_audio = max(audio_files, key=lambda f: os.path.getmtime(f))
                mod_time = os.path.getmtime(latest_audio)
                has_audio = True
            else:
                # Fallback to folder modification time
                mod_time = os.path.getmtime(project_path)
            created_at = datetime.fromtimestamp(mod_time).isoformat()
        except:
            created_at = datetime.utcnow().isoformat()

        # Check if already in DB
        if project_id in current_db:
            # Update created_at if missing
            if "created_at" not in current_db[project_id]:
                current_db[project_id]["created_at"] = created_at
                updates_made = True
            # Update status based on audio file presence
            if has_audio and current_db[project_id].get("status") == "directed":
                current_db[project_id]["status"] = "produced"
                updates_made = True
            continue
            
        # Check for abml.json
        abml_path = os.path.join(project_path, "abml.json")
        if os.path.exists(abml_path):
            try:
                print(f"[Discovery] Found new project on disk: {project_id}")
                with open(abml_path, 'r') as f:
                    manifest = json.load(f)
                
                # Create project entry
                # We try to reconstruct as much as possible
                title = manifest.get("title", project_id)
                
                # Try to find raw text if possible, otherwise empty
                # (In a real app we might save raw text to a file too)
                raw_text = ""
                
                # Set status based on whether audio files exist
                status = "produced" if has_audio else "directed"
                
                new_project = {
                    "id": project_id,
                    "title": title,
                    "status": status,
                    "manifest": manifest,
                    "bible": manifest.get("bible"),
                    "voice_overrides": {}, 
                    "render_history": [], # Will be populated by _scan_and_update_project_outputs later
                    "raw_text": raw_text,
                    "created_at": created_at
                }
                
                current_db[project_id] = new_project
                updates_made = True
            except Exception as e:
                print(f"[Discovery] Failed to import {project_id}: {e}")
                
    return current_db, updates_made


def _save_playground_history(category: str, entry: dict):
    """Save playground generation to history JSON file."""
    history_file = os.path.join("outputs", "playground_history", f"{category}_history.json")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                history = json.load(f)
        except Exception as e:
            print(f"Error reading history: {e}")
    
    history.insert(0, entry)
    history = history[:50]  # Keep last 50
    
    try:
        with open(history_file, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Error saving history: {e}")

@app.get("/playground/history/{category}")
async def get_playground_history(category: str):
    """Get playground history for SFX or Music."""
    if category not in ["sfx", "music"]:
        raise HTTPException(status_code=400, detail="Invalid category")
    
    history_file = os.path.join("outputs", "playground_history", f"{category}_history.json")
    if not os.path.exists(history_file):
        return {"history": []}
    
    try:
        with open(history_file, "r") as f:
            history = json.load(f)
        return {"history": history[:10]}  # Return last 10
    except Exception as e:
        print(f"Error reading history: {e}")
        return {"history": []}


# --- Outputs listing for UI ---

def _list_outputs_from_disk(limit: int = 50):
    """Scan outputs directory for .m4b files and return recent ones."""
    results = []
    for root, dirs, files in os.walk("outputs"):
        for fname in files:
            if not fname.lower().endswith(".m4b"):
                continue
            fpath = os.path.join(root, fname)
            try:
                stat = os.stat(fpath)
                results.append({
                    "name": fname,
                    "path": fpath,
                    "url": "/" + os.path.relpath(fpath, "."),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "size_kb": int(stat.st_size / 1024)
                })
            except FileNotFoundError:
                continue
    results.sort(key=lambda x: x["modified"], reverse=True)
    return results[:limit]


def _list_tracks_grouped(limit: int = 200):
    """Return grouped tracks for multitrack player."""
    produced = []
    playground_sfx = []
    playground_music = []
    voice_tests = []

    for root, dirs, files in os.walk("outputs"):
        # Skip cache/temp
        parts = root.split(os.sep)
        if "cache" in parts or "temp" in parts:
            continue
        for fname in files:
            lower = fname.lower()
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, ".")
            url = "/" + rel
            try:
                stat = os.stat(fpath)
                meta = {
                    "name": fname,
                    "path": fpath,
                    "url": url,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "size_kb": int(stat.st_size / 1024)
                }
            except FileNotFoundError:
                continue

            if lower.endswith(".m4b"):
                # exclude playground and tests
                if "playground_history" in parts:
                    continue
                if "voice_tests" in parts:
                    continue
                produced.append(meta)
            elif lower.endswith(".wav"):
                if "playground_history" in parts and "sfx" in parts:
                    playground_sfx.append(meta)
                elif "playground_history" in parts and "music" in parts:
                    playground_music.append(meta)
                elif "voice_tests" in parts:
                    voice_tests.append(meta)

    produced.sort(key=lambda x: x["modified"], reverse=True)
    playground_sfx.sort(key=lambda x: x["modified"], reverse=True)
    playground_music.sort(key=lambda x: x["modified"], reverse=True)
    voice_tests.sort(key=lambda x: x["modified"], reverse=True)

    return {
        "produced": produced[:limit],
        "playground_sfx": playground_sfx[:limit],
        "playground_music": playground_music[:limit],
        "voice_tests": voice_tests[:limit],
    }

@app.get("/outputs/tracks")
async def list_tracks():
    return _list_tracks_grouped()

@app.get("/stories")
async def list_stories():
    """
    List 'Stories' found in outputs directory.
    Shows ALL audio files in each project folder.
    """
    stories = []
    outputs_dir = "outputs"
    
    if not os.path.exists(outputs_dir):
        return {"stories": []}

    for project_id in os.listdir(outputs_dir):
        project_path = os.path.join(outputs_dir, project_id)
        if not os.path.isdir(project_path) or project_id in ["cache", "playground_history", "voice_tests", "voice_cloning_tests"]:
            continue
            
        # Collect ALL audio files (mp3, wav, m4b)
        audio_files = {}
        for file in os.listdir(project_path):
            if file.lower().endswith(('.mp3', '.wav', '.m4b')):
                file_path = os.path.join(project_path, file)
                file_url = f"/outputs/{project_id}/{file}"
                
                # Categorize by filename
                if 'narration' in file.lower() or 'voice' in file.lower():
                    audio_files.setdefault('voice', []).append({"name": file, "url": file_url})
                elif 'music' in file.lower():
                    audio_files.setdefault('music', []).append({"name": file, "url": file_url})
                elif 'sfx' in file.lower():
                    audio_files.setdefault('sfx', []).append({"name": file, "url": file_url})
                elif file.lower().endswith('.m4b'):
                    audio_files.setdefault('mix', []).append({"name": file, "url": file_url})
                else:
                    # Uncategorized audio
                    audio_files.setdefault('other', []).append({"name": file, "url": file_url})
        
        if not audio_files:
            continue
            
        # Get metadata
        title = project_id # Default
        timestamp = os.path.getmtime(project_path)
        
        # Try to read abml.json for title
        abml_path = os.path.join(project_path, "abml.json")
        if os.path.exists(abml_path):
            try:
                with open(abml_path, 'r') as f:
                    data = json.load(f)
                    title = data.get("title", title)
            except:
                pass
        elif audio_files.get('mix'):
            # Try to use filename as title
            title = os.path.splitext(audio_files['mix'][0]['name'])[0].split("__")[0].replace("_", " ")

        stories.append({
            "id": project_id,
            "title": title,
            "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
            "audio_files": audio_files
        })
    
    # Sort by newest
    stories.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"stories": stories}


@app.get("/outputs/list")
async def list_outputs(limit: int = 50):
    try:
        limit = max(1, min(int(limit), 200))
    except Exception:
        limit = 50
    return {"outputs": _list_outputs_from_disk(limit)}


@app.get("/outputs/tracks")
async def list_tracks(limit: int = 200):
    try:
        limit = max(1, min(int(limit), 500))
    except Exception:
        limit = 200
    return _list_tracks_grouped(limit)
