"""What a quote's role and condition were, recorded but not yet judged.

The verdict asks one question at write time: do the user's words back this claim? Two things the
quote itself carries are not part of that question, and are dropped when the evidence row is
written:

* **role** — whether the user was speaking for themselves or repeating what someone else decided.
  ``"Alice decided we're using Postgres"`` verifies as a quote today and stores as
  ``the user decided Postgres`` after the quote is gone.
* **condition** — whether the decision was stated conditionally (``only if``, ``unless``,
  ``as long as``). The quote verifies; the condition is dropped, and the row reads as a plain
  decision.

Both are recorded here so a later version has the data instead of reconstructing it from text
that no longer exists. **Neither changes a verdict.** A condition that is *stated* is not a
condition that is *unmet* — deciding that needs world state this plugin does not have, and a
false block on ``Alice suggested X and I agree`` would be worse than the miss it replaces.

Hence ``condition`` has no ``stated-unmet`` value: the plugin can see that a condition was
stated, never whether it was later satisfied. ``unresolved`` means exactly that, and owning the
resolution is a ledger, not a patch to a write-time check.
"""

from __future__ import annotations

import re

from . import quotes

ROLE_SELF = "self"
ROLE_RELAYED = "relayed"
ROLE_UNKNOWN = "unknown"

CONDITION_NONE = "none"
CONDITION_UNRESOLVED = "unresolved"
CONDITION_UNKNOWN = "unknown"

# Verbs that report a decision rather than describe one, stemmed by dropping a trailing "s".
_REPORT_STEMS = frozenset({
    "said", "say", "told", "tell", "mentioned", "mention", "suggested", "suggest", "proposed",
    "propose", "recommended", "recommend", "decided", "decide", "wanted", "want", "asked", "ask",
    "thought", "think", "insisted", "insist", "preferred", "prefer", "agreed", "agree", "approved",
    "approve", "rejected", "reject", "reckoned", "reckon", "believed", "believe", "claimed", "claim",
})

# A third party, then a verb. The subject is deliberately narrow: a capitalised name, or a
# possessive role ("my manager", "Our lead"). "we decided" and "I decided" are the user speaking
# and must not match; a lowercase "the user decided" is the plugin's own subject, not reported
# speech, so the capitalised-name branch is what carries the signal.
_EN_REPORT = re.compile(r"\b((?i:my|our)\s+\w+|[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)\s+([a-zA-Z']+)")
_FIRST_PERSON_SUBJECTS = frozenset({"i", "we"})
# The plugin's own subject. "The user decided" is a claim the gate wrote, not reported speech.
_NOT_THIRD_PARTY_SUBJECTS = frozenset({"i", "we", "the user", "user", "the owner", "owner"})

_EN_SELF_STRONG = re.compile(
    r"\b(?:i|we)\s+(?:have\s+|had\s+|already\s+|just\s+)?(?:"
    r"decided|decide|chose|choose|chosen|agreed|agree|approved|approve|rejected|reject|"
    r"want|wants|prefer|prefers|need|needs|will|would"
    r")\b",
    re.IGNORECASE,
)
# "we're using X" and "let's do X" can sit inside what a third party was quoted as saying
# ("Alice decided we're using Postgres"), so on their own they only count as the user speaking
# when nothing is being relayed.
_EN_SELF_WEAK = re.compile(r"\blet's\b|\bi'm\b|\bwe're\b", re.IGNORECASE)

_ZH_REPORT = re.compile(r"(?:他|她|同事|老板|老闆|领导|領導|经理|經理|客户|客戶|对方|對方|上游|甲方|组长|組長|负责人|負責人)"
                        r"[^，。；！？,;!?\n]{0,12}?(?:说|說|决定|決定|要求|建议|建議|提出|同意|否决|否決|拍板)")
_ZH_SELF = re.compile(r"(?:我|我们|我們|咱|咱们|咱們)[^，。；！？,;!?\n]{0,12}?(?:决定|決定|选|選|同意|要|批准|否掉|否決)")

_EN_CONDITION_PHRASES = ("only if", "only when", "only once", "as long as", "so long as",
                         "provided that", "providing that", "on condition that", "in the event that",
                         "subject to")
_EN_CONDITION_WORDS = frozenset({"if", "unless", "whether", "once", "until", "till"})
_ZH_CONDITION_MARKERS = ("如果", "若", "除非", "只要", "一旦", "待到", "待", "前提是", "前提為", "前提为",
                         "等到", "将来", "將來", "如果", "假如", "倘若", "否则", "否則", "才能", "才可以", "方可")


def role_of(quote: str | None) -> str:
    """``self``, ``relayed`` or ``unknown`` for one quote.

    Ambiguity resolves to ``unknown``: ``Alice said X and I agree`` carries both a third-party
    report and a first-person agreement, and guessing between them is the expensive mistake (a
    wrong ``relayed`` would suppress the user's own decision).
    """
    if not quote or not quote.strip():
        return ROLE_UNKNOWN
    text = quote  # case is the signal for a name, so this must not be case-folded
    relayed = _is_relayed(text)
    strong_self = bool(_EN_SELF_STRONG.search(text) or _ZH_SELF.search(text))
    weak_self = bool(_EN_SELF_WEAK.search(text))
    if relayed and strong_self:
        return ROLE_UNKNOWN
    if relayed:
        return ROLE_RELAYED
    if strong_self or weak_self:
        return ROLE_SELF
    return ROLE_UNKNOWN


def _is_relayed(text: str) -> bool:
    if _ZH_REPORT.search(text):
        return True
    for subject, verb in _EN_REPORT.findall(text):
        if subject.lower() in _FIRST_PERSON_SUBJECTS:
            continue
        if verb.lower().rstrip("s") in _REPORT_STEMS:
            return True
    return False


def condition_of(quote: str | None) -> str:
    """``none``, ``unresolved`` or ``unknown`` for one quote.

    ``unresolved`` says a condition marker is present in the user's words. It does **not** say the
    condition is unmet: nothing here can see whether the benchmark later passed. An empty quote is
    ``unknown`` rather than ``none`` — there is no sentence to read.
    """
    if not quote or not quote.strip():
        return CONDITION_UNKNOWN
    lowered = quotes.normalize(quote).lower()
    if any(phrase in lowered for phrase in _EN_CONDITION_PHRASES):
        return CONDITION_UNRESOLVED
    if any(marker in quote for marker in _ZH_CONDITION_MARKERS):
        return CONDITION_UNRESOLVED
    words = re.findall(r"[a-z']+", lowered)
    if any(word in _EN_CONDITION_WORDS for word in words):
        return CONDITION_UNRESOLVED
    return CONDITION_NONE
