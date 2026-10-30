# Releasing a new version

Publishing to PyPI is automatic: a GitHub Release runs `.github/workflows/publish.yml`,
which builds the package and uploads it with PyPI *trusted publishing* (no password or
token is stored anywhere).

## One-time setup (already done for this repo once it has been published)

1. Create an account at <https://pypi.org/account/register/> and turn on 2FA.
2. Go to <https://pypi.org/manage/account/publishing/> -> **Add a new pending publisher**:
   - PyPI Project Name: `workday-autofill`
   - Owner: `HarshadLambate`
   - Repository name: `workday-autofill`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. On GitHub: repo -> **Settings -> Environments -> New environment** -> name it `pypi`
   (optionally add yourself as a required reviewer).

## Every release

1. Bump the version in **both** `pyproject.toml` (`version = "X.Y.Z"`) and
   `src/wdautofill/__init__.py` (`__version__ = "X.Y.Z"`).
2. Commit and push; check the **tests** workflow is green.
3. GitHub -> **Releases -> Draft a new release** -> tag `vX.Y.Z` (create new tag) ->
   title + notes -> **Publish release**.
4. Watch **Actions -> publish to PyPI**. When it's green, `pip install -U workday-autofill`
   gets the new version. (The workflow refuses to publish if the tag and the version in
   `pyproject.toml` don't match.)

PyPI never lets you re-upload the same version number - if something went wrong, bump
to the next patch version (e.g. 0.5.1) and release again.
