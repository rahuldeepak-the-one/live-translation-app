"""Central configuration — every tunable lives here."""

# --- Models ---
# English-only distilled Whisper. v3.5 is the same 756M architecture and the
# same VRAM as v3, distilled from 4x more data (98k hours) with a longer
# schedule, so it is a drop-in swap — only this string changes.
#
# Measured here 2026-09-19, 60 LibriSpeech test-clean utterances (1275 words),
# clean and under noise shaped to 82% of its energy below 300Hz to stand in for
# this laptop's fans:
#            clean      10dB SNR   5dB SNR
#   v3       1.88%      1.96%      2.12%
#   v3.5     1.73%      1.88%      1.73%
# Better in all three, and it barely degrades under noise while v3 gets steadily
# worse — which matters more in this room than the clean figure does.
#
# Read those deltas as a direction, not a magnitude: 22-27 errors on 1275 words
# is a small count, and LibriSpeech is clean read American English while the
# real input is live Indian-English preaching through a mixer. The honest test
# is a recorded service; this is the best evidence available without one.
#
# 2026-10-06: replaced by large-v3-turbo for the 5-language upgrade. The
# distilled models above are English-only; turbo is multilingual (it keeps
# large-v3's encoder and cuts the decoder to 4 layers, so ~1.6GB fp16).
# ROLLBACK: put "distil-whisper/distil-large-v3.5-ct2" back (still in the HF
# cache) AND set SOURCE_LANGS = ("en",) — distil cannot detect a language.
#
# 2026-10-10: ROLLED BACK. Replaying the three 2026-09-20 recordings (1280s,
# 356 cuts, same machine, one model at a time): turbo took 1345s of STT for
# 1280s of audio — slower than real time before MT even runs — against 636s for
# distil (median chunk 3.9s vs 1.75s, part of it the extra detect_language
# encoder pass). And in 20 of 356 chunks turbo read WHISPER_INITIAL_PROMPT back
# as speech ("Father, we thank You for Your mercy, ..."); distil did so in 0.
# Language detection itself was clean: 356/356 English. Fix both before
# retrying turbo; until then it is English-in, five lanes out.
WHISPER_MODEL = "distil-whisper/distil-large-v3.5-ct2"
TRANSLATOR_BACKEND = "indictrans2"          # "indictrans2" | "nllb"
INDICTRANS2_MODEL = "ai4bharat/indictrans2-en-indic-dist-200M"
NLLB_MODEL = "facebook/nllb-200-distilled-600M"

# --- Languages ---
SOURCE_LANG = "en"
# Languages a chunk may be detected as. Whisper chooses among ~100; anything
# outside this set is noise or a misdetection, so the choice is restricted to
# these. Only English is translated today (the MT model is en->indic only):
# a chunk in another of these is shown as said, in every lane.
SOURCE_LANGS = ("en",)   # was ("en", "ml", "hi", "pa", "te") on turbo; see above
# The sermon is English and the speaker's accent is Indian, which Whisper can
# read as Hindi. So English is the default and another language must win this
# much probability on its own to take the chunk.
# CALIBRATION KNOB — see the 2026-10-06 replay note in stt.choose_language.
LANG_SWITCH_MIN_PROB = 0.5
TARGET_LANGS = ["ml", "te", "hi", "pa"]     # translation targets (en is the source)
FLORES_CODES = {
    "en": "eng_Latn",
    "ml": "mal_Mlym",
    "te": "tel_Telu",
    "hi": "hin_Deva",
    "pa": "pan_Guru",
}

