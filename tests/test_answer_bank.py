from wdautofill.answer_bank import AnswerBank, match_option, normalize


def test_normalize_strips_markers_and_punctuation():
    assert normalize("  Are you 18+ years old?* ") == "are you 18 years old"


def test_exact_and_fuzzy_lookup(tmp_path):
    bank = AnswerBank(tmp_path / "a.yaml")
    bank.add("How did you hear about us?", "LinkedIn")
    assert bank.find("How did you hear about us").answer == "LinkedIn"
    assert bank.find("How did you hear about us here?").answer == "LinkedIn"  # fuzzy
    assert bank.find("How did you hear about our company?") is None  # too different: ask instead
    assert bank.find("Total years of experience") is None


def test_sensitive_lookup_is_exact_only(tmp_path):
    bank = AnswerBank(tmp_path / "a.yaml")
    bank.add("Are you authorized to work in the US?", "Yes", scope="US", sensitive=True)
    assert bank.find("Are you authorized to work in the US?", "US", fuzzy=False).answer == "Yes"
    # near-identical wording but a different country must NOT match
    assert bank.find("Are you authorized to work in the UK?", "US", fuzzy=False) is None


def test_scope_isolation_and_persistence(tmp_path):
    path = tmp_path / "a.yaml"
    bank = AnswerBank(path)
    bank.add("Need sponsorship?", "Yes", scope="US", sensitive=True)
    bank.add("Need sponsorship?", "No", scope="IN", sensitive=True)
    reloaded = AnswerBank(path)
    assert reloaded.find("Need sponsorship?", "US", fuzzy=False).answer == "Yes"
    assert reloaded.find("Need sponsorship?", "IN", fuzzy=False).answer == "No"
    assert reloaded.find("Need sponsorship?", "UK", fuzzy=False) is None


def test_add_replaces_same_question_and_scope(tmp_path):
    bank = AnswerBank(tmp_path / "a.yaml")
    bank.add("Q?", "one")
    bank.add("q", "two")
    assert len(bank.entries) == 1 and bank.entries[0].answer == "two"


def test_match_option():
    opts = ["Yes, I am authorized", "No, I am not", "Prefer not to say"]
    assert match_option("Yes", opts) == 0
    assert match_option("no", opts) == 1
    assert match_option("Prefer not to say", opts) == 2
    assert match_option("Maybe", opts) is None
    assert match_option("No", ["Non-binary", "Yes"]) is None
