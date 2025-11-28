import os
from celery import Celery
from src.core.director import ScriptDirector
from src.core.abml import ScriptManifest, SeriesBible
from src.core.voice_engine import get_voice_provider
from src.core.assembly import AudioAssembler
from src.core.music_engine import get_music_provider
from src.core.sfx_engine import get_sfx_provider
from src.core.text_cleaner import clean_text_if_needed
import asyncio
import json
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# Configure Celery
# In production, use env vars for broker URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MODAL_URL = os.getenv("MODAL_URL")
STYLETTS2_MODAL_URL = os.getenv("STYLETTS2_MODAL_URL")
INDEXTTS2_MODAL_URL = os.getenv("INDEXTTS2_MODAL_URL")
SESAME_MODAL_URL = os.getenv("SESAME_MODAL_URL")
print(f"[Worker] Loaded configuration - REDIS_URL: {REDIS_URL}, MODAL_URL: {MODAL_URL}")
celery_app = Celery("audibound_worker", broker=REDIS_URL, backend=REDIS_URL)

# We need a way to share state with the API for MVP (since we used in-memory dict 'projects')
# In a real app, both API and Worker would talk to a Postgres DB.
# For this MVP, we will simulate DB access by reading/writing to a JSON file on disk.
DB_FILE = "projects_db.json"

def get_project_from_db(project_id: str):
    if not os.path.exists(DB_FILE):
        return None
    with open(DB_FILE, 'r') as f:
        data = json.load(f)
    return data.get(project_id)

def update_project_in_db(project_id: str, update_data: dict):
    data = {}
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            data = json.load(f)
    
    if project_id in data:
        data[project_id].update(update_data)
    else:
        data[project_id] = update_data
        
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)

def _resolve_modal_url(engine: str) -> str | None:
    mapping = {
        "kokoro": MODAL_URL,
        "styletts2": STYLETTS2_MODAL_URL,
        "indextts2": INDEXTTS2_MODAL_URL,
        "sesame": SESAME_MODAL_URL,
    }
    return mapping.get(engine, MODAL_URL)

@celery_app.task(name="tasks.direct_script")
def task_direct_script(project_id: str):
    print(f"[Worker] Starting direction for {project_id}...")
    project = get_project_from_db(project_id)
    if not project:
        print(f"Project {project_id} not found in DB")
        return

    director = ScriptDirector() 
    
    # 1. Create Bible
    print("[Worker] Generating Series Bible...")
    bible = director.create_series_bible(project["raw_text"], project["title"])
    
    # 2. Direct Scenes (Limit text for MVP)
    print("[Worker] Directing Scene...")
    scene, validation_result = director.direct_scene(project["raw_text"][:10000], bible)
    
    # 3. Create Manifest
    manifest = ScriptManifest(
        project_id=project_id,
        title=project["title"],
        bible=bible,
        scenes=[scene]
    )

    validation_summary = {
        "score": validation_result.score,
        "issues": validation_result.issues,
        "warnings": validation_result.warnings,
        "timestamp": datetime.utcnow().isoformat() + 'Z'
    }
    
    # Update DB
    update_project_in_db(project_id, {
        "bible": bible.model_dump(),
        "manifest": manifest.model_dump(),
        "status": "directed",
        "validation_summary": validation_summary
    })
    print(f"[Worker] Direction complete for {project_id}")

@celery_app.task(name="tasks.produce_audio")
def task_produce_audio(project_id: str, voice_engine: str = "kokoro"):
    # Since Celery is synchronous by default, we run the async code via asyncio.run
    asyncio.run(run_production_pipeline_async(project_id, voice_engine))

