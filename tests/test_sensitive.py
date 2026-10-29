import pytest

from wdautofill.sensitive import is_country_dependent, is_sensitive


@pytest.mark.parametrize(
    "q",
    [
        "Have you ever been convicted of a felony?",
        "Are you legally authorized to work in the United States?",
        "Will you require visa sponsorship?",
        "Gender",
        "Veteran Status",
        "I acknowledge and agree to the terms and conditions",
        "What are your salary expectations?",
        "Are you subject to a non-compete agreement?",
    ],
)
def test_sensitive_questions(q):
    assert is_sensitive(q)


@pytest.mark.parametrize(
    "q",
    ["How did you hear about us?", "Why do you want to work here?", "Years of Python experience"],
)
def test_ordinary_questions(q):
    assert not is_sensitive(q)


def test_country_dependent():
    assert is_country_dependent("Do you require sponsorship?")
    assert not is_country_dependent("Have you ever been convicted of a felony?")
