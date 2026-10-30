# Contributing

Thanks for helping job seekers save time.

## Reporting a site that doesn't fill

Use the "A site isn't filling correctly" issue form. The most useful thing is the
files from `personal/debug/` for that job: they list each page's field names and
buttons (never your answers). Check them before attaching anyway.

## Development

```bash
pip install -e ".[dev]"
wdautofill setup      # installs the browser
pytest
```

- `src/wdautofill/workday.py` - the Workday driver and the job queue
- `src/wdautofill/forms.py` - Lever, Greenhouse and other ordinary forms
- `src/wdautofill/resolver.py` - how each answer is chosen (profile, rules, saved answers, AI, you)
- `src/wdautofill/search.py` - `wdautofill search`
- `tests/` - mock pages copied from the structure of real sites; please add one for any
  site behaviour you fix

## Ground rules

- The tool never clicks Submit, never solves CAPTCHAs and never fills hidden bot-trap
  fields. Pull requests that change that won't be merged.
- Legal and identity questions are never answered by AI.
- No personal data in tests or screenshots - use demo people and fictional companies.
- `docs/make_screenshots.py` regenerates the README images from the test pages.
