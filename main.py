#!/usr/bin/env python3
"""
Duolingo Streak Keeper

Uses a headless browser to log in, grabs the auth token, then calls
the Duolingo API to complete a practice session and maintain your streak.

Setup:
    1. pip install -r requirements.txt
    2. playwright install chromium
    3. Create a .env file:
           DUOLINGO_EMAIL=you@example.com
           DUOLINGO_PASSWORD=yourpassword
    4. python3 main.py
"""

import asyncio
import os
import sys
import time

import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

EMAIL    = os.getenv("DUOLINGO_EMAIL")
PASSWORD = os.getenv("DUOLINGO_PASSWORD")
JWT      = os.getenv("DUOLINGO_JWT")

BASE = "https://www.duolingo.com"
UA   = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Step 1 — Use a headless browser to log in and grab the JWT cookie
# ---------------------------------------------------------------------------

async def get_jwt() -> tuple[str, str]:
    """Returns (jwt_token, user_id) by logging in via headless browser."""
    print("→ Launching headless browser to log in …")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=UA,
        )
        page = await context.new_page()
        # Go to homepage first, then navigate to login like a real user
        await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Click "Log in" from the homepage
        for sel in ['[data-test="have-account"]', 'a[href*="login"]', 'button:has-text("Log in")', 'a:has-text("Log in")']:
            try:
                await page.click(sel, timeout=4000)
                print(f"  Clicked login link: {sel}")
                break
            except Exception:
                pass

        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        # Click and type email (typing triggers React state updates)
        email_sel = '[data-test="email-input"]'
        try:
            await page.wait_for_selector(email_sel, timeout=8000)
            await page.click(email_sel)
            await page.type(email_sel, EMAIL, delay=50)
            print("  Typed email")
        except Exception as e:
            await page.screenshot(path="debug_login.png")
            print(f"✗ Could not find email field ({e}) — saved debug_login.png")
            await browser.close()
            sys.exit(1)

        # Click and type password
        password_sel = '[data-test="password-input"]'
        try:
            await page.wait_for_selector(password_sel, timeout=5000)
            await page.click(password_sel)
            await page.type(password_sel, PASSWORD, delay=50)
            print("  Typed password")
        except Exception as e:
            await page.screenshot(path="debug_login.png")
            print(f"✗ Could not find password field ({e}) — saved debug_login.png")
            await browser.close()
            sys.exit(1)

        await asyncio.sleep(0.5)

        # Handle "account created with Google" screen — click Use another account
        try:
            el = page.locator('text=USE ANOTHER ACCOUNT')
            if await el.is_visible(timeout=2000):
                await el.click()
                await asyncio.sleep(1)
                # Re-fill password after switching
                await page.wait_for_selector('[data-test="password-input"]', timeout=5000)
                await page.click('[data-test="password-input"]')
                await page.type('[data-test="password-input"]', PASSWORD, delay=50)
        except Exception:
            pass

        # Submit
        for sel in ['[data-test="register-button"]', 'button[type="submit"]']:
            try:
                await page.click(sel, timeout=5000)
                print(f"  Submitted form")
                break
            except Exception:
                pass

        # Wait for redirect to /learn
        try:
            await page.wait_for_url("**/learn**", timeout=20000)
        except Exception:
            await page.screenshot(path="debug_login.png")
            print("✗ Login failed — saved debug_login.png to see what went wrong")
            await browser.close()
            sys.exit(1)

        # Grab JWT and user ID from cookies / local storage
        cookies = await context.cookies()
        jwt = next((c["value"] for c in cookies if c["name"] == "jwt_token"), None)

        # Try to get user_id from the page URL or localStorage
        user_id = None
        try:
            user_id = await page.evaluate("() => localStorage.getItem('user_id')")
        except Exception:
            pass
        if not user_id:
            # parse from URL e.g. /learn -> profile API
            pass

        await browser.close()

    if not jwt:
        print("✗ Could not retrieve JWT token after login")
        sys.exit(1)

    print("✓ Logged in successfully")
    return jwt, user_id


# ---------------------------------------------------------------------------
# Step 2 — Use the JWT to complete a practice session via the API
# ---------------------------------------------------------------------------

def get_user_info(client: httpx.Client, jwt: str) -> dict:
    resp = client.get(
        f"{BASE}/api/1/users/show",
        headers={"Authorization": f"Bearer {jwt}", "User-Agent": UA},
    )
    if resp.status_code == 200:
        data = resp.json()
        return {
            "user_id":         data.get("id") or data.get("user_id"),
            "learning_lang":   data.get("learning_language", "es"),
            "from_lang":       data.get("ui_language", "en"),
        }
    return {"user_id": None, "learning_lang": "es", "from_lang": "en"}


def complete_session(client: httpx.Client, jwt: str) -> None:
    info = get_user_info(client, jwt)
    learning = info["learning_lang"]
    from_lang = info["from_lang"]
    print(f"  Language: {from_lang} → {learning}")

    headers = {
        "Authorization": f"Bearer {jwt}",
        "User-Agent": UA,
        "Content-Type": "application/json",
    }

    # Create session
    print("→ Creating practice session …")
    resp = client.post(
        f"{BASE}/2017-06-30/sessions",
        json={
            "fromLanguage":     from_lang,
            "learningLanguage": learning,
            "challengeTypes":   ["translate"],
            "type":             "GLOBAL_PRACTICE",
        },
        headers=headers,
    )

    if resp.status_code not in (200, 201):
        print(f"✗ Could not create session ({resp.status_code}): {resp.text[:300]}")
        sys.exit(1)

    session    = resp.json()
    session_id = session.get("id") or session.get("session_id")
    print(f"  Session ID: {session_id}")

    # Submit as complete — send the full session back with completion fields
    print("→ Submitting session as complete …")
    payload = dict(session)
    payload.update({
        "heartsLeft":        0,
        "failed":            False,
        "shouldLearnThings": True,
        "endTime":           time.time(),
        "timeTaken":         60,
        "responses":         [],
    })
    resp = client.put(
        f"{BASE}/2017-06-30/sessions/{session_id}",
        json=payload,
        headers=headers,
    )

    if resp.status_code not in (200, 201):
        print(f"✗ Session submit failed ({resp.status_code}): {resp.text[:500]!r}")
        print(f"  Session data keys: {list(session.keys())}")
        sys.exit(1)

    result = resp.json()
    xp = result.get("xpGain") or result.get("xpGained") or result.get("xp_gained") or "?"
    print(f"✓ Done! XP gained: {xp}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    # Use JWT from .env directly if available (faster, no browser needed)
    if JWT:
        print("→ Using saved JWT token …")
        jwt = JWT
    else:
        if not EMAIL or not PASSWORD:
            print("Error: add DUOLINGO_JWT to your .env file")
            sys.exit(1)
        jwt, _ = await get_jwt()

    with httpx.Client(follow_redirects=True, timeout=15) as client:
        complete_session(client, jwt)

    print("\n Streak maintained!")


if __name__ == "__main__":
    asyncio.run(main())
