import asyncio
import os
import sys
import zipfile
import requests
import re
import shutil
import json
import base64
from playwright.async_api import async_playwright

# ✅ إصلاح إلزامي لـ Playwright على Windows لدعم العمليات الفرعية (Subprocesses)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ==========================================
# الإعدادات - تُقرأ من متغيرات البيئة
# ==========================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "8092953314")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
REGION_OVERRIDE = os.environ.get("REGION_OVERRIDE", "")  
LOG_BOT_TOKEN = os.environ.get("LOG_BOT_TOKEN", BOT_TOKEN) 
LOG_CHANNEL_ID = "-1004367699466"
COOKIES_B64 = os.environ.get("COOKIES_B64", "")
MODE = os.environ.get("MODE", "full_automation")  # 'cloud_run_only' أو 'full_automation'

# متغيرات كونسول السحاب الافتراضية
MIN_INSTANCES = os.environ.get("MIN_INSTANCES", "2")
MAX_INSTANCES = os.environ.get("MAX_INSTANCES", "8")

BUSTER_COMPILED_URL = "https://github.com/dessant/buster/releases/download/v3.1.0/buster_captcha_solver_for_humans-3.1.0-chrome.zip"

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

# فك تشفير الكوكيز من Base64
try:
    MY_COOKIES = json.loads(base64.b64decode(COOKIES_B64).decode("utf-8"))
except Exception:
    MY_COOKIES = []

class LoginRequiredError(Exception): pass

# ==========================================
# دوال الإرسال (تتوافق مع أسماء الكود الأساسي)
# ==========================================
def send_telegram_msg(chat_id, text):
    if BOT_TOKEN and chat_id:
        try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=30)
        except: pass

def send_log_to_channel(text):
    token_to_use = LOG_BOT_TOKEN if LOG_BOT_TOKEN else BOT_TOKEN
    if token_to_use and LOG_CHANNEL_ID:
        try: requests.post(f"https://api.telegram.org/bot{token_to_use}/sendMessage", json={"chat_id": LOG_CHANNEL_ID, "text": text}, timeout=30)
        except: pass

def send_telegram_photo(chat_id, photo_path, caption):
    if BOT_TOKEN and chat_id:
        try:
            with open(photo_path, "rb") as photo:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files={"photo": photo}, timeout=30)
        except: 
            send_telegram_msg(chat_id, caption)

# ==========================================
# دوال التحكم والـ UI لقسم الكلاود شيل
# ==========================================
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
                    if await cb.is_visible(): await cb.click(timeout=1500, force=True); return True
                locs = target.locator("label, div, span, [role='checkbox']").filter(has_text=terms_regex)
                for i in range(await locs.count()):
                    el = locs.nth(i)
                    if await el.is_visible(): await el.click(timeout=1500, force=True); return True
            except: pass
        await asyncio.sleep(0.5)
    return False

async def get_cloudshell_frame(page):
    for _ in range(60):
        for f in page.frames:
            if "shell.cloud.google.com" in (f.url or "").lower() or "embeddedcloudshell" in (f.url or "").lower(): return f
        await asyncio.sleep(1)
    return None

async def wait_for_cloud_shell_prompt(page, timeout_loop=180):
    prompt_patterns = [r"\$\s*$", r"cloudshell:~", r"student_.*@cloudshell", r"welcome to cloud shell"]
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        if f:
            try:
                txt = await f.inner_text("body")
                if any(re.search(pat, txt, re.I | re.M) for pat in prompt_patterns): return True
            except: pass
        await asyncio.sleep(1)
    return False

async def focus_terminal_near_prompt(page, timeout_loop=60):
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        if f:
            for sel in ["textarea.xterm-helper-textarea", "textarea", "div.xterm", "div.xterm-screen", "canvas"]:
                try:
                    loc = f.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=1500, force=True)
                        box = await loc.bounding_box()
                        if box: await page.mouse.click(box["x"] + 40, box["y"] + max(10, box["height"] - 20))
                        return True
                except: pass
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
        except Exception:
            pass
        await page.keyboard.insert_text(command)
        
    if f:
        try:
            ta = f.locator("textarea.xterm-helper-textarea").first
            if await ta.count() > 0:
                await ta.focus(); await asyncio.sleep(0.2); await _paste_into_focused()
            else: await _paste_into_focused()
        except Exception: await _paste_into_focused()
    else: await _paste_into_focused()
        
    await asyncio.sleep(0.8)
    try:
        if f:
            try:
                ta = f.locator("textarea.xterm-helper-textarea").first
                if await ta.count() > 0: await ta.focus(); await asyncio.sleep(0.2)
            except Exception: pass
        await page.keyboard.press("Enter")
        return True
    except Exception:
        return False

