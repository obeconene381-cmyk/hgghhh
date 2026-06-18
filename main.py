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

# إصلاح مشكلة asyncio على Windows (ProactorEventLoop قد تسبب خطأ مع Playwright)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ==========================================
# الإعدادات - تُقرأ من متغيرات البيئة
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
CHAT_ID = os.environ.get("CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", ""))
ADMIN_ID = os.environ.get("ADMIN_ID", "8092953314")
LAB_URL = os.environ.get("LAB_URL", "")  # رابط اللاب أو الكونسول
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "-1003781090454")
COOKIES_B64 = os.environ.get("COOKIES_B64", "")
MODE = os.environ.get("MODE", "full_automation")  # 'cloud_run_only' أو 'full_automation'
REGION_OVERRIDE = os.environ.get("REGION_OVERRIDE", "")
LOG_BOT_TOKEN = os.environ.get("LOG_BOT_TOKEN", BOT_TOKEN)
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
# دوال الإرسال
# ==========================================
def send_tg(msg, img=None):
    """إرسال رسالة للمستخدم"""
    if not BOT_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
    try:
        if img and os.path.exists(img):
            with open(img, "rb") as f:
                requests.post(url + "sendPhoto", data={"chat_id": CHAT_ID, "caption": msg, "parse_mode": "HTML"}, files={"photo": f}, timeout=30)
        else:
            requests.post(url + "sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=30)
    except:
        pass

def send_admin(msg, img=None):
    """إرسال رسالة/صورة للمشرف فقط"""
    if not BOT_TOKEN or not ADMIN_ID: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/"
    try:
        if img and os.path.exists(img):
            with open(img, "rb") as f:
                requests.post(url + "sendPhoto", data={"chat_id": ADMIN_ID, "caption": msg, "parse_mode": "HTML"}, files={"photo": f}, timeout=30)
        else:
            requests.post(url + "sendMessage", json={"chat_id": ADMIN_ID, "text": msg, "parse_mode": "HTML"}, timeout=30)
    except:
        pass

def send_log(msg):
    """إرسال نتيجة لقناة اللوج"""
    token_to_use = LOG_BOT_TOKEN if LOG_BOT_TOKEN else BOT_TOKEN
    if not token_to_use or not LOG_CHANNEL_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{token_to_use}/sendMessage",
                      json={"chat_id": LOG_CHANNEL_ID, "text": msg}, timeout=30)
    except:
        pass

# ==========================================
# دوال مساعدة للتحكم والـ UI لقسم الكلاود شيل
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
    for _ in range(60):
        for f in page.frames:
            if "shell.cloud.google.com" in (f.url or "").lower() or "embeddedcloudshell" in (f.url or "").lower():
                return f
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
                        if box:
                            await page.mouse.click(box["x"] + 40, box["y"] + max(10, box["height"] - 20))
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
                await ta.focus()
                await asyncio.sleep(0.2)
                await _paste_into_focused()
            else:
                await _paste_into_focused()
        except Exception:
            await _paste_into_focused()
            
    await asyncio.sleep(0.8)
    
    try:
        if f:
            try:
                ta = f.locator("textarea.xterm-helper-textarea").first
                if await ta.count() > 0:
                    await ta.focus()
                    await asyncio.sleep(0.2)
            except Exception:
                pass
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
                if any(re.search(p, txt, re.I | re.M) for p in patterns):
                    return True
            except: pass
        await asyncio.sleep(1)
    return False

async def type_short_answer_only(page, answer_text="y"):
    await focus_terminal_near_prompt(page, timeout_loop=20)
    f = await get_cloudshell_frame(page)
    try:
        if f and await f.locator("textarea.xterm-helper-textarea").first.count() > 0:
            await f.locator("textarea.xterm-helper-textarea").first.focus()
            await asyncio.sleep(0.2)
            await f.locator("textarea.xterm-helper-textarea").first.type(answer_text, delay=50)
        else:
            await page.keyboard.insert_text(answer_text)
    except:
        await page.keyboard.type(answer_text, delay=50)
    await asyncio.sleep(0.4)
    return True

# ==========================================
# دوال أتمتة Qwiklabs (Start Lab)
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
        send_tg("📥 جاري تحميل إضافة حل الكابتشا...")
        r = requests.get(BUSTER_COMPILED_URL, timeout=30)
        with open(zip_path, "wb") as f: f.write(r.content)
        with zipfile.ZipFile(zip_path, 'r') as z: z.extractall(ext_dir)
        os.remove(zip_path)
        return ext_dir
    except Exception as e:
        send_admin(f"❌ فشل تحميل Buster: {e}")
        return None

