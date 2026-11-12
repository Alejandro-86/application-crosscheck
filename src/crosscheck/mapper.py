"""Model fallback for terms the terminology table does not know.

The model is shown one term and a CLOSED list of canonical names and must answer with one entry
from the list or "none". Code checks the answer is exactly an entry (anything else becomes
"none"), so the model can never introduce a term the system does not know. It is only asked
about terms the table could not map, and only for conditions and medications.
"""

from crosscheck.llm import ChatClient
from crosscheck.synthetic import CONDITIONS, MED_CATALOG

CLOSED_LISTS = {"condition": list(CONDITIONS), "medication": list(MED_CATALOG)}

SYSTEM_PROMPT = """You map one term from a clinical note to a fixed list of canonical names.
Reply with exactly one entry from the list, copied verbatim, or the word none.
Rules:
- Choose an entry only if it means the same thing as the term. Do not choose a broader, narrower
  or merely related one. If you are not sure, reply none.
- Reply with the entry only: no explanation, no punctuation."""


class ConceptMapper:
    def __init__(self, chat: ChatClient):
        self._chat = chat
        self._seen: dict[tuple[str, str], str | None] = {}

    def map(self, kind: str, term: str, context: str) -> str | None:
        """The canonical name for the term, or None. Calls the model once per distinct term."""
        options = CLOSED_LISTS.get(kind)
        key = (kind, " ".join(term.lower().split()))
        if not options or not key[1]:
            return None
        if key not in self._seen:
            self._seen[key] = self._ask(kind, options, term, context)
        return self._seen[key]

    def _ask(self, kind: str, options: list[str], term: str, context: str) -> str | None:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"List ({kind}s):\n" + "\n".join(f"- {o}" for o in options) + f"\n\n"
                    f'Term: "{term}"\nSentence it came from: "{context}"'
                ),
            },
        ]
        answer = self._chat.complete(messages).text.strip().strip(".\"'").lower()
        return answer if answer in options else None