async def wait_for_yes_no_prompt(page, timeout_loop=3):
    patterns = [r"\[y\/n\]", r"\(y\/n\)", r"\[y\/N\]", r"Do you want to continue", r"continue\?\s*$"]
    for _ in range(timeout_loop):
        f = await get_cloudshell_frame(page)
        for target in ([f] if f else []) + [fr for fr in page.frames if fr != f] + [page]:
            try:
                txt = await target.inner_text("body")
                if any(re.search(p, txt, re.I | re.M) for p in patterns): return True
            except: pass
        await asyncio.sleep(1)
    return False

async def type_short_answer_only(page, answer_text="y"):
    await focus_terminal_near_prompt(page, timeout_loop=20)
    f = await get_cloudshell_frame(page)
    try:
        if f and await f.locator("textarea.xterm-helper-textarea").first.count() > 0:
            await f.locator("textarea.xterm-helper-textarea").first.focus(); await asyncio.sleep(0.2); await f.locator("textarea.xterm-helper-textarea").first.type(answer_text, delay=50)
        else: await page.keyboard.insert_text(answer_text)
    except: await page.keyboard.type(answer_text, delay=50)
    await asyncio.sleep(0.4)
    return True

# ==========================================
# دوال أتمتة Qwiklabs / Google Skills
# ==========================================
def fix_cookies_for_playwright(cookies):
    valid_samesite = ["Strict", "Lax", "None"]
    cleaned = []
    for cookie in cookies:
        c = cookie.copy()
        if c.get("sameSite") not in valid_samesite:
            c.pop("sameSite", None)
        cleaned.append(c)
    return cleaned

async def setup_compiled_buster():
    ext_dir = os.path.abspath("buster_compiled_ext")
    if os.path.exists(ext_dir): shutil.rmtree(ext_dir)
    os.makedirs(ext_dir)
    zip_path = "buster_ready.zip"
    try:
        r = requests.get(BUSTER_COMPILED_URL, timeout=30)
        with open(zip_path, "wb") as f: f.write(r.content)
        with zipfile.ZipFile(zip_path, 'r') as z: z.extractall(ext_dir)
        os.remove(zip_path)
        return ext_dir
    except Exception as e:
        if ADMIN_ID: send_telegram_msg(ADMIN_ID, f"❌ فشل تحميل Buster: {e}")
        return None

async def human_click(page, locator):
    try:
        await locator.scroll_into_view_if_needed()
        await locator.click(force=True, delay=200)
        return True
    except: return False

async def dismiss_credits_modal(page):
    try:
        btn = page.get_by_role("button", name=re.compile(r"Dismiss", re.I))
        if await btn.count() > 0 and await btn.first.is_visible():
            await btn.first.click()
            await asyncio.sleep(2)
            return True
    except: pass
    return False

async def click_start_lab_button(page):
    pattern = re.compile(r"Start\s*Lab", re.IGNORECASE)
    for _ in range(30):
        try:
            btn = page.get_by_role("button", name=pattern).first
            if await btn.is_visible():
                await btn.click(force=True)
                return True
        except: pass
        await asyncio.sleep(1)
    return False

async def click_captcha_checkbox(page):
    await asyncio.sleep(3)
    iframes = await page.locator('iframe[title*="reCAPTCHA"]').all()
    for iframe in iframes:
        try:
            frame_content = iframe.content_frame
            checkbox = frame_content.locator('.recaptcha-checkbox-border').first
            if await checkbox.is_visible():
                await human_click(page, checkbox)
                return True
        except: continue
    return False

async def click_launch_with_credits_aggressive(page):
    for _ in range(15):
        try:
            js_success = await page.evaluate('''() => {
                let els = Array.from(document.querySelectorAll('*'));
                let t = els.find(e => e.textContent && e.textContent.trim() === 'Launch with 5 Credits');
                if(t) { t.click(); return true; } return false;
            }''')
            if js_success: return True
            xp = page.locator("xpath=//*[text()='Launch with 5 Credits']").first
            if await xp.is_visible(): await xp.click(force=True); return True
            tl = page.locator("text=Launch with 5 Credits").first
            if await tl.is_visible(): await tl.click(force=True); return True
        except: pass
        await asyncio.sleep(1)
    try:
        await page.screenshot(path="debug_credits.png")
        if ADMIN_ID: send_telegram_photo(ADMIN_ID, "debug_credits.png", "⚠️ لم يُعثر على زر Credits")
    except: pass
    return False

