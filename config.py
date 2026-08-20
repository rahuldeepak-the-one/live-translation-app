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
SPEECH_WINDOW_S = 0.1       # window size for the "is there any speech here" check

# --- Broadcast ---
HISTORY_SIZE = 10            # sentences replayed to late-joining screens

# --- Server ---
HOST = "0.0.0.0"
PORT = 8080

# --- STT quality gates (hallucination suppression) ---
# distil-large-v3 emits confident text ("Thank you.", "Thanks for watching!")
# when handed silence or hiss. AudioBuffer.has_speech() is the first line of
# defence; these are the second, applied per Whisper segment.
NO_SPEECH_PROB_MAX = 0.6      # Whisper's own "this isn't speech" score
AVG_LOGPROB_MIN = -1.0        # drop low-confidence decodes (mangled proper nouns)
COMPRESSION_RATIO_MAX = 2.2   # drop repetition collapse ("eh, eh, eh, eh")

# Phrases Whisper learned from YouTube outros. Deliberately conservative: bare
# "Thank you." is NOT here, because a speaker genuinely says it — that case is
# handled by the energy gate and NO_SPEECH_PROB_MAX instead.
HALLUCINATION_PHRASES = (
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subscribe to my channel",
    "like and subscribe",
    "subtitles by",
    "subtitling by",
    "transcription by",
    "amara org",
    "captions by",
)

# --- Domain adaptation: church / Bible ---
# Reference editions chosen for terminology and (later) verse lookup:
#   en -> King James Version      te -> Telugu Bible, BSI "Old Version"
#   ml -> Sathyavedapusthakam     hi -> Pavitra Bible, BSI Hindi "Old Version"
#     (BSI Malayalam OV)
# All four sit in the same formal register, so quoted verses stay consistent.
#
# WHISPER_INITIAL_PROMPT biases decoding toward sermon vocabulary. It mixes
# scripture register WITH ordinary conversational speech on purpose: a prompt
# made only of King James English pushes Whisper to hallucinate "thee/thou/
# verily" into the everyday parts of a message.
WHISPER_INITIAL_PROMPT = (
    "Good morning, church. Turn with me to Ephesians chapter two, verse eight. "
    "For by grace are ye saved through faith; and that not of yourselves: it is "
    "the gift of God. Amen. Now, let me tell you what happened to me this week. "
    "I was driving to work on Monday and the traffic was really bad, and I was "
    "getting frustrated. But here is the thing — the Lord Jesus Christ meets us "
    "right there. Hallelujah. Let us pray. Father, we thank You for Your mercy, "
    "Your grace, and the Holy Spirit. In Jesus' name, amen."
)

# Boosted vocabulary — proper nouns and terms Whisper most often mangles.
# Add your church's names (pastor, congregation, places) to the end of this.
WHISPER_HOTWORDS = (
    "Jesus Christ Jehovah Yahweh Holy Spirit Hallelujah Amen Hosanna "
    "Genesis Exodus Leviticus Deuteronomy Joshua Samuel Nehemiah Psalms "
    "Proverbs Ecclesiastes Isaiah Jeremiah Ezekiel Daniel Hosea Malachi "
    "Matthew Mark Luke John Acts Romans Corinthians Galatians Ephesians "
    "Philippians Colossians Thessalonians Timothy Titus Philemon Hebrews "
    "James Peter Jude Revelation "
    "grace mercy righteousness repentance salvation redemption sanctification "
    "covenant testimony scripture gospel disciple apostle prophecy anointing "
    "congregation fellowship communion baptism tithe offering altar pulpit"
)

# --- Sentence assembly (translation side only) ---
# The screens get English the moment Whisper produces it, but a fragment must
# never reach the translator: ml/te/hi are verb-final, so half an English
# sentence forces the model to invent a verb and then strand the remainder.
# An utterance that does not end in SENTENCE_END_CHARS is held and joined to
# the next one, bounded by MAX_SENTENCE_HOLD_S / MAX_PENDING_CHARS.
# Only true sentence enders. ":" and ";" are deliberately excluded — "he said
# this:" and "the Lord is my shepherd;" are dangling clauses, and a verse
# reference ("Ephesians 2:") must not be cut off from the verse itself.
# "…" is excluded for the same reason, and pipeline.looks_complete() also
# rejects a trailing "..." whose final "." would otherwise match here: an
# ellipsis is Whisper saying the speaker was cut off, not that they finished.
SENTENCE_END_CHARS = ".?!"
# The hold MUST exceed TWO buffer periods, not one. Audio is buffered before
# Whisper ever sees it, and AudioBuffer.should_process() refuses a silence-only
# buffer *without clearing it* — so a quiet stretch defers the cut while the
# buffer keeps filling. Measured gaps between segments on 2026-08-21 ran 2-14s
# (median 7.9) against an 8.0s MAX_BUFFER_S.
# At 4.0s the hold expired before the continuation existed in 26 of 28 cases,
# and "...is never" / "endorsed." were translated as separate sentences —
# inverting the sermon in all three languages. 12.0s still split that exact
# pair, whose gap was 12s. 16.0s covers every gap observed.
# Only the translated line waits; English captions publish immediately.
MAX_SENTENCE_HOLD_S = 16.0   # translate anyway if the speaker trails off
# Backstop for a speaker who never punctuates. Must be large enough for two
# chunks to join (observed chunks ran 116-175 chars) or it re-creates the
# fragment problem the hold above exists to prevent. looks_complete() is
# checked first, so a properly ended sentence longer than this still flushes.
MAX_PENDING_CHARS = 400      # ...or if they never punctuate at all

