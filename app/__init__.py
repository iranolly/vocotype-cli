"""Core runtime package for the speak-keyboard application."""

from .config import DEFAULT_CONFIG, ensure_logging_dir, load_config
from .audio_capture import AudioCapture
from .transcribe import TranscriptionWorker, TranscriptionResult
from .hotkeys import HotkeyManager
from .output import type_text
from .post_processor import PostProcessor
from .replacement_dict import ReplacementDict
from .proper_nouns import ProperNouns
from .ai_corrector import AICorrector

__all__ = [
    "DEFAULT_CONFIG",
    "ensure_logging_dir",
    "load_config",
    "AudioCapture",
    "TranscriptionWorker",
    "TranscriptionResult",
    "HotkeyManager",
    "type_text",
    "PostProcessor",
    "ReplacementDict",
    "ProperNouns",
    "AICorrector",
]