async def get_cloud_console_link(page):
    try:
        btn = page.locator("text=Open Google Cloud console").first
        await btn.wait_for(state="visible", timeout=15000)
        link = await btn.get_attribute("href")
        if not link:
            link = await page.evaluate('''() => {
                let els = Array.from(document.querySelectorAll('*'));
                let t = els.find(e => e.textContent && e.textContent.includes('Open Google Cloud console'));
                return t ? (t.getAttribute('href') || (t.parentElement && t.parentElement.getAttribute('href'))) : null;
            }''')
        return link
    except Exception as e:
        try:
            await page.screenshot(path="debug_console.png")
            if ADMIN_ID: send_telegram_photo(ADMIN_ID, "debug_console.png", f"⚠️ فشل استخراج رابط الكونسول: {e}")
        except: pass
    return None

async def method_1_direct_click(page):
    try:
        cf = page.frame_locator('iframe[src*="recaptcha/api2/bframe"]').first
        audio_btn = cf.locator('#recaptcha-audio-button')
        if await audio_btn.is_visible(timeout=5000):
            await audio_btn.click(force=True)
            await asyncio.sleep(2)
        buster_btn = cf.locator('.help-button-holder, button[title*="Solve the challenge"], button[title*="Buster"]').first
        if await buster_btn.is_visible(timeout=5000):
            await buster_btn.click(force=True)
            await asyncio.sleep(8)
            try:
                vb = cf.locator('#recaptcha-verify-button')
                if not await vb.evaluate("n => n.disabled") and await vb.is_visible():
                    await vb.evaluate("n => n.click()")
            except: pass
            return True
    except Exception as e:
        if ADMIN_ID: send_telegram_msg(ADMIN_ID, f"❌ خطأ حل الكابتشا: {e}")
    return False

async def try_all_buster_methods(page):
    if await page.locator('.recaptcha-checkbox-checked').is_visible(): return True
    if not await page.locator('iframe[src*="recaptcha/api2/bframe"]').is_visible():
        await click_captcha_checkbox(page)
        await asyncio.sleep(3)
    return await method_1_direct_click(page)

# ==========================================
# استخراج بيانات الدخول ومعالجة حساب الطالب لقوقل
# ==========================================
async def extract_credentials(page):
    try:
        email, password = None, None
        email_el = page.locator("[data-credential='username'], #student-username, #content-credentials-email").first
        if await email_el.count() > 0: email = (await email_el.inner_text()).strip()
        pass_el = page.locator("[data-credential='password'], #student-password, #content-credentials-password").first
        if await pass_el.count() > 0: password = (await pass_el.inner_text()).strip()
        
        if not email:
            html = await page.content()
            match = re.search(r"student-[0-9a-fA-F-]+@qwiklabs\.net", html)
            if match: email = match.group(0)
        return email, password
    except: return None, None

async def handle_google_login(page, email, password):
    try:
        email_input = page.locator("input#identifierId").first
        if await email_input.count() > 0 and await email_input.is_visible():
            await email_input.fill(email); await page.keyboard.press("Enter"); await asyncio.sleep(4)
        pass_input = page.locator("input[type='password']").first
        if await pass_input.count() > 0 and await pass_input.is_visible():
            await pass_input.fill(password); await page.keyboard.press("Enter"); await asyncio.sleep(6)
    except Exception as e:
        print(f"Error Google login: {e}")