# --- Non-lexical fillers ---
# Dropped only when a filler is the ENTIRE segment. After the audio stopped on
# 2026-08-21 the pipeline emitted 8 phantom segments in 74 seconds and
# translated each onto the screens. A false start inside real speech ("Uh, turn
# with me to Ephesians") is left alone, and "amen" is deliberately absent:
# short is not the same as meaningless.
FILLER_TOKENS = frozenset(
    "uh uhh um umm er err ah ahh oh ohh eh ehh mm mmm hmm hmmm mhm "
    "huh ugh yeah yeh yep yup nah nope".split()
)

# --- Translation context ---
# IndicTrans2 is a sentence-level model with no memory between calls, which is
# why the same character was renamed four times in one sermon. Prepending the
# previous sentence gives it something to be consistent with; its translation
# is discarded.
#
# Measured on 2026-08-21 seg 3/8/9/13 (Malayalam, "steward"):
#   no context ....................... 4 distinct terms, 0 sentences lost
#   whole previous segment ........... 2 distinct terms, 2 sentences LOST
#   last sentence only ............... 2 distinct terms, 0 sentences lost
# Whole-segment context contaminated the current sentence ("Not the master's
# wealth" came back as "the steward's wealth decreased"), so only the final
# sentence is carried. Target-side decoder priming also stabilised terminology
# and was faster, but dropped the opening of 2 of 3 segments outright.
#
# SHIPS OFF. Re-measured on 8 segments with the debris fix in place:
#   context off .... 3 distinct "steward" terms, 5.7s
#   context on ..... 2 distinct "steward" terms, 8.1s
# The terminology gain is real but small, and it is not free. On seg20 context
# turned "respond accurately and appropriately" into Malayalam
# "...will NOT be responding accurately and appropriately" (ആയിരിക്കില്ല) and
# coined the malformed മേൽനോട്ടംക്കാരൻ. An invented negation is exactly the
# failure the audit was about, so this is not on by default. GLOSSARY below
# cannot change meaning and is the safer tool for terminology.
#
# Turn it on only with A/B evidence for your own speaker. The integrity guard
# in translate_all_sync() retries without context whenever the target comes
# back short, so the downside is latency, not lost content.
MT_USE_CONTEXT = False

# --- Source-side pre-editing ---
# Some English words IndicTrans2 maps wrongly no matter how much context it has.
# Rewriting the English on the way INTO the translator fixes them; the screens
# and the transcript keep what the speaker actually said.
#
# Every row below is a measured before/after on real session text, not a guess.
# Applied case-insensitively — the target scripts have no case, and only the
# translator's input is affected.
#
# Add a row only after checking the replacement in ALL THREE languages: on
# "not dishonesty", dropping the comma alone ("not dishonest behaviour") made
# Malayalam say Jesus *praises* dishonest behaviour. Both halves of the rewrite
# were needed.
SOURCE_REWRITES = (
    # "to contrast X" -> "is contrary to X" in ml, te AND hi.
    # "compare" is correct in all three (താരതമ്യം / పోల్చడం / तुलना).
    (r"\bcontrast\b", "compare"),

    # "praises shrewdness, not dishonesty" lost the negative prefix in ml and
    # te, becoming "praises cleverness, not HONESTY".
    (r",\s*not dishonesty\b", " rather than dishonest behaviour"),
)

# --- Terminology glossary ---
# Each segment is translated independently, so nothing carries a chosen term
# forward: "steward" appeared as four different Malayalam words in one sermon
# on 2026-08-21 (കാവൽക്കാരൻ "watchman", സ്റ്റീവർഡ്, മേൽനോട്ടക്കാർ "overseer",
# സൂക്ഷിപ്പുകാരൻ "custodian"). MT_USE_CONTEXT makes drift less likely; only this
# table pins a term.
#
# Substitution is at STEM level and the suffix is preserved, because Malayalam
# and Telugu agentive nouns inflect on the stem:
#     {"ml": {"കാവൽക്കാര": "കാര്യസ്ഥ"}}
#     കാവൽക്കാരൻ -> കാര്യസ്ഥൻ      (nominative)
#     കാവൽക്കാരന്റെ -> കാര്യസ്ഥന്റെ  (genitive)
#     കാവൽക്കാരർ -> കാര്യസ്ഥർ       (plural)
# Longest key wins, so a stem that prefixes another is safe.
#
# VERIFY EVERY INFLECTED FORM. A trial run mapping the transliterated variant
# സ്റ്റീവർഡ -> കാര്യസ്ഥ produced valid കാര്യസ്ഥൻ in most segments but also
# malformed കാര്യസ്ഥ് (bare virama) and കാര്യസ്ഥിന് (wrong case marker, should be
# കാര്യസ്ഥന്). Map the stem the model actually emits, and read back every case
# it appears in before trusting a row.
#
# DELIBERATELY EMPTY. Choosing the right word for "steward" in each language is
# a native-speaker judgement about register (the sermon quotes KJV but also
# speaks plainly), and a wrong entry is applied to every sentence for the rest
# of the service. Verify each inflected form before adding a row.
GLOSSARY = {
    "ml": {},
    "te": {},
    "hi": {},
}

# --- Transcripts ---
TRANSCRIPT_DIR = "transcripts"

# --- MT decoding ---
# AI4Bharat's own inference recipe uses beam search; greedy (1) was measurably
# rougher. Drop to 1 if the GPU can't keep up with three targets per sentence.
MT_NUM_BEAMS = 5
# Beam search alone will happily emit one token until it hits max_length.
# On 2026-08-21 a trailing "..." made segment 3 produce 214-230 consecutive
# full stops in all three languages, taking 4.34s against a 1.10s mean — and
# because the GPU lock serialises the queue, that stalls every segment behind
# it. These two bound the damage.
MT_NO_REPEAT_NGRAM = 4
MT_REPETITION_PENALTY = 1.15
