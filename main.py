import asyncio
import re
import os
import requests
from playwright.async_api import async_playwright

# إعدادات التليجرام
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") # تم إضافة هذا السطر لتفادي الأخطاء
LOG_GROUP_ID = "-5227321205" # ⚠️ آيدي مجموعة المراقبة الخاصة بك

def send_telegram_msg(chat_id, text):
    if BOT_TOKEN and chat_id:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def send_telegram_photo(chat_id, photo_path, caption):
    if BOT_TOKEN and chat_id:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        try:
            with open(photo_path, "rb") as photo:
                requests.post(url, data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files={"photo": photo})
        except Exception: 
            send_telegram_msg(chat_id, caption)

DEPLOY_CMD = "gcloud run deploy my-app --image=docker.io/nkka404/vless-ws:latest --platform=managed --allow-unauthenticated --port=8080 --cpu=2 --memory=4Gi --region=europe-west12"

# =========================
# دوال التخطي والضغط
# =========================
async def click_button_by_text_anywhere(page, text, exact=True, timeout_loop=120, post_click_wait=3):
    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I) if exact else re.compile(re.escape(text), re.I)
    async def _post_click_stabilize():
        try: await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception: pass
        await asyncio.sleep(post_click_wait)
        
    for _ in range(timeout_loop):
        targets = [page] + list(page.frames)
        for target in targets:
            try:
                btns = target.get_by_role("button", name=pattern)
                count = await btns.count()
                for i in range(count - 1, -1, -1):
                    b = btns.nth(i)
                    if await b.is_visible() and await b.is_enabled():
                        await b.scroll_into_view_if_needed(timeout=1000)
                        await b.click(timeout=3000, force=True) 
                        await _post_click_stabilize()
                        return True
            except Exception: pass
        await asyncio.sleep(1)
    return False

async def try_click_terms_checkbox(page):
    terms_regex = re.compile(r"i agree to the google cloud platform", re.I)
    targets = [page] + list(page.frames)
    for _ in range(2):
        for target in targets:
            try:
                cbs = target.get_by_role("checkbox")
                for i in range(await cbs.count()):
                    cb = cbs.nth(i)
                    if await cb.is_visible():
                        await cb.scroll_into_view_if_needed(timeout=1000)
                        await cb.click(timeout=1500, force=True)
                        return True
                for sel in ["label", "div", "span", "[role='checkbox']"]:
                    locs = target.locator(sel).filter(has_text=terms_regex)
                    for i in range(await locs.count()):
                        el = locs.nth(i)
                        if await el.is_visible():
                            await el.click(timeout=1500, force=True)
                            return True
            except Exception: pass
        await asyncio.sleep(0.5)
    return False

# =========================
# دوال التيرمنال
# =========================
async def get_cloudshell_frame(page):
    for _ in range(60):
        for f in page.frames:
            try:
                u = (f.url or "").lower()
                if "shell.cloud.google.com" in u or "embeddedcloudshell" in u: return f
            except Exception: pass
        await asyncio.sleep(1)
    return None

async def wait_for_cloud_shell_prompt(page, timeout_loop=180):
    prompt_patterns = [r"\$\s*$", r"cloudshell:~", r"student_.*@cloudshell", r"welcome to cloud shell"]
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        if f:
            try:
                txt = await f.inner_text("body")
                for pat in prompt_patterns:
                    if re.search(pat, txt, re.I | re.M): return True
            except Exception: pass
        await asyncio.sleep(1)
    return False

async def focus_terminal_near_prompt(page, timeout_loop=60):
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        if f:
            selectors = ["textarea.xterm-helper-textarea", "textarea", "div.xterm", "div.xterm-screen", "canvas"]
            for sel in selectors:
                try:
                    loc = f.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=1500, force=True)
                        box = await loc.bounding_box()
                        if box:
                            x = box["x"] + 40
                            y = box["y"] + max(10, box["height"] - 20)
                            await page.mouse.click(x, y)
                        return True
                except Exception: pass
        await asyncio.sleep(1)
    return False

async def paste_command_and_run(page, command):
    await focus_terminal_near_prompt(page, timeout_loop=30)
    f = await get_cloudshell_frame(page)
    async def _paste_into_focused():
        try:
            f2 = await get_cloudshell_frame(page)
            if f2:
                await f2.evaluate("""(text) => {
                    const ta = document.querySelector('textarea.xterm-helper-textarea');
                    if (!ta) throw new Error('no xterm-helper-textarea');
                    ta.focus();
                    const dt = new DataTransfer();
                    dt.setData('text/plain', text);
                    const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true });
                    ta.dispatchEvent(ev);
                }""", command)
                return
        except Exception: pass
        await page.keyboard.insert_text(command)

    if f:
        try:
            ta = f.locator("textarea.xterm-helper-textarea").first
            if await ta.count() > 0:
                await ta.focus()
                await asyncio.sleep(0.2)
        except Exception: pass
    
    await _paste_into_focused()
    await asyncio.sleep(0.8)
    
    try:
        if f:
            ta = f.locator("textarea.xterm-helper-textarea").first
            if await ta.count() > 0:
                await ta.focus()
        await page.keyboard.press("Enter")
    except Exception: pass
    return True

async def wait_for_yes_no_prompt(page, timeout_loop=120):
    patterns = [r"\[y\/n\]", r"\(y\/n\)", r"\[y\/N\]", r"Do you want to continue", r"continue\?\s*$"]
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        frames_to_check = [f] if f else []
        frames_to_check += [fr for fr in page.frames if fr != f]
        frames_to_check.append(page)
        for target in frames_to_check:
            try:
                txt = await target.inner_text("body")
                if any(re.search(p, txt, re.I | re.M) for p in patterns): return True
            except Exception: pass
        await asyncio.sleep(1)
    return False

