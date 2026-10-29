import asyncio

from wdautofill.answer_bank import AnswerBank
from wdautofill.jobs import Job
from wdautofill.prompter import Reply
from wdautofill.resolver import Field, Resolver

PROFILE = {
    "personal": {"first_name": "Jane", "email": "jane@example.com"},
    "links": {"linkedin": "https://linkedin.com/in/jane"},
    "facts": ["Notice period: 30 days"],
}


class FakeLLM:
    def __init__(self, reply, conf=0.95):
        self.reply, self.conf, self.calls, self.suggest_calls = reply, conf, 0, 0

    def answer(self, question, kind, options, context, suggest=False):
        self.calls += 1
        self.suggest_calls += suggest
        self.context = context
        return self.reply, self.conf


class FakePrompter:
    def __init__(self, reply=None, country="US"):
        self.reply, self.country = reply, country
        self.asked, self.suggestions, self.country_asks = [], [], 0

    async def ask(self, label, kind, options, job, scope, suggestion=None, why="", recheck=None):
        self.asked.append(label)
        self.suggestions.append(suggestion)
        r = self.reply(label, options, suggestion) if callable(self.reply) else self.reply
        return Reply(r)

    async def ask_country(self, job):
        self.country_asks += 1
        return self.country


def make(tmp_path, llm=None, reply=None, instructions=""):
    p = FakePrompter(reply)
    r = Resolver(PROFILE, AnswerBank(tmp_path / "a.yaml"), llm=llm, prompter=p, instructions=instructions)
    return r, p


def run(coro):
    return asyncio.run(coro)


def test_profile_wins(tmp_path):
    r, _ = make(tmp_path, llm=FakeLLM("x"))
    res = run(r.resolve(Field("First Name*", "text"), Job("u", "US")))
    assert (res.answer, res.source) == ("Jane", "profile")


def test_profile_not_used_for_yes_no_question(tmp_path):
    r, p = make(tmp_path, reply="Yes")
    res = run(r.resolve(Field("Do you have a LinkedIn profile?", "radio", lambda: ["Yes", "No"]), Job("u", "US")))
    assert res.source == "user" and p.asked


def test_llm_never_fills_sensitive_questions(tmp_path):
    llm = FakeLLM("No")
    r, p = make(tmp_path, llm=llm, reply="Yes")
    res = run(r.resolve(Field("Have you ever been convicted of a crime?", "radio", lambda: ["Yes", "No"]), Job("u", "US")))
    assert res.source == "user" and res.answer == "Yes"  # the human decides
    assert p.suggestions == ["No"]  # the AI only suggested
    assert llm.suggest_calls == 1


def test_sensitive_suggestion_accepted_with_enter_is_remembered(tmp_path):
    llm = FakeLLM("Yes")
    r, p = make(tmp_path, llm=llm, reply=lambda l, o, s: s, instructions="Outside India I need sponsorship")
    q = Field("Will you now or in the future require visa sponsorship?", "radio", lambda: ["Yes", "No"])
    job = Job("u", "US")
    assert run(r.resolve(q, job)).answer == "Yes"
    assert llm.context["applicant_instructions"].startswith("Outside India")
    assert run(r.resolve(q, job)).source == "bank"  # asked once only
    assert len(p.asked) == 1


def test_similar_saved_sensitive_answer_is_only_a_suggestion(tmp_path):
    r, p = make(tmp_path, reply=lambda l, o, s: s)
    r.bank.add("Will you require sponsorship?", "No", "IN", True)
    res = run(r.resolve(Field("Will you now require sponsorship?", "radio", lambda: ["Yes", "No"]), Job("u", "IN")))
    assert p.suggestions == ["No"] and res.answer == "No"


def test_country_scoped_legal_answers(tmp_path):
    r, p = make(tmp_path, reply="No")
    q = Field("Will you require sponsorship?", "radio", lambda: ["Yes", "No"])
    run(r.resolve(q, Job("u", "IN")))
    p.reply = "Yes"
    res = run(r.resolve(q, Job("u2", "US")))
    assert res.source == "user" and res.answer == "Yes" and len(p.asked) == 2


def test_unknown_country_is_asked_once_per_job(tmp_path):
    r, p = make(tmp_path, reply="Yes")
    job = Job("u")
    run(r.resolve(Field("Will you require sponsorship?", "radio", lambda: ["Yes", "No"]), job))
    run(r.resolve(Field("Are you legally authorized to work here?", "radio", lambda: ["Yes", "No"]), job))
    assert p.country_asks == 1 and job.country == "US"


def test_llm_used_for_ordinary_question_when_confident(tmp_path):
    r, p = make(tmp_path, llm=FakeLLM("30 days"))
    res = run(r.resolve(Field("What is your notice period?", "text"), Job("u")))
    assert (res.answer, res.source) == ("30 days", "llm") and not p.asked


def test_low_confidence_llm_falls_back_to_user(tmp_path):
    r, p = make(tmp_path, llm=FakeLLM("maybe", conf=0.3), reply="60 days")
    res = run(r.resolve(Field("What is your notice period?", "text"), Job("u")))
    assert res.source == "user" and res.answer == "60 days" and p.asked


def test_llm_answer_must_be_a_real_option(tmp_path):
    r, p = make(tmp_path, llm=FakeLLM("Banana"), reply="LinkedIn")
    res = run(r.resolve(Field("How did you hear about us?", "dropdown", lambda: ["LinkedIn", "Referral"]), Job("u")))
    assert res.source == "user" and p.asked


def test_skip_returns_none(tmp_path):
    r, _ = make(tmp_path, reply=None)
    assert run(r.resolve(Field("Anything new?", "text"), Job("u"))) is None


def test_real_prompter_asks_shared_question_once(tmp_path, monkeypatch):
    """Two windows reach the same new question at the same moment: one prompt."""
    from wdautofill import prompter as pm

    calls = []

    def fake_blocking(label, kind, options, job, scope, suggestion, why):
        calls.append(label)
        import time
        time.sleep(0.2)
        return Reply("Yes")

    tp = pm.TerminalPrompter()
    monkeypatch.setattr(tp, "_blocking_ask", fake_blocking)
    r = Resolver(PROFILE, AnswerBank(tmp_path / "a.yaml"), prompter=tp)
    q = lambda: Field("Are you willing to work night shifts?", "radio", lambda: ["Yes", "No"])

    async def both():
        return await asyncio.gather(r.resolve(q(), Job("a", "IN")), r.resolve(q(), Job("b", "IN")))

    res = run(both())
    assert [x.answer for x in res] == ["Yes", "Yes"] and calls == ["Are you willing to work night shifts?"]
