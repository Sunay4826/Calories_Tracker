"""Finds which database food an AI dish name refers to.

Order of checks for every name the AI gives (its name + search_terms):
1. The user's personal aliases  (this user corrected this name before)  -> score 1.0
2. Global aliases               (seed list + nutritionist-approved)    -> score 1.0
3. Fuzzy match against every name variant of every food              -> score 0..1

Fuzzy score = half "how similar are the letters" + half "how many words match",
after normalising Indian spelling differences (see normalize()).
"""
import re
from difflib import SequenceMatcher

from .food_db import FoodDb

STOPWORDS = {
    "with", "and", "of", "the", "a", "an", "in", "ka", "ki", "ke", "aur", "style",
    "indian", "home", "homemade", "made", "recipe", "dish", "fresh", "hot", "cooked",
}


def normalize(text: str) -> str:
    """Lower-case and smooth over transliteration differences.

    "Khichdi", "khichri", "Khitchdi" and "kichdi" should all look alike, so:
    ph->f, drop 'h' after a consonant (kh->k, dh->d, bh->b ...), w->v, z->j,
    q->k, doubled letters -> single (ee->e, oo->o), plural 's' removed,
    final 'y' -> 'i' (idly -> idli).
    The same function runs on both sides, so it never hurts an exact match.
    """
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = text.replace("ph", "f")
    text = re.sub(r"([bcdgjkpst])h+", r"\1", text)
    text = text.replace("w", "v").replace("z", "j").replace("q", "k")
    text = re.sub(r"(.)\1+", r"\1", text)
    words = []
    for word in text.split():
        if word in STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        if len(word) > 2 and word.endswith("y"):  # idly -> idli, chutny -> chutni
            word = word[:-1] + "i"
        words.append(word)
    return " ".join(words)


def name_variants(name: str) -> set[str]:
    """All the ways an INDB name can be written.

    "Plain khitchdi (Plain khichri/khichdi)" ->
        plain khitchdi | plain khichri | plain khichdi | full name
    "Onion-green chilli parantha/paratha" ->
        onion green chilli parantha | onion green chilli paratha
    A single word after "/" replaces the last word of the part before it,
    so "paratha" alone never becomes a variant of "onion paratha".
    """
    parts = [name] + re.findall(r"\(([^)]*)\)", name) + [re.sub(r"\([^)]*\)", " ", name)]
    variants: set[str] = set()
    for part in parts:
        pieces = [p.strip() for p in re.split(r"[/,]", part) if p.strip()]
        previous = ""
        for piece in pieces:
            if previous and len(piece.split()) == 1 and len(previous.split()) > 1:
                piece = " ".join(previous.split()[:-1] + [piece])
            variants.add(piece)
            previous = piece
        variants.add(part)
    return {v for v in (normalize(x) for x in variants) if v}


def _word_overlap(query_words: list[str], variant_words: list[str]) -> float:
    """F1 of fuzzy word matches (two words match if >= 80 % similar)."""
    if not query_words or not variant_words:
        return 0.0

    def matched(source: list[str], target: list[str]) -> int:
        return sum(
            1 for w in source
            if any(w == t or SequenceMatcher(None, w, t).ratio() >= 0.8 for t in target)
        )

    recall = matched(query_words, variant_words) / len(query_words)
    precision = matched(variant_words, query_words) / len(variant_words)
    return 0.0 if recall + precision == 0 else 2 * recall * precision / (recall + precision)


def similarity(query: str, variant: str) -> float:
    if query == variant:
        return 1.0
    letters = SequenceMatcher(None, query, variant).ratio()
    words = _word_overlap(query.split(), variant.split())
    return round(0.5 * letters + 0.5 * words, 4)


def trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


class Matcher:
    # Only this many variants (those sharing the most 3-letter pieces with the
    # query) get the slower full comparison. Keeps a lookup at a few ms.
    SHORTLIST = 60

    def __init__(self, db: FoodDb, seed_aliases: dict[str, str] | None = None):
        self.db = db
        self.variants = [(food["code"], v) for food in db.foods for v in name_variants(food["name"])]
        self.variant_grams = [trigrams(v) for _, v in self.variants]
        self.index: dict[str, list[int]] = {}
        for i, grams in enumerate(self.variant_grams):
            for gram in grams:
                self.index.setdefault(gram, []).append(i)
        self.seed_aliases = {normalize(k): v for k, v in (seed_aliases or {}).items() if db.get(v)}

    def _shortlist(self, query: str) -> list[int]:
        query_grams = trigrams(query)
        shared: dict[int, int] = {}
        for gram in query_grams:
            for i in self.index.get(gram, ()):
                shared[i] = shared.get(i, 0) + 1
        dice = {i: 2 * n / (len(query_grams) + len(self.variant_grams[i])) for i, n in shared.items()}
        return sorted(dice, key=dice.get, reverse=True)[: self.SHORTLIST]

    def candidates(self, terms: list[str], limit: int = 8) -> list[dict]:
        """Best database foods for any of `terms`, highest score first.

        viaMainName is True when the score came from terms[0] (the dish name
        itself) rather than one of the AI's "other names" for it.
        """
        best: dict[str, tuple[float, int]] = {}
        for term_index, term in enumerate(terms):
            query = normalize(term)
            if not query:
                continue
            for i in self._shortlist(query):
                code, variant = self.variants[i]
                score = similarity(query, variant)
                if score > best.get(code, (0.0, 0))[0]:
                    best[code] = (score, term_index)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
        return [{"foodCode": code, "foodName": self.db.by_code[code]["name"], "score": score,
                 "viaMainName": term_index == 0} for code, (score, term_index) in ranked]

    def match(
        self,
        terms: list[str],
        personal_aliases: dict[str, str] | None = None,
        global_aliases: dict[str, str] | None = None,
    ) -> dict:
        """Returns {"foodCode", "score", "method", "viaMainName", "candidates"}.

        method is "personal_alias", "alias" or "fuzzy"; foodCode is the best
        guess (the engine decides whether it is good enough). terms[0] must be
        the dish name; the rest are the AI's other names for it.
        """
        aliases = {**self.seed_aliases, **(global_aliases or {})}
        for term_index, term in enumerate(terms):
            key = normalize(term)
            if personal_aliases and self.db.get(personal_aliases.get(key)):
                return self._result(personal_aliases[key], "personal_alias", terms, term_index == 0)
            if self.db.get(aliases.get(key)):
                return self._result(aliases[key], "alias", terms, term_index == 0)

        candidates = self.candidates(terms)
        if not candidates:
            return {"foodCode": None, "score": 0.0, "method": "none", "viaMainName": False, "candidates": []}
        top = candidates[0]
        return {"foodCode": top["foodCode"], "score": top["score"], "method": "fuzzy",
                "viaMainName": top["viaMainName"], "candidates": candidates}

    def _result(self, code: str, method: str, terms: list[str], via_main_name: bool) -> dict:
        others = [c for c in self.candidates(terms) if c["foodCode"] != code]
        chosen = {"foodCode": code, "foodName": self.db.by_code[code]["name"], "score": 1.0, "viaMainName": via_main_name}
        return {"foodCode": code, "score": 1.0, "method": method, "viaMainName": via_main_name,
                "candidates": [chosen] + others[:7]}
