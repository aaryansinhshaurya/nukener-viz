"""Find entity offsets in chunk text during dataset upload.

Matching cascade (first tier that yields a free span wins):
  exact     literal, case-sensitive, word-bounded
  case      literal, case-insensitive, word-bounded
  strict    canonical stream (LaTeX / unicode / greek / symbols / signs normalised)
  loose     canonical stream with symbols dropped
  gap       entity words in order, few extra digit-free source words between
  window    entity words in any order inside a tight window
  fuzzy     approximate substring (edit distance; digits must match exactly)
  trim      entity has one extra digit-free word the source lacks
  nosub     extraction dropped subscript digits (Li2O -> "Li O")
  acronym   initialism of a multi-word entity (metallic glasses -> MGs)
  subword   match without word-boundary check (last resort)

The legacy enrichment helper rewrites entity_text to the source span and records
the model's wording in `original_entity_text`. Dataset uploads now use exact or
equivalent mathematical notation matching in ingestion.py instead.
"""
import re
import unicodedata
from collections import Counter

# ----------------------------------------------------------------------------
# TUNABLES
# ----------------------------------------------------------------------------
MAX_GAP_TOKENS = 4        # extra source tokens tolerated inside a "gap" match
MAX_GAP_CHARS = 26        # ... and max compact chars in that gap (digits never allowed)
WINDOW_EXTRA_TOKENS = 2   # window tier: allowed window size = n_tokens + this
FUZZY_MIN_LEN = 6         # shortest compact entity eligible for fuzzy matching
FUZZY_ERR_RATE = 0.15     # max edit distance as a fraction of compact length
FUZZY_MAX_ERR = 4         # hard cap on edit distance
TRIM_MIN_FRACTION = 0.4  # trimmed core must keep this share of the entity
MAX_SPAN_FACTOR = 4       # raw span may not exceed factor * len(entity) + slack
MAX_SPAN_SLACK = 40

# ----------------------------------------------------------------------------
# NORMALISATION TABLES
# ----------------------------------------------------------------------------
DROP_COMMANDS = {
    "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal", "mathbb",
    "mathfrak", "mathscr", "text", "textrm", "textbf", "textit", "textsf",
    "texttt", "operatorname", "boldsymbol", "bm", "rm", "bf", "it", "sf", "tt",
    "cal", "left", "right", "big", "bigg", "bigl", "bigr", "displaystyle",
    "scriptstyle", "textstyle", "begin", "end", "dot", "ddot", "overline",
    "underline", "bar", "tilde", "hat", "vec", "phantom", "hphantom", "hspace",
    "vspace", "quad", "qquad", "limits", "nolimits", "mathord", "mathrel",
    "mathop", "mathbin", "mathpunct", "mathopen", "mathclose", "not", "prime",
    "array", "label", "tag", "frac", "dfrac", "tfrac", "sqrt", "mbox",
}
# command-name aliases -> canonical word (so \leq == \le == U+2264, etc.)
COMMAND_ALIAS = {
    "leq": "le", "leqslant": "le", "geq": "ge", "geqslant": "ge",
    "upmu": "mu", "upalpha": "alpha", "upbeta": "beta", "upgamma": "gamma",
    "varepsilon": "epsilon", "vartheta": "theta", "varphi": "phi",
    "varrho": "rho", "varsigma": "sigma", "backslash": "", "textmu": "mu",
    "rightarrow": "to", "longrightarrow": "to", "approx": "approx",
    "simeq": "sim", "thicksim": "sim", "textdegree": "deg", "circ": "deg",
    "degree": "deg", "cdotp": "cdot", "bullet": "cdot", "ast": "cdot",
    "lt": "lt", "gt": "gt", "perp": "perp", "bot": "perp",
    "parallel": "parallel", "mid": "",
}
GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ϵ": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta", "ϑ": "theta",
    "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi",
    "ο": "o", "π": "pi", "ρ": "rho", "ϱ": "rho", "σ": "sigma", "ς": "sigma",
    "τ": "tau", "υ": "upsilon", "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi",
    "ω": "omega", "Γ": "gamma", "Δ": "delta", "Θ": "theta", "Λ": "lambda",
    "Ξ": "xi", "Π": "pi", "Σ": "sigma", "Φ": "phi", "Ψ": "psi", "Ω": "omega",
}
# unicode symbol -> canonical word (strict mode only)
SYMBOLS = {
    "°": "deg", "◦": "deg", "∘": "deg", "º": "deg", "˚": "deg",
    "×": "times", "✕": "times", "⋅": "cdot", "·": "cdot", "∙": "cdot", "•": "cdot",
    "±": "pm", "∓": "mp", "∼": "sim", "~": "sim", "≈": "approx", "≃": "sim",
    "≤": "le", "≥": "ge", "<": "lt", ">": "gt", "=": "eq", "≠": "ne",
    "%": "pct", "€": "epsilon", "→": "to", "∞": "infty", "√": "sqrt", "∝": "propto",
    "⟨": "langle", "⟩": "rangle", "〈": "langle", "〉": "rangle",
}
# command names that are symbols in strict mode (kept as words)
SYMBOL_COMMANDS = {
    "times", "cdot", "pm", "mp", "sim", "approx", "le", "ge", "lt", "gt", "ne",
    "deg", "to", "infty", "propto", "langle", "rangle", "perp", "parallel",
    "eq", "pct",
}
NEG_CHARS = set("-−–‐‑‒⁻₋")
NEG_PREV = set("{^(=<>,;[:") | {""}
SUP_SUB_NEG = set("⁻₋")