async def type_short_answer_only(page, answer_text="y"):
    await focus_terminal_near_prompt(page, timeout_loop=20)
    f = await get_cloudshell_frame(page)
    try:
        if f:
            ta = f.locator("textarea.xterm-helper-textarea").first
            if await ta.count() > 0:
                await ta.focus()
                await asyncio.sleep(0.2)
                await ta.type(answer_text, delay=50)
            else:
                await page.keyboard.insert_text(answer_text)
        else:
            await page.keyboard.insert_text(answer_text)
    except Exception:
        await page.keyboard.type(answer_text, delay=50)
    await asyncio.sleep(0.4)
    return True

# =========================
# العملية الأساسية (Main Workflow)
# =========================

class LoginRequiredError(Exception):
    pass

async def run_automation(lab_url):
    send_telegram_msg(CHAT_ID, "✅ تم بدء العمل في الخلفية يرجى الانتظار لمدة تتراوح بين 3الى 5دقائق لانتهاء العمل")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--lang=en-US", "--no-sandbox", "--disable-gpu"])
        context = await browser.new_context(locale="en-US", viewport={'width': 1280, 'height': 720})
        page = await context.new_page()
        
        try:
            await page.goto(lab_url, timeout=120000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            
            login_input = page.locator("input#identifierId").first
            if await login_input.count() > 0 and await login_input.is_visible():
                raise LoginRequiredError("LOGIN_REQUIRED")
            
            login_text = page.locator("text='Use your Google Account'").first
            if await login_text.count() > 0 and await login_text.is_visible():
                raise LoginRequiredError("LOGIN_REQUIRED")
            
            clicked_understand = await click_button_by_text_anywhere(page, "I understand", exact=True, timeout_loop=60, post_click_wait=0)
            if clicked_understand:
                await asyncio.sleep(10) 
            
            await try_click_terms_checkbox(page)
            await asyncio.sleep(2)
            
            await click_button_by_text_anywhere(page, "Agree and continue", exact=True, timeout_loop=60)
            await asyncio.sleep(3)
            
            send_telegram_msg(CHAT_ID, "✅ تم الموافقة على شروط الخصوصية...")
            
            for sel in ['button[aria-label*="Activate Cloud Shell"]', 'button[title*="Cloud Shell"]']:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=3000, force=True)
                        break
                except Exception: pass
                
            await asyncio.sleep(5) 
            
            await click_button_by_text_anywhere(page, "Continue", exact=True, timeout_loop=60)
            await click_button_by_text_anywhere(page, "Authorize", exact=True, timeout_loop=60)
            
            if await wait_for_cloud_shell_prompt(page):
                await paste_command_and_run(page, DEPLOY_CMD)
                
                url_re = re.compile(r"Service URL:\s*(https://[a-zA-Z0-9.-]+\.run\.app)", re.I)
                y_sent = False
                
                for _ in range(50): 
                    f = await get_cloudshell_frame(page)
                    if not f: continue
                    txt = await f.inner_text("body")
                    
                    if not y_sent and await wait_for_yes_no_prompt(page, timeout_loop=1):
                        await type_short_answer_only(page, "y")
                        try:
                            await page.keyboard.press("Enter")
                        except: pass
                        y_sent = True
                    
                    match = url_re.search(txt)
                    if match:
                        final_url = match.group(1)
                        # ==========================================
                        # إرسال الرسالة السرية للقروب باه البوت يخدم الملفات
                        # ==========================================
                        secret_msg = f"#DONE | {CHAT_ID} | {final_url}"
                        send_telegram_msg(LOG_GROUP_ID, secret_msg)
                        return
                    await asyncio.sleep(3)
                raise Exception("اكتمل الوقت ولم يظهر الرابط النهائي في التيرمنال.")
            else:
                raise Exception("فشل الوصول للتيرمنال أو لم يجهز في الوقت المحدد.")

        except LoginRequiredError:
            error_msg = "⚠️ الرابط يطلب تسجيل الدخول تأكد من صلاحية الرابط او قم بإعادة ارساله للتحقق مرة اخرى"
            send_telegram_msg(CHAT_ID, error_msg)
            # إرسال إشعار فشل لتفريغ الطابور
            send_telegram_msg(LOG_GROUP_ID, f"#FAILED | {CHAT_ID}")
            
            path = "login_error.png"
            try:
                await page.screenshot(path=path, full_page=True)
                if str(CHAT_ID) != ADMIN_ID:
                    send_telegram_photo(ADMIN_ID, path, f"🔴 مستخدم {CHAT_ID} أرسل رابط يطلب تسجيل دخول (منتهي).")
            except: pass
        
        except Exception as e:
            error_msg = f"❌ <b>حدث خطأ أثناء المعالجة!</b>\nيرجى التأكد من صلاحية الرابط."
            send_telegram_msg(CHAT_ID, error_msg)
            # إرسال إشعار فشل لتفريغ الطابور
            send_telegram_msg(LOG_GROUP_ID, f"#FAILED | {CHAT_ID}")
            
            path = "error.png"
            try:
                await page.screenshot(path=path, full_page=True)
                send_telegram_photo(ADMIN_ID, path, f"🔴 خطأ لمستخدم {CHAT_ID}:\n{str(e)[:150]}")
            except: pass
        finally:
            await browser.close()

if __name__ == "__main__":
    url = os.environ.get("LAB_URL")
    if url: asyncio.run(run_automation(url))