async def run_production_pipeline_async(project_id: str, voice_engine: str = "kokoro"):
    print(f"[Worker] Starting production for {project_id} using {voice_engine}...")
    project = get_project_from_db(project_id)
    if not project or not project.get("manifest"):
        print("Invalid project state")
        return

    voice_overrides = project.get("voice_overrides") or {}

    engine_base = voice_engine
    single_voice_mode = False
    if voice_engine == "kokoro_single":
        engine_base = "kokoro"
        single_voice_mode = True
    elif voice_engine == "kokoro_multi":
        engine_base = "kokoro"

    # Reconstruct Manifest object from dict
    manifest = ScriptManifest(**project["manifest"])
    
    # Get voice overrides (this is the primary source of truth for voice assignments)
    voice_overrides = project.get("voice_overrides") or {}
    
    # Initialize voice mapper ONLY to get default assignments for characters without overrides
    from src.core.voice_mapper import VoiceMapper
    voice_mapper = VoiceMapper(manifest.bible)
    
    # Build final voice map: use overrides first, fall back to VoiceMapper defaults
    final_voice_map = {}
    for character in manifest.bible.characters:
        char_name = character.name
        if char_name in voice_overrides and voice_overrides[char_name].get("voice"):
            # Use override
            final_voice_map[char_name] = voice_overrides[char_name]["voice"]
        else:
            # Use VoiceMapper default
            final_voice_map[char_name] = voice_mapper.get_voice_for_speaker(char_name)
    
    # Always include Narrator
    if "Narrator" in voice_overrides and voice_overrides["Narrator"].get("voice"):
        final_voice_map["Narrator"] = voice_overrides["Narrator"]["voice"]
    else:
        final_voice_map["Narrator"] = voice_mapper.get_voice_for_speaker("Narrator")
    
    print(f"[Worker] Voice mappings: {final_voice_map}")
    
    output_dir = os.path.join("outputs", project_id)
    temp_dir = os.path.join(output_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Cache for providers (keyed by engine name)
    provider_cache = {}
    
    modal_url = _resolve_modal_url(engine_base)
    if engine_base in {"kokoro", "styletts2", "indextts2", "sesame"} and not modal_url:
        raise RuntimeError(f"Missing Modal URL for engine '{engine_base}'")
    provider_kwargs = {}
    if modal_url:
        provider_kwargs['modal_url'] = modal_url
    voice_provider = get_voice_provider(engine_base, **provider_kwargs)  # Default provider
    provider_cache[engine_base] = voice_provider
    assembler = AudioAssembler(output_dir)
    
    # 1. Generate Audio
    print("[Worker] Generating Voice Clips...")
    print(f"[Worker] Manifest has {len(manifest.scenes)} scenes.")
    for i, scene in enumerate(manifest.scenes):
        print(f"[Worker] Scene {i} has {len(scene.blocks)} blocks.")
        for j, block in enumerate(scene.blocks):
            print(f"[Worker] Checking Block {j} (ID: {block.id}): Narration={block.narration is not None}")
            if block.narration:
                try:
                    # Use final voice map to get character-specific voice
                    speaker_name = block.narration.speaker
                    voice_id = block.narration.voice_id or final_voice_map.get(speaker_name, "kokoro:af_nicole")

                    if single_voice_mode and speaker_name != "Narrator":
                        voice_id = final_voice_map.get("Narrator", "kokoro:af_nicole")

                    # Parse engine-prefixed voice ID (e.g., "kokoro:af_bella" or "styletts2:default")
                    if ":" in voice_id:
                        voice_engine_override, voice_id_parsed = voice_id.split(":", 1)
                    else:
                        # Backward compatibility: assume kokoro if no prefix
                        voice_engine_override = "kokoro"
                        voice_id_parsed = voice_id
                    
                    print(f"[Worker] Block {block.id}: Speaker '{speaker_name}' → Voice '{voice_id}' (engine: {voice_engine_override})")

                    # Get or create provider for this engine
                    if voice_engine_override not in provider_cache:
                        engine_modal_url = _resolve_modal_url(voice_engine_override)
                        if voice_engine_override in {"kokoro", "styletts2", "indextts2", "sesame"} and not engine_modal_url:
                            raise RuntimeError(f"Missing Modal URL for engine '{voice_engine_override}'")
                        engine_kwargs = {}
                        if engine_modal_url:
                            engine_kwargs['modal_url'] = engine_modal_url
                        provider_cache[voice_engine_override] = get_voice_provider(voice_engine_override, **engine_kwargs)
                    
                    current_provider = provider_cache[voice_engine_override]

                    # Get style from block or overrides (Narrator CAN now have styles!)
                    style = block.narration.style
                    override = voice_overrides.get(speaker_name, {})
                    if override.get("style"):
                        style = override["style"]
                    
                    raw_text = block.narration.text
                    
                    # Clean text to remove stage directions
                    cleaned_text, was_modified = clean_text_if_needed(raw_text, is_dialogue=True)
                    
                    if was_modified:
                        print(f"[Worker] Block {block.id}: Text was cleaned")
                    
                    if style:
                        print(f"[Worker] Generating block {block.id} with style: '{style}'")
                    
                    audio_bytes = await current_provider.generate_audio(
                        text=cleaned_text,  # Use cleaned text
                        voice_id=voice_id_parsed,  # Use parsed voice ID (without engine prefix)
                        style=style  # Pass style for expressive generation
                    )
                    filename = f"{block.id}_narration.wav"
                    filepath = os.path.join(temp_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(audio_bytes)
                    file_size = os.path.getsize(filepath)
                    print(f"[Worker] Saved {filename}: {file_size} bytes")
                    block.narration.file_path = filepath
                except Exception as e:
                    print(f"Error generating block {block.id}: {e}")

    # 2. Stitch
    print("[Worker] Stitching...")
    all_blocks = [b for s in manifest.scenes for b in s.blocks]
    narration_path = assembler.stitch_voice_track(all_blocks, temp_dir)
    
    # 3. Mix
    print("[Worker] Mixing...")
    final_m4b = assembler.mix_stems_to_m4b(
        narration_path=narration_path,
        music_path=None, 
        sfx_path=None,
        manifest=manifest,
        engine_tag=voice_engine
    )
    
    history = list(project.get("render_history") or [])
    history.append({
        "timestamp": datetime.utcnow().isoformat() + 'Z',
        "engine": voice_engine,
        "output_path": final_m4b
    })

    update_project_in_db(project_id, {
        "status": "produced",
        "output_path": final_m4b,
        "last_engine": voice_engine,
        "render_history": history[-20:]
    })
    print(f"[Worker] Production complete: {final_m4b}")


def persist_voice_overrides(project_id: str, overrides: dict):
    project = get_project_from_db(project_id)
    if not project:
        return False
    project['voice_overrides'] = overrides
    update_project_in_db(project_id, project)
    return True