RANGE_WORD = re.compile(r"(?<=[\d}])\s+(to|and)\s+(?=[\d$\\{])", re.I)
RANGE_GAP = re.compile(r"\s*(?:to|and|[–—-])?\s*[\d.,]+\s*", re.I)
MARKUP = re.compile(r"\\[A-Za-z]+|[${}]")

# ----------------------------------------------------------------------------
# CANONICAL STREAM
# ----------------------------------------------------------------------------
class Stream:
    """chars[i] is one canonical char; s[i]/e[i] its source span; sep[i] True if a
    separator (space, punctuation, markup) precedes it in the source."""
    __slots__ = ("chars", "s", "e", "sep", "text")

    def __init__(self, text):
        self.chars, self.s, self.e, self.sep, self.text = [], [], [], [], text

    def string(self):
        return "".join(self.chars)


def build(text, loose=False):
    st = Stream(text)
    pending_sep = True
    n = len(text)
    i = 0
    skip = set()
    for m in RANGE_WORD.finditer(text):
        skip.update(range(m.start(1), m.end(1)))

    def emit(word, s, e):
        nonlocal pending_sep
        for k, ch in enumerate(word):
            st.chars.append(ch)
            st.s.append(s)
            st.e.append(e)
            st.sep.append(pending_sep if k == 0 else False)
            pending_sep = False

    def prev_nonspace(idx):
        idx -= 1
        while idx >= 0 and text[idx].isspace():
            idx -= 1
        return text[idx] if idx >= 0 else ""

    def next_nonspace(idx):
        while idx < n and (text[idx].isspace() or text[idx] in "{"):
            idx += 1
        return text[idx] if idx < n else ""

    while i < n:
        ch = text[i]

        if i in skip:
            pending_sep = True
            i += 1
            continue

        if ch == "\\":
            s = i
            while i < n and text[i] == "\\":
                i += 1
            if i < n and text[i].isalpha():
                j = i
                while j < n and text[j].isalpha():
                    j += 1
                cmd = text[i:j].casefold()
                i = j
                if cmd in DROP_COMMANDS:
                    pending_sep = True
                    continue
                cmd = COMMAND_ALIAS.get(cmd, cmd)
                if not cmd:
                    pending_sep = True
                elif cmd in SYMBOL_COMMANDS:
                    if loose:
                        pending_sep = True
                    else:
                        emit(cmd, s, i)
                else:
                    emit(cmd, s, i)
            else:
                if i < n:
                    i += 1          # \  \, \% \_ \{ ...
                pending_sep = True
            continue

        if ch in "{}$^_":
            pending_sep = True
            i += 1
            continue

        if ch in NEG_CHARS:
            if (not loose
                    and (ch in SUP_SUB_NEG
                         or (prev_nonspace(i) in NEG_PREV
                             and next_nonspace(i + 1).isdigit()))):
                emit("neg", i, i + 1)
            else:
                pending_sep = True
            i += 1
            continue

        if ch in SYMBOLS:
            if loose:
                pending_sep = True
            else:
                emit(SYMBOLS[ch], i, i + 1)
            i += 1
            continue

        if ch.isspace():
            pending_sep = True
            i += 1
            continue

        norm = unicodedata.normalize("NFKD", ch)
        produced = False
        for part in norm:
            if unicodedata.combining(part):
                continue
            if part in GREEK:
                emit(GREEK[part], i, i + 1)
                produced = True
            elif part in SYMBOLS:
                if not loose:
                    emit(SYMBOLS[part], i, i + 1)
                    produced = True
            elif part in NEG_CHARS:
                if not loose and ch in SUP_SUB_NEG:
                    emit("neg", i, i + 1)
                    produced = True
            elif part.isalnum():
                emit(part.casefold(), i, i + 1)
                produced = True
        if not produced and not unicodedata.combining(ch):
            pending_sep = True
        i += 1
    return st


