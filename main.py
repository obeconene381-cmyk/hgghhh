import asyncio
import re
import os
import requests
from playwright.async_api import async_playwright

# --- الإعدادات (تأكد من ضبطها في السيرفر) ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID") # الأيدي الخاص بك لتفعيل التحديث التلقائي
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
LOG_BOT_TOKEN = os.environ.get("LOG_BOT_TOKEN") 
LOG_CHANNEL_ID = "-1003781090454" 

# قائمة مؤشرات الخطأ
ERROR_INDICATORS = [
    "error:", "invalid value", "permission_denied", "quota exceeded",
    "quota limit", "unavailable", "failed to create service",
    "organization policy", "deployment failed", "failed_precondition"
]

# --- دالة تحديث Cloudflare (نظام الربط الأبدي) ---
def update_cloudflare_worker(new_gcp_url):
    CF_ACCOUNT_ID = "e66f9daaf04a57789345976693dfaa94"
    CF_API_TOKEN = "cfat_g0yDmvVp1nZZQ6DeARFRg4jWtIZxPANp0usU1y3Zf0c563cd"
    WORKER_NAME = "wild-limit-6d0c"

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/workers/scripts/{WORKER_NAME}"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/javascript"
    }

    # كود الوركر: يستقبل /omarero-2026 ويحوله إلى / ليتوافق مع الـ Image
    worker_script = f"""
    export default {{
      async fetch(request) {{
        const url = new URL(request.url);
        
        // 1. الحماية بكلمة السر (المسار)
        if (url.pathname !== "/omarero-2026") {{
          return new Response("Unauthorized Access", {{ status: 403 }});
        }}
        
        // 2. تحديد الوجهة الجديدة (GCP)
        const TARGET = "{new_gcp_url.replace('https://', '').replace('http://', '')}";
        url.hostname = TARGET;

        // 3. تحويل المسار ليتوافق مع nkka404 (إرجاعه للمسار الرئيسي)
        url.pathname = "/"; 

        const newRequest = new Request(url, request);
        newRequest.headers.set('Host', TARGET);
        
        return fetch(newRequest);
      }}
    }}
    """
    try:
        res = requests.put(url, headers=headers, data=worker_script)
        return res.status_code == 200
    except Exception as e:
        print(f"Cloudflare Update Error: {e}")
        return False

# --- دوال إرسال التنبيهات ---
def send_telegram_msg(chat_id, text):
    if BOT_TOKEN and chat_id:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def send_log_to_channel(text):
    if LOG_BOT_TOKEN and LOG_CHANNEL_ID:
        requests.post(f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": LOG_CHANNEL_ID, "text": text})

# --- دوال مساعدة للأتمتة (Playwright) ---
async def click_button(page, text):
    for target in [page] + list(page.frames):
        try:
            btn = target.get_by_role("button", name=re.compile(rf"^\s*{text}\s*$", re.I))
            if await btn.is_visible():
                await btn.click(force=True)
                return True
        except: pass
    return False

async def get_cloudshell_frame(page):
    for _ in range(30):
        for f in page.frames:
            if "shell.cloud.google.com" in (f.url or "").lower(): return f
        await asyncio.sleep(2)
    return None

# --- المحرك الرئيسي للأتمتة ---
async def run_automation(lab_url):
    send_telegram_msg(CHAT_ID, "⏳ <b>بدأت العملية...</b>\nجاري إنشاء السيرفر وتحديث Cloudflare.")
    
    deploy_cmd = (
        "gcloud run deploy my-app --image=docker.io/nkka404/vless-ws:latest "
        "--platform=managed --allow-unauthenticated --port=8080 --cpu=2 --memory=4Gi "
        "--concurrency=1000 --timeout=3600 --min-instances=2 --max-instances=8 "
        "--execution-environment=gen2 --cpu-boost --region={REGION}"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        page = await context.new_page()

        try:
            await page.goto(lab_url, timeout=60000)
            
            # التعامل مع أزرار الموافقة والشروط
            await click_button(page, "I understand")
            await asyncio.sleep(2)
            await click_button(page, "Agree and continue")
            
            # تشغيل Cloud Shell
            await page.locator('button[aria-label*="Activate Cloud Shell"]').first.click()
            await click_button(page, "Continue")
            await click_button(page, "Authorize")

            # انتظار ظهور التيرمنال
            f = await get_cloudshell_frame(page)
            if not f: raise Exception("Cloud Shell لم يفتح!")

            url_re = re.compile(r"Service URL:\s*(https://[a-zA-Z0-9.-]+\.run\.app)", re.I)
            regions = ["europe-west1", "europe-west4", "us-central1"]

            for region in regions:
                cmd = deploy_cmd.replace("{REGION}", region)
                # إرسال الأمر للتيرمنال
                ta = f.locator("textarea.xterm-helper-textarea").first
                await ta.focus()
                await page.keyboard.insert_text(cmd)
                await page.keyboard.press("Enter")

                y_sent = False
                for _ in range(150): # انتظار النشر
                    txt = await f.inner_text("body")
                    
                    if not y_sent and ("[y/n]" in txt.lower() or "continue?" in txt.lower()):
                        await page.keyboard.type("y"); await page.keyboard.press("Enter")
                        y_sent = True

                    match = url_re.search(txt)
                    if match:
                        final_url = match.group(1)
                        
                        # 🌟 الجزء الخاص بك (عمريرو) 🌟
                        if str(CHAT_ID) == str(ADMIN_ID):
                            success = update_cloudflare_worker(final_url)
                            status_msg = "✅ <b>تم تحديث Cloudflare بنجاح!</b>" if success else "❌ <b>فشل تحديث Cloudflare!</b>"
                            send_telegram_msg(CHAT_ID, f"{status_msg}\nرابط GCP الجديد:\n<code>{final_url}</code>")
                        
                        send_log_to_channel(f"#DONE|{CHAT_ID}|{final_url}")
                        return

                    if any(err in txt.lower() for err in ERROR_INDICATORS):
                        await page.keyboard.insert_text("clear"); await page.keyboard.press("Enter")
                        break
                    await asyncio.sleep(3)

        except Exception as e:
            send_telegram_msg(CHAT_ID, f"❌ حدث خطأ: {str(e)[:100]}")
            send_log_to_channel(f"#FAILED|{CHAT_ID}")
        finally:
            await browser.close()

if __name__ == "__main__":
    url = os.environ.get("LAB_URL")
    if url: asyncio.run(run_automation(url))
