import modal
import io

# Define the image with Coqui TTS
image = (
    modal.Image.debian_slim()
    .apt_install("libsndfile1", "espeak-ng")  # espeak-ng is needed for phonemization
    .pip_install(
        "TTS",  # Coqui TTS
        "numpy",
        "scipy",
        "fastapi"
    )
)

app = modal.App("audibound-coqui-tts", image=image)

# Use Modal's Volume for model caching
model_volume = modal.Volume.from_name("coqui-models", create_if_missing=True)

@app.function(
    gpu="T4",
    volumes={"/cache": model_volume},
    timeout=300
)
def generate_audio(text: str, voice: str = "female", speed: float = 1.0):
    """
    Generate audio using Coqui TTS.
    
    Args:
        text: Text to synthesize
        voice: Voice type (not used in single-speaker model, kept for compatibility)
        speed: Speech speed multiplier (not fully supported yet)
    
    Returns:
        bytes: WAV audio data
    """
    print(f"[Coqui] === Starting generation ===")
    print(f"[Coqui] Text: {text[:100]}...")
    
    try:
        from TTS.api import TTS
        import numpy as np
        import scipy.io.wavfile
        import torch
        
        # Initialize TTS with a good English model
        # Using tts_models/en/ljspeech/tacotron2-DDC - fast and good quality
        print("[Coqui] Initializing TTS model...")
        
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[Coqui] Using device: {device}")
        
        tts = TTS(
            model_name="tts_models/en/ljspeech/tacotron2-DDC",
            progress_bar=False,
            gpu=(device == "cuda")
        )
        
        print("[Coqui] Model loaded successfully")
        
        # Generate audio
        print("[Coqui] Generating speech...")
        
        # TTS.tts() returns a numpy array
        audio = tts.tts(text=text)
        
        # Convert to int16 for WAV
        audio = np.array(audio)
        audio = (audio * 32767).astype(np.int16)
        
        print(f"[Coqui] Audio generated, shape: {audio.shape}")
        
        # Convert to WAV bytes
        print("[Coqui] Converting to WAV...")
        buffer = io.BytesIO()
        
        # Coqui TTS typically outputs at 22050 Hz
        sample_rate = 22050
        scipy.io.wavfile.write(buffer, sample_rate, audio)
        
        audio_bytes = buffer.getvalue()
        
        print(f"[Coqui] Generated {len(audio_bytes)} bytes")
        print(f"[Coqui] === Success! ===")
        
        return audio_bytes
        
    except Exception as e:
        print(f"[Coqui] !!! ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        raise

@app.function()
@modal.web_endpoint(method="POST")
def generate_speech(item: dict):
    """
    Web endpoint for TTS generation.
    Expects JSON: {"text": "...", "voice": "female", "speed": 1.0}
    """
    from fastapi.responses import Response
    
    print(f"[Endpoint] Received request")
    
    text = item.get("text")
    voice = item.get("voice", "female")
    speed = item.get("speed", 1.0)
    
    if not text:
        print("[Endpoint] ERROR: No text provided")
        return {"error": "No text provided"}
    
    try:
        # Call the generate function
        audio_bytes = generate_audio.remote(text, voice, speed)
        print(f"[Endpoint] Success! Returning {len(audio_bytes)} bytes")
        
        # Return as WAV file with proper headers
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav"
            }
        )
    except Exception as e:
        print(f"[Endpoint] Error: {e}")
        import traceback
        traceback.print_exc()
        raise
