"""Program names in a student's words: which programs a text names, or names wrongly.

One home for every reader of program names, so research, the router's file turns
and the artifact pipeline judge a name the same way. Each function answers a
different question:

- ``programs_named_in``: every program whose full title a text names (all of its
  words appear), longest titles winning over titles they contain. Used to find
  the programs a file request is about.
- ``program_named_in``: the single program a text names, or None when two equally
  long titles tie.
- ``choose_program_title``: the longest title appearing in the text as one
  contiguous phrase.
- ``missing_program``: a degree program the student named that no program in
  scope matches, read with acronyms (CS, NBB) and short forms (bio, math).
- ``normalized_title``: lower case, punctuation folded to single spaces.

Only ``missing_program`` reads aliases; the title matchers need every word of a
title (2026-11-20: "CS BS" names no program for them).
"""

from __future__ import annotations

import re

DEGREE_TOKEN = re.compile(
    r"(?<![A-Za-z])(?:BS|BA|BBA|B\.S\.|B\.A\.|minor|major|co-major)(?![A-Za-z])",
    re.IGNORECASE)


_NAME_STOPWORDS = frozenset({"and", "of", "in", "for", "the", "&"})


_REQUEST_LEAD = frozenset({
    "make", "makes", "me", "a", "an", "the", "plan", "plans", "planning", "for",
    "my", "our", "build", "create", "draft", "generate", "give", "want", "need",
    "can", "could", "would", "should", "may", "you", "please", "help", "with",
    "of", "to", "in", "on", "and", "or", "i", "i'm", "im", "we", "already",
    "took", "taken", "completed", "finished", "passed", "done", "have", "has",
    "had", "compare", "comparison", "what", "which", "how", "does", "do", "is",
    "are", "tell", "about", "explain", "describe", "show", "list", "difference",
    "between", "any", "all", "requirements", "requirement", "interested",
    "curious", "considering", "wondering", "map", "out", "lay", "sketch",
    "put", "together", "write", "up", "declare", "declaring", "pick", "picking",
    "choose", "choosing", "find", "finding", "study", "studying", "switch",
    "switching", "pursue", "pursuing", "doing", "completing", "taking", "take",
    "finish", "finishing", "start", "starting", "earn", "earning", "get",
    "getting", "spreadsheet", "workbook", "checklist", "roadmap", "handout",
    "worksheet", "page", "webpage", "website", "document", "doc", "guide",
    "overview", "report", "table", "chart", "tracker", "semester", "semesters",
    "term", "terms", "year", "years", "four-year", "4-year", "three-year",
    "3-year", "two-year", "2-year", "semester-by-semester", "term-by-term",
    "year-by-year", "remaining", "rest", "next", "graduation", "premed",
    "pre-med", "complete", "full", "detailed", "sample", "possible",
    "recommended", "double", "dual", "joint", "combined", "plus", "vs",
    "versus", "if", "when", "while", "since", "after", "before", "undergrad",
    "undergraduate", "emory", "oxford", "college", "bs", "ba", "bba", "minor",
    "major", "co-major",
})


_PROGRAM_MODIFIERS = frozenset({"honors", "honours", "with", "program", "degree"})


_DEGREE_FOLLOWERS = frozenset({
    "requirement", "requirements", "degree", "program", "programs", "declaration",
    "advisor", "advisors", "office", "sheet", "worksheet", "checklist", "class",
    "classes", "course", "courses", "credit", "credits", "hour", "hours", "gpa",
})


_PROGRAM_ACRO_ALIASES: dict[str, tuple[str, ...]] = {
    "cs": ("computer", "science"),
    "nbb": ("neuroscience", "behavioral", "biology"),
    "qss": ("quantitative", "sciences"),
    "qtm": ("quantitative", "theory", "methods"),
    "ams": ("applied", "mathematics", "statistics"),
    "ppa": ("public", "policy", "analysis"),
    "bba": ("business", "administration"),
    "envs": ("environmental", "sciences"),
}


_GENERIC_PROGRAM_WORDS = frozenset({
    "studies", "study", "science", "sciences", "health", "arts", "art",
    "engineering", "management", "policy", "affairs", "history", "culture",
    "cultures", "language", "languages", "literature", "system", "systems",
    "analysis", "theory", "methods",
})