# ==========================================
# أتمتة نشر Cloud Run (نفس كود السيكريت الأصلي)
# ==========================================
async def run_cloud_run_deploy_flow(page, console_link):
    clicked_understand = await click_button_by_text_anywhere(page, "I understand", exact=True, timeout_loop=60, post_click_wait=0)
    if clicked_understand: await asyncio.sleep(10) 
    
    await try_click_terms_checkbox(page)
    await asyncio.sleep(2)
    await click_button_by_text_anywhere(page, "Agree and continue", exact=True, timeout_loop=60)
    await asyncio.sleep(3)
    
    for sel in ['button[aria-label*="Activate Cloud Shell"]', 'button[title*="Cloud Shell"]']:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(): await loc.click(timeout=3000, force=True); break
        except: pass
        
    await asyncio.sleep(5) 
    await click_button_by_text_anywhere(page, "Continue", exact=True, timeout_loop=60)
    await click_button_by_text_anywhere(page, "Authorize", exact=True, timeout_loop=60)
    
    if await wait_for_cloud_shell_prompt(page):
        url_re = re.compile(r"Service URL:\s*(https://[a-zA-Z0-9.-]+\.run\.app)", re.I)
        
        if REGION_OVERRIDE and REGION_OVERRIDE.strip():
            regions = [REGION_OVERRIDE.strip()]
        else:
            regions = ["europe-west12", "europe-west1", "europe-west4", "us-west1", "us-central1", "us-east1"]
            
        deploy_wait_loops = 20
        deploy_cmd_template = (
            "gcloud run deploy my-app \\\n"
            "  --project=$DEVSHELL_PROJECT_ID \\\n"
            "  --image=docker.io/nkka404/vless-ws:latest \\\n"
            "  --platform=managed \\\n"
            "  --allow-unauthenticated \\\n"
            "  --port=8080 \\\n"
            "  --cpu=2 \\\n"
            "  --memory=4Gi \\\n"
            "  --concurrency=1000 \\\n"
            "  --timeout=3600 \\\n"
            "  --min-instances=" + MIN_INSTANCES + " \\\n"
            "  --max-instances=" + MAX_INSTANCES + " \\\n"
            "  --execution-environment=gen2 \\\n"
            "  --cpu-boost \\\n"
            "  --region={REGION}"
        )
        
        for region in regions:
            try:
                await focus_terminal_near_prompt(page, timeout_loop=5)
                await page.keyboard.press("Control+C")
                await asyncio.sleep(1)
                await paste_command_and_run(page, "clear")
                await asyncio.sleep(2)
            except: pass

            cmd = deploy_cmd_template.replace("{REGION}", region)
            await paste_command_and_run(page, cmd)
            
            y_sent = False
            for step in range(deploy_wait_loops):
                f = await get_cloudshell_frame(page)
                if not f: 
                    await asyncio.sleep(3)
                    continue
                
                txt = await f.inner_text("body")
                txt_lower = txt.lower()
                
                if not y_sent and await wait_for_yes_no_prompt(page, timeout_loop=1):
                    await type_short_answer_only(page, "y")
                    try: await page.keyboard.press("Enter")
                    except: pass
                    y_sent = True
                
                match = url_re.search(txt)
                if match:
                    final_url = match.group(1)
                    send_log_to_channel(f"#DONE|{CHAT_ID}|{final_url}")
                    send_telegram_msg(CHAT_ID, f"🎉 <b>تم النشر بنجاح!</b>\nالرابط: <code>{final_url}</code>\nالمنطقة: {region}")
                    return
                
                has_error = any(indicator in txt_lower for indicator in ERROR_INDICATORS)
                if has_error:
                    print(f"Failed in {region}, moving to next...")
                    break 
                    
                await asyncio.sleep(3)
                
        raise Exception("انتهت المحاولات: فشل النشر في جميع المناطق المتاحة.")
    else:
        raise Exception("فشل تحميل واجهة الأوامر Cloud Shell.")