# --- Audio / chunking (M1 engine) ---
SAMPLE_RATE = 16000
# Calibrated against the Yamaha MG-XU feed in the church, 2026-09-20: 79
# windows of live preaching measured a noise floor of RMS 31-37 (p5-p20) and
# speech of RMS 232-1031 (p50-p99). At 300 this threshold sat INSIDE the speech
# distribution and called 57% of windows silence, so the buffer hit its
# SILENCE_DURATION_S between ordinary words and cut at the MIN_TRIGGER_S floor.
# A 1.5s fragment is the input distil-whisper repeats on, which is where
# "the foundation of the foundation of the foundation" came from - a single
# 1.5s chunk. 120 is ~4x the measured floor and ~half the median speech window,
# so it sits in the gap rather than in either population.
# Re-measure if the room or the board gain changes. ./use-mic.sh --floor used
# to do this and went with the browser capture path; run its pipe directly,
# with nobody speaking (miclevel reads to EOF, so the capture must be bounded):
#   timeout 8 parec --device <name> --raw --format=s16le --rate=16000 \
#       --channels=2 | python3 miclevel.py floor
SILENCE_THRESHOLD = 120      # RMS below this = silence
SILENCE_DURATION_S = 0.6     # trailing silence that triggers processing
MAX_BUFFER_S = 8.0           # force-process after this long
MIN_SPEECH_S = 0.5           # ignore blips shorter than this
MIN_TRIGGER_S = 1.5          # need at least this much audio before silence-trigger
SPEECH_WINDOW_S = 0.1       # window size for the "is there any speech here" check

# faster-whisper's own Silero VAD, applied INSIDE transcribe() before decoding.
# Off, because AudioBuffer already decides what is speech — using its own RMS
# windows, calibrated to this room — and a second opinion here is not a safety
# net but a second, stricter gate that silently deletes what the first one
# passed. Measured on the tee's clips, 2026-09-20 (transcripts/audio/):
#
#   8.0s of audio, 100% of windows above SILENCE_THRESHOLD
#     vad_filter=True  -> 'be my'
#     vad_filter=False -> 'the cross be my glory ever to'
#
# Eight seconds in, two words out, was this parameter — not the mixer, not the
# transport (the tee proves 0% of received audio failed to reach the model) and
# not the hallucination filters (no_speech_prob was 0.00 on every clip).
# Turn it back on only if silence starts producing hallucinated text that
# NO_SPEECH_PROB_MAX and HALLUCINATION_PHRASES do not already catch.
WHISPER_VAD_FILTER = False

# --- Broadcast ---
# Sentences replayed to a late-joining screen. This counted CHUNKS until Stage 2
# made a sentence the published unit; a chunk ran a median of 3 sentences on
# 2026-08-21, so 10 became roughly 3 sentences of backlog rather than 10.
# Raised to keep the same amount of readable history behind a reconnect — a
# projector that drops its WiFi mid-service must come back with the passage
# still on it, not three lines.
HISTORY_SIZE = 30

# --- Server ---
HOST = "0.0.0.0"
PORT = 8080

# --- TLS ---
# Optional. This was mandatory while capture lived in a browser: Chrome exposes
# getUserMedia only in a "secure context", http://localhost is one and
# http://192.168.1.110 is not, so over plain HTTP the phone meant to be the
# microphone never got a permission prompt. Capture moved to ./feed-mic.py, so
# what is left is keeping /view's sermon text off the clear on a venue WiFi.
# Generated by make-cert.sh; see tls.py for why self-signed is the only option
# for a private IP and why missing files fall back to HTTP rather than failing.
SSL_CERTFILE = "certs/server.crt"
SSL_KEYFILE = "certs/server.key"

