/* Extractive recap: which sentences carried the message.
 *
 * ponytail: frequency scoring (Luhn), not a summariser model. It SELECTS
 * sentences that were actually said rather than writing new prose. That is a
 * deliberate ceiling — it cannot say "the speaker moved on to forgiveness",
 * only show you the three sentences where forgiveness was said. Upgrade path:
 * an abstractive summary once there is VRAM to spare beside Whisper +
 * IndicTrans2; the view layer takes a list of sentences either way, so only
 * this file changes.
 *
 * Runs on the ENGLISH text even when the reader has chosen Malayalam: English
 * is the one field always present (translations arrive late or not at all), so
 * scoring on it keeps the selection stable while translations land. The view
 * then renders each picked sentence in the reader's own language.
 */

// English function words, plus the KJV register a sermon quotes in. Without
// the second group "unto"/"thou"/"shall" score as content and drag scripture
// quotations to the top purely for being scripture.
const STOP = new Set(`
a about above after again against all also am an and any are as at be because been
before being below between both but by came can cannot come could did do does doing
down during each even for from further get go going had has have having he her here
hers herself him himself his how i if in into is it its itself just let like make
may me might more most much must my myself no nor not now of off on once one only
or other others ought our ours ourselves out over own said same say see she should
so some such take than that the their theirs them themselves then there these they
this those though through to too under until up upon us very was we were what when
where which while who whom whose why will with within would you your yours yourself
thee thou thy thine ye unto shall hath saith doth dost art verily behold lo yea
`.trim().split(/\s+/));

const WORD = /[\p{L}\p{N}'’]+/gu;

function contentWords(text) {
  const out = [];
  for (const raw of text.toLowerCase().match(WORD) || []) {
    const w = raw.replace(/['’]s$/, "");
    if (w.length > 2 && !STOP.has(w)) out.push(w);
  }
  return out;
}

/* |A ∩ B| / |smaller|. Preaching repeats itself deliberately; without this two
   near-identical restatements both score high and both get picked. */
function overlap(a, b) {
  let shared = 0;
  for (const w of a) if (b.has(w)) shared++;
  return shared / Math.min(a.size, b.size);
}

function score(set, df, ceiling) {
  let total = 0;
  for (const w of set) {
    const n = df.get(w);
    if (n <= ceiling) total += n;       // see `ceiling` below
  }
  // sqrt, not length: dividing by the full count hands every win to terse
  // fragments, dividing by nothing hands it to the longest sentence spoken.
  return total / Math.sqrt(set.size);
}

/**
 * Pick the sentences worth re-reading, oldest first.
 *
 * @param {Array<{id:*, en:string, final?:boolean}>} sentences  in spoken order
 * @returns {Array} a subset of the SAME objects — never copies, so the caller
 *   still has `translations` and can render each one in the chosen language.
 */
export function selectRecap(sentences, opts = {}) {
  const {
    minPool = 8,        // below this there is nothing to summarise, just read it
    minWords = 4,       // content words; "Amen." is not a key moment
    ratio = 0.12,
    min = 3,
    max = 12,
    maxOverlap = 0.6,
  } = opts;

  const pool = [];
  for (const s of sentences) {
    // A sentence the server may still revise would be picked on half a
    // thought and then silently rewritten under the reader.
    if (s.final === false || typeof s.en !== "string") continue;
    const set = new Set(contentWords(s.en));
    if (set.size >= minWords) pool.push({ s, set, i: pool.length });
  }
  if (pool.length < minPool) return [];

  // Document frequency, not term frequency: one sentence that says "grace"
  // nine times should not outrank nine sentences that each say it once.
  const df = new Map();
  for (const { set } of pool) for (const w of set) df.set(w, (df.get(w) || 0) + 1);

  // A word in half the sentences separates nothing — it is a stopword for THIS
  // transcript specifically ("jesus" in a sermon, "quarter" in a results call).
  const ceiling = pool.length * 0.5;

  const ranked = pool
    .map((p) => ({ ...p, score: score(p.set, df, ceiling) }))
    .sort((a, b) => b.score - a.score || a.i - b.i);

  const k = Math.min(max, Math.max(min, Math.round(pool.length * ratio)));
  const picked = [];
  for (const cand of ranked) {
    if (picked.length >= k) break;
    if (picked.some((p) => overlap(p.set, cand.set) > maxOverlap)) continue;
    picked.push(cand);
  }

  // Ranked by score for selection, returned in spoken order: a recap you read
  // top to bottom is a retelling, one ranked by score is a word-frequency dump.
  return picked.sort((a, b) => a.i - b.i).map((p) => p.s);
}
