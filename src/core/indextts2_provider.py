from abc import ABC, abstractmethod
import base64
import httpx
import os
from typing import Optional

from src.core.voice_library import get_voice_library


class VoiceProvider(ABC):
    @abstractmethod
    async def generate_audio(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        style: Optional[str] = None,
        reference_audio_path: Optional[str] = None,
    ) -> bytes:
        pass


class IndexTTS2Provider(VoiceProvider):
    """
    Provider for IndexTTS-2 with emotion vector control.
    
    IndexTTS-2 supports 8 emotions: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
    """
    
    # Available IndexTTS2 voices (single model with emotion vectors)
    AVAILABLE_VOICES = {
        "indextts2:default": "IndexTTS-2 - Emotion vector control (8 emotions)"
    }
    
    def __init__(self, modal_url: str):
        self.modal_url = modal_url
    
    @classmethod
    def get_available_voices(cls):
        """Return dictionary of available IndexTTS2 voices."""
        return cls.AVAILABLE_VOICES.copy()
    
    def _style_to_emotion_vector(self, style: Optional[str]) -> list:
        """
        Map ABML style to IndexTTS-2 emotion vector.
        
        Emotion vector format: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
        - Each value: 0.0 to 1.0
        - Total sum should not exceed 1.5
        
        Returns: List of 8 floats
        """
        if not style:
            # Neutral: mostly calm with slight happy
            return [0.2, 0, 0, 0, 0, 0, 0, 0.6]
        
        style_lower = style.lower()
        
        # Happy/Cheerful/Joyful
        if any(word in style_lower for word in ['happy', 'cheerful', 'joyful', 'excited']):
            return [0.8, 0, 0, 0, 0, 0, 0.2, 0.2]  # Happy dominant with bit of surprise and calm
        
        # Angry/Furious/Harsh
        elif any(word in style_lower for word in ['angry', 'furious', 'harsh', 'shout', 'yell']):
            return [0, 0.9, 0, 0, 0, 0, 0, 0]  # Pure anger
        
        # Sad/Melancholy/Weary
        elif any(word in style_lower for word in ['sad', 'melancholy', 'weary', 'somber', 'tired']):
            return [0, 0, 0.6, 0, 0, 0.6, 0, 0]  # Sad + melancholic
        
        # Afraid/Scared/Nervous
        elif any(word in style_lower for word in ['afraid', 'scared', 'nervous', 'frightened', 'fearful']):
            return [0, 0, 0, 0.8, 0, 0, 0.3, 0]  # Afraid + surprised
        
        # Disgusted
        elif any(word in style_lower for word in ['disgusted', 'revolted', 'repulsed']):
            return [0, 0.3, 0, 0, 0.8, 0, 0, 0]  # Disgusted with slight anger
        
        # Surprised/Shocked/Astonished
        elif any(word in style_lower for word in ['surprised', 'shocked', 'astonished', 'amazed']):
            return [0.3, 0, 0, 0, 0, 0, 0.7, 0.2]  # Surprised with happy and calm
        
        # Calm/Peaceful/Serene/Quiet/Soft
        elif any(word in style_lower for word in ['calm', 'peaceful', 'serene', 'quiet', 'soft', 'whisper']):
            return [0, 0, 0, 0, 0, 0.3, 0, 0.8]  # Calm dominant with slight melancholic
        
        # Urgent/Rushed/Hurried
        elif any(word in style_lower for word in ['urgent', 'rushed', 'hurried']):
            return [0, 0.4, 0, 0.3, 0, 0, 0.5, 0]  # Mix of anger, afraid, surprised
        
        # Default: Neutral calm
        return [0.2, 0, 0, 0, 0, 0, 0, 0.6]
    
    def _get_voice_reference_path(self, voice_id: str) -> Optional[str]:
        """
        Map Kokoro voice IDs to reference audio paths for IndexTTS-2.
        
        For now, we'll use a placeholder. In production, you would:
        1. Pre-record reference clips for each voice type
        2. Store them in Modal volume or S3
        3. Return the path here
        
        Returns: Path to reference audio file (or None to use default)
        """
        if not voice_id:
            return None
        if voice_id.startswith("custom_"):
            library_id = voice_id.split("custom_", 1)[1]
            voice = get_voice_library().get_voice(library_id)
            if voice:
                ref = voice.get("reference_file")
                if ref and os.path.exists(ref):
                    return ref
        return None
    
    async def generate_audio(
        self,
        text: str,
        voice_id: str,
        speed: float = 1.0,
        style: Optional[str] = None,
        reference_audio_path: Optional[str] = None,
    ) -> bytes:
        """
        Generate audio using IndexTTS-2 via Modal endpoint.
        
        Args:
            text: Text to synthesize
            voice_id: Voice identifier (Kokoro format, will be mapped)
            speed: Speech speed multiplier (IndexTTS-2 doesn't directly support this, so we ignore it)
            style: ABML style (converted to emotion vector)
        
        Returns:
            WAV audio bytes
        """
        # Convert style to emotion vector
        emo_vector = self._style_to_emotion_vector(style)
        
        # Determine reference audio (library or explicit)
        voice_ref_path = reference_audio_path or self._get_voice_reference_path(voice_id)
        voice_sample_b64 = None
        if voice_ref_path and os.path.exists(voice_ref_path):
            with open(voice_ref_path, 'rb') as f:
                voice_sample_b64 = base64.b64encode(f.read()).decode('utf-8')
        
        async with httpx.AsyncClient(follow_redirects=True) as client:
            payload = {
                "text": text,
                "emo_vector": emo_vector,
                "emo_alpha": 0.7,  # Moderate emotion influence (0.6-0.8 recommended)
                "use_random": False  # Disable randomness for consistency
            }
            if voice_sample_b64:
                payload["voice_sample_b64"] = voice_sample_b64
            
            if style:
                print(f"[IndexTTS2] Generating with style '{style}': emo_vector={emo_vector}")
            else:
                print(f"[IndexTTS2] Generating neutral speech for voice: {voice_id}")
            
            response = await client.post(self.modal_url, json=payload, timeout=300.0)  # 5 minutes for cold start
            print(f"[IndexTTS2] Response Status: {response.status_code}")
            response.raise_for_status()
            content = response.content
            
            # Validate audio data
            if len(content) < 100:
                print(f"[IndexTTS2] Response too small: {len(content)} bytes")
                raise ValueError(f"Audio response too small ({len(content)} bytes)")
            
            # Check WAV format
            if not content.startswith(b'RIFF'):
                print(f"[IndexTTS2] WARNING: Response doesn't look like a WAV file")
                raise ValueError("Invalid audio format received from IndexTTS-2")
            
            print(f"[IndexTTS2] Received {len(content)} bytes")
            return content
