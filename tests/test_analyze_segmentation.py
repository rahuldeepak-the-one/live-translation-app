"""The analysis that turns a service into a threshold.

The classification is the part worth testing: a cut is WRONG only if it was
flushed as a sentence ending AND the next chunk continued the sentence. Get
that backwards and the proposed SENTENCE_GRACE_S is derived from the wrong
population entirely.
"""
import json

from analyze_segmentation import classify, continues_previous, load_cuts


def cut(text, gap, complete=True, reason="silence"):
    return {"kind": "cut", "reason": reason, "chunk_s": 5.0,
            "trailing_silence_s": 0.7, "speech_gap_s": gap,
            "looked_complete": complete, "text": text}


def write(tmp_path, rows):
    path = tmp_path / "service.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_load_ignores_utterance_rows():
    # A row with no `kind` is an utterance; only cuts are graded.
    assert continues_previous("with practical foresight.") is True


def test_a_lowercase_opener_is_a_continuation():
    assert continues_previous("with practical foresight.") is True
    assert continues_previous("purposefully when time is running out.") is True


def test_a_capitalised_opener_is_a_new_sentence():
    assert continues_previous("The steward is not an example.") is False
    assert continues_previous("Jesus praises shrewdness.") is False


def test_leading_punctuation_does_not_hide_a_continuation():
    assert continues_previous('"with practical foresight.') is True
    assert continues_previous("(and that matters)") is True


def test_empty_text_is_not_a_continuation():
    assert continues_previous("") is False
    assert continues_previous(None) is False


def test_load_cuts_reads_only_cut_rows(tmp_path):
    path = write(tmp_path, [
        {"ts": "x", "id": 1, "en": "an utterance", "translations": {}},
        cut("The purpose is to contrast moral character.", 0.3),
        {"ts": "x", "id": 2, "en": "another utterance", "translations": {}},
    ])
    assert len(load_cuts(path)) == 1


def test_load_cuts_survives_a_truncated_final_line(tmp_path):
    # A service that loses power mid-write must still be analysable.
    path = tmp_path / "torn.jsonl"
    path.write_text(json.dumps(cut("a", 0.3)) + "\n{\"kind\": \"cu",
                    encoding="utf-8")
    assert len(load_cuts(path)) == 1


def test_a_breath_is_classified_wrong():
    # Flushed as an ending, but the next chunk continued the sentence.
    cuts = [cut("...to contrast moral character.", 0.28),
            cut("with practical foresight.", 3.0)]
    wrong, right = classify(cuts)
    assert [c["speech_gap_s"] for c in wrong] == [0.28]
    assert right == []


def test_a_real_ending_is_classified_right():
    cuts = [cut("He is an example of urgency.", 2.4),
            cut("Jesus praises shrewdness.", 3.0)]
    wrong, right = classify(cuts)
    assert wrong == []
    assert [c["speech_gap_s"] for c in right] == [2.4]


def test_a_cut_that_did_not_look_complete_is_not_graded():
    # Those were already held and rejoined — the behaviour that works.
    cuts = [cut("the man's ability to un-", 0.3, complete=False),
            cut("understand his situation.", 3.0)]
    wrong, right = classify(cuts)
    assert wrong == [] and right == []


def test_a_cut_with_no_gap_is_not_graded():
    # Speech never resumed, so there is nothing to grade the wait against.
    cuts = [cut("The final sentence.", None), cut("anything", 1.0)]
    wrong, right = classify(cuts)
    assert wrong == [] and right == []


def test_the_last_cut_is_not_graded_having_no_successor():
    wrong, right = classify([cut("only one.", 0.3)])
    assert wrong == [] and right == []


def test_the_two_populations_are_kept_separate():
    cuts = [
        cut("...to contrast moral character.", 0.28),   # wrong: breath
        cut("with practical foresight.", 2.9),          # right: real ending
        cut("He is an example of urgency.", 0.31),      # wrong: breath
        cut("purposefully when time runs out.", 3.4),   # right
        cut("Moral values matter.", 1.0),
    ]
    wrong, right = classify(cuts)
    assert sorted(c["speech_gap_s"] for c in wrong) == [0.28, 0.31]
    assert sorted(c["speech_gap_s"] for c in right) == [2.9, 3.4]
