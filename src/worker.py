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
        "clarifications": validation_result.clarifications,
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
def task_produce_audio(
    project_id: str,
    voice_engine: str = "kokoro",
    include_voice: bool = True,
    include_sfx: bool = True,
    include_music: bool = True,
    reuse_voice_cache: bool = True,
):
    # Since Celery is synchronous by default, we run the async code via asyncio.run
    asyncio.run(
        run_production_pipeline_async(
            project_id,
            voice_engine,
            include_voice=include_voice,
            include_sfx=include_sfx,
            include_music=include_music,
            reuse_voice_cache=reuse_voice_cache,
        )
    )

async def run_production_pipeline_async(
    project_id: str,
    voice_engine: str = "kokoro",
    include_voice: bool = True,
    include_sfx: bool = True,
    include_music: bool = True,
    reuse_voice_cache: bool = True,
):
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
    
    # Cache for providers (keyed by engine name) - lazy loaded only when needed
    provider_cache = {}
    
    # Initialize SFX and Music providers
    sfx_provider_type = os.getenv("SFX_PROVIDER", "stock")
    sfx_provider = None
    if include_sfx:
        try:
            sfx_provider = get_sfx_provider(sfx_provider_type)
            print(f"[Worker] SFX provider initialized: {type(sfx_provider).__name__} (type: {sfx_provider_type})")
        except Exception as e:
            print(f"[Worker] ERROR: Failed to initialize SFX provider '{sfx_provider_type}': {e}")
            print(f"[Worker] SFX will be disabled for this render")
    
    music_provider_type = os.getenv("MUSIC_PROVIDER", "stock")
    music_provider = None
    if music_provider_type != "none" and include_music:
        try:
            music_provider = get_music_provider(music_provider_type)
            print(f"[Worker] Music provider initialized: {type(music_provider).__name__} (type: {music_provider_type})")
        except Exception as e:
            print(f"[Worker] ERROR: Failed to initialize music provider '{music_provider_type}': {e}")
            print(f"[Worker] Music will be disabled for this render")
    assembler = AudioAssembler(output_dir)
    
    # 1. Generate Audio
    print("[Worker] Generating Voice Clips (Parallel)...")
    print(f"[Worker] Manifest has {len(manifest.scenes)} scenes.")
    
    # Flatten all blocks for parallel processing
    all_blocks_flat = []
    for scene in manifest.scenes:
        for block in scene.blocks:
            all_blocks_flat.append(block)
    
    # Track how many SFX/Music generations we attempt vs succeed (for debugging / cost transparency)
    gen_stats = {
        "sfx_total": 0,
        "sfx_ok": 0,
        "music_total": 0,
        "music_ok": 0,
    }
    sfx_failures = []
    music_failures = []
            
    print(f"[Worker] Total blocks to process: {len(all_blocks_flat)}")
    
    # Semaphore to limit concurrency
    voice_max = int(os.getenv("VOICE_MAX_CONCURRENCY", "3"))
    sem = asyncio.Semaphore(max(1, voice_max))  # voice/dialogue generation
    sfx_max = int(os.getenv("SFX_MAX_CONCURRENCY", "3"))
    music_max = int(os.getenv("MUSIC_MAX_CONCURRENCY", "3"))
    sfx_max_calls = int(os.getenv("SFX_MAX_CALLS", "8"))
    sfx_sem = asyncio.Semaphore(max(1, sfx_max))
    music_sem = asyncio.Semaphore(max(1, music_max))
    
    # Ensure cache directory exists
    cache_dir = os.path.join("outputs", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    
    import hashlib
    import shutil

    def compute_audio_hash(params: dict) -> str:
        """Compute a deterministic hash for audio generation parameters."""
        # Sort keys to ensure consistent ordering
        s = json.dumps(params, sort_keys=True)
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def estimate_duration_ms(text: str, speed: float = 1.0) -> int:
        """
        Roughly estimate duration based on word count (0.4s/word) for fallback when voice is skipped.
        """
        words = len((text or "").split())
        seconds = max(1.0, (words * 0.4) / max(speed, 0.1))
        return int(seconds * 1000)

    async def process_block(block):
        async with sem:
            # --- Voice Generation ---
            if block.narration:
                try:
                    # Use final voice map to get character-specific voice
                    speaker_name = block.narration.speaker
                    voice_id = block.narration.voice_id or final_voice_map.get(speaker_name, "kokoro:af_nicole")

                    if single_voice_mode and speaker_name != "Narrator":
                        voice_id = final_voice_map.get("Narrator", "kokoro:af_nicole")

                    # Parse engine-prefixed voice ID
                    if ":" in voice_id:
                        voice_engine_override, voice_id_parsed = voice_id.split(":", 1)
                    else:
                        voice_engine_override = "kokoro"
                        voice_id_parsed = voice_id
                    
                    # Get style
                    style = block.narration.style
                    override = voice_overrides.get(speaker_name, {})
                    if override.get("style"):
                        style = override["style"]
                    
                    raw_text = block.narration.text
                    cleaned_text, was_modified = clean_text_if_needed(raw_text, is_dialogue=True)
                    
                    # --- CACHE CHECK ---
                    cache_params = {
                        "type": "voice",
                        "engine": voice_engine_override,
                        "voice_id": voice_id_parsed,
                        "text": cleaned_text,
                        "style": style
                    }
                    cache_key = compute_audio_hash(cache_params)
                    cache_path = os.path.join(cache_dir, f"{cache_key}.wav")
                    filename = f"{block.id}_narration.wav"
                    filepath = os.path.join(temp_dir, filename)
                    
                    cache_hit = os.path.exists(cache_path)
                    should_generate_voice = include_voice

                    if cache_hit and (include_voice or reuse_voice_cache):
                        print(f"[Worker] Cache HIT for block {block.id} ({speaker_name})")
                        shutil.copy2(cache_path, filepath)
                        block.narration.file_path = filepath
                        block.duration_ms = assembler.get_track_duration_ms(filepath)
                    elif should_generate_voice:
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
                        
                        print(f"[Worker] Generating block {block.id} ({speaker_name})...")
                        
                        audio_bytes = await current_provider.generate_audio(
                            text=cleaned_text,
                            voice_id=voice_id_parsed,
                            style=style
                        )
                        
                        # Save to cache first
                        with open(cache_path, "wb") as f:
                            f.write(audio_bytes)
                        
                        # Then copy to temp dir
                        shutil.copy2(cache_path, filepath)
                        block.narration.file_path = filepath
                        block.duration_ms = assembler.get_track_duration_ms(filepath)
                    else:
                        # Voice generation skipped; try to reuse cache if available, otherwise create silence placeholder
                        if reuse_voice_cache and cache_hit:
                            shutil.copy2(cache_path, filepath)
                            block.narration.file_path = filepath
                            block.duration_ms = assembler.get_track_duration_ms(filepath)
                        else:
                            est_ms = estimate_duration_ms(cleaned_text)
                            silence_path = os.path.join(temp_dir, f"{block.id}_silence.wav")
                            assembler.create_silence(est_ms, silence_path)
                            block.narration.file_path = silence_path
                            block.duration_ms = est_ms
                        
                except Exception as e:
                    print(f"Error generating block {block.id}: {e}")
                    try:
                        # Fallback to silence placeholder to keep timing intact
                        est_ms = estimate_duration_ms(block.narration.text)
                        silence_path = os.path.join(temp_dir, f"{block.id}_silence.wav")
                        assembler.create_silence(est_ms, silence_path)
                        block.narration.file_path = silence_path
                        block.duration_ms = est_ms
                    except Exception as inner_e:
                        print(f"Failed to create fallback silence for block {block.id}: {inner_e}")
            
            # --- SFX Generation ---
            if block.sfx and block.sfx.enabled and sfx_provider:
                if gen_stats["sfx_total"] >= sfx_max_calls:
                    print(f"[Worker] SFX cap reached ({sfx_max_calls}); skipping block {block.id}")
                    block.sfx.enabled = False
                    return
                gen_stats["sfx_total"] += 1
                try:
                    # --- CACHE CHECK ---
                    cache_params = {
                        "type": "sfx",
                        "provider": sfx_provider_type,
                        "description": block.sfx.description,
                        "category": block.sfx.category or "general"
                    }
                    cache_key = compute_audio_hash(cache_params)
                    # SFX might return different extensions, but let's assume wav for consistency or handle it
                    # The provider returns a path, usually .wav
                    cache_path = os.path.join(cache_dir, f"{cache_key}.wav")

                    if os.path.exists(cache_path):
                        print(f"[Worker] Cache HIT for SFX block {block.id}")
                        # We need a unique path in temp dir
                        sfx_filename = f"{block.id}_sfx.wav"
                        sfx_file_path = os.path.join(temp_dir, sfx_filename)
                        shutil.copy2(cache_path, sfx_file_path)
                        block.sfx.file_path = sfx_file_path
                        gen_stats["sfx_ok"] += 1
                    else:
                        print(f"[Worker] Generating SFX for block {block.id}: '{block.sfx.description}'")
                        async with sfx_sem:
                            sfx_file_path_generated = await sfx_provider.get_sfx(
                                description=block.sfx.description,
                                category=block.sfx.category or "general"
                            )

                            # Validate generated file exists and has content
                            if not sfx_file_path_generated or not os.path.exists(sfx_file_path_generated):
                                raise FileNotFoundError(f"SFX provider returned invalid path: {sfx_file_path_generated}")

                            file_size = os.path.getsize(sfx_file_path_generated)
                            if file_size < 100:
                                raise ValueError(f"Generated SFX file too small ({file_size} bytes)")

                            # Copy to cache
                            shutil.copy2(sfx_file_path_generated, cache_path)

                            # Copy to project temp folder (NOT system temp)
                            sfx_filename = f"{block.id}_sfx.wav"
                            sfx_file_path = os.path.join(temp_dir, sfx_filename)
                            shutil.copy2(sfx_file_path_generated, sfx_file_path)
                            block.sfx.file_path = sfx_file_path
                            gen_stats["sfx_ok"] += 1
                            print(f"[Worker] SFX generated and copied to {sfx_file_path} ({file_size} bytes)")

                except Exception as e:
                    error_detail = f"Block {block.id} ('{block.sfx.description}'): {type(e).__name__}: {str(e)}"
                    print(f"[Worker] ERROR generating SFX: {error_detail}")
                    sfx_failures.append(error_detail)
                    # Mark SFX as disabled so it doesn't break the final mix
                    block.sfx.enabled = False

            # Music generation moved to PASS 2 (after narration) to use actual durations

    # Run voice and SFX generation in parallel (music comes after)
    await asyncio.gather(*[process_block(b) for b in all_blocks_flat])

    # --- INTERMEDIATE STEP: Calculate start times based on actual narration durations ---
    print("[Worker] Calculating block start times...")
    current_time_ms = 0
    for block in all_blocks_flat:
        block.start_time_ms = current_time_ms
        if block.sfx:
            block.sfx.start_time_ms = current_time_ms
        
        # Add duration (default to 0 if missing)
        duration = block.duration_ms or 0
        current_time_ms += duration

    # --- PASS 2: Generate music using ACTUAL narration durations ---
    print("[Worker] Generating music using actual voice durations...")

    async def generate_music_for_block(block):
        if block.music and block.music.enabled and music_provider:
            if block.music.action in ["start", "fade_in"]:
                gen_stats["music_total"] += 1
                try:
                    # Calculate ACTUAL duration from generated narration
                    current_idx = all_blocks_flat.index(block)

                    # Sum ACTUAL duration_ms from blocks until next music change
                    required_duration_ms = 0

                    # Include current block's actual duration
                    if block.duration_ms:
                        required_duration_ms += block.duration_ms

                    # Look ahead and sum actual durations
                    for i in range(current_idx + 1, len(all_blocks_flat)):
                        next_block = all_blocks_flat[i]

                        # Stop if next block changes music
                        if next_block.music and next_block.music.enabled and next_block.music.action in ["start", "fade_in", "stop", "fade_out"]:
                            break

                        # Add actual duration from narration
                        if next_block.duration_ms:
                            required_duration_ms += next_block.duration_ms

                    # Convert to seconds
                    duration = required_duration_ms / 1000.0
                    print(f"[Worker] Calculated music duration for block {block.id}: {duration:.2f}s (required_ms={required_duration_ms})")
                    
                    # Minimum duration 5s, Max 300s (5 mins)
                    duration = max(5.0, min(300.0, duration))
                    print(f"[Worker] Music block {block.id}: Final duration {duration:.1f}s (from {required_duration_ms}ms of actual voice)")

                    # Cache check
                    cache_params = {
                        "type": "music",
                        "provider": music_provider_type,
                        "description": block.music.style_description,
                        "duration": duration
                    }
                    cache_key = compute_audio_hash(cache_params)
                    cache_path = os.path.join(cache_dir, f"{cache_key}.wav")

                    if os.path.exists(cache_path):
                        print(f"[Worker] Cache HIT for Music block {block.id}")
                        music_filename = f"{block.id}_music.wav"
                        music_file_path = os.path.join(temp_dir, music_filename)
                        shutil.copy2(cache_path, music_file_path)
                        block.music.file_path = music_file_path
                        gen_stats["music_ok"] += 1
                    else:
                        print(f"[Worker] Generating music: '{block.music.style_description}' ({duration:.1f}s)")
                        async with music_sem:
                            music_file_path_generated = await music_provider.get_music(
                                style_description=block.music.style_description,
                                duration=duration
                            )

                            if music_file_path_generated and os.path.exists(music_file_path_generated):
                                file_size = os.path.getsize(music_file_path_generated)
                                print(f"[Worker] Music generated: {file_size:,} bytes")

                                # Copy to cache
                                shutil.copy2(music_file_path_generated, cache_path)

                                # Copy to project temp folder (NOT system temp)
                                music_filename = f"{block.id}_music.wav"
                                music_file_path = os.path.join(temp_dir, music_filename)
                                shutil.copy2(music_file_path_generated, music_file_path)
                                block.music.file_path = music_file_path
                                gen_stats["music_ok"] += 1
                                print(f"[Worker] Music copied to {music_file_path}")
                            else:
                                raise FileNotFoundError(f"Music provider returned invalid path: {music_file_path_generated}")

                except Exception as e:
                    error_detail = f"Block {block.id}: {type(e).__name__}: {str(e)}"
                    print(f"[Worker] ERROR generating music: {error_detail}")
                    music_failures.append(error_detail)
                    block.music.enabled = False

    # Generate all music in parallel using actual durations
    music_blocks = [b for b in all_blocks_flat if b.music and b.music.enabled]
    if music_blocks:
        await asyncio.gather(*[generate_music_for_block(b) for b in music_blocks])

    print(f"[Worker] SFX requests: {gen_stats['sfx_total']} | generated: {gen_stats['sfx_ok']}")
    print(f"[Worker] Music requests: {gen_stats['music_total']} | generated: {gen_stats['music_ok']}")
    if gen_stats["sfx_total"] and gen_stats["sfx_ok"] == 0:
        print("[Worker] WARNING: No SFX files were generated; final mix will be voice-only for SFX.")
    if gen_stats["music_total"] and gen_stats["music_ok"] == 0:
        print("[Worker] WARNING: No music files were generated; final mix will be voice-only for music.")
    notes = []
    if gen_stats["sfx_total"]:
        notes.append(f"SFX generated {gen_stats['sfx_ok']}/{gen_stats['sfx_total']}.")
    if sfx_failures:
        notes.append(f"SFX failures: {len(sfx_failures)}")
    if gen_stats["music_total"]:
        notes.append(f"Music generated {gen_stats['music_ok']}/{gen_stats['music_total']}.")
    if music_failures:
        notes.append(f"Music failures: {len(music_failures)}")

    # 2. Stitch
    print("[Worker] Stitching...")
    all_blocks = [b for s in manifest.scenes for b in s.blocks]
    narration_path = assembler.stitch_voice_track(all_blocks, temp_dir)
    
    # Calculate total duration from narration track for SFX and music timing
    total_duration_ms = assembler.get_track_duration_ms(narration_path) if os.path.exists(narration_path) else 0
    
    # Stitch SFX track if any SFX blocks exist
    sfx_blocks = [b for b in all_blocks if b.sfx and b.sfx.file_path]
    sfx_path = None
    if sfx_blocks:
        print(f"[Worker] Stitching SFX track ({len(sfx_blocks)} SFX blocks)...")
        sfx_path = assembler.stitch_sfx_track(sfx_blocks, total_duration_ms, temp_dir)
    
    # Stitch music track if any music blocks exist
    music_blocks = [b for b in all_blocks if b.music and b.music.file_path]
    music_path = None
    if music_blocks:
        print(f"[Worker] Stitching music track ({len(music_blocks)} music blocks)...")
        # For MVP, we use the first music file. We must copy it to stem_music.mp3 for the player.
        raw_music_path = music_blocks[0].music.file_path
        if raw_music_path and os.path.exists(raw_music_path):
            music_stem_path = os.path.join(output_dir, "stem_music.mp3")  # FIX: output_dir not temp_dir
            shutil.copy2(raw_music_path, music_stem_path)
            music_path = music_stem_path
        else:
            music_path = None
    
    # 3. Mix
    print("[Worker] Mixing...")
    requested_layers = []
    if include_voice:
        requested_layers.append("voice")
    if include_sfx:
        requested_layers.append("sfx")
    if include_music:
        requested_layers.append("music")
    layers_label = "_".join(requested_layers) if requested_layers else "voice"
    final_m4b = assembler.mix_stems_to_m4b(
        narration_path=narration_path,
        music_path=music_path, 
        sfx_path=sfx_path,
        manifest=manifest,
        engine_tag=voice_engine,
        layers_label=layers_label,
    )
    
    history = list(project.get("render_history") or [])
    history.append({
        "timestamp": datetime.utcnow().isoformat() + 'Z',
        "engine": voice_engine,
        "output_path": final_m4b,
        "layers": requested_layers,
        "notes": notes
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
