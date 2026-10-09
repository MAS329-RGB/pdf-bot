import os
import sys
import json
import asyncio
import logging
import random
import urllib.parse
import xml.etree.ElementTree as ET
import aiohttp
from deep_translator import GoogleTranslator
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

# المتغيرات الأساسية
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")
ADMIN_ID = 642550263  # آيدي حسابك للتحكم والإحصائيات

STATE_FILE = "state.json"
MAX_FILE_SIZE_MB = 45  # حد الأمان لحجم الملفات
DEFAULT_BATCH = 10     # عدد الملفات في كل دفعة

def load_state():
    default_state = {
        "total_posts": 0,
        "arabic_posts": 0,
        "english_posts": 0,
        "promo_posts": 0,
        "is_paused": False,
        "posted_ids": [],
        "last_command_msg_id": 0
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_state.update(data)
                return default_state
        except Exception:
            pass
    return default_state

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def translate_to_arabic(text: str) -> str:
    """ترجمة النصوص الإنجليزية بدقة إلى العربية"""
    if not text:
        return ""
    try:
        translated = GoogleTranslator(source='en', target='ar').translate(text[:1500])
        return translated if translated else text
    except Exception as e:
        logger.warning(f"فشل المترجم: {e}")
        return text

# ----------------- جلب المصادر العربية (80%) -----------------
ARABIC_KEYWORDS = [
    "تاريخ", "رسالة ماجستير", "بحوث جامعية", "تقرير جامعي", 
    "أطروحة دكتوراه", "حضارة وتراث", "فلسفة وفكر", "دراسات تاريخية", "مصادر ومراجع"
]

async def fetch_arabic_source(session, posted_ids):
    keyword = random.choice(ARABIC_KEYWORDS)
    query = f"language:(arabic OR ara) AND mediatype:(texts) AND ({keyword})"
    url = f"https://archive.org/advancedsearch.php?q={urllib.parse.quote(query)}&fl[]=identifier,title,creator,description,year&sort[]=publicdate desc&rows=30&output=json"
    
    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            docs = data.get("response", {}).get("docs", [])
            random.shuffle(docs)

            for doc in docs:
                item_id = doc.get("identifier")
                if item_id in posted_ids:
                    continue

                meta_url = f"https://archive.org/metadata/{item_id}/files"
                async with session.get(meta_url, timeout=20) as meta_resp:
                    if meta_resp.status != 200:
                        continue
                    meta_data = await meta_resp.json()
                    files = meta_data.get("result", [])
                    
                    pdf_file = None
                    for f in files:
                        fname = f.get("name", "")
                        fsize = int(f.get("size", 0))
                        if fname.lower().endswith(".pdf") and (100000 < fsize <= MAX_FILE_SIZE_MB * 1024 * 1024):
                            pdf_file = fname
                            break

                    if pdf_file:
                        pdf_url = f"https://archive.org/download/{item_id}/{urllib.parse.quote(pdf_file)}"
                        title = doc.get("title", "كتاب وبحث أكاديمي")
                        author = doc.get("creator", "غير مسجل / نخبة من الباحثين")
                        desc = doc.get("description", "دراسة وبحث تخصصي يتناول موضوعات تاريخية وأكاديمية موثقة.")
                        if isinstance(desc, list):
                            desc = " ".join(desc)

                        return {
                            "id": item_id,
                            "title": title[:110],
                            "authors": author if isinstance(author, str) else ", ".join(author[:2]),
                            "category": f"دراسات عربية وأكاديمية ({keyword})",
                            "summary": desc[:380].strip() + "...",
                            "pdf_url": pdf_url,
                            "is_arabic": True
                        }
    except Exception as e:
        logger.error(f"خطأ أثناء جلب المصدر العربي: {e}")
    return None

# ----------------- جلب المصادر الإنجليزية وترجمتها (20%) -----------------
async def fetch_english_source(session, posted_ids):
    categories = ["cs.AI", "cs.LG", "stat.ML", "math.ST", "physics.soc-ph"]
    cat = random.choice(categories)
    url = f"https://export.arxiv.org/api/query?search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending&max_results=20"
    
    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                return None
            xml_text = await resp.text()

        root = ET.fromstring(xml_text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)

        for entry in entries:
            raw_id = entry.find('atom:id', ns).text.split('/')[-1]
            if raw_id in posted_ids:
                continue

            en_title = entry.find('atom:title', ns).text.strip().replace("\n", " ")
            en_summary = entry.find('atom:summary', ns).text.strip().replace("\n", " ")
            authors = [a.find('atom:name', ns).text.strip() for a in entry.findall('atom:author', ns)]

            # ترجمة العنوان والملخص بالكامل إلى العربية
            ar_title = translate_to_arabic(en_title)
            ar_summary = translate_to_arabic(en_summary)

            pdf_url = f"https://arxiv.org/pdf/{raw_id}.pdf"
            return {
                "id": raw_id,
                "title": ar_title[:110],
                "authors": ", ".join(authors[:2]) + (" وآخرون" if len(authors) > 2 else ""),
                "category": "أبحاث علمية عالمية (مترجمة)",
                "summary": ar_summary[:380].strip() + "...",
                "pdf_url": pdf_url,
                "is_arabic": False
            }
    except Exception as e:
        logger.error(f"خطأ أثناء جلب البحث الإنجليزي: {e}")
    return None

async def download_file(session, url, file_path):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers, timeout=60) as resp:
            if resp.status == 200:
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = await resp.content.read(1024 * 64)
                        if not chunk:
                            break
                        f.write(chunk)
                return True
    except Exception as e:
        logger.error(f"فشل التحميل: {e}")
    return False

