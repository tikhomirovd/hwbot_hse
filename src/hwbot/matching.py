from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from rapidfuzz import fuzz

from hwbot.models import Student

STRONG_SCORE = 90
WEAK_SCORE = 80


def normalize_text(value: str) -> str:
    lowered = value.replace("Ё", "е").replace("ё", "е").casefold()
    return " ".join(lowered.split())


def normalize_email(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True, slots=True)
class MatchResult:
    students: tuple[Student, ...]
    reason: str

    @property
    def unique(self) -> Student | None:
        if len(self.students) == 1:
            return self.students[0]
        return None


def match_students(query: str, students: Sequence[Student]) -> MatchResult:
    raw = query.strip()
    if not raw:
        return MatchResult((), "empty")
    if "@" in raw:
        email = normalize_email(raw)
        hits = tuple(s for s in students if normalize_email(s.email) == email)
        return MatchResult(hits, "email" if hits else "email_miss")

    normalized = normalize_text(raw)
    exact = tuple(s for s in students if normalize_text(s.full_name) == normalized)
    if exact:
        return MatchResult(exact, "exact")

    tokens = normalized.split()
    if len(tokens) >= 2:
        prefix = " ".join(tokens[:2])
        prefix_hits = tuple(
            s
            for s in students
            if normalize_text(s.full_name) == prefix
            or normalize_text(s.full_name).startswith(f"{prefix} ")
        )
        if len(prefix_hits) == 1:
            return MatchResult(prefix_hits, "name_prefix")
        if len(prefix_hits) > 1:
            return MatchResult(prefix_hits, "name_prefix_ambiguous")

    scored: list[tuple[int, Student]] = []
    for student in students:
        score = int(fuzz.WRatio(normalized, normalize_text(student.full_name)))
        scored.append((score, student))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < WEAK_SCORE:
        return MatchResult((), "fuzzy_miss")
    best = scored[0][0]
    close = tuple(
        student
        for score, student in scored
        if score >= max(WEAK_SCORE, best - 4)
    )
    if best >= STRONG_SCORE and len(close) == 1:
        return MatchResult(close, "fuzzy")
    if best >= STRONG_SCORE:
        return MatchResult(close[:5], "fuzzy_ambiguous")
    return MatchResult((), "fuzzy_miss")


def search_students(query: str, students: Sequence[Student]) -> list[Student]:
    """Поиск однокурсника: каждое слово запроса — начало какого-то слова ФИО.

    «петр ан» найдёт Петрову Анну, регистр и «ё» не важны. Если так никого
    нет — опечатка: каждое слово запроса похоже на какое-то слово ФИО
    («иваноф» → Иванов).
    """
    words = normalize_text(query).split()
    if not words:
        return []
    hits = [
        student
        for student in students
        if all(
            any(part.startswith(word) for part in normalize_text(student.full_name).split())
            for word in words
        )
    ]
    if hits:
        return sorted(hits, key=lambda student: normalize_text(student.full_name))
    scored: list[tuple[float, Student]] = []
    for student in students:
        parts = normalize_text(student.full_name).split()
        score = min(max(fuzz.ratio(word, part) for part in parts) for word in words)
        if score >= WEAK_SCORE:
            scored.append((score, student))
    scored.sort(key=lambda item: (-item[0], normalize_text(item[1].full_name)))
    return [student for _score, student in scored]