def tokens_of(st):
    """Token = run of atoms with no separator inside. Returns (words, atom_ranges)."""
    words, ranges = [], []
    for idx, ch in enumerate(st.chars):
        if idx == 0 or st.sep[idx]:
            words.append([ch])
            ranges.append([idx, idx + 1])
        else:
            words[-1].append(ch)
            ranges[-1][1] = idx + 1
    return ["".join(w) for w in words], ranges

# ----------------------------------------------------------------------------
# SPAN HELPERS
# ----------------------------------------------------------------------------
def balance(text, start, end):
    """Extend end so LaTeX braces / $ opened inside the span are closed."""
    depth = 0
    for ch in text[start:end]:
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
    cur = end
    while depth > 0 and cur < len(text):
        look = cur
        while look < len(text) and text[look] in " ,.;:":
            look += 1
        if look < len(text) and text[look] == "}":
            cur = look + 1
            depth -= 1
        else:
            break
    end = cur
    if text[start:end].count("$") % 2 == 1 and end < len(text):
        look = end
        while look < len(text) and text[look] in " }":
            look += 1
        if look < len(text) and text[look] == "$":
            end = look + 1
    return start, end


def span_from_atoms(st, a, b):
    """Atoms [a, b) -> raw (start, end), brace-balanced."""
    return balance(st.text, st.s[a], st.e[b - 1])


def word_bounded(text, start, end):
    left = start == 0 or not text[start - 1].isalnum()
    right = end == len(text) or not text[end].isalnum()
    return left and right


LIST_WORDS = re.compile(r"\b(and|or|but|nor|vs|versus)\b", re.I)
LIST_COMMA = re.compile(r",(?!\s?\d)|;")


def span_ok(span, name, text=None):
    """Length cap, plus: a fuzzy span may not swallow list items the entity lacks."""
    if span[1] - span[0] > MAX_SPAN_FACTOR * len(name) + MAX_SPAN_SLACK:
        return False
    if text is not None:
        raw = RANGE_WORD.sub(" ", text[span[0]:span[1]])
        if LIST_COMMA.search(raw) and not LIST_COMMA.search(name):
            return False
        if LIST_WORDS.search(raw) and not LIST_WORDS.search(name):
            return False
    return True


def free(start, end, occupied):
    return all(end <= s or start >= e for s, e in occupied)

# ----------------------------------------------------------------------------
# TIERS  (each is a generator of (start, end) raw spans, best first)
# ----------------------------------------------------------------------------
def tier_literal(text, name, flags):
    for m in re.finditer(re.escape(name), text, flags):
        s, e = m.span()
        # boundary only enforced on the side where the entity itself is alnum
        lb = not name[0].isalnum() or s == 0 or not text[s - 1].isalnum()
        rb = not name[-1].isalnum() or e == len(text) or not text[e].isalnum()
        if lb and rb:
            yield s, e


