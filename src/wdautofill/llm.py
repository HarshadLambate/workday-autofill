"""Optional LLM backends used only for NON-sensitive, unknown questions."""
from __future__ import annotations

import json
import re

SYSTEM = (
    "You help fill in a job application form on behalf of the applicant. "
    "Use ONLY the facts in the provided profile, the applicant's own instructions "
    "and previous answers. Follow the applicant's instructions. "
    "Never invent facts, dates, numbers, employers or credentials. "
    "If the facts do not clearly answer the question, answer null. "
    "If options are given, answer with exactly one option copied verbatim. "
    "For open-ended questions write 2-4 plain first-person sentences grounded in the facts. "
    'Reply with JSON only: {"answer": string or null, "confidence": number from 0 to 1}.'
)

# Used for legal / identity questions. The result is only ever SHOWN to the
# applicant as a suggestion; it is never filled in without their confirmation.
SUGGEST = (
    "An applicant is filling in a job application. Based ONLY on the applicant's own "
    "instructions and saved answers, suggest how they would answer this question. "
    "Read the question wording very carefully (watch for negations such as "
    "'do you NOT require'). Consider the job's country and location. "
    "If the instructions do not clearly cover it, answer null. "
    "If options are given, answer with exactly one option copied verbatim. "
    'Reply with JSON only: {"answer": string or null, "confidence": number from 0 to 1}.'
)


def parse_answer(raw: str) -> tuple[str | None, float]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None, 0.0
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, 0.0
    answer = data.get("answer")
    if answer is None or str(answer).strip().lower() in {"", "null", "none"}:
        return None, 0.0
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    return str(answer).strip(), conf


class BaseLLM:
    def complete(self, system: str, user: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def answer(
        self, question: str, kind: str, options: list[str] | None, context: dict,
        suggest: bool = False,
    ) -> tuple[str | None, float]:
        payload = {"question": question, "field_type": kind, "options": options, **context}
        raw = self.complete(SUGGEST if suggest else SYSTEM, json.dumps(payload, ensure_ascii=False, indent=2))
        return parse_answer(raw)


class AnthropicLLM(BaseLLM):
    def __init__(self, model: str):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install the extra: pip install 'workday-autofill[anthropic]'") from exc
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model

    def complete(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


class OpenAICompatLLM(BaseLLM):
    """Works with OpenAI, Ollama (http://localhost:11434/v1), Gemini/Groq/OpenRouter via base_url."""

    def __init__(self, model: str, base_url: str | None, api_key_env: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install the extra: pip install 'workday-autofill[openai]'") from exc
        import os

        kwargs: dict = {"base_url": base_url} if base_url else {}
        # Key from the env var named in the profile (e.g. GEMINI_API_KEY), else
        # OPENAI_API_KEY. Local servers such as Ollama accept any value.
        key = os.environ.get(api_key_env or "OPENAI_API_KEY")
        if not key and base_url and ("localhost" in base_url or "127.0.0.1" in base_url):
            key = "ollama"
        if key:
            kwargs["api_key"] = key
        self.client = OpenAI(**kwargs)
        self.model = model

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""


def make_llm(
    provider: str | None, model: str, base_url: str | None, api_key_env: str | None = None
) -> BaseLLM | None:
    provider = (provider or "none").lower()
    if provider in {"none", ""}:
        return None
    if not (model or "").strip():
        print(f"Note: llm.provider is '{provider}' but llm.model is empty - AI is off. "
              "Set a model (see the examples in profile.yaml) or use provider: none.")
        return None
    if provider == "anthropic":
        return AnthropicLLM(model)
    if provider == "openai":
        return OpenAICompatLLM(model, base_url or None, api_key_env or None)
    raise ValueError(f"Unknown llm.provider '{provider}' (use none, anthropic or openai)")
