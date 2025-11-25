import modal
import io

# Define the image with IndexTTS-2 and dependencies
# IndexTTS-2 requires Python 3.8-3.11, CUDA, and specific dependencies
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("git", "git-lfs")  # Required for model downloads
    .pip_install(
        "torch==2.1.0",
        "torchaudio==2.1.0", 
        extra_options="--index-url https://download.pytorch.org/whl/cu121"  # CUDA 12.1
    )
    .pip_install(
        "transformers",
        "accelerate",
        "scipy",
        "numpy",
        "soundfile",
        "fastapi",
    )
    .run_commands(
        # Clone IndexTTS-2 repository
        "git clone https://github.com/index-tts/index-tts /root/index-tts",
        # Install IndexTTS-2 dependencies
        "cd /root/index-tts && pip install -e .",
    )
)

app = modal.App("audibound-indextts2", image=image)

# Use Modal's Volume for model caching
model_volume = modal.Volume.from_name("indextts2-models", create_if_missing=True)


@app.function(
    gpu="T4",  # NVIDIA T4 GPU (cost-effective, 16GB VRAM)
    volumes={"/cache": model_volume},
    timeout=600,  # 10 minutes (IndexTTS-2 can be slow)
    memory=16384,  # 16GB RAM
)
def generate_audio(
    text: str,
    emo_vector: list = None,
    emo_alpha: float = 0.7,
    voice_ref: str = None,
    use_random: bool = False
):
    """
    Generate audio using IndexTTS-2.
    
    Args:
        text: Text to synthesize
        emo_vector: 8-float emotion vector [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
        emo_alpha: Emotion influence (0.0-1.0, recommended 0.6-0.8)
        voice_ref: Optional path to voice cloning reference audio
        use_random: Whether to use random sampling (reduces fidelity)
    
    Returns:
        bytes: WAV audio data
    """
    import sys
    import os
    sys.path.insert(0, "/root/index-tts")
    
    print(f"[IndexTTS2] === Starting generation ===")
    print(f"[IndexTTS2] Text: {text[:100]}...")
    print(f"[IndexTTS2] Emotion vector: {emo_vector}")
    print(f"[IndexTTS2] Emotion alpha: {emo_alpha}")
    
    try:
        from indextts.infer_v2 import IndexTTS2
        import soundfile as sf
        import numpy as np
        
        # Model paths in cache volume
        cfg_path = "/cache/checkpoints/config.yaml"
        model_dir = "/cache/checkpoints"
        
        # Download models if they don't exist
        if not os.path.exists(cfg_path):
            print("[IndexTTS2] Downloading model files...")
            import subprocess
            
            # Create checkpoints directory
            os.makedirs(model_dir, exist_ok=True)
            
            # Download from HuggingFace
            subprocess.run([
                "git", "clone",
                "https://huggingface.co/IndexTeam/IndexTTS-2",
                model_dir
            ], check=True)
            
            print(f"[IndexTTS2] Models downloaded to {model_dir}")
        
        # Initialize IndexTTS-2
        print("[IndexTTS2] Initializing model...")
        tts = IndexTTS2(
            cfg_path=cfg_path,
            model_dir=model_dir,
            use_fp16=True,  # Use FP16 for faster inference
            use_cuda_kernel=True,  # Enable CUDA kernels for speed
            use_deepspeed=False  # DeepSpeed can be slower on some systems
        )
        
        print("[IndexTTS2] Model loaded successfully")
        
        # Default emotion vector if not provided
        if emo_vector is None:
            emo_vector = [0.2, 0, 0, 0, 0, 0, 0, 0.6]  # Neutral: slight happy + calm
        
        # Generate audio to temporary file
        output_path = "/tmp/indextts2_output.wav"
        
        print(f"[IndexTTS2] Generating speech...")
        tts.infer(
            spk_audio_prompt=voice_ref,  # Voice cloning (None = default voice)
            text=text,
            output_path=output_path,
            emo_vector=emo_vector,
            emo_alpha=emo_alpha,
            use_random=use_random,
            verbose=True
        )
        
        print(f"[IndexTTS2] Audio generated at {output_path}")
        
        # Read the generated WAV file
        with open(output_path, 'rb') as f:
            audio_bytes = f.read()
        
        print(f"[IndexTTS2] Generated {len(audio_bytes)} bytes")
        print(f"[IndexTTS2] === Success! ===")
        
        return audio_bytes
        
    except Exception as e:
        print(f"[IndexTTS2] !!! ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        raise


@app.function()
@modal.web_endpoint(method="POST")
def generate_speech(item: dict):
    """
    Web endpoint for IndexTTS-2 generation.
    Expects JSON: {
        "text": "...",
        "emo_vector": [0.8, 0, 0, 0, 0, 0, 0.2, 0],
        "emo_alpha": 0.7,
        "voice_ref": null,
        "use_random": false
    }
    """
    from fastapi.responses import Response
    
    print(f"[Endpoint] Received IndexTTS-2 request")
    
    text = item.get("text")
    emo_vector = item.get("emo_vector", [0.2, 0, 0, 0, 0, 0, 0, 0.6])
    emo_alpha = item.get("emo_alpha", 0.7)
    voice_ref = item.get("voice_ref")
    use_random = item.get("use_random", False)
    
    if not text:
        print("[Endpoint] ERROR: No text provided")
        return {"error": "No text provided"}
    
    try:
        # Call the generate function
        audio_bytes = generate_audio.remote(
            text=text,
            emo_vector=emo_vector,
            emo_alpha=emo_alpha,
            voice_ref=voice_ref,
            use_random=use_random
        )
        print(f"[Endpoint] Success! Returning {len(audio_bytes)} bytes")
        
        # Return as WAV file
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