def tier_canonical(ctx, name, loose, bounded):
    st, en = ctx["strict" if not loose else "loose"], ctx["name_strict" if not loose else "name_loose"]
    hay, needle = st.string(), en.string()
    if not needle:
        return
    pos = hay.find(needle)
    while pos >= 0:
        end = pos + len(needle)
        ok = True
        if bounded:
            ok = (pos == 0 or st.sep[pos]) and (end == len(hay) or st.sep[end])
        if ok:
            span = span_from_atoms(st, pos, end)
            if span_ok(span, name):
                yield span
        pos = hay.find(needle, pos + 1)


def gap_search(st, pieces, name):
    """Ordered pieces on the compact stream; small digit-free gaps allowed between."""
    hay = st.string()
    out = []
    p0 = pieces[0]
    pos = hay.find(p0)
    while pos >= 0:
        if st.sep[pos]:
            cur, total, ok = pos + len(p0), 0, True
            for piece in pieces[1:]:
                idx = hay.find(piece, cur)
                while idx >= 0 and idx != cur and not st.sep[idx]:
                    idx = hay.find(piece, idx + 1)
                if idx < 0:
                    ok = False
                    break
                gap = hay[cur:idx]
                gap_tokens = sum(st.sep[k] for k in range(cur, idx))
                numeric_range = False
                if gap and cur > 0:
                    raw = MARKUP.sub(" ", st.text[st.e[cur - 1]:st.s[idx]])
                    numeric_range = bool(RANGE_GAP.fullmatch(raw))
                if not numeric_range and (len(gap) > MAX_GAP_CHARS
                        or gap_tokens > MAX_GAP_TOKENS
                        or any(c.isdigit() for c in gap)):
                    ok = False
                    break
                total += len(gap)
                cur = idx + len(piece)
            if ok and (cur == len(hay) or st.sep[cur]):
                out.append((total, pos, cur))
        pos = hay.find(p0, pos + 1)
    for _, a, b in sorted(out):
        span = span_from_atoms(st, a, b)
        if span_ok(span, name, st.text):
            yield span


def tier_gap(ctx, name):
    ewords, _ = tokens_of(ctx["name_loose"])
    if len(ewords) >= 2:
        yield from gap_search(ctx["loose"], ewords, name)


def tier_trim(ctx, name):
    """Entity carries one extra digit-free word the source does not contain."""
    ewords, _ = tokens_of(ctx["name_loose"])
    full = len("".join(ewords))
    variants = []
    if len(ewords) >= 2 and not any(c.isdigit() for c in ewords[-1]):
        variants.append(ewords[:-1])
    if len(ewords) >= 2 and not any(c.isdigit() for c in ewords[0]):
        variants.append(ewords[1:])
    if len(ewords) >= 4 and not any(c.isdigit() for c in "".join(ewords[-2:])):
        variants.append(ewords[:-2])
    for pieces in variants:
        core = "".join(pieces)
        if len(core) < TRIM_MIN_FRACTION * full:
            continue
        if len(pieces) == 1 and not (len(core) >= 4 and any(c.isdigit() for c in core)):
            continue
        yield from gap_search(ctx["loose"], pieces, name)


def tier_window(ctx, name):
    """All entity tokens, any order, in a tight digit-free-gap window."""
    st = ctx["loose"]
    ewords, _ = tokens_of(ctx["name_loose"])
    if len(ewords) < 2:
        return
    twords, tranges = tokens_of(st)
    need = Counter(ewords)
    cap = len(ewords) + WINDOW_EXTRA_TOKENS
    out = []
    for i, w in enumerate(twords):
        if w not in need:
            continue
        have = Counter()
        for j in range(i, min(len(twords), i + cap)):
            have[twords[j]] += 1
            if all(have[k] >= v for k, v in need.items()):
                extra = twords[i:j + 1]
                budget = Counter(need)
                digits_in_extra = False
                for tok in extra:
                    if budget[tok] > 0:
                        budget[tok] -= 1
                    elif any(c.isdigit() for c in tok):
                        digits_in_extra = True
                if not digits_in_extra:
                    out.append((j - i, tranges[i][0], tranges[j][1]))
                break
    for _, a, b in sorted(out):
        span = span_from_atoms(st, a, b)
        if span_ok(span, name, st.text):
            yield span


