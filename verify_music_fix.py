import asyncio
import os
import json
import uuid
from datetime import datetime
from src.worker import run_production_pipeline_async, update_project_in_db
from src.core.abml import ScriptManifest, SeriesBible, Scene, AudioBlock, VoiceLayer, MusicLayer

async def run_test():
    project_id = str(uuid.uuid4())
    print(f"Creating test project {project_id}...")
    
    # Create a long narration text to ensure it exceeds 10s
    long_text = "This is a long narration block to test the music generation duration. " * 10
    
    # Create dummy manifest
    manifest = ScriptManifest(
        project_id=project_id,
        title="Music Fix Test",
        bible=SeriesBible(project_title="Test Bible", characters=[]),
        scenes=[
            Scene(
                scene_id="scene_1",
                setting="Test Studio",
                ambience_description="Quiet room",
                blocks=[
                    AudioBlock(
                        id="block_1",
                        music=MusicLayer(
                            enabled=True,
                            action="start",
                            style_description="upbeat pop",
                            volume=0.5
                        ),
                        narration=VoiceLayer(
                            text=long_text,
                            speaker="Narrator"
                        )
                    )
                ]
            )
        ]
    )
    
    # Save to DB
    update_project_in_db(project_id, {
        "id": project_id,
        "title": "Music Fix Test",
        "status": "directed",
        "manifest": manifest.model_dump(),
        "voice_overrides": {}
    })
    
    print("Triggering production...")
    try:
        await run_production_pipeline_async(
            project_id=project_id,
            voice_engine="mock", # Use mock voice to save time/cost, we care about music duration calculation
            include_voice=True,
            include_sfx=False,
            include_music=True,
            reuse_voice_cache=False
        )
        print("Production finished.")
        
        # Check logs/output manually or add assertions here if possible
        # Since we can't easily capture stdout of the imported function, we rely on the console output when running this script.
        
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_test())
