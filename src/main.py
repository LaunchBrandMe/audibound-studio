from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, Dict, List
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