def digits(s):
    return "".join(c for c in s if c.isdigit())


def approx_find(hay, needle, max_err):
    """Sellers approximate substring search -> [(err, start, end)] non-overlapping."""
    m = len(needle)
    cost = list(range(m + 1))
    start = [0] * (m + 1)
    hits = []
    for i, hc in enumerate(hay, 1):
        ncost = [0] * (m + 1)
        nstart = [i] * (m + 1)
        for j in range(1, m + 1):
            best, bs = cost[j - 1] + (hc != needle[j - 1]), start[j - 1]
            if cost[j] + 1 < best:
                best, bs = cost[j] + 1, start[j]
            if ncost[j - 1] + 1 < best:
                best, bs = ncost[j - 1] + 1, nstart[j - 1]
            ncost[j], nstart[j] = best, bs
        cost, start = ncost, nstart
        if cost[m] <= max_err:
            hits.append((cost[m], start[m], i))
    hits.sort()
    chosen = []
    for err, s, e in hits:
        if all(e <= cs or s >= ce for _, cs, ce in chosen):
            chosen.append((err, s, e))
    return sorted(chosen)


def tier_fuzzy(ctx, name):
    st, en = ctx["loose"], ctx["name_loose"]
    hay, needle = st.string(), en.string()
    if len(needle) < FUZZY_MIN_LEN:
        return
    max_err = min(FUZZY_MAX_ERR, max(1, int(FUZZY_ERR_RATE * len(needle))))
    for err, a, b in approx_find(hay, needle, max_err):
        if b - a < len(needle) - max_err:
            continue
        while a > 0 and not st.sep[a]:       # snap to token boundaries
            a -= 1
        while b < len(hay) and not st.sep[b]:
            b += 1
        if digits(hay[a:b]) != digits(needle):   # never "fix" a number
            continue
        if hay[a] != needle[0]:                  # anchored start
            continue
        span = span_from_atoms(st, a, b)
        if span_ok(span, name, st.text):
            yield span


SUBSCRIPT_DIGIT = re.compile(r"(?<=[a-z])\d(?=[a-z])")


def tier_nosub(ctx, name):
    """Li2O vs 'Li O': extraction dropped subscript digits sandwiched in letters."""
    st, en = ctx["loose"], ctx["name_loose"]
    needle = SUBSCRIPT_DIGIT.sub("", en.string())
    hay = st.string()
    if needle == en.string() or len(needle) < 4:
        return
    pos = hay.find(needle)
    while pos >= 0:
        end = pos + len(needle)
        if st.sep[pos] and (end == len(hay) or st.sep[end]):
            span = span_from_atoms(st, pos, end)
            if span_ok(span, name, st.text):
                yield span
        pos = hay.find(needle, pos + 1)


def tier_acronym(text, name):
    """'metallic glasses' -> 'MG' / 'MGs' (initialism of a multi-word entity)."""
    words = re.findall(r"[A-Za-z]+", name)
    if len(words) < 2 or len(words) > 5:
        return
    init = "".join(w[0] for w in words).upper()
    for m in re.finditer(r"(?<![A-Za-z0-9])" + init + r"s?(?![A-Za-z0-9])", text):
        yield m.span()


def tiers(text, name, ctx):
    yield "exact", tier_literal(text, name, 0)
    yield "case", tier_literal(text, name, re.IGNORECASE)
    yield "strict", tier_canonical(ctx, name, False, True)
    yield "loose", tier_canonical(ctx, name, True, True)
    yield "gap", tier_gap(ctx, name)
    yield "window", tier_window(ctx, name)
    yield "fuzzy", tier_fuzzy(ctx, name)
    yield "trim", tier_trim(ctx, name)
    yield "nosub", tier_nosub(ctx, name)
    yield "acronym", tier_acronym(text, name)
    yield "subword", tier_canonical(ctx, name, False, False)
    yield "subword", tier_canonical(ctx, name, True, False)

# ----------------------------------------------------------------------------
# MAIN ENRICHMENT
# ----------------------------------------------------------------------------
def make_ctx(text):
    return {"strict": build(text, False), "loose": build(text, True)}


