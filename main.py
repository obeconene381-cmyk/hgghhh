import asyncio
import re
import os
import requests
from playwright.async_api import async_playwright

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
REGION_OVERRIDE = os.environ.get("REGION_OVERRIDE", "")
LOG_BOT_TOKEN = os.environ.get("LOG_BOT_TOKEN")
LOG_CHANNEL_ID = "-1003781090454"

ERROR_INDICATORS = [
    "error:",
    "invalid value for [--region]",
    "permission_denied",
    "quota exceeded",
    "quota limit",
    "unavailable",
    "failed to create service",
    "organization policy",
    "resourcelocations violated",
    "constraint constraints/gcp.resourcelocations",
    "deployment failed",
    "badrequest",
    "failed_precondition"
]

# ✅ الـ regex المصلح - يتعرف على URL حتى لو فيه أسطر فارغة قبله أو بعده
URL_RE = re.compile(r"Service URL:\s*(https://[^\s\n]+\.run\.app)", re.I | re.M)

def send_telegram_msg(chat_id, text):
    if BOT_TOKEN and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10
            )
        except: pass

def send_log_to_channel(text):
    if LOG_BOT_TOKEN and LOG_CHANNEL_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage",
                json={"chat_id": LOG_CHANNEL_ID, "text": text},
                timeout=10
            )
        except: pass

def send_telegram_photo(chat_id, photo_path, caption):
    if BOT_TOKEN and chat_id:
        try:
            with open(photo_path, "rb") as photo:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": photo},
                    timeout=15
                )
        except: send_telegram_msg(chat_id, caption)

async def click_button_by_text_anywhere(page, text, exact=True, timeout_loop=120, post_click_wait=3):
    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I) if exact else re.compile(re.escape(text), re.I)
    async def _post_click_stabilize():
        try: await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except: pass
        await asyncio.sleep(post_click_wait)
    for _ in range(timeout_loop):
        for target in [page] + list(page.frames):
            try:
                btns = target.get_by_role("button", name=pattern)
                for i in range(await btns.count() - 1, -1, -1):
                    b = btns.nth(i)
                    if await b.is_visible() and await b.is_enabled():
                        await b.scroll_into_view_if_needed(timeout=1000)
                        await b.click(timeout=3000, force=True)
                        await _post_click_stabilize()
                        return True
            except: pass
        await asyncio.sleep(1)
    return False

async def try_click_terms_checkbox(page):
    terms_regex = re.compile(r"i agree to the google cloud platform", re.I)
    for _ in range(2):
        for target in [page] + list(page.frames):
            try:
                cbs = target.get_by_role("checkbox")
                for i in range(await cbs.count()):
                    cb = cbs.nth(i)
                    if await cb.is_visible():
                        await cb.click(timeout=1500, force=True)
                        return True
                locs = target.locator("label, div, span, [role='checkbox']").filter(has_text=terms_regex)
                for i in range(await locs.count()):
                    el = locs.nth(i)
                    if await el.is_visible():
                        await el.click(timeout=1500, force=True)
                        return True
            except: pass
        await asyncio.sleep(0.5)
    return False

async def get_cloudshell_frame(page):
    for f in page.frames:
        url = (f.url or "").lower()
        if "shell.cloud.google.com" in url or "embeddedcloudshell" in url:
            return f
    return None

async def wait_for_cloudshell_frame(page, timeout_loop=60):
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        if f: return f
        await asyncio.sleep(1)
    return None

async def wait_for_cloud_shell_prompt(page, timeout_loop=180):
    prompt_patterns = [r"\$\s*$", r"cloudshell:~", r"student_.*@cloudshell", r"welcome to cloud shell"]
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        if f:
            try:
                txt = await f.inner_text("body")
                if any(re.search(pat, txt, re.I | re.M) for pat in prompt_patterns):
                    return True
            except: pass
        await asyncio.sleep(1)
    return False

async def focus_terminal(page):
    f = await get_cloudshell_frame(page)
    if not f: return False
    for sel in ["textarea.xterm-helper-textarea", "textarea", "div.xterm-screen", "canvas"]:
        try:
            loc = f.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=1500, force=True)
                return True
        except: pass
    return False

async def paste_command_and_run(page, command):
    await focus_terminal(page)
    f = await get_cloudshell_frame(page)

    pasted = False
    if f:
        try:
            await f.evaluate("""(text) => {
                const ta = document.querySelector('textarea.xterm-helper-textarea');
                if (!ta) throw new Error('no textarea');
                ta.focus();
                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }));
            }""", command)
            pasted = True
        except: pass

    if not pasted:
        await page.keyboard.insert_text(command)

    await asyncio.sleep(0.5)
    await page.keyboard.press("Enter")
    await asyncio.sleep(0.5)

async def check_yes_no_and_reply(page):
    """يتحقق بسرعة من وجود سؤال y/n ويرد عليه - بدون timeout طويل"""
    patterns = [r"\[y/n\]", r"\(y/n\)", r"\[y/N\]", r"do you want to continue", r"continue\?"]
    f = await get_cloudshell_frame(page)
    if not f: return False
    try:
        txt = await f.inner_text("body")
        if any(re.search(p, txt, re.I | re.M) for p in patterns):
            await focus_terminal(page)
            try:
                ta = f.locator("textarea.xterm-helper-textarea").first
                if await ta.count() > 0:
                    await ta.focus()
                    await ta.type("y", delay=50)
                else:
                    await page.keyboard.insert_text("y")
            except:
                await page.keyboard.type("y")
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            return True
    except: pass
    return False

