"""
Text Cleaner - Post-process ABML text to remove stage directions

Since LLMs don't always follow instructions perfectly, this module
automatically cleans dialogue and narration text.
"""

import re
from typing import Optional

class TextCleaner:
    """Removes stage directions and emotion words from dialogue/narration"""
    
    # Stage direction patterns to remove
    STAGE_DIRECTION_PATTERNS = [
        # "said" variations
        r'\s*,?\s*(?:she|he|they|it)\s+said\s*',
        r'\s*,?\s*said\s+(?:Sarah|Tom|the narrator)\s*',
        
        # "shouted" variations  
        r'\s*,?\s*(?:she|he|they|it)\s+shouted\s*(?:excitedly|angrily|loudly)?\s*',
        r'\s*,?\s*shouted\s*',
        
        # "whispered" variations
        r'\s*,?\s*(?:she|he|they|it)\s+whispered\s*(?:quietly|softly)?\s*',
        r'\s*,?\s*whispered\s*',
        
        # Other common verbs
        r'\s*,?\s*(?:she|he|they|it)\s+(?:exclaimed|asked|replied|murmured|muttered|yelled|called)\s*',
        r'\s*,?\s*(?:exclaimed|asked|replied|murmured|muttered|yelled|called)\s*',
        
        # Adverb patterns
        r'\s*,?\s*(?:excitedly|angrily|sadly|happily|cheerfully|quietly|softly|loudly|urgently)\s*,?\s*',
        
        # "laugh" patterns (different from <laugh> tag)
        r'\s*laugh\s*',
        r'\s*laught\s*',  # Common misspelling
        r'\s*laughing\s*',
    ]
    
    def clean_dialogue(self, text: str) -> str:
        """
        Remove stage directions from dialogue text.
        Returns cleaned dialogue.
        """
        if not text:
            return text
        
        cleaned = text
        
        # Apply all patterns
        for pattern in self.STAGE_DIRECTION_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        # Clean up extra spaces and commas
        cleaned = re.sub(r'\s+', ' ', cleaned)  # Multiple spaces -> single space
        cleaned = re.sub(r'\s*,\s*,\s*', ', ', cleaned)  # Double commas
        cleaned = re.sub(r'^\s*,\s*', '', cleaned)  # Leading comma
        cleaned = re.sub(r'\s*,\s*$', '', cleaned)  # Trailing comma
        cleaned = cleaned.strip()
        
        # Ensure proper capitalization
        if cleaned and cleaned[0].islower():
            cleaned = cleaned[0].upper() + cleaned[1:]
        
        return cleaned
    
    def clean_narration(self, text: str) -> str:
        """
        Remove emotion adverbs from narration.
        """
        if not text:
            return text
        
        cleaned = text
        
        # Remove emotional adverbs
        emotion_adverbs = [
            r'\s+heavily\s+',
            r'\s+quickly\s+',
            r'\s+slowly\s+',
            r'\s+angrily\s+',
            r'\s+sadly\s+',
            r'\s+happily\s+',
            r'\s+excitedly\s+',
            r'\s+nervously\s+',
        ]
        
        for pattern in emotion_adverbs:
            cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
        
        # Clean up spacing
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = cleaned.strip()
        
        return cleaned


def clean_text_if_needed(text: str, is_dialogue: bool = True) -> tuple[str, bool]:
    """
    Clean text and return (cleaned_text, was_modified).
    Use is_dialogue=True for dialogue, False for narration.
    """
    cleaner = TextCleaner()
    
    if is_dialogue:
        cleaned = cleaner.clean_dialogue(text)
    else:
        cleaned = cleaner.clean_narration(text)
    
    was_modified = cleaned != text
    
    if was_modified:
        print(f"[TextCleaner] Cleaned: '{text}' -> '{cleaned}'")
    
    return cleaned, was_modified