async def human_click(page, locator):
    try:
        await locator.scroll_into_view_if_needed()
        await locator.click(force=True, delay=200)
        return True
    except:
        return False

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
                send_tg("✅ تم الضغط على Start Lab")
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
                send_tg("✅ تم الضغط على مربع الكابتشا")
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
        send_admin("⚠️ لم يُعثر على زر Credits", "debug_credits.png")
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
        if link:
            send_tg(f"🔗 تم الحصول على رابط الكونسول بنجاح.")
            return link
    except Exception as e:
        try:
            await page.screenshot(path="debug_console.png")
            send_admin(f"⚠️ فشل استخراج رابط الكونسول: {e}", "debug_console.png")
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
        send_admin(f"❌ خطأ حل الكابتشا: {e}")
    return False

async def try_all_buster_methods(page):
    if await page.locator('.recaptcha-checkbox-checked').is_visible(): return True
    if not await page.locator('iframe[src*="recaptcha/api2/bframe"]').is_visible():
        await click_captcha_checkbox(page)
        await asyncio.sleep(3)
    return await method_1_direct_click(page)

# ==========================================
# استخراج وحل تسجيل الدخول لـ Google
# ==========================================
async def extract_credentials(page):
    """استخراج البريد وكلمة المرور من لوحة بيانات Qwiklabs"""
    try:
        email = None
        password = None
        
        email_el = page.locator("[data-credential='username'], #student-username, #content-credentials-email").first
        if await email_el.count() > 0:
            email = (await email_el.inner_text()).strip()
            
        pass_el = page.locator("[data-credential='password'], #student-password, #content-credentials-password").first
        if await pass_el.count() > 0:
            password = (await pass_el.inner_text()).strip()
            
        if not email:
            html = await page.content()
            match = re.search(r"student-[0-9a-fA-F-]+@qwiklabs\.net", html)
            if match:
                email = match.group(0)
                
        return email, password
    except:
        return None, None

async def handle_google_login(page, email, password):
    """أتمتة تسجيل الدخول لحساب الطالب المخصص"""
    try:
        # إدخال البريد الإلكتروني
        email_input = page.locator("input#identifierId").first
        if await email_input.count() > 0 and await email_input.is_visible():
            await email_input.fill(email)
            await page.keyboard.press("Enter")
            await asyncio.sleep(4)
            
        # إدخال كلمة المرور
        pass_input = page.locator("input[type='password']").first
        if await pass_input.count() > 0 and await pass_input.is_visible():
            await pass_input.fill(password)
            await page.keyboard.press("Enter")
            await asyncio.sleep(6)
    except Exception as e:
        print(f"Error handling Google login: {e}")

async def detect_page_state(page):
    try:
        url = page.url.lower()
        for s in ["sign_in", "signin", "login", "accounts.google.com", "servicelogin"]:
            if s in url: return "EXPIRED_ACCOUNT"
        content = (await page.content()).lower()
        for s in ["404", "not found", "page not found", "unavailable", "does not exist"]:
            if s in content: return "INVALID_LAB"
        for s in ["sign in to continue", "sign in with google", "choose an account"]:
            if s in content: return "EXPIRED_ACCOUNT"
    except: pass
    return "OK"