def missing_program(question, titles) -> str | None:
    """A degree program the student named that no program in scope matches.

    Known acronyms (CS, NBB, QSS, QTM, AMS, PPA, BBA, ENVS) and one-word names
    ("Astrology BS") are judged against the programs in scope.
    """
    titles = list(titles)
    text = question or ""
    if not titles:
        return None
    # The last degree phrase is usually the program the request turns on.
    for match in reversed(list(DEGREE_TOKEN.finditer(text))):
        if match.group(0).casefold() in {"major", "minor", "co-major"}:
            following = re.match(r"[A-Za-z]+", text[match.end():].lstrip())
            if following and following.group(0).casefold() in _DEGREE_FOLLOWERS:
                continue  # "the major requirements", not a program named "major"
        clause = re.split(r"[.?!\n:;]+", text[:match.start()])[-1]
        window = re.findall(r"[A-Za-z][\w'’&-]*", clause)[-10:]
        while window and window[0].casefold() in _REQUEST_LEAD:
            window.pop(0)
        raw = [word for word in window]
        content = [word for word in raw if word.casefold() not in _NAME_STOPWORDS]
        if not content:
            continue
        has_non_acro = any(
            not (word.isupper() and len(word) <= 4)
            or word.casefold() in _PROGRAM_ACRO_ALIASES
            for word in content
        )
        words: list[str] = []
        for word in content:
            alias = _PROGRAM_ACRO_ALIASES.get(word.casefold())
            if alias is not None:
                words.extend(alias)
            elif word.isupper() and len(word) <= 4 and has_non_acro:
                continue
            else:
                words.append(word.casefold())
        if not words:
            continue
        meaningful = [word for word in words if word not in _PROGRAM_MODIFIERS] or words
        if len(meaningful) == 1 and meaningful[0] in _GENERIC_PROGRAM_WORDS:
            continue
        if _matches_program(meaningful, titles):
            return None
        return " ".join(window).strip(" ,.;:")
    return None


_PROGRAM_WORD_ALIASES = {
    "math": "mathematics", "maths": "mathematics", "stat": "statistics",
    "stats": "statistics", "bio": "biology", "chem": "chemistry",
    "phys": "physics", "econ": "economics", "psych": "psychology",
    "neuro": "neuroscience", "neurosciences": "neuroscience",
    "behaviour": "behavioral", "behavior": "behavioral",
    "behavioural": "behavioral", "sciences": "science", "comp": "computer",
    "sci": "science",
}


def _program_word(word: str) -> str:
    return _PROGRAM_WORD_ALIASES.get(word.casefold(), word.casefold())


def _matches_program(words: list[str], titles: list[str]) -> bool:
    norm_words = [_program_word(word) for word in words]
    raw_words = [word.casefold() for word in words]
    for title in titles:
        have = [_program_word(word) for word in re.findall(r"[a-z]+", title.casefold())]
        for start in range(len(norm_words)):
            tail = norm_words[start:]
            raw_tail = raw_words[start:]
            if len(tail) == 1 and raw_tail[0] in _GENERIC_PROGRAM_WORDS:
                continue
            if all(word in have for word in tail):
                return True
    return False


def normalized_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def choose_program_title(prompt: str, titles: list[str]) -> str | None:
    normalized = normalized_title(prompt)
    matches = sorted(
        ((len(normalized_title(title)), title) for title in titles
         if normalized_title(title) and normalized_title(title) in normalized),
        reverse=True,
    )
    return matches[0][1] if matches else None


def programs_named_in(text: str, titles: list[str]) -> list[str]:
    """Every program whose full title the request names, in the order it names them.

    A title inside a longer matched title ("Economics (BA)" inside "Economics and
    Mathematics (BA)") is left out.
    """
    lowered = text.casefold()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    matched = [(set(re.findall(r"[a-z0-9]+", title.casefold())), title) for title in titles]
    matched = [(title_words, title) for title_words, title in matched
               if title_words and title_words <= words]
    kept = [(title_words, title) for title_words, title in matched
            if not any(title_words < other for other, _title in matched)]

    def first_seen(item) -> int:
        positions = [lowered.find(word) for word in item[0] if lowered.find(word) >= 0]
        return min(positions, default=len(lowered))

    return [title for _words, title in sorted(kept, key=first_seen)]


def program_named_in(text: str, titles: list[str]) -> str | None:
    """The one program whose full title appears in the request, if unambiguous."""
    words = set(re.findall(r"[a-z0-9]+", text.casefold()))
    matches = sorted(
        ((len(title_words), title) for title in titles
         for title_words in [set(re.findall(r"[a-z0-9]+", title.casefold()))]
         if title_words and title_words <= words),
        reverse=True,
    )
    if not matches:
        return None
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]