def enrich(rows):
    if not isinstance(rows, list):
        raise ValueError("Input must be a JSON array of chunks")
    counts, unresolved = Counter(), []

    for row_no, row in enumerate(rows, 1):
        text = row.get("chunk_text")
        if not isinstance(text, str):
            raise ValueError(f"Item {row_no}: missing chunk_text")
        entities = row.get("entities", [])
        ctx_base = None
        occupied = []
        pending = []

        for ent_no, ent in enumerate(entities, 1):
            name = ent.get("entity_text")
            if not isinstance(name, str) or not name:
                raise ValueError(f"Item {row_no}, entity {ent_no}: missing entity_text")
            s, e = ent.get("start_char"), ent.get("end_char")
            if s is not None and e is not None:
                if isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text) \
                        and text[s:e] == name and free(s, e, occupied):
                    occupied.append((s, e))
                    counts["existing"] += 1
                    continue
                raise ValueError(f"Item {row_no}, entity {ent_no}: invalid existing offsets")
            pending.append((ent_no, ent))

        if not pending:
            continue
        ctx_base = make_ctx(text)

        # longest first so short entities cannot steal spans of longer ones;
        # ties keep the model's order so duplicate names go left-to-right
        order = sorted(pending, key=lambda p: (-len(p[1]["entity_text"]), p[0]))
        results, reasons = {}, {}
        for ent_no, ent in order:
            name = ent["entity_text"]
            ctx = dict(ctx_base)
            ctx["name_strict"] = build(name, False)
            ctx["name_loose"] = build(name, True)

            found = None
            overlap_candidate = None
            overlap_clash = []
            for method, gen in tiers(text, name, ctx):
                for s, e in gen:
                    if free(s, e, occupied):
                        found = (s, e, method)
                        break
                    if overlap_candidate is None:
                        overlap_candidate = (s, e, method + "+overlap")
                        overlap_clash = [u for u in occupied if not (e <= u[0] or s >= u[1])]
                if found:
                    break
            if found:
                s, e, method = found
                results[ent_no] = (s, e, method)
                if not method.endswith("+overlap"):
                    occupied.append((s, e))
            elif overlap_candidate:
                s, e, _ = overlap_candidate
                reasons[ent_no] = ("duplicate of another entity" if (s, e) in overlap_clash
                                   else "only overlaps another entity")

        for ent_no, ent in pending:
            name = ent["entity_text"]
            if ent_no in results:
                s, e, method = results[ent_no]
                ent["start_char"], ent["end_char"] = s, e
                if text[s:e] != name:
                    ent.setdefault("original_entity_text", name)
                    ent["entity_text"] = text[s:e]
                ent["match_method"] = method
                counts[method] += 1
            else:
                ent["start_char"] = ent["end_char"] = None
                counts["unresolved"] += 1
                unresolved.append({
                    "item": row_no,
                    "document_id": row.get("document_id", ""),
                    "chunk_id": row.get("chunk_id", ""),
                    "entity_number": ent_no,
                    "entity_text": name,
                    "reason": reasons.get(ent_no, "not found in chunk_text"),
                })
    return counts, unresolved


def filter_uploadable(rows, unresolved=None):
    """Remove predictions that cannot produce a valid, distinct highlight."""
    skipped = 0
    for row_no, row in enumerate(rows, 1):
        chunk_text = row["chunk_text"]
        kept = []
        occupied = []
        for ent_no, ent in enumerate(row.get("entities", []), 1):
            start, end = ent.get("start_char"), ent.get("end_char")
            valid = (type(start) is int and type(end) is int
                     and 0 <= start < end <= len(chunk_text)
                     and chunk_text[start:end] == ent.get("entity_text")
                     and free(start, end, occupied))
            if valid:
                kept.append(ent)
                occupied.append((start, end))
            else:
                skipped += 1
                if unresolved is not None and (start is not None or end is not None):
                    unresolved.append({
                        "item": row_no,
                        "document_id": row.get("document_id", ""),
                        "chunk_id": row.get("chunk_id", ""),
                        "entity_number": ent_no,
                        "entity_text": ent.get("entity_text", ""),
                        "reason": "invalid or overlapping span",
                    })
        row["entities"] = kept
    return skipped