class LoginRequiredError(Exception): pass

async def run_automation(lab_url):
    send_telegram_msg(CHAT_ID, "✅ تم بدء العمل في السيرفر، يرجى الانتظار لمدة تتراوح بين 3 إلى 5 دقائق.")

    deploy_cmd_template = (
        "gcloud run deploy my-app "
        "--image=docker.io/nkka404/vless-ws:latest "
        "--platform=managed "
        "--allow-unauthenticated "
        "--port=8080 "
        "--cpu=2 "
        "--memory=4Gi "
        "--concurrency=1000 "
        "--timeout=3600 "
        "--min-instances=2 "
        "--max-instances=8 "
        "--execution-environment=gen2 "
        "--cpu-boost "
        "--region={REGION}"
    )

    if REGION_OVERRIDE:
        regions = [REGION_OVERRIDE.strip()]
        # 4 دقائق للمنطقة المحددة = 240 ثانية، كل دورة 2 ثانية = 120 دورة
        max_wait_per_region = 120
    else:
        regions = [
            "europe-west12",
            "europe-west1",
            "europe-west4",
            "us-west1",
            "us-central1",
            "us-east1",
        ]
        # 5 دقائق لكل منطقة = 300 ثانية، كل دورة 2 ثانية = 150 دورة
        max_wait_per_region = 150

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--lang=en-US", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(locale="en-US", viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        try:
            await page.goto(lab_url, timeout=120000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # التحقق من تسجيل الدخول
            try:
                if await page.locator("input#identifierId").first.is_visible():
                    raise LoginRequiredError()
            except LoginRequiredError: raise
            except: pass
            try:
                if await page.locator("text='Use your Google Account'").first.is_visible():
                    raise LoginRequiredError()
            except LoginRequiredError: raise
            except: pass

            # الضغط على الأزرار الأولية
            clicked = await click_button_by_text_anywhere(page, "I understand", exact=True, timeout_loop=30, post_click_wait=0)
            if clicked: await asyncio.sleep(8)

            await try_click_terms_checkbox(page)
            await asyncio.sleep(1)
            await click_button_by_text_anywhere(page, "Agree and continue", exact=True, timeout_loop=30)
            await asyncio.sleep(2)

            # تفعيل Cloud Shell
            for sel in ['button[aria-label*="Activate Cloud Shell"]', 'button[title*="Cloud Shell"]']:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=3000, force=True)
                        break
                except: pass

            await asyncio.sleep(4)
            await click_button_by_text_anywhere(page, "Continue", exact=True, timeout_loop=30)
            await click_button_by_text_anywhere(page, "Authorize", exact=True, timeout_loop=30)

            # انتظار ظهور الـ prompt
            prompt_ready = await wait_for_cloud_shell_prompt(page, timeout_loop=120)
            if not prompt_ready:
                raise Exception("لم يظهر الـ Cloud Shell prompt.")

            await asyncio.sleep(2)

            for region in regions:
                cmd = deploy_cmd_template.replace("{REGION}", region)
                await paste_command_and_run(page, cmd)

                y_sent = False
                success = False

                for tick in range(max_wait_per_region):
                    f = await get_cloudshell_frame(page)
                    if not f:
                        await asyncio.sleep(1)
                        continue

                    try:
                        txt = await f.inner_text("body")
                    except:
                        await asyncio.sleep(1)
                        continue

                    txt_lower = txt.lower()

                    # ✅ التحقق من URL النجاح أولاً
                    match = URL_RE.search(txt)
                    if match:
                        final_url = match.group(1).strip()
                        send_log_to_channel(f"#DONE|{CHAT_ID}|{final_url}")
                        success = True
                        return

                    # التحقق من سؤال y/n مرة واحدة فقط
                    if not y_sent:
                        replied = await check_yes_no_and_reply(page)
                        if replied:
                            y_sent = True

                    # التحقق من الأخطاء
                    has_error = any(ind in txt_lower for ind in ERROR_INDICATORS)
                    if has_error:
                        print(f"[✗] فشل في {region}، الانتقال للمنطقة التالية...")
                        await paste_command_and_run(page, "clear")
                        await asyncio.sleep(1)
                        break

                    await asyncio.sleep(2)

                if success:
                    return

            # فشل كل المناطق
            raise Exception("فشل النشر في جميع المناطق.")

        except LoginRequiredError:
            send_telegram_msg(CHAT_ID, "⚠️ <b>الرابط منتهي ويطلب تسجيل الدخول!</b>\nتم إلغاء طلبك، يمكنك المحاولة برابط جديد.")
            send_log_to_channel(f"#FAILED|{CHAT_ID}")

        except Exception as e:
            send_telegram_msg(CHAT_ID, "❌ <b>حدث خطأ أثناء المعالجة!</b>\nتم إلغاء طلبك، يرجى التأكد من صلاحية الرابط.")
            send_log_to_channel(f"#FAILED|{CHAT_ID}")
            try:
                await page.screenshot(path="error.png", full_page=True)
                send_telegram_photo(ADMIN_ID, "error.png", f"🔴 خطأ لمستخدم {CHAT_ID}:\n{str(e)[:200]}")
            except: pass

        finally:
            try: await browser.close()
            except: pass

if __name__ == "__main__":
    url = os.environ.get("LAB_URL")
    if url:
        asyncio.run(run_automation(url))
