"""Central configuration — every tunable lives here."""

# --- Models ---
WHISPER_MODEL = "distil-large-v3"          # English-only distilled Whisper
TRANSLATOR_BACKEND = "indictrans2"          # "indictrans2" | "nllb"
INDICTRANS2_MODEL = "ai4bharat/indictrans2-en-indic-dist-200M"
NLLB_MODEL = "facebook/nllb-200-distilled-600M"

# --- Languages ---
SOURCE_LANG = "en"
TARGET_LANGS = ["ml", "te", "hi"]           # translation targets (en is the source)
FLORES_CODES = {
    "en": "eng_Latn",
    "ml": "mal_Mlym",
    "te": "tel_Telu",
    "hi": "hin_Deva",
}

# --- Audio / chunking (M1 engine) ---
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 300      # RMS below this = silence
SILENCE_DURATION_S = 0.6     # trailing silence that triggers processing
MAX_BUFFER_S = 8.0           # force-process after this long
MIN_SPEECH_S = 0.5           # ignore blips shorter than this
MIN_TRIGGER_S = 1.5          # need at least this much audio before silence-trigger

# --- Broadcast ---
HISTORY_SIZE = 10            # sentences replayed to late-joining screens

# --- Server ---
HOST = "0.0.0.0"
PORT = 8080
