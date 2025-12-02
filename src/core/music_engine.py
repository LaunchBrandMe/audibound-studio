from abc import ABC, abstractmethod
import os
import shutil
import httpx
import tempfile

class MusicProvider(ABC):
    @abstractmethod
    async def get_music(self, style_description: str, duration: float) -> str:
        """
        Generate or retrieve music and return the path to the music file.
        
        Args:
            style_description: Description of the music style or track_id for stock provider
            duration: Duration in seconds
            
        Returns:
            Path to music file
        """
        pass

class StockMusicProvider(MusicProvider):
    def __init__(self, library_path: str):
        self.library_path = library_path
        # In a real app, this would index a folder of MP3s
    
    async def get_music(self, style_description: str, duration: float) -> str:
        # For MVP, we just return a placeholder file based on style_description as track_id
        # We assume the user puts some test files in `assets/music`
        
        # Use style_description as filename (sanitized)
        safe_name = "".join(c for c in style_description if c.isalnum() or c in (' ', '_')).rstrip()
        mock_path = os.path.join(self.library_path, f"{safe_name.replace(' ', '_')}.mp3")
        
        # Return path even if it doesn't exist (caller will handle missing files)
        return mock_path

class MusicGenProvider(MusicProvider):
    """
    Music provider using Meta's MusicGen via Modal endpoint.
    """
    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url

    async def get_music(self, style_description: str, duration: float, max_retries: int = 3) -> str:
        """
        Generate music using MusicGen and return path to temp file.

        Args:
            style_description: Text description of music style
                              (e.g., "suspenseful orchestral strings")
            duration: Duration in seconds
            max_retries: Maximum number of retry attempts

        Returns:
            Path to generated WAV file
        """
        print(f"[MusicGenProvider] Generating music: '{style_description}' ({duration}s)")

        last_error = None
        for attempt in range(max_retries):
            try:
                # Call Modal endpoint with longer timeout (music takes longer than voice)
                async with httpx.AsyncClient(timeout=1200.0) as client:
                    response = await client.post(
                        self.endpoint_url,
                        json={
                            "style_description": style_description,
                            "duration": duration
                        }
                    )
                    response.raise_for_status()
                    audio_bytes = response.content

                # Validate we got actual audio data
                if not audio_bytes or len(audio_bytes) < 100:
                    raise ValueError(f"Received invalid audio data (size: {len(audio_bytes) if audio_bytes else 0} bytes)")

                # Save to temporary file
                temp_file = tempfile.NamedTemporaryFile(
                    suffix='.wav',
                    delete=False,
                    prefix='music_'
                )
                temp_file.write(audio_bytes)
                temp_file.close()

                print(f"[MusicGenProvider] Saved music to {temp_file.name} ({len(audio_bytes)} bytes)")
                return temp_file.name

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"[MusicGenProvider] Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    import asyncio
                    await asyncio.sleep(wait_time)
                else:
                    print(f"[MusicGenProvider] All {max_retries} attempts failed for '{style_description}'")

        raise RuntimeError(f"Failed to generate music after {max_retries} attempts: {last_error}")

def get_music_provider(provider_type: str = "stock", **kwargs) -> MusicProvider:
    """
    Factory function to get a music provider.
    
    Args:
        provider_type: Type of provider ("stock", "musicgen")
        **kwargs: Additional arguments for the provider
        
    Returns:
        MusicProvider instance
    """
    if provider_type == "musicgen":
        endpoint = kwargs.get("endpoint_url") or os.getenv("MUSICGEN_MODAL_ENDPOINT")
        if not endpoint:
            raise ValueError("MusicGen endpoint URL not provided")
        return MusicGenProvider(endpoint_url=endpoint)
    else:
        return StockMusicProvider(library_path=kwargs.get("library_path", "assets/music"))