# ==========================================
# الدالة الرئيسية للتشغيل والدمج الذكي
# ==========================================
async def run():
    if MODE == "full_automation" and (not COOKIES_B64 or not MY_COOKIES):
        send_telegram_msg(CHAT_ID, "❌ <b>فشل بدء العملية:</b> الكوكيز منتهية أو تالفة، يرجى تحديثها.")
        send_log_to_channel(f"#FAILED|{CHAT_ID}")
        return

    if not LAB_URL:
        send_telegram_msg(CHAT_ID, "❌ <b>فشل بدء العملية:</b> رابط التشغيل مفقود.")
        send_log_to_channel(f"#FAILED|{CHAT_ID}")
        return

    # 🚀 إرسال الرسالة النظيفة والوحيدة المطلوبة للمستخدم عند بدء التشغيل
    send_telegram_msg(CHAT_ID, "✅ تم بدء العمل في السيرفر، يرجى الانتظار...")
    if ADMIN_ID: send_telegram_msg(ADMIN_ID, f"🔔 مهمة أتمتة جديدة ({MODE})\nالمستخدم: {CHAT_ID}\nالرابط: {LAB_URL}")

    ext_path = None
    if MODE == "full_automation":
        ext_path = await setup_compiled_buster()
        if not ext_path:
            send_telegram_msg(CHAT_ID, "❌ <b>حدث خطأ أثناء المعالجة أو فشل النشر!</b>\nتم إلغاء طلبك.")
            send_log_to_channel(f"#FAILED|{CHAT_ID}")
            return

    user_data_dir = os.path.abspath("chrome_profile")
    # تنظيف الكاش القديم لتجنب تعليق الجلسة وثبات الأخطاء
    if os.path.exists(user_data_dir):
        try: shutil.rmtree(user_data_dir)
        except: pass

    async with async_playwright() as p:
        launch_args = ["--disable-blink-features=AutomationControlled", "--start-maximized", "--disable-infobars", "--disable-dev-shm-usage", "--no-sandbox"]
        if ext_path:
            launch_args.extend([f"--disable-extensions-except={ext_path}", f"--load-extension={ext_path}", "--disable-features=IsolateOrigins,site-per-process"])
            
        context = await p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False, # مبقي عليها False لكي تعمل إضافة تخطي الكابتشا بنجاح
            no_viewport=True,
            args=launch_args,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        
        try:
            page = context.pages[0]
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.navigator.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            """)

            console_link = None
            email = None
            password = None

            if MODE == "full_automation":
                raw_cookies = MY_COOKIES[0] if isinstance(MY_COOKIES[0], list) else MY_COOKIES
                await context.add_cookies(fix_cookies_for_playwright(raw_cookies))
                
                await page.goto(LAB_URL, timeout=300000, wait_until="domcontentloaded")
                await asyncio.sleep(5)

                await dismiss_credits_modal(page)
                if await click_start_lab_button(page):
                    await asyncio.sleep(5)
                    if await click_captcha_checkbox(page):
                        await asyncio.sleep(3)
                        await try_all_buster_methods(page)
                        await asyncio.sleep(3)

                    if await click_launch_with_credits_aggressive(page):
                        await asyncio.sleep(5)
                        email, password = await extract_credentials(page)
                        console_link = await get_cloud_console_link(page)
                    else:
                        raise Exception("فشل الضغط على Launch with Credits")
                else:
                    raise Exception("فشل العثور أو النقر على زر Start Lab الرئيسي")
            else:
                console_link = LAB_URL

            if console_link:
                await page.goto(console_link, timeout=300000, wait_until="domcontentloaded")
                await asyncio.sleep(5)

                is_login_page = await page.locator("input#identifierId").first.count() > 0 and await page.locator("input#identifierId").first.is_visible()
                is_google_acc = await page.locator("text='Use your Google Account'").first.count() > 0 and await page.locator("text='Use your Google Account'").first.is_visible()
                
                if is_login_page or is_google_acc:
                    if email and password:
                        await handle_google_login(page, email, password)
                        if await page.locator("input#identifierId").first.count() > 0 and await page.locator("input#identifierId").first.is_visible():
                            raise LoginRequiredError()
                    else:
                        raise LoginRequiredError()

                await run_cloud_run_deploy_flow(page, console_link)
            else:
                raise Exception("رابط كونسول السحابة فارغ أو لم يستخرج")

        except LoginRequiredError:
            send_telegram_msg(CHAT_ID, "⚠️ <b>الرابط منتهي ويطلب تسجيل الدخول!</b>\nتم إلغاء طلبك، يمكنك المحاولة برابط جديد.")
            send_log_to_channel(f"#FAILED|{CHAT_ID}")
        except Exception as e:
            # 🟢 يتم دائماً إرسال رسالة فشل عامة وواضحة جداً للمستخدم بدون أي تفاصيل تقنية تفضح الكابتشا أو الـ Credits
            send_telegram_msg(CHAT_ID, "❌ <b>حدث خطأ أثناء المعالجة أو فشل النشر!</b>\nتم إلغاء طلبك.")
            send_log_to_channel(f"#FAILED|{CHAT_ID}")
            try:
                await page.screenshot(path="error.png", full_page=True)
                if ADMIN_ID: send_telegram_photo(ADMIN_ID, "error.png", f"🔴 خطأ داخلي لمستخدم {CHAT_ID}:\n{str(e)[:200]}")
            except: pass
        finally:
            await asyncio.sleep(5)
            await context.close()

if __name__ == "__main__":
    asyncio.run(run())
