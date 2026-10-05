"""HTML for the phone page. Signed-out visitors get the login page only."""

from __future__ import annotations

import html

_STYLE = """
:root {
  color-scheme: dark;
  --bg: #12161a;
  --text: #f3f6f7;
  --muted: #b4bdc3;
  --accent: #e6a817;
  --ink: #221a06;
  --ok: #3cba7c;
  --bad: #ef6b73;
  --wait: #8ebfff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 28px 16px;
  background: var(--bg);
  color: var(--text);
  font: 18px/1.45 "Segoe UI", system-ui, sans-serif;
}
main { width: min(100%, 26rem); }
h1 { font-size: 2rem; line-height: 1.15; margin: 0 0 12px; }
.who, .detail, .note { color: var(--muted); margin: 0 0 20px; }
.who { font-size: 0.95rem; }
a.button, button.primary {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 4.25rem;
  border: 0;
  border-radius: 14px;
  background: var(--accent);
  color: var(--ink);
  font: 650 1.2rem/1 "Segoe UI", system-ui, sans-serif;
  text-decoration: none;
}
button.primary:disabled { opacity: 0.5; }
a.button:focus-visible, button:focus-visible {
  outline: 3px solid var(--text);
  outline-offset: 3px;
}
form.out { margin: 22px 0 0; }
button.link {
  width: 100%;
  border: 0;
  background: transparent;
  color: var(--muted);
  font: 1rem/1 "Segoe UI", system-ui, sans-serif;
  padding: 12px;
}
main[data-phase="waking"] h1 { color: var(--accent); }
main[data-phase="waiting"] h1 { color: var(--wait); }
main[data-phase="started"] h1 { color: var(--ok); }
main[data-phase="failed"] h1 { color: var(--bad); }
"""

_HEADINGS = {
    "idle": "Wake the PC",
    "waking": "Waking",
    "waiting": "Waiting for Windows",
    "started": "Steam started",
    "failed": "Failed",
}

_BUTTONS = {
    "idle": "Wake",
    "started": "Wake again",
    "failed": "Try again",
}


def login_page(error: str) -> str:
    notes = {
        "signin": "Google sign-in did not finish. Try again.",
        "denied": "This Google account cannot use this page.",
    }
    note = ""
    if error in notes:
        note = f'<p class="note">{html.escape(notes[error])}</p>'
    body = f"""
<main>
  <h1>Sign in</h1>
  {note}
  <a class="button" href="/auth/google">Sign in with Google</a>
</main>
"""
    return _document("Sign in", body, refresh=False)


def app_page(email: str, phase: str, detail: str) -> str:
    if phase not in _HEADINGS:
        phase = "failed"
    heading = _HEADINGS[phase]
    if phase == "failed":
        message = detail or "Wake failed."
    elif phase == "idle":
        message = "Turn on the gaming PC and start Steam."
    else:
        message = detail
    busy = phase in ("waking", "waiting")
    disabled = " disabled" if busy else ""
    label = _BUTTONS.get(phase, "Wake")
    body = f"""
<main data-phase="{html.escape(phase)}">
  <p class="who">{html.escape(email)}</p>
  <h1>{html.escape(heading)}</h1>
  <p class="detail">{html.escape(message)}</p>
  <form method="post" action="/wake">
    <button class="primary" type="submit"{disabled}>{html.escape(label)}</button>
  </form>
  <form class="out" method="post" action="/logout">
    <button class="link" type="submit">Sign out</button>
  </form>
</main>
"""
    return _document(heading, body, refresh=busy)


def _document(title: str, body: str, refresh: bool) -> str:
    refresh_tag = '<meta http-equiv="refresh" content="2">' if refresh else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{refresh_tag}
<title>{html.escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
{body}
</body>
</html>
"""
