import asyncio
import re
import os
import requests
from playwright.async_api import async_playwright

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
LOG_BOT_TOKEN = os.environ.get("LOG_BOT_TOKEN") 
LOG_CHANNEL_ID = "-1003781090454" # آيدي قناتك

# قائمة الكلمات التي تدل على الفشل أو قيود المنطقة
ERROR_INDICATORS = [
    "error:", "invalid value for [--region]", "permission_denied",
    "quota exceeded", "quota limit", "unavailable", "failed to create service",
    "organization policy", "resourcelocations violated", 
    "constraint constraints/gcp.resourcelocations", "deployment failed",
    "badrequest", "failed_precondition"
]

# ---------------------------------------------------------
# دالة تحديث كلاود فلير (النسخة المحدثة التي ترجع سبب الخطأ)
# ---------------------------------------------------------
def update_cloudflare_worker(new_gcp_url):
    CF_ACCOUNT_ID = "e66f9daaf04a57789345976693dfaa94"
    # تأكد من أن التوكن صحيح 100% (انتبه لحالة الأحرف الكبيرة والصغيرة)
    CF_API_TOKEN = "cfat_g0yDmvVp1nZZQ6DeARFRg4jWtIZxPANp0usU1y3Zf0c563cd"
    WORKER_NAME = "wild-limit-6d0c"

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/workers/scripts/{WORKER_NAME}"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/javascript"
    }

    # استخدام صيغة Service Worker المقبولة 100% للرفع المباشر
    worker_script = f"""
    addEventListener('fetch', event => {{
      event.respondWith(handleRequest(event.request))
    }})

    async function handleRequest(request) {{
      const url = new URL(request.url);
      
      if (url.pathname !== "/omarero-2026") {{
        return new Response("Unauthorized", {{ status: 403 }});
      }}
      
      const TARGET = "{new_gcp_url.replace('https://', '').replace('http://', '')}";
      url.hostname = TARGET;
      url.pathname = "/"; 
      
      const newRequest = new Request(url, request);
      newRequest.headers.set('Host', TARGET);
      
      return fetch(newRequest);
    }}
    """
    try:
        res = requests.put(url, headers=headers, data=worker_script.encode('utf-8'))
        if res.status_code == 200:
            return True, "تم"
        else:
            return False, res.text # إرجاع سبب الخطأ من كلاود فلير
    except Exception as e:
        return False, str(e)
# ---------------------------------------------------------

def send_telegram_msg(chat_id, text):
    if BOT_TOKEN and chat_id:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def send_log_to_channel(text):
    if LOG_BOT_TOKEN and LOG_CHANNEL_ID:
        requests.post(f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage", json={"chat_id": LOG_CHANNEL_ID, "text": text})

def send_telegram_photo(chat_id, photo_path, caption):
    if BOT_TOKEN and chat_id:
        try:
            with open(photo_path, "rb") as photo:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}, files={"photo": photo})
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
                        await b.scroll_into_view_if_needed(timeout=1000); await b.click(timeout=3000, force=True); await _post_click_stabilize(); return True
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

async def paste_command_and_run(page, command, timeout_verify=5):
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
    else:
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

async def wait_for_yes_no_prompt(page, timeout_loop=120):
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

class LoginRequiredError(Exception): pass

async def run_automation(lab_url):
    send_telegram_msg(CHAT_ID, "✅ تم بدء العمل في السيرفر، يرجى الانتظار لمدة تتراوح بين 3 إلى 5 دقائق.")
    
    deploy_cmd_template = (
        "gcloud run deploy my-app \\\n"
        "  --image=docker.io/nkka404/vless-ws:latest \\\n"
        "  --platform=managed \\\n"
        "  --allow-unauthenticated \\\n"
        "  --port=8080 \\\n"
        "  --cpu=2 \\\n"
        "  --memory=4Gi \\\n"
        "  --concurrency=1000 \\\n"
        "  --timeout=3600 \\\n"
        "  --min-instances=2 \\\n"
        "  --max-instances=8 \\\n"
        "  --execution-environment=gen2 \\\n"
        "  --cpu-boost \\\n"
        "  --region={REGION}"
    )
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--lang=en-US", "--no-sandbox", "--disable-gpu"])
        context = await browser.new_context(locale="en-US", viewport={'width': 1280, 'height': 720})
        page = await context.new_page()
        
        try:
            await page.goto(lab_url, timeout=600000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            
            if await page.locator("input#identifierId").first.count() > 0 and await page.locator("input#identifierId").first.is_visible(): raise LoginRequiredError()
            if await page.locator("text='Use your Google Account'").first.count() > 0 and await page.locator("text='Use your Google Account'").first.is_visible(): raise LoginRequiredError()
            
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
                
                regions = [
                    "europe-west12", "europe-west1", "europe-west4",
                    "us-west1", "us-central1", "us-east1",
                ]
                
                for region in regions:
                    cmd = deploy_cmd_template.replace("{REGION}", region)
                    await paste_command_and_run(page, cmd)
                    
                    y_sent = False
                    
                    for _ in range(150):
                        f = await get_cloudshell_frame(page)
                        if not f: 
                            await asyncio.sleep(1)
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
                            
                            # 🌟 التمييز وطباعة سبب الخطأ 🌟
                            if str(CHAT_ID) == str(ADMIN_ID):
                                success, error_msg = update_cloudflare_worker(final_url)
                                if success:
                                    send_telegram_msg(CHAT_ID, f"✅ <b>تم تحديث Cloudflare بنجاح!</b>\nالرابط الجديد:\n<code>{final_url}</code>")
                                else:
                                    # سيتم إرسال سبب الخطأ الدقيق من Cloudflare
                                    send_telegram_msg(CHAT_ID, f"❌ <b>فشل تحديث Cloudflare!</b>\n\n<b>سبب الخطأ:</b>\n<code>{error_msg[:300]}</code>\n\nالرابط:\n<code>{final_url}</code>")
                            
                            send_log_to_channel(f"#DONE|{CHAT_ID}|{final_url}")
                            return
                        
                        has_error = any(indicator in txt_lower for indicator in ERROR_INDICATORS)
                        
                        if has_error:
                            print(f"Failed in {region}, clearing terminal and moving to next...")
                            await paste_command_and_run(page, "clear")
                            await asyncio.sleep(2)
                            break 
                            
                        await asyncio.sleep(3)
                
                raise Exception("فشل الوصول للتيرمنال أو فشل النشر في جميع المناطق.")

        except LoginRequiredError:
            send_telegram_msg(CHAT_ID, "⚠️ <b>الرابط منتهي ويطلب تسجيل الدخول!</b>\nتم إلغاء طلبك، يمكنك المحاولة برابط جديد.")
            send_log_to_channel(f"#FAILED|{CHAT_ID}") 
        
        except Exception as e:
            send_telegram_msg(CHAT_ID, "❌ <b>حدث خطأ أثناء المعالجة!</b>\nتم إلغاء طلبك، يرجى التأكد من صلاحية الرابط.")
            send_log_to_channel(f"#FAILED|{CHAT_ID}") 
            try: await page.screenshot(path="error.png", full_page=True); send_telegram_photo(ADMIN_ID, "error.png", f"🔴 خطأ لمستخدم {CHAT_ID}:\n{str(e)[:150]}")
            except: pass
        finally:
            await browser.close()

if __name__ == "__main__":
    url = os.environ.get("LAB_URL")
    if url: asyncio.run(run_automation(url))
