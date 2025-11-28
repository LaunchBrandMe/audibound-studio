"""Voice Library Management System

Handles storage, retrieval, and management of custom voice references
for voice cloning with StyleTTS2/Sesame.
"""

import json
import os
import uuid
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
import shutil

VOICE_LIBRARY_FILE = "voice_library.json"
REFERENCES_DIR = "references"
CUSTOM_UPLOADS_DIR = os.path.join(REFERENCES_DIR, "custom_uploads")

class VoiceLibrary:
    def __init__(self):
        self._ensure_directories()
        self.voices = self._load_library()
    
    def _ensure_directories(self):
        """Create necessary directories if they don't exist"""
        os.makedirs(CUSTOM_UPLOADS_DIR, exist_ok=True)
        os.makedirs(os.path.join(REFERENCES_DIR, "kokoro_generated"), exist_ok=True)
        os.makedirs(os.path.join(REFERENCES_DIR, "dataset_samples"), exist_ok=True)
    
    def _load_library(self) -> List[Dict]:
        """Load voice library from JSON file"""
        if not os.path.exists(VOICE_LIBRARY_FILE):
            return []
        
        try:
            with open(VOICE_LIBRARY_FILE, 'r') as f:
                data = json.load(f)
                return data.get('voices', [])
        except Exception as e:
            print(f"Error loading voice library: {e}")
            return []
    
    def _save_library(self):
        """Save voice library to JSON file"""
        with open(VOICE_LIBRARY_FILE, 'w') as f:
            json.dump({'voices': self.voices}, f, indent=2)
    
    def add_voice(
        self,
        name: str,
        audio_bytes: bytes,
        filename: str,
        engine: str = "styletts2",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Add a new voice to the library
        
        Args:
            name: Human-readable name for the voice
            audio_bytes: Audio file bytes
            filename: Original filename
            engine: TTS engine (styletts2, sesame)
            tags: List of tags (female, male, british, etc.)
            metadata: Additional metadata
        
        Returns:
            Voice entry dictionary
        """
        voice_id = str(uuid.uuid4())
        
        # Determine file extension
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ['.wav', '.mp3', '.m4a']:
            ext = '.wav'
        
        # Save audio file
        reference_filename = f"{voice_id}{ext}"
        reference_path = os.path.join(CUSTOM_UPLOADS_DIR, reference_filename)
        
        with open(reference_path, 'wb') as f:
            f.write(audio_bytes)
        
        # Get audio metadata
        try:
            import librosa
            audio_data, sr = librosa.load(reference_path, sr=None)
            duration = len(audio_data) / sr
            sample_rate = sr
        except Exception as e:
            print(f"Warning: Could not extract audio metadata: {e}")
            duration = 0
            sample_rate = 24000
        
        # Create voice entry
        voice_entry = {
            'id': voice_id,
            'name': name,
            'engine': engine,
            'reference_file': reference_path,
            'tags': tags or [],
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'sample_rate': sample_rate,
            'duration_seconds': round(duration, 2),
            'metadata': metadata or {}
        }
        
        self.voices.append(voice_entry)
        self._save_library()
        
        print(f"[VoiceLibrary] Added voice: {name} (ID: {voice_id})")
        return voice_entry
    
    def get_voice(self, voice_id: str) -> Optional[Dict]:
        """Get voice by ID"""
        for voice in self.voices:
            if voice['id'] == voice_id:
                return voice
        return None
    
    def get_all_voices(self) -> List[Dict]:
        """Get all voices in library"""
        return self.voices
    
    def delete_voice(self, voice_id: str) -> bool:
        """Delete a voice from library"""
        voice = self.get_voice(voice_id)
        if not voice:
            return False
        
        # Delete audio file
        reference_file = voice['reference_file']
        if os.path.exists(reference_file):
            os.remove(reference_file)
            print(f"[VoiceLibrary] Deleted file: {reference_file}")
        
        # Remove from library
        self.voices = [v for v in self.voices if v['id'] != voice_id]
        self._save_library()
        
        print(f"[VoiceLibrary] Deleted voice: {voice['name']} (ID: {voice_id})")
        return True
    
    def update_voice(self, voice_id: str, updates: Dict) -> Optional[Dict]:
        """Update voice metadata"""
        voice = self.get_voice(voice_id)
        if not voice:
            return None
        
        # Update allowed fields
        allowed_fields = ['name', 'tags', 'metadata', 'engine']
        for field in allowed_fields:
            if field in updates:
                voice[field] = updates[field]
        
        self._save_library()
        print(f"[VoiceLibrary] Updated voice: {voice_id}")
        return voice
    
    def search_voices(self, query: str = "", tags: Optional[List[str]] = None) -> List[Dict]:
        """Search voices by name or tags"""
        results = self.voices
        
        # Filter by query
        if query:
            query_lower = query.lower()
            results = [
                v for v in results 
                if query_lower in v['name'].lower() or 
                   any(query_lower in tag.lower() for tag in v.get('tags', []))
            ]
        
        # Filter by tags
        if tags:
            results = [
                v for v in results
                if any(tag in v.get('tags', []) for tag in tags)
            ]
        
        return results

# Global instance
_voice_library = None

def get_voice_library() -> VoiceLibrary:
    """Get or create global voice library instance"""
    global _voice_library
    if _voice_library is None:
        _voice_library = VoiceLibrary()
    return _voice_library
