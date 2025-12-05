"""
Populate Voice Library with Default Voices

This script adds all 14 default voices (10 Kokoro + 4 engine defaults)
to the voice library with clean names and metadata.
"""

import asyncio
import os
from src.core.voice_library import get_voice_library
from src.core.voice_mapper import VoiceMapper

# Define all default voices with clean metadata
DEFAULT_VOICES = [
    # Kokoro Voices
    {
        "id": "af_sarah",
        "name": "Sarah",
        "engine": "kokoro",
        "bio": "Young, energetic American female",
        "gender": "female",
        "tags": ["american", "female", "young", "energetic"],
        "is_default": True
    },
    {
        "id": "af_bella",
        "name": "Bella",
        "engine": "kokoro",
        "bio": "Warm, mature American female",
        "gender": "female",
        "tags": ["american", "female", "mature", "warm"],
        "is_default": True
    },
    {
        "id": "af_nicole",
        "name": "Nicole",
        "engine": "kokoro",
        "bio": "Neutral, professional American female",
        "gender": "female",
        "tags": ["american", "female", "professional", "neutral"],
        "is_default": True
    },
    {
        "id": "af_sky",
        "name": "Sky",
        "engine": "kokoro",
        "bio": "Bright, enthusiastic American female",
        "gender": "female",
        "tags": ["american", "female", "bright", "enthusiastic"],
        "is_default": True
    },
    {
        "id": "am_adam",
        "name": "Adam",
        "engine": "kokoro",
        "bio": "Mature, authoritative American male",
        "gender": "male",
        "tags": ["american", "male", "mature", "authoritative"],
        "is_default": True
    },
    {
        "id": "am_michael",
        "name": "Michael",
        "engine": "kokoro",
        "bio": "Strong, confident American male",
        "gender": "male",
        "tags": ["american", "male", "strong", "confident"],
        "is_default": True
    },
    {
        "id": "bf_emma",
        "name": "Emma",
        "engine": "kokoro",
        "bio": "Refined British female",
        "gender": "female",
        "tags": ["british", "female", "refined"],
        "is_default": True
    },
    {
        "id": "bf_isabella",
        "name": "Isabella",
        "engine": "kokoro",
        "bio": "Elegant British female",
        "gender": "female",
        "tags": ["british", "female", "elegant"],
        "is_default": True
    },
    {
        "id": "bm_george",
        "name": "George",
        "engine": "kokoro",
        "bio": "Distinguished British male",
        "gender": "male",
        "tags": ["british", "male", "distinguished"],
        "is_default": True
    },
    {
        "id": "bm_lewis",
        "name": "Lewis",
        "engine": "kokoro",
        "bio": "Warm British male",
        "gender": "male",
        "tags": ["british", "male", "warm"],
        "is_default": True
    },
    # Other Engine Defaults
    {
        "id": "default",
        "name": "StyleTTS2 Default",
        "engine": "styletts2",
        "bio": "Highly expressive with style control",
        "gender": "neutral",
        "tags": ["expressive", "versatile"],
        "is_default": True
    },
    {
        "id": "default",
        "name": "Sesame Default",
        "engine": "sesame",
        "bio": "Expressive neutral voice",
        "gender": "neutral",
        "tags": ["expressive", "neutral"],
        "is_default": True
    },
    {
        "id": "default",
        "name": "IndexTTS2 Default",
        "engine": "indextts2",
        "bio": "Emotion vector control (8 emotions)",
        "gender": "neutral",
        "tags": ["emotional", "versatile"],
        "is_default": True
    },
    {
        "id": "default",
        "name": "Dia Default",
        "engine": "dia",
        "bio": "Expressive multi-speaker",
        "gender": "neutral",
        "tags": ["expressive", "multi-speaker"],
        "is_default": True
    },
]


async def generate_sample_for_voice(voice_id: str, engine: str) -> str:
    """
    Generate a sample audio file for a default voice.
    Returns the path to the generated audio file.
    """
    from src.core.voice_engine import get_voice_provider
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv()

    # Sample text for generation
    sample_text = "This is a sample of my voice."

    # Map engine to Modal URL environment variable
    modal_url_mapping = {
        "kokoro": os.getenv("MODAL_URL"),
        "styletts2": os.getenv("STYLETTS2_MODAL_URL"),
        "indextts2": os.getenv("INDEXTTS2_MODAL_URL"),
        "sesame": os.getenv("SESAME_MODAL_URL"),
        "dia": os.getenv("DIA_MODAL_ENDPOINT"),
    }

    # Get the appropriate provider with Modal URL
    modal_url = modal_url_mapping.get(engine)
    if modal_url:
        provider = get_voice_provider(engine, modal_url=modal_url)
    else:
        provider = get_voice_provider(engine)

    # Generate audio
    audio_bytes = await provider.generate_audio(
        text=sample_text,
        voice_id=voice_id,
        speed=1.0,
        style=None
    )

    # Save to references directory
    os.makedirs("references/default_samples", exist_ok=True)
    output_path = f"references/default_samples/{engine}_{voice_id}_sample.wav"

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    return output_path


async def populate_default_voices():
    """Add all default voices to the voice library."""
    voice_lib = get_voice_library()

    print("=" * 60)
    print("POPULATING VOICE LIBRARY WITH DEFAULT VOICES")
    print("=" * 60)

    for voice_data in DEFAULT_VOICES:
        voice_id = voice_data["id"]
        engine = voice_data["engine"]
        name = voice_data["name"]

        # Check if this default voice already exists in library
        existing = None
        for v in voice_lib.get_all_voices():
            if (v.get("metadata", {}).get("is_default") and
                v.get("engine") == engine and
                v.get("metadata", {}).get("original_id") == voice_id):
                existing = v
                break

        if existing:
            print(f"✓ {name} ({engine}) already in library")
            continue

        print(f"\n[{name}] Generating sample audio...")

        try:
            # Generate sample audio
            sample_path = await generate_sample_for_voice(voice_id, engine)

            # Read the audio file
            with open(sample_path, "rb") as f:
                audio_bytes = f.read()

            # Add to library
            voice_entry = voice_lib.add_voice(
                name=name,
                audio_bytes=audio_bytes,
                filename=f"{engine}_{voice_id}_sample.wav",
                engine=engine,
                tags=voice_data["tags"],
                metadata={
                    "is_default": True,
                    "original_id": voice_id
                },
                bio=voice_data["bio"],
                gender=voice_data["gender"]
            )

            print(f"✓ Added {name} to library (ID: {voice_entry['id']})")

        except Exception as e:
            print(f"✗ Failed to add {name}: {e}")

    print("\n" + "=" * 60)
    print("DONE! Default voices have been populated.")
    print(f"Total voices in library: {len(voice_lib.get_all_voices())}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(populate_default_voices())
