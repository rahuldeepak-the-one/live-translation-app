"""Turn a service's cut records into a SENTENCE_GRACE_S value.

    python analyze_segmentation.py transcripts/2026-08-30.jsonl

WHAT IT DECIDES. A cut that `looks_complete()` accepted was treated as the end
of a sentence and flushed to the translator. It was WRONG to do that whenever
the next chunk turns out to continue the same sentence — the speaker had only
paused for breath. Whisper marks continuations by capitalisation: all 24
continuation chunks in the 2026-08-21 session began lowercase, so a following
chunk that starts lowercase is the ground truth for a bad cut.

The grace hold fixes those by waiting before flushing. It must wait long enough
to cover the breaths and not so long that it adds latency to every real
sentence ending, so SENTENCE_GRACE_S wants to sit between the two populations
of `speech_gap_s`. This prints both distributions and proposes a value.

WHY THIS IS NOT GUESSWORK. MAX_SENTENCE_HOLD_S was guessed at 4.0s once. It
expired before the continuation existed in 26 of 28 cases and inverted the
sermon in three languages. Every tuned constant in config.py cites a measurement
for that reason; this script produces the one this constant needs.

READ THE COVERAGE LINE BEFORE TRUSTING THE NUMBER. If the populations overlap,
no threshold separates them and the honest answer is that timing alone cannot
fix these cuts — which is the finding that would justify the Stage 3 repair
pass instead.
"""
import json
import statistics
import sys


def load_cuts(path):
    """Cut records in file order. Rows without `kind` are utterances."""
    cuts = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") == "cut":
                cuts.append(row)
    return cuts


def continues_previous(text):
    """Whisper capitalises a new sentence; a lowercase opener is a continuation."""
    stripped = (text or "").lstrip("\"'([ ")
    return bool(stripped) and stripped[0].islower()


def classify(cuts):
    """(wrong, right): cuts we flushed as sentence endings, and whether we should have.

    Only cuts where looks_complete() said yes are graded — the others were
    already held and rejoined, which is the behaviour that works.
    """
    wrong, right = [], []
    for cut, following in zip(cuts, cuts[1:]):
        if not cut.get("looked_complete"):
            continue
        if cut.get("speech_gap_s") is None:
            continue          # speech never resumed; nothing to grade against
        (wrong if continues_previous(following.get("text")) else right).append(cut)
    return wrong, right


def describe(label, cuts):
    gaps = sorted(c["speech_gap_s"] for c in cuts)
    if not gaps:
        print(f"  {label:<28} none")
        return gaps
    print(f"  {label:<28} n={len(gaps):<4} "
          f"min={gaps[0]:.2f}s  median={statistics.median(gaps):.2f}s  "
          f"p90={gaps[int(len(gaps) * 0.9)]:.2f}s  max={gaps[-1]:.2f}s")
    return gaps


def main(path):
    cuts = load_cuts(path)
    if not cuts:
        print(f"No cut records in {path}. Was the service run with the Stage 2b "
              f"instrumentation in place?")
        return 1

    reasons = {}
    for cut in cuts:
        reasons[cut.get("reason")] = reasons.get(cut.get("reason"), 0) + 1
    print(f"{len(cuts)} cuts in {path}")
    print("  by reason:", ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    print()

    wrong, right = classify(cuts)
    graded = len(wrong) + len(right)
    if not graded:
        print("No gradeable cuts — every cut either was not treated as a sentence "
              "ending, or had no following chunk to check against.")
        return 1

    print(f"Of {graded} cuts flushed as sentence endings, "
          f"{len(wrong)} were wrong ({len(wrong) / graded:.0%}) — the next chunk "
          f"continued the sentence.")
    print()
    breaths = describe("WRONG (a breath)", wrong)
    endings = describe("RIGHT (a real ending)", right)
    print()

    if not breaths or not endings:
        print("Need both populations to choose a threshold.")
        return 1

    # A grace hold must outlast the breaths. Overshooting costs latency on every
    # real ending, so the cheapest sufficient value is just past the longest
    # breath — provided that still clears the shortest real ending.
    proposal = max(breaths) + 0.1
    overlap = [g for g in endings if g < max(breaths)]

    print(f"Longest breath      {max(breaths):.2f}s")
    print(f"Shortest real ending {min(endings):.2f}s")
    print()
    if overlap:
        print(f"POPULATIONS OVERLAP — {len(overlap)} of {len(endings)} real endings "
              f"are shorter than the longest breath.")
        print("No single threshold separates them. A grace hold set past the "
              "breaths would add that much latency to those endings too.")
        print(f"Best compromise: SENTENCE_GRACE_S = {statistics.median(breaths) * 2:.1f}  "
              f"(catches most breaths; measure the residual before trusting it).")
        print("If the overlap is large, timing alone cannot fix these cuts and "
              "the case for the Stage 3 repair pass is the real finding here.")
    else:
        print(f"Clean separation. Proposed:  SENTENCE_GRACE_S = {proposal:.1f}")
        print(f"Costs {proposal:.1f}s of extra latency on a finished sentence, and "
              f"only when the speaker has actually stopped talking.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
