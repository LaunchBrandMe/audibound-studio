from abc import ABC, abstractmethod
import os
import shutil

class MusicProvider(ABC):
    @abstractmethod
    async def get_music(self, track_id: str, style: str) -> str:
        """
        Returns the path to the music file for the given track_id/style.
        """
        pass

class StockMusicProvider(MusicProvider):
    def __init__(self, library_path: str):
        self.library_path = library_path
        # In a real app, this would index a folder of MP3s
    
    async def get_music(self, track_id: str, style: str) -> str:
        # For MVP, we just return a placeholder file if it exists, 
        # or raise an error/return a default.
        # We assume the user puts some test files in `assets/music`
        
        # Mocking: Create a dummy file if it doesn't exist for testing
        mock_path = os.path.join(self.library_path, f"{track_id}.mp3")
        if not os.path.exists(mock_path):
            # Create a dummy file (silence or simple tone would be better, but empty file might break ffmpeg)
            # We will rely on the caller to provide real assets or we just return a path and hope 
            # the assembler handles missing files or we generate a silence file.
            pass
            
        return mock_path

def get_music_provider(provider_type: str = "stock", **kwargs) -> MusicProvider:
    return StockMusicProvider(library_path=kwargs.get("library_path", "assets/music"))
