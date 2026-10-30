from wdautofill import browser


def test_ensure_browser_installs_only_when_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(browser, "install_chromium", lambda: calls.append("install") or True)

    monkeypatch.setattr(browser, "chromium_installed", lambda: True)
    assert browser.ensure_browser() and calls == []

    monkeypatch.setattr(browser, "chromium_installed", lambda: False)
    assert browser.ensure_browser() and calls == ["install"]

    assert browser.ensure_browser(channel="chrome") and calls == ["install"]  # your own Chrome: nothing to install