# ----------------- معالجة رسائل المطور والإحصائيات -----------------
async def handle_admin_commands(app: Client, state: dict):
    """فحص الأوامر المرسلة من حساب الأدمن في الخاص"""
    try:
        async for msg in app.get_chat_history(ADMIN_ID, limit=5):
            if msg.id <= state.get("last_command_msg_id", 0):
                break
            
            text = (msg.text or "").strip().lower()
            if text == "/pause":
                state["is_paused"] = True
                await msg.reply_text("⏸ تم إيقاف النشر التلقائي مؤقتاً بنجاح.")
            elif text == "/resume":
                state["is_paused"] = False
                await msg.reply_text("▶️ تم استئناف النشر التلقائي بنجاح.")
            elif text == "/promo":
                # إرسال الإعلان يدوياً فوراً
                promo_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("الانضمام إلى قناة الأدوات 🚀", url="https://t.me/AKADEME_GG")]
                ])
                promo_text = (
                    "🚀 **منظومة أدوات أكاديميا | Academia بين يديك!**\n\n"
                    "لا تكتفِ بالمكتبة فقط، احصل على تجربة أكاديمية كاملة مع أدواتنا الذكية:\n"
                    "🔍 **مُعِين** — للبحث في البحوث والمصادر.\n"
                    "📄 **غِلاف** — لإنشاء غلاف تقريرك الجامعي بثوانٍ والكثير أيضاً.\n\n"
                    "تابع كل جديد عبر القناة المخصصة للأدوات:"
                )
                await app.send_message(CHANNEL_USERNAME, promo_text, reply_markup=promo_markup, parse_mode=ParseMode.MARKDOWN)
                await msg.reply_text("✅ تم إرسال إعلان منظومة أكاديميا إلى القناة الآن!")
            elif text == "/stats":
                pass # سيتم إرسال بطاقة الإحصائيات في النهاية
                
            if msg.id > state.get("last_command_msg_id", 0):
                state["last_command_msg_id"] = msg.id
    except Exception as e:
        logger.info(f"ملاحظة عند فحص رسائل الأدمن: {e}")

async def send_admin_dashboard(app: Client, state: dict, sent_this_round: int):
    """إرسال لوحة الإحصائيات إلى حساب الأدمن"""
    total = state["total_posts"]
    ar_count = state["arabic_posts"]
    en_count = state["english_posts"]
    promo_count = state["promo_posts"]
    next_promo = 30 - (total % 30) if (total % 30) != 0 else 30
    status_text = "⏸ متوقف مؤقتاً" if state["is_paused"] else "🟢 نشط ويعمل"

    dashboard = (
        "📊 **لوحة تحكم وإحصائيات منظومة أكاديميا**\n"
        "────────────────────\n"
        f"⚙️ **حالة النظام:** {status_text}\n"
        f"📤 **الملفات المرسلة في هذه الجولة:** `{sent_this_round}` ملف\n"
        f"📚 **إجمالي المنشورات الكلي:** `{total}` منشور\n"
        "────────────────────\n"
        f"📖 **الكتب والمصادر العربية:** `{ar_count}`\n"
        f"🌐 **الأبحاث العالمية المترجمة:** `{en_count}`\n"
        f"📢 **إعلانات الأدوات المرسلة:** `{promo_count}`\n"
        f"⏳ **المتبقي على الإعلان القادم:** `{next_promo}` منشور\n"
        "────────────────────\n"
        "💡 **أوامر التحكم السريعة (أرسلها هنا في الخاص):**\n"
        "• `/pause` للإيقاف المؤقت\n"
        "• `/resume` لاستئناف النشر\n"
        "• `/promo` لإرسال إعلان الأدوات فوراً\n"
        "• `/stats` لتحديث هذه الإحصائيات"
    )
    try:
        await app.send_message(chat_id=ADMIN_ID, text=dashboard, parse_mode=ParseMode.MARKDOWN)
        logger.info("تم إرسال بطاقة الإحصائيات لحساب الأدمن بنجاح.")
    except Exception as e:
        logger.error(f"تعذر إرسال الإحصائيات للأدمن (تأكد من الضغط على Start للبوت أولاً): {e}")