# ==========================================
# أتمتة نشر Cloud Run
# ==========================================
async def run_cloud_run_deploy_flow(page, console_link):
    send_tg("⏳ جاري تهيئة موصل الخدمة (Cloud Shell)...")
    
    clicked_understand = await click_button_by_text_anywhere(page, "I understand", exact=True, timeout_loop=60, post_click_wait=0)
    if clicked_understand: await asyncio.sleep(5) 
    
    await try_click_terms_checkbox(page)
    await asyncio.sleep(2)
    await click_button_by_text_anywhere(page, "Agree and continue", exact=True, timeout_loop=60)
    await asyncio.sleep(3)
    
    # تشغيل الكلاود شيل
    activated = False
    for sel in ['button[aria-label*="Activate Cloud Shell"]', 'button[title*="Cloud Shell"]']:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=3000, force=True)
                activated = True
                break
        except: pass
        
    await asyncio.sleep(5) 
    await click_button_by_text_anywhere(page, "Continue", exact=True, timeout_loop=60)
    await click_button_by_text_anywhere(page, "Authorize", exact=True, timeout_loop=60)
    
    if await wait_for_cloud_shell_prompt(page):
        send_tg("💻 تم فتح Cloud Shell بنجاح! جاري النشر...")
        url_re = re.compile(r"Service URL:\s*(https://[a-zA-Z0-9.-]+\.run\.app)", re.I)
        
        if REGION_OVERRIDE and REGION_OVERRIDE.strip():
            regions = [REGION_OVERRIDE.strip()]
        else:
            regions = ["europe-west12", "europe-west1", "europe-west4", "us-west1", "us-central1", "us-east1"]
            
        deploy_wait_loops = 20
        
        for region in regions:
            try:
                await focus_terminal_near_prompt(page, timeout_loop=5)
                await page.keyboard.press("Control+C")
                await asyncio.sleep(1)
                await paste_command_and_run(page, "clear")
                await asyncio.sleep(2)
            except: pass

            deploy_cmd = (
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
                "  --region=" + region
            )
            
            await paste_command_and_run(page, deploy_cmd)
            
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
                
                # كشف رابط النجاح
                match = url_re.search(txt)
                if match:
                    final_url = match.group(1)
                    send_tg(f"🎉 <b>تم النشر بنجاح!</b>\nالرابط: <code>{final_url}</code>\nالمنطقة: {region}")
                    send_log(f"#AUTO_DONE|{CHAT_ID}|{final_url}")
                    return
                
                # كشف الأخطاء
                has_error = any(indicator in txt_lower for indicator in ERROR_INDICATORS)
                if has_error:
                    print(f"Failed in {region}, moving to next...")
                    break # الانتقال للمنطقة التالية
                    
                await asyncio.sleep(3)
                
        raise Exception("انتهت المحاولات: فشل النشر في جميع المناطق المتاحة.")
    else:
        raise Exception("فشل تحميل واجهة الأوامر Cloud Shell.")

