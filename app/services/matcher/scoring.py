"""Pure, deterministic fragrance-profile similarity; no network or LLM calls.

Scores are deliberately conservative: a candidate must share at least one real
accord or note with its source. Missing signals never count as similarities.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping

ACCORD_WEIGHT = 0.50
NOTE_WEIGHT = 0.30
OLFACTORY_WEIGHT = 0.20
MINIMUM_SCORE = 0.30  # Tune using real (anonymized) recommendation audits.
NOTE_LAYERS = (("notesTop", 0.25), ("notesMiddle", 0.30), ("notesBase", 0.45))
GENDER_GROUP = {"female": "female", "moreFemale": "female", "unisex": "unisex",
                "moreMale": "male", "male": "male"}
OLFACTORY_SCALE = {
    "temperature": {"cool": -1, "neutral": 0, "warm": 1},
    "sweetness": {"low": 0, "medium": 1, "high": 2},
    "freshness": {"low": 0, "medium": 1, "high": 2},
}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def _name(value):
    if not isinstance(value, str):
        return ""
    value = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return value if value and not any(unicodedata.category(c).startswith("C") for c in value) else ""


def dominant_gender(fragrance):
    votes = fragrance.get("genderVotes") if isinstance(fragrance, Mapping) else None
    votes = votes.get("votes") if isinstance(votes, Mapping) else None
    if not isinstance(votes, Mapping):
        return None
    available = {key: _number(votes.get(key)) for key in GENDER_GROUP}
    available = {key: val for key, val in available.items() if val is not None}
    if not available:
        return None
    highest = max(available.values())
    winners = [key for key, value in available.items() if value == highest]
    return GENDER_GROUP[winners[0]] if len(winners) == 1 else None


def compatible_gender(source, candidate):
    return source == candidate or (source in ("female", "male") and candidate == "unisex")


def accords(fragrance):
    result = {}
    values = fragrance.get("mainAccords") if isinstance(fragrance, Mapping) else None
    for row in values if isinstance(values, list) else []:
        if not isinstance(row, Mapping):
            continue
        name, strength = _name(row.get("name")), _number(row.get("strength"))
        if name and strength is not None:
            result[name] = max(result.get(name, 0), min(100.0, strength) / 100.0)
    return result


def _note_id(value):
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9]\d*", value):
        return value
    return None


def notes(fragrance, field):
    result = {}
    values = fragrance.get(field) if isinstance(fragrance, Mapping) else None
    for row in values if isinstance(values, list) else []:
        if not isinstance(row, Mapping):
            continue
        note_id, name = _note_id(row.get("noteId")), _name(row.get("name"))
        if not note_id and not name:
            continue
        weight = _number(row.get("weight"))
        # Unknown weight is neutral, not an indication of zero importance.
        weight = min(1.0, weight) if weight is not None else 1.0
        identity = ("id", note_id) if note_id else ("name", name)
        result[identity] = max(result.get(identity, 0), weight)
    return result


def profile(fragrance):
    if not isinstance(fragrance, Mapping):
        return None
    group = dominant_gender(fragrance)
    if group is None:
        return None  # Strict gender requires a known, unambiguous category.
    a = accords(fragrance)
    n = {field: notes(fragrance, field) for field, _ in NOTE_LAYERS}
    preferences = fragrance.get("olfactory_preferences")
    preferences = preferences if isinstance(preferences, Mapping) else {}
    o = {key: OLFACTORY_SCALE[key].get(preferences.get(key)) for key in OLFACTORY_SCALE}
    if not a and not any(n.values()):
        return None  # Preferences alone do not prove a perfume resemblance.
    return {"gender": group, "accords": a, "notes": n, "olfactory": o}


def _cosine(left, right):
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    norm_left = math.sqrt(sum(value ** 2 for value in left.values()))
    norm_right = math.sqrt(sum(value ** 2 for value in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _jaccard(left, right):
    """Weighted overlap / union. Fragrantica noteId is primary identity."""
    if not left or not right:
        return 0.0
    shared = sum(min(value, right[key]) for key, value in left.items() if key in right)
    total = sum(left.values()) + sum(right.values()) - shared
    return shared / total if total else 0.0


def _note_similarity(left, right):
    # Missing layers count as 0, rather than making 1 matching note a perfect score.
    return sum(layer_weight * _jaccard(left[field], right[field]) for field, layer_weight in NOTE_LAYERS)


def _olfactory_similarity(left, right):
    compared = []
    for key in OLFACTORY_SCALE:
        x, y = left.get(key), right.get(key)
        if x is not None and y is not None:
            compared.append(max(0.0, 1.0 - abs(x - y) / 2.0))
    return sum(compared) / len(compared) if compared else 0.0


def similarity(source, candidate):
    """Return a 0..1 score; 0 means gender-incompatible or no scent overlap."""
    if not source or not candidate or not compatible_gender(source["gender"], candidate["gender"]):
        return 0.0
    a = _cosine(source["accords"], candidate["accords"])
    n = _note_similarity(source["notes"], candidate["notes"])
    if a == 0 and n == 0:
        return 0.0
    o = _olfactory_similarity(source["olfactory"], candidate["olfactory"])
    return min(1.0, ACCORD_WEIGHT * a + NOTE_WEIGHT * n + OLFACTORY_WEIGHT * o)


def rank_recommendations(sources, candidates, excluded, minimum_score=MINIMUM_SCORE):
    """Merge independent source rankings fairly, dedupe, and cap at 20.

    3 sources -> target 18; 2 -> 14; 1 -> 12. A single reference may
    contribute >7 to meet the 10-item goal, when enough strong matches exist.
    """
    sources = [item for item in sources if item is not None][:3]
    if not sources:
        return []
    goal = {1: 12, 2: 14, 3: 18}[len(sources)]
    ranked = []
    for source in sources:
        choices = []
        for pid, candidate in candidates.items():
            if pid in excluded:
                continue
            score = similarity(source, candidate)
            if score >= minimum_score:
                choices.append((pid, score))
        ranked.append(sorted(choices, key=lambda row: (-row[1], row[0])))

    output, seen = [], set(excluded)
    positions = [0] * len(ranked)
    allocations = [0] * len(ranked)
    per_source_limit = 12 if len(ranked) == 1 else 7
    # At least four from each source if available, then grow fairly.
    for tier in (4, per_source_limit):
        while len(output) < goal:
            progressed = False
            for index, choices in enumerate(ranked):
                if allocations[index] >= tier:
                    continue
                while positions[index] < len(choices) and choices[positions[index]][0] in seen:
                    positions[index] += 1
                if positions[index] >= len(choices):
                    continue
                pid, _ = choices[positions[index]]
                positions[index] += 1
                seen.add(pid)
                output.append(pid)
                allocations[index] += 1
                progressed = True
                if len(output) >= goal:
                    break
            if not progressed:
                break

    # Overlap can deplete one source. Fill from any sufficiently good match.
    if len(output) < min(10, goal):
        best = {}
        for choices in ranked:
            for pid, score in choices:
                if pid not in seen and score > best.get(pid, -1):
                    best[pid] = score
        for pid, _ in sorted(best.items(), key=lambda pair: (-pair[1], pair[0])):
            seen.add(pid)
            output.append(pid)
            if len(output) >= min(10, goal):
                break
    return output[:20]