# --- STT quality gates (hallucination suppression) ---
# distil-large-v3.5 emits confident text ("Thank you.", "Thanks for watching!")
# when handed silence or hiss. AudioBuffer.has_speech() is the first line of
# defence; these are the second, applied per Whisper segment.
NO_SPEECH_PROB_MAX = 0.6      # Whisper's own "this isn't speech" score
# An ACCENT LOWERS CONFIDENCE THE SAME WAY A HALLUCINATION DOES, so this
# threshold cannot tell them apart — and at -1.0 it was discarding 364 of the
# 514 words the model produced (71% of real content) on Indian-English speech.
# Measured 2026-09-20 over 120 NPTEL lecture clips; corpus WER vs reference,
# sweeping this value and touching nothing else:
#     -0.8  91.8%      -1.4  70.3%      -2.0  65.9%
#     -1.0  85.6% <-- was    -1.6  67.7%      off   65.5%
#     -1.2  77.0%      -1.8  66.1%
# Monotonic. The threshold never once paid for itself.
#
# It is also NOT what protects against noise, which was the reason it existed.
# Fed digital silence, fan noise at this laptop's measured floor (RMS 3806) and
# white hiss, Silero VAD stopped it before any score was read. Silero is off
# now (WHISPER_VAD_FILTER above), so the loudness gate (SILENCE_THRESHOLD) is what keeps
# noise out of Whisper, and the "you" filter catches what slips past. And it
# never caught the hallucination this church actually
# got: "Thank you." 39 times on 2026-08-23, high-confidence, straight through.
#
# -2.0 rather than off: ~97% of the available gain, while still flooring total
# garbage for conditions synthetic noise does not reproduce (a conversation in
# the next room, music through a wall). Repetition has its own, better-aimed
# filter in COMPRESSION_RATIO_MAX below.
AVG_LOGPROB_MIN = -2.0        # drop only catastrophically bad decodes
COMPRESSION_RATIO_MAX = 2.2   # drop repetition collapse ("eh, eh, eh, eh")

# Phrases Whisper learned from YouTube outros. Deliberately conservative: bare
# "Thank you." is NOT here, because a speaker genuinely says it. Nothing stops a
# hallucinated bare "Thank you." today: it passed both the energy gate and
# NO_SPEECH_PROB_MAX 39 times on 2026-08-23.
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
# MEASURED 2026-09-20, on 7 clips from the tee that stuttered live: passing
# hotwords AND an initial_prompt together produced 42.4 stutters per 100 words.
# Dropping EITHER one produced ZERO — the two are not independently harmful,
# their SUM is:
#
#   hotwords+prompt  'from from from from from the'
#   prompt only      'from Catalina Island to the'
#   hotwords only    'from Catalina Island to the'
#
# faster-whisper builds one decoder prompt out of both. At 870 + 502 chars this
# runs past Whisper's ~224-token prompt allowance, the context is truncated
# mid-stream, and the decoder answers by looping. Every "off off off off" and
# "drink is drink is drink is drink" on the wall came from here, not from the
# mixer and not from the room.
#
# The prompt is kept rather than the hotwords because it already carries this
# vocabulary IN CONTEXT — "Ephesians", "Jesus Christ", "Holy Spirit", "grace",
# "mercy" all appear in it — while also setting the register the hotword list
# cannot: sermons here are KJV-quoting AND everyday speech.
#
# The curated list survives below for anyone who would rather drop the prompt.
# stt.py refuses to send both, so re-enabling this alone is safe; re-enabling
# it ALONGSIDE the prompt is the regression this comment exists to prevent.
SERMON_HOTWORDS = (
    "Jesus Christ Jehovah Yahweh Holy Spirit Hallelujah Amen Hosanna "
    "Genesis Exodus Leviticus Deuteronomy Joshua Samuel Nehemiah Psalms "
    "Proverbs Ecclesiastes Isaiah Jeremiah Ezekiel Daniel Hosea Malachi "
    "Matthew Mark Luke John Acts Romans Corinthians Galatians Ephesians "
    "Philippians Colossians Thessalonians Timothy Titus Philemon Hebrews "
    "James Peter Jude Revelation "
    "grace mercy righteousness repentance salvation redemption sanctification "
    "covenant testimony scripture gospel disciple apostle prophecy anointing "
    "congregation fellowship communion baptism tithe offering altar pulpit "
    # --- People and places the SERMONS are actually about --------------------
    # The list above names the books of the Bible but not the characters in
    # them, and a sermon is about the characters. Proper nouns are also exactly
    # what an accent breaks first, because there is no language model context
    # to recover them from.
    #
    # Measured on this speaker, 2026-09-19. Reading the KJV aloud he gets
    # "...I will send you to Pharaoh..." correct; speaking in his own voice it
    # came out "Paravo" FOUR times out of four, in an Exodus sermon that also
    # said Egypt 37 times, Israel 28 and Moses 12. The error then propagated:
    # Malayalam rendered the mistake phonetically (പാരാവോ / പരവോ, and
    # inconsistently) instead of ഫറവോൻ, so the congregation read a name that
    # does not exist. Fixing it here fixes the whole chain.
    #
    # Budget: hotwords are truncated at max_length//2 - 1 = 223 tokens and the
    # tail is dropped SILENTLY. Re-measure before adding more.
    "Pharaoh Moses Aaron Egypt Israel Israelites Nile Sinai Canaan Jordan "
    "Abraham Isaac Jacob Joseph David Solomon Elijah Elisha Noah "
    "Jonah Nineveh Esther Ruth Job Babylon Jerusalem Bethlehem "
    "Galilee Nazareth Passover Pentecost Calvary Bible Christian "
    # Jonah, evidenced: "fled to Tarshish" was transcribed "pled to
    # Tarshes" on 2026-08-30, and all three languages then faithfully
    # translated the wrong proper noun.
    "Tarshish Joppa Samaria Gethsemane Golgotha Zion"
)

