import os
from celery import Celery
from src.core.director import ScriptDirector
from src.core.abml import ScriptManifest, SeriesBible
from src.core.voice_engine import get_voice_provider
from src.core.assembly import AudioAssembler
from src.core.music_engine import get_music_provider
from src.core.sfx_engine import get_sfx_provider
import asyncio
import json

from dotenv import load_dotenv

load_dotenv()

# Configure Celery
# In production, use env vars for broker URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MODAL_URL = os.getenv("MODAL_URL")
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
    scene = director.direct_scene(project["raw_text"][:10000], bible)
    
    # 3. Create Manifest
    manifest = ScriptManifest(
        project_id=project_id,
        title=project["title"],
        bible=bible,
        scenes=[scene]
    )
    
    # Update DB
    update_project_in_db(project_id, {
        "bible": bible.model_dump(),
        "manifest": manifest.model_dump(),
        "status": "directed"
    })
    print(f"[Worker] Direction complete for {project_id}")

@celery_app.task(name="tasks.produce_audio")
def task_produce_audio(project_id: str):
    # Since Celery is synchronous by default, we run the async code via asyncio.run
    asyncio.run(run_production_pipeline_async(project_id))

async def run_production_pipeline_async(project_id: str):
    print(f"[Worker] Starting production for {project_id}...")
    project = get_project_from_db(project_id)
    if not project or not project.get("manifest"):
        print("Invalid project state")
        return

    # Reconstruct Manifest object from dict
    manifest = ScriptManifest(**project["manifest"])
    
    output_dir = os.path.join("outputs", project_id)
    temp_dir = os.path.join(output_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    voice_provider = get_voice_provider("kokoro", modal_url=os.getenv("MODAL_URL"))  # Switched back to Kokoro with simplified Modal app 
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
                    voice_id = block.narration.voice_id or "af"
                    style = block.narration.style  # Extract emotional style from ABML
                    
                    if style:
                        print(f"[Worker] Generating block {block.id} with style: '{style}'")
                    
                    audio_bytes = await voice_provider.generate_audio(
                        text=block.narration.text,
                        voice_id=voice_id,
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
        manifest=manifest
    )
    
    update_project_in_db(project_id, {
        "status": "produced",
        "output_path": final_m4b
    })
    print(f"[Worker] Production complete: {final_m4b}")
