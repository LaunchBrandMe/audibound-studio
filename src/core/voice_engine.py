from abc import ABC, abstractmethod
import httpx
import os
from typing import Optional

class VoiceProvider(ABC):
    @abstractmethod
    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0, style: Optional[str] = None) -> bytes:
        pass

class KokoroProvider(VoiceProvider):
    def __init__(self, modal_url: str):
        self.modal_url = modal_url
    
    def _add_emotion_tags(self, text: str, style: Optional[str]) -> str:
        """
        (Disabled) Inject Kokoro emotion tags.
        Currently disabled because tags are being read literally by the model.
        """
        return text

    
    def _get_prosody_params(self, style: Optional[str]) -> dict:
        """
        Map ABML style to Kokoro prosody parameters.
        Returns speed and pitch adjustments based on emotional context.
        """
        if not style:
            return {'speed': 1.0, 'pitch': 0.0}
        
        style_lower = style.lower()
        
        # Whispering: slower, lower pitch
        if 'whisper' in style_lower or 'quiet' in style_lower:
            return {'speed': 0.85, 'pitch': -5}
        
        # Shouting/Angry: faster, higher pitch
        elif any(word in style_lower for word in ['shout', 'yell', 'angry', 'furious']):
            return {'speed': 1.2, 'pitch': 8}
        
        # Excited/Urgent: faster, higher pitch
        elif any(word in style_lower for word in ['excit', 'urgent', 'hurried', 'rushed']):
            return {'speed': 1.15, 'pitch': 5}
        
        # Sad/Melancholy/Tired: slower, lower pitch
        elif any(word in style_lower for word in ['sad', 'melancholy', 'tired', 'weary', 'somber']):
            return {'speed': 0.9, 'pitch': -6}
        
        # Cheerful/Happy: slightly faster, slightly higher pitch
        elif any(word in style_lower for word in ['cheerful', 'happy', 'joyful']):
            return {'speed': 1.05, 'pitch': 3}
        
        # Default: neutral speed and pitch
        return {'speed': 1.0, 'pitch': 0.0}

    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0, style: Optional[str] = None) -> bytes:
        """
        Calls the Modal.com endpoint to generate audio using Kokoro.
        Now supports expressive speech via speed and pitch control.
        """
        # Add emotion tags to text (currently disabled)
        text_with_emotion = self._add_emotion_tags(text, style)
        
        # Get prosody adjustments (speed + pitch)
        prosody = self._get_prosody_params(style)
        
        # Combine base speed with prosody speed
        final_speed = speed * prosody['speed']
        pitch = prosody['pitch']
        
        async with httpx.AsyncClient() as client:
            payload = {
                "text": text_with_emotion,
                "voice": voice_id,
                "speed": final_speed,
                "pitch": pitch,  # NEW: Add pitch for expression
                "style": style  # Pass for logging/future use
            }
            
            if style:
                print(f"[VoiceEngine] Generating with style '{style}': speed={final_speed:.2f}, pitch={pitch}")
            else:
                print(f"[VoiceEngine] Requesting audio for voice: {voice_id}...")
            
            response = await client.post(self.modal_url, json=payload, timeout=60.0)
            print(f"[VoiceEngine] Response Status: {response.status_code}")
            response.raise_for_status()
            content = response.content
            
            # Validate audio data
            if len(content) < 100:
                print(f"[VoiceEngine] Response too small: {len(content)} bytes")
                print(f"[VoiceEngine] Content: {content}")
                raise ValueError(f"Audio response too small ({len(content)} bytes), likely an error")
            
            # Check if it's actually a WAV file (starts with RIFF header)
            if not content.startswith(b'RIFF'):
                print(f"[VoiceEngine] WARNING: Response doesn't look like a WAV file")
                print(f"[VoiceEngine] First 100 bytes: {content[:100]}")
                raise ValueError("Invalid audio format received from TTS service")
            
            print(f"[VoiceEngine] Received {len(content)} bytes")
            return content

class ElevenLabsProvider(VoiceProvider):
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")

    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0, style: Optional[str] = None) -> bytes:
        # Placeholder for ElevenLabs implementation
        # Would use their API to get MP3 bytes
        raise NotImplementedError("ElevenLabs provider not yet implemented")

class MockProvider(VoiceProvider):
    """Local mock TTS for testing - generates simple sine wave audio"""
    
    async def generate_audio(self, text: str, voice_id: str, speed: float = 1.0, style: Optional[str] = None) -> bytes:
        import numpy as np
        import scipy.io.wavfile
        import io
        
        # Generate simple audio based on text length
        # ~150 words per minute = 2.5 words per second = 0.4 seconds per word
        word_count = len(text.split())
        duration_seconds = max(1.0, word_count * 0.4 / speed)
        
        sample_rate = 24000
        
        # Generate a simple sine wave (440 Hz tone)
        t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds))
        # Mix of frequencies to make it less annoying
        audio = (
            0.3 * np.sin(2 * np.pi * 440 * t) +  # A4
            0.2 * np.sin(2 * np.pi * 554 * t) +  # C#5
            0.1 * np.sin(2 * np.pi * 659 * t)    # E5
        )
        
        # Add envelope (fade in/out)
        fade_samples = int(0.1 * sample_rate)
        audio[:fade_samples] *= np.linspace(0, 1, fade_samples)
        audio[-fade_samples:] *= np.linspace(1, 0, fade_samples)
        
        # Convert to int16
        audio = (audio * 32767).astype(np.int16)
        
        # Write to WAV
        buffer = io.BytesIO()
        scipy.io.wavfile.write(buffer, sample_rate, audio)
        
        return buffer.getvalue()

def get_voice_provider(provider_type: str = "kokoro", **kwargs) -> VoiceProvider:
    if provider_type == "kokoro":
        return KokoroProvider(modal_url=kwargs.get("modal_url"))
    elif provider_type == "elevenlabs":
        return ElevenLabsProvider(api_key=kwargs.get("api_key"))
    elif provider_type == "mock":
        return MockProvider()
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")