# Active choice: the prompt, not the hotwords. See the measurement above.
WHISPER_HOTWORDS = None


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

# --- Stage 2b: a breath is not a full stop ---
# SILENCE_DURATION_S is 0.6s, which is a breath. The speaker inhales mid
# sentence, the buffer cuts, Whisper sees audio stopping and writes a confident
# full stop, and looks_complete() believes it. Measured share of cuts flushed as
# sentence endings that were WRONG — the next chunk continued the sentence:
#   2026-08-30 ....  42 of 161  (26%)
#   2026-08-23 .... 163 of 395  (41%)
#
# THIS IS NOT A DISCRIMINATOR, AND THAT IS THE WHOLE POINT. The obvious fix is
# to hold a terminated sentence and flush it if no speech resumes within some
# threshold. No such threshold exists: the gap-to-next-speech populations
# overlap almost entirely (116 of 119 real endings on 2026-08-30 are shorter
# than the longest breath). Worse, a wrong hold is not merely late — the join
# must strip Whisper's false full stop to work at all, so a wrongly held real
# ending becomes a run-on that split_source_sentences() can no longer undo.
#
# What separates them is the NEXT chunk's first letter: Whisper capitalises a
# new sentence, and every continuation chunk observed began lowercase. So the
# decision waits for that letter, and this constant only bounds how long we
# wait before accepting that the speaker simply stopped and no letter is
# coming. Set from the p90 of REAL-ending gaps (1.57s on 2026-08-30, 2.22s on
# 2026-08-23) with headroom, NOT from analyze_segmentation.py's proposal —
# that number answered the discrimination question this design abandons.
#
# The timer measures SILENCE. Speech resumes a median 1.3s after a cut but the
# chunk carrying it is not transcribed for a median 6.2s; a timer counting
# elapsed time would fire in the gap and flush before the deciding letter ever
# existed. See UtterancePipeline.flush_if_stale().
SENTENCE_GRACE_S = 3.0       # how long to wait for the letter that decides

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

# What Whisper says when handed silence. Measured 2026-09-20 with
# WHISPER_VAD_FILTER off: two seconds of digital silence, and two seconds of
# room noise at RMS 35, both decode to a bare "you" with no_speech_prob=0.00,
# avg_logprob=-0.63 and compression_ratio=0.27 — every confidence signal says
# the model is certain, so none of the numeric filters above can touch it.
# Silero's VAD used to hide this; with it off the artifact is ours to catch.
#
# Matched as a WHOLE SEGMENT ONLY, never as a prefix. That distinction is the
# entire point: "you" as a complete utterance is the artifact, while
# "You are the light of the world" is Matthew 5:14, and HALLUCINATION_PHRASES
# would take both because it also matches on startswith.
#
# The cost is a genuine one-word "You." being dropped, which is rare enough and
# cheaper than a caption appearing on the wall during every silent pause.
#
# ONLY "you" — the one artifact actually measured. "thank"/"thanks"/"bye" were
# tried here and reverted: test_bare_thank_you_is_still_kept pins a bare
# "Thank you." as KEPT because a preacher genuinely says it, and that decision
# predates this one. Add a word here only after seeing silence produce it.
SILENCE_ARTIFACTS = frozenset({"you"})

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

    # "The work can become an idol." came back as ఆదర్శం / आदर्श — "ideal".
    # An aspiration to chase, not a false god to reject: the exact opposite of
    # what the sermon said, in two of three languages. (ml got വിഗ്രഹം right,
    # but this table is uniform, so the row was checked in all three.)
    #
    # Plural first: \bidol\b cannot match "idols" anyway, but the order makes
    # that independent of regex subtleties. Neither touches "idolatry" or
    # "idolater", which already translate correctly.
    #
    # Substitution, NOT apposition. "your idols, false gods" was tried and is
    # worse than the bug: all three languages read the appositive as a vocative
    # ("O false gods, turn away from your idols"). Verified 2026-09-20 across 5
    # sentences x 3 languages against a 27B multilingual grader.
    # "an idol" must lose its article too, or the translator is handed
    # "an false god" — IndicTrans2 tolerates it, but feeding a model
    # ungrammatical English to fix a different bug is how the next one starts.
    (r"\ban\s+idol\b", "a false god"),
    (r"\bidols\b", "false gods"),
    (r"\bidol\b", "false god"),
)