# ==========================================
# الدالة الرئيسية
# ==========================================
async def run():
    if MODE == "full_automation":
        if not COOKIES_B64 or not MY_COOKIES:
            send_tg("⚠️ الكوكيز غير صالحة. يرجى تحديث الكوكيز.")
            send_log(f"#AUTO_FAILED|{CHAT_ID}|EXPIRED_ACCOUNT")
            send_admin(f"❌ كوكيز فارغة/تالفة - المستخدم: {CHAT_ID}")
            return
        if not LAB_URL:
            send_tg("⚠️ رابط اللاب غير موجود.")
            send_log(f"#AUTO_FAILED|{CHAT_ID}|INVALID_LAB")
            return
    else:  # cloud_run_only
        if not LAB_URL:
            send_tg("⚠️ رابط الكونسول غير موجود.")
            send_log(f"#AUTO_FAILED|{CHAT_ID}|INVALID_LAB")
            return

    send_tg("🚀 بدء المهمة...")
    send_admin(f"🔔 مهمة أتمتة جديدة ({MODE})\nالمستخدم: {CHAT_ID}\nالرابط: {LAB_URL}")

    ext_path = None
    if MODE == "full_automation":
        ext_path = await setup_compiled_buster()
        if not ext_path:
            send_log(f"#AUTO_FAILED|{CHAT_ID}|ERROR")
            return

    user_data_dir = os.path.abspath("chrome_profile")
    page = None

    async with async_playwright() as p:
        # استخدام headless=True بشكل جيد على Windows بدون الحاجة لبيئة عرض (display server)
        # لا نستخدم --no-sandbox و--disable-gpu لأنها مخصصة لـ Linux فقط
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--disable-infobars",
            "--disable-dev-shm-usage",
        ]
        if ext_path:
            launch_args.extend([
                f"--disable-extensions-except={ext_path}",
                f"--load-extension={ext_path}",
                "--disable-features=IsolateOrigins,site-per-process"
            ])
            
        context = await p.chromium.launch_persistent_context(
            user_data_dir,
            headless=True,
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
                await page.goto(LAB_URL, timeout=60000)
                await asyncio.sleep(4)

                state = await detect_page_state(page)
                if state == "EXPIRED_ACCOUNT":
                    send_tg("⚠️ حسابك تالف. يرجى تحديث الكوكيز.")
                    send_log(f"#AUTO_FAILED|{CHAT_ID}|EXPIRED_ACCOUNT")
                    try: await page.screenshot(path="expired.png"); send_admin(f"حساب تالف - {CHAT_ID}", "expired.png")
                    except: pass
                    return
                if state == "INVALID_LAB":
                    send_tg("⚠️ رابط اللاب غير صالح.")
                    send_log(f"#AUTO_FAILED|{CHAT_ID}|INVALID_LAB")
                    try: await page.screenshot(path="invalid.png"); send_admin(f"لاب غير موجود - {CHAT_ID}", "invalid.png")
                    except: pass
                    return

                await dismiss_credits_modal(page)
                if await click_start_lab_button(page):
                    await asyncio.sleep(5)
                    if await click_captcha_checkbox(page):
                        await asyncio.sleep(3)
                        await try_all_buster_methods(page)
                        await asyncio.sleep(3)

                    state2 = await detect_page_state(page)
                    if state2 == "EXPIRED_ACCOUNT":
                        send_tg("⚠️ حسابك تالف. يرجى تحديث الكوكيز.")
                        send_log(f"#AUTO_FAILED|{CHAT_ID}|EXPIRED_ACCOUNT")
                        return

                    if await click_launch_with_credits_aggressive(page):
                        await asyncio.sleep(5)
                        email, password = await extract_credentials(page)
                        console_link = await get_cloud_console_link(page)
                    else:
                        send_tg("❌ فشل الإنشاء (Credits).")
                        send_log(f"#AUTO_FAILED|{CHAT_ID}|ERROR")
                        return
                else:
                    s3 = await detect_page_state(page)
                    if s3 == "EXPIRED_ACCOUNT":
                        send_tg("⚠️ حسابك تالف.")
                        send_log(f"#AUTO_FAILED|{CHAT_ID}|EXPIRED_ACCOUNT")
                    elif s3 == "INVALID_LAB":
                        send_tg("⚠️ رابط اللاب غير صالح.")
                        send_log(f"#AUTO_FAILED|{CHAT_ID}|INVALID_LAB")
                    else:
                        send_tg("❌ لم يُعثر على زر Start Lab.")
                        send_log(f"#AUTO_FAILED|{CHAT_ID}|ERROR")
                    try: await page.screenshot(path="no_start.png"); send_admin(f"فشل Start Lab - {CHAT_ID}", "no_start.png")
                    except: pass
                    return
            else:  # cloud_run_only
                console_link = LAB_URL

            if console_link:
                # فتح الكونسول
                await page.goto(console_link, timeout=300000, wait_until="domcontentloaded")
                await asyncio.sleep(5)

                is_login_page = await page.locator("input#identifierId").first.count() > 0 and await page.locator("input#identifierId").first.is_visible()
                is_google_acc = await page.locator("text='Use your Google Account'").first.count() > 0 and await page.locator("text='Use your Google Account'").first.is_visible()
                
                if is_login_page or is_google_acc:
                    if email and password:
                        send_tg("🔐 تسجيل الدخول التلقائي في Google Cloud Console...")
                        await handle_google_login(page, email, password)
                        if await page.locator("input#identifierId").first.count() > 0 and await page.locator("input#identifierId").first.is_visible():
                            raise LoginRequiredError()
                    else:
                        raise LoginRequiredError()

                await run_cloud_run_deploy_flow(page, console_link)
            else:
                send_tg("❌ لم يتم الحصول على رابط كونسول صالح.")
                send_log(f"#AUTO_FAILED|{CHAT_ID}|ERROR")

        except LoginRequiredError:
            send_tg("⚠️ الرابط منتهي ويطلب تسجيل الدخول! تم إلغاء طلبك.")
            send_log(f"#AUTO_FAILED|{CHAT_ID}|EXPIRED_ACCOUNT")
            try:
                await page.screenshot(path="login_required.png")
                send_admin(f"🔴 يطلب تسجيل دخول - {CHAT_ID}", "login_required.png")
            except: pass
        except Exception as e:
            send_tg("❌ حدث خطأ أثناء المعالجة أو فشل النشر.")
            send_log(f"#AUTO_FAILED|{CHAT_ID}|ERROR")
            try:
                if page:
                    await page.screenshot(path="crash.png")
                    send_admin(f"🔥 خطأ: {e}\nمستخدم: {CHAT_ID}", "crash.png")
                else:
                    send_admin(f"🔥 خطأ: {e}\nمستخدم: {CHAT_ID}")
            except:
                send_admin(f"🔥 خطأ: {e}")
        finally:
            await asyncio.sleep(5)
            await context.close()

if __name__ == "__main__":
    asyncio.run(run())