# ----------------- الدالة الرئيسية -----------------
async def main():
    state = load_state()
    total_posts = state["total_posts"]
    posted_ids = set(state["posted_ids"])

    app = Client(
        name="academia_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )
    await app.start()

    # فحص أوامر الأدمن أولاً
    await handle_admin_commands(app, state)

    sent_in_this_run = 0

    # إذا لم يكن النظام متوقفاً بواسطة أمر /pause
    if not state.get("is_paused", False):
        promo_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("الانضمام إلى قناة الأدوات 🚀", url="https://t.me/AKADEME_GG")]
        ])
        promo_text = (
            "🚀 **منظومة أدوات أكاديميا | Academia بين يديك!**\n\n"
            "لا تكتفِ بالمكتبة فقط، احصل على تجربة أكاديمية كاملة مع أدواتنا الذكية:\n"
            "🔍 **مُعِين** — للبحث في البحوث والمصادر.\n"
            "📄 **غِلاف** — لإنشاء غلاف تقريرك الجامعي بثوانٍ والكثير أيضاً.\n\n"
            "تابع كل جديد عبر القناة المخصصة للأدوات:"
        )

        async with aiohttp.ClientSession() as session:
            for _ in range(DEFAULT_BATCH):
                # نسبة 80% عربي و 20% إنجليزي
                is_arabic = random.random() < 0.8
                paper = None
                if is_arabic:
                    paper = await fetch_arabic_source(session, posted_ids)
                if not paper:
                    paper = await fetch_english_source(session, posted_ids)

                if not paper:
                    continue

                temp_file = f"temp_{sent_in_this_run}.pdf"
                if await download_file(session, paper["pdf_url"], temp_file):
                    total_posts += 1
                    
                    # الكابشن بدون أي هاشتاقات
                    caption = (
                        f"📚 **المنشور #{total_posts}**\n\n"
                        f"📖 **العنوان:** {paper['title']}\n"
                        f"✍️ **المؤلف / الباحث:** {paper['authors']}\n"
                        f"🏷 **التصنيف:** {paper['category']}\n\n"
                        f"📝 **الملخص بالعربية:**\n{paper['summary']}"
                    )

                    try:
                        await app.send_document(
                            chat_id=CHANNEL_USERNAME,
                            document=temp_file,
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            file_name=f"{paper['title'][:35].strip()}.pdf"
                        )
                        posted_ids.add(paper["id"])
                        sent_in_this_run += 1
                        
                        if paper.get("is_arabic"):
                            state["arabic_posts"] = state.get("arabic_posts", 0) + 1
                        else:
                            state["english_posts"] = state.get("english_posts", 0) + 1

                        # التحقق من شرط الإعلان كل 30 منشور
                        if total_posts % 30 == 0:
                            await asyncio.sleep(3)
                            await app.send_message(
                                chat_id=CHANNEL_USERNAME,
                                text=promo_text,
                                reply_markup=promo_markup,
                                parse_mode=ParseMode.MARKDOWN
                            )
                            state["promo_posts"] = state.get("promo_posts", 0) + 1

                    except Exception as err:
                        logger.error(f"خطأ أثناء الإرسال: {err}")
                    finally:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)

                    await asyncio.sleep(4)

    state["total_posts"] = total_posts
    state["posted_ids"] = list(posted_ids)[-1000:]

    # إرسال لوحة الإحصائيات الكاملة إلى حساب الأدمن في الخاص
    await send_admin_dashboard(app, state, sent_in_this_run)

    await app.stop()
    save_state(state)

if __name__ == "__main__":
    asyncio.run(main())