# --- Terminology glossary ---
# Each segment is translated independently, so nothing carries a chosen term
# forward: "steward" appeared as four different Malayalam words in one sermon
# on 2026-08-21 (കാവൽക്കാരൻ "watchman", സ്റ്റീവർഡ്, മേൽനോട്ടക്കാർ "overseer",
# സൂക്ഷിപ്പുകാരൻ "custodian"). MT_USE_CONTEXT (off today) would make drift less
# likely; only this table pins a term.
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
# Filled in after replaying the 2026-08-21 sermon through the current pipeline
# and listing every form the model actually produced. Keys are the forms it
# emits, values the agreed term. Every row is covered by
# tests/test_translator.py::test_every_observed_form_maps_to_a_valid_form.
#
# Not listed on purpose: ml "master" (യജമാനൻ already dominates 4:1, and a stem
# rule on ഉടമസ്ഥ would also corrupt ഉടമസ്ഥത "ownership") and te "steward"
# transliterations beyond the two forms seen.
GLOSSARY = {
    "ml": {
        # steward -> കാര്യസ്ഥൻ. The model said കാവൽക്കാരൻ ("watchman") in 5 of
        # 8 segments. Stem-level: ൻ / ന് / ന്റെ all reattach correctly.
        "കാവൽക്കാര": "കാര്യസ്ഥ",
        "മേൽനോട്ടക്കാര": "കാര്യസ്ഥ",
        # plural, listed whole: കാര്യസ്ഥ + മാർ would be wrong.
        "സ്റ്റീവർമാർ": "കാര്യസ്ഥന്മാർ",
    },
    "te": {
        # steward -> గృహనిర్వాహకుడు, which the model already produces unaided
        # in 2 segments; the transliteration won 7. Longest key first, so the
        # plural is rewritten before the singular.
        "స్టీవార్డులు": "గృహనిర్వాహకులు",
        "స్టీవర్డ్": "గృహనిర్వాహకుడు",
        # master -> యజమాని. "మాస్టర్" reads as a schoolteacher.
        "మాస్టర్": "యజమాని",
    },
    "hi": {
        # steward -> कारभारी, the standard biblical Hindi term and already 9 of
        # 11. Oblique plural first: प्रबंधक is a prefix of प्रबंधकों.
        "प्रबंधकों": "कारभारियों",
        "प्रबंधक": "कारभारी",
        # master -> मालिक. "मास्टर" reads as a schoolteacher; "स्वामी" risks
        # being heard as "Lord" and confused with God.
        "मास्टर": "मालिक",
    },
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

# --- Wall display (stage 1) ---
# The wall's startup state. /control overrides it at runtime; nothing persists
# across a restart, which is deliberate — a service always begins predictable.
DEFAULT_LANES = ["en", "ml", "te", "hi", "pa"]
# Auto-rotate bounds. Below 5s nobody finishes a line; above 120s it is not
# rotation, it is a pin the operator forgot about.
ROTATE_MIN_S = 5
ROTATE_MAX_S = 120
