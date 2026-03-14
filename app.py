#!/usr/bin/env python3
"""
Duolingo Streak Keeper — Web UI
Run: python3 app.py
Then open: http://127.0.0.1:5000
"""

import asyncio
import time
import httpx
from flask import Flask, render_template_string, request
from playwright.async_api import async_playwright

app = Flask(__name__)

BASE = "https://www.duolingo.com"
UA   = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Duolingo Streak Keeper</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: sans-serif; max-width: 500px; margin: 60px auto; padding: 0 20px; }
    h1   { color: #58cc02; }
    input[type=text], input[type=password], input[type=email] {
      width: 100%; padding: 10px; font-size: 14px;
      border: 2px solid #ddd; border-radius: 8px;
      box-sizing: border-box; margin: 6px 0 14px;
    }
    button {
      background: #58cc02; color: white; border: none;
      padding: 12px 24px; font-size: 16px; border-radius: 8px;
      cursor: pointer; width: 100%;
    }
    button:hover { background: #46a302; }
    .result  { margin-top: 20px; padding: 16px; border-radius: 8px; font-size: 15px; }
    .success { background: #e7f8d0; color: #2d6a00; }
    .error   { background: #fde8e8; color: #8b0000; }
    label    { font-weight: bold; font-size: 14px; }
    .divider { text-align: center; margin: 20px 0; color: #aaa; font-size: 13px; }
    .divider hr { border: none; border-top: 1px solid #eee; margin: 0; }
    .tab { display: flex; gap: 8px; margin-bottom: 20px; }
    .tab a {
      flex: 1; text-align: center; padding: 10px; border-radius: 8px;
      text-decoration: none; font-weight: bold; font-size: 14px;
      border: 2px solid #eee; color: #666;
    }
    .tab a.active { border-color: #58cc02; color: #58cc02; background: #f0fde4; }
    small { color: #999; font-size: 11px; display: block; margin-bottom: 14px; }
  </style>
</head>
<body>
  <h1>🦜 Duolingo Streak Keeper</h1>

  <div class="tab">
    <a href="/?mode=password" class="{{ 'active' if mode == 'password' else '' }}">Email & Password</a>
    <a href="/?mode=jwt"      class="{{ 'active' if mode == 'jwt'      else '' }}">JWT Token</a>
  </div>

  <form method="POST">
    <input type="hidden" name="mode" value="{{ mode }}">

    {% if mode == 'password' %}
      <label>Duolingo Email</label>
      <input type="email" name="email" placeholder="you@example.com" required>
      <label>Password</label>
      <input type="password" name="password" placeholder="your password" required>
      <small>Note: only works for accounts created with email/password, not Google login.</small>

    {% else %}
      <label>JWT Token</label>
      <input type="text" name="jwt" placeholder="eyJhbGci..." required>
      <small>Get it from Chrome → F12 → Application → Cookies → duolingo.com → jwt_token</small>
    {% endif %}

    <button type="submit">Maintain Streak ✓</button>
  </form>

  {% if message %}
  <div class="result {{ css }}">{{ message }}</div>
  {% endif %}
</body>
</html>
"""


async def get_jwt_from_credentials(email: str, password: str) -> tuple[bool, str]:
    """Log in via headless browser and return (success, jwt_or_error)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=UA)
        page    = await context.new_page()

        await page.goto(BASE, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        for sel in ['[data-test="have-account"]', 'a[href*="login"]', 'button:has-text("Log in")']:
            try:
                await page.click(sel, timeout=4000)
                break
            except Exception:
                pass

        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        try:
            await page.wait_for_selector('[data-test="email-input"]', timeout=8000)
            await page.click('[data-test="email-input"]')
            await page.type('[data-test="email-input"]', email, delay=50)
            await page.click('[data-test="password-input"]')
            await page.type('[data-test="password-input"]', password, delay=50)
            await asyncio.sleep(0.5)
            await page.click('[data-test="register-button"]', timeout=5000)
        except Exception as e:
            await browser.close()
            return False, f"Could not fill login form: {e}"

        try:
            await page.wait_for_url("**/learn**", timeout=20000)
        except Exception:
            await browser.close()
            return False, "Login failed — wrong email/password, or account uses Google login"

        cookies = await context.cookies()
        jwt = next((c["value"] for c in cookies if c["name"] == "jwt_token"), None)
        await browser.close()

        if not jwt:
            return False, "Logged in but could not retrieve token"
        return True, jwt


def complete_session(jwt: str) -> tuple[bool, str]:
    headers = {
        "Authorization": f"Bearer {jwt}",
        "User-Agent": UA,
        "Content-Type": "application/json",
    }

    with httpx.Client(follow_redirects=True, timeout=15) as client:
        r = client.get(
            f"{BASE}/api/1/users/show",
            headers={"Authorization": f"Bearer {jwt}", "User-Agent": UA},
        )
        learning = "es"
        from_lang = "en"
        if r.status_code == 200:
            data = r.json()
            learning  = data.get("learning_language", "es")
            from_lang = data.get("ui_language", "en")

        r = client.post(
            f"{BASE}/2017-06-30/sessions",
            json={
                "fromLanguage":     from_lang,
                "learningLanguage": learning,
                "challengeTypes":   ["translate"],
                "type":             "GLOBAL_PRACTICE",
            },
            headers=headers,
        )
        if r.status_code not in (200, 201):
            return False, f"Could not create session ({r.status_code}) — is your token valid?"

        session = r.json()
        payload = dict(session)
        payload.update({
            "heartsLeft":        0,
            "failed":            False,
            "shouldLearnThings": True,
            "endTime":           time.time(),
            "timeTaken":         60,
            "responses":         [],
        })
        r = client.put(
            f"{BASE}/2017-06-30/sessions/{session.get('id')}",
            json=payload,
            headers=headers,
        )
        if r.status_code not in (200, 201):
            return False, f"Session submit failed ({r.status_code})"

        result = r.json()
        xp = result.get("xpGain") or result.get("xpGained") or result.get("xp_gained") or "?"
        return True, f"Streak maintained! +{xp} XP earned."


@app.route("/", methods=["GET", "POST"])
def index():
    message = None
    css     = None
    mode    = request.args.get("mode") or request.form.get("mode") or "password"

    if request.method == "POST":
        if mode == "password":
            email    = request.form.get("email", "").strip()
            password = request.form.get("password", "").strip()
            ok, result = asyncio.run(get_jwt_from_credentials(email, password))
            if not ok:
                message, css = result, "error"
            else:
                ok, message = complete_session(result)
                css = "success" if ok else "error"
        else:
            jwt = request.form.get("jwt", "").strip()
            ok, message = complete_session(jwt)
            css = "success" if ok else "error"

    return render_template_string(HTML, message=message, css=css, mode=mode)


if __name__ == "__main__":
    app.run(debug=False, port=5000)
