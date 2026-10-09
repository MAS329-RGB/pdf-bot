import os
import sys
import json
import asyncio
import logging
import random
import re
import urllib.parse
import xml.etree.ElementTree as ET
import aiohttp
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

# المتغيرات الأساسية
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")
ADMIN_ID = 642550263  # آيدي حسابك

STATE_FILE = "state.json"
MAX_FILE_SIZE_MB = 48

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
    if len(state["posted_ids"]) > 25000:
        state["posted_ids"] = state["posted_ids"][-25000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_arabic_text(text: str) -> bool:
    """التحقق الصارم من وجود أحرف عربية"""
    if not text:
        return False
    arabic_chars = re.findall(r'[\u0600-\u06FF]', text)
    return len(arabic_chars) > 6

async def translate_to_arabic(session: aiohttp.ClientSession, text: str) -> str:
    """محرك ترجمة سحابي مباشر عالي السرعة"""
    if not text:
        return ""
    clean_text = text.strip().replace("\n", " ")[:1200]

    # المحرك الأساسي: Google API السريع
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ar&dt=t&q={urllib.parse.quote(clean_text)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with session.get(url, headers=headers, timeout=12) as resp:
            if resp.status == 200:
                data = await resp.json()
                translated = "".join([part[0] for part in data[0] if part and part[0]])
                if is_arabic_text(translated):
                    return translated.strip()
    except Exception as e:
        logger.warning(f"تخطي محرك Google: {e}")

    # المحرك الاحتياطي: MyMemory API
    try:
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(clean_text[:450])}&langpair=en|ar"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                translated = data.get("responseData", {}).get("translatedText", "")
                if is_arabic_text(translated):
                    return translated.strip()
    except Exception as e:
        logger.warning(f"تخطي محرك MyMemory: {e}")

    return ""

ARABIC_SECTORS = [
    "تاريخ الأندلس والحروب", "تاريخ الدولة العباسية والأموية", "المخطوطات العربية النادرة",
    "رسائل ماجستير في التاريخ", "أطروحات دكتوراه في العلوم الإنسانية", "بحوث علم النفس والتربية",
    "أطروحات القانون المقارن", "كتب اللغة العربية والبلاغة", "الفلسفة والمنطق والعلوم القديمة",
    "دراسات جغرافية وحضارية", "تقارير وبحوث هندسية وتقنية", "رسائل الاقتصاد والتجارة",
    "أصول الفقه والشريعة", "بحوث الذكاء الاصطناعي والحوسبة", "المراجع والكتب الطبية العربية"
]

async def fetch_arabic_source(session, posted_ids):
    sector = random.choice(ARABIC_SECTORS)
    random_page = random.randint(1, 45)
    query = f"language:(arabic OR ara) AND mediatype:(texts) AND ({sector})"
    url = f"https://archive.org/advancedsearch.php?q={urllib.parse.quote(query)}&fl[]=identifier,title,creator,description,year&sort[]=publicdate desc&rows=25&page={random_page}&output=json"

    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            docs = data.get("response", {}).get("docs", [])
            random.shuffle(docs)

            for doc in docs:
                item_id = doc.get("identifier")
                if not item_id or item_id in posted_ids:
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
                        if fname.lower().endswith(".pdf") and (150000 < fsize <= MAX_FILE_SIZE_MB * 1024 * 1024):
                            pdf_file = fname
                            break

                    if pdf_file:
                        pdf_url = f"https://archive.org/download/{item_id}/{urllib.parse.quote(pdf_file)}"
                        title = doc.get("title", "دراسة وبحث أكاديمي مرجعي")
                        author = doc.get("creator", "باحثون وأكاديميون متخصصون")
                        desc = doc.get("description", "دراسة علمية محكمة وبحث أكاديمي يتناول مواضيع تفصيلية ومراجع موثقة.")
                        if isinstance(desc, list):
                            desc = " ".join(desc)

                        return {
                            "id": item_id,
                            "title": title[:110].strip(),
                            "authors": author if isinstance(author, str) else ", ".join(author[:2]),
                            "category": f"كتب وبحوث عربية ({sector.split()[0]})",
                            "summary": desc[:350].strip() + "...",
                            "pdf_url": pdf_url,
                            "is_arabic": True
                        }
    except Exception as e:
        logger.error(f"خطأ في الأرشيف: {e}")
    return None

async def fetch_english_source(session, posted_ids):
    categories = ["cs.AI", "cs.LG", "stat.ML", "math.ST", "physics.soc-ph"]
    cat = random.choice(categories)
    random_offset = random.randint(0, 300)
    url = f"https://export.arxiv.org/api/query?search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending&start={random_offset}&max_results=15"

    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                return None
            xml_text = await resp.text()

        root = ET.fromstring(xml_text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        random.shuffle(entries)

        for entry in entries:
            raw_id = entry.find('atom:id', ns).text.split('/')[-1]
            if raw_id in posted_ids:
                continue

            en_title = entry.find('atom:title', ns).text.strip().replace("\n", " ")
            en_summary = entry.find('atom:summary', ns).text.strip().replace("\n", " ")
            authors = [a.find('atom:name', ns).text.strip() for a in entry.findall('atom:author', ns)]

            ar_title = await translate_to_arabic(session, en_title)
            ar_summary = await translate_to_arabic(session, en_summary)

            # التحقق: إذا لم تكن الترجمة عربية تماماً يرفض الملف
            if not is_arabic_text(ar_title) or not is_arabic_text(ar_summary):
                continue

            return {
                "id": raw_id,
                "title": ar_title[:110].strip(),
                "authors": ", ".join(authors[:2]) + (" وآخرون" if len(authors) > 2 else ""),
                "category": "أبحاث علمية عالمية (مترجمة)",
                "summary": ar_summary[:350].strip() + "...",
                "pdf_url": f"https://arxiv.org/pdf/{raw_id}.pdf",
                "is_arabic": False
            }
    except Exception as e:
        logger.error(f"خطأ في arXiv: {e}")
    return None

async def download_file(session, url, file_path):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers, timeout=90) as resp:
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

async def handle_admin_commands(app: Client, state: dict):
    try:
        async for msg in app.get_chat_history(ADMIN_ID, limit=5):
            if msg.id <= state.get("last_command_msg_id", 0):
                break
            text = (msg.text or "").strip().lower()
            if text == "/pause":
                state["is_paused"] = True
                await msg.reply_text("⏸ تم إيقاف النشر التلقائي مؤقتاً.")
            elif text == "/resume":
                state["is_paused"] = False
                await msg.reply_text("▶️ تم استئناف النشر التلقائي بنجاح.")
            elif text == "/reset":
                state["total_posts"] = 0
                state["arabic_posts"] = 0
                state["english_posts"] = 0
                state["promo_posts"] = 0
                state["posted_ids"] = []
                await msg.reply_text("🔄 تم تصفير العداد وسجل المنشورات بنجاح! المنشور القادم سيكون #1.")
            elif text == "/promo":
                promo_markup = InlineKeyboardMarkup([[InlineKeyboardButton("الانضمام إلى قناة الأدوات 🚀", url="https://t.me/AKADEME_GG")]])
                promo_text = (
                    "🚀 **منظومة أدوات أكاديميا | Academia بين يديك!**\n\n"
                    "لا تكتفِ بالمكتبة فقط، احصل على تجربة أكاديمية كاملة مع أدواتنا الذكية:\n"
                    "🔍 **مُعِين** — للبحث في البحوث والمصادر.\n"
                    "📄 **غِلاف** — لإنشاء غلاف تقريرك الجامعي بثوانٍ والكثير أيضاً.\n\n"
                    "تابع كل جديد عبر القناة المخصصة للأدوات:"
                )
                await app.send_message(CHANNEL_USERNAME, promo_text, reply_markup=promo_markup, parse_mode=ParseMode.MARKDOWN)
                await msg.reply_text("✅ تم إرسال إعلان الأدوات فوراً!")
            if msg.id > state.get("last_command_msg_id", 0):
                state["last_command_msg_id"] = msg.id
    except Exception:
        pass

async def send_admin_report(app: Client, state: dict, sent_this_round: int):
    total = state["total_posts"]
    next_promo = 30 - (total % 30) if (total % 30) != 0 else 30
    status_str = "⏸ متوقف" if state["is_paused"] else "⚡ نشط بأقصى سرعة"
    msg = (
        "📊 **تقرير الأداء الأكاديمي**\n"
        "────────────────────\n"
        f"⚙️ **الحالة:** {status_str}\n"
        f"🚀 **نُشر في هذه الدورة:** `{sent_this_round}` ملف\n"
        f"📈 **إجمالي المنشورات الكلي:** `{total}` ملف\n"
        "────────────────────\n"
        f"📚 **كتب ومصادر عربية (80%):** `{state['arabic_posts']}`\n"
        f"🌐 **أبحاث مترجمة (20%):** `{state['english_posts']}`\n"
        f"📢 **الإعلانات المنشورة:** `{state['promo_posts']}`\n"
        f"⏳ **المتبقي على الإعلان القادم:** `{next_promo}` منشور\n"
        "────────────────────\n"
        "التحكم: `/reset` لتصفير العداد | `/pause` للإيقاف | `/resume` للاستئناف | `/promo` للإعلان"
    )
    try:
        await app.send_message(ADMIN_ID, msg, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

async def main():
    state = load_state()
    total_posts = state["total_posts"]
    posted_ids = set(state["posted_ids"])

    app = Client(name="academia_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
    await app.start()

    await handle_admin_commands(app, state)
    sent_in_this_run = 0

    if not state.get("is_paused", False):
        promo_markup = InlineKeyboardMarkup([[InlineKeyboardButton("الانضمام إلى قناة الأدوات 🚀", url="https://t.me/AKADEME_GG")]])
        promo_text = (
            "🚀 **منظومة أدوات أكاديميا | Academia بين يديك!**\n\n"
            "لا تكتفِ بالمكتبة فقط، احصل على تجربة أكاديمية كاملة مع أدواتنا الذكية:\n"
            "🔍 **مُعِين** — للبحث في البحوث والمصادر.\n"
            "📄 **غِلاف** — لإنشاء غلاف تقريرك الجامعي بثوانٍ والكثير أيضاً.\n\n"
            "تابع كل جديد عبر القناة المخصصة للأدوات:"
        )

        # جدول الحصص الصارم بنسبة 80% عربي و 20% إنجليزي في كل دورة
        # 4 عربي ثم 1 إنجليزي | 4 عربي ثم 1 إنجليزي = 8 عربي (80%) و 2 إنجليزي (20%)
        quota_plan = [
            "arabic", "arabic", "arabic", "arabic", "english",
            "arabic", "arabic", "arabic", "arabic", "english"
        ]

        async with aiohttp.ClientSession() as session:
            for task_type in quota_plan:
                paper = None
                
                if task_type == "arabic":
                    paper = await fetch_arabic_source(session, posted_ids)
                else:
                    paper = await fetch_english_source(session, posted_ids)
                    # احتياط: إن تعذر الإنجليزي أو فشلت ترجمته يستبدل بعربي
                    if not paper:
                        paper = await fetch_arabic_source(session, posted_ids)

                if not paper:
                    continue

                temp_file = f"temp_{sent_in_this_run}.pdf"
                if await download_file(session, paper["pdf_url"], temp_file):
                    total_posts += 1
                    caption = (
                        f"📚 **المنشور #{total_posts}**\n\n"
                        f"📖 **العنوان:** {paper['title']}\n"
                        f"✍️ **المؤلف / الباحث:** {paper['authors']}\n"
                        f"🏷 **التصنيف:** {paper['category']}\n\n"
                        f"📝 **الملخص بالعربية:**\n{paper['summary']}"
                    )

                    success = False
                    while not success:
                        try:
                            await app.send_document(
                                chat_id=CHANNEL_USERNAME,
                                document=temp_file,
                                caption=caption,
                                parse_mode=ParseMode.MARKDOWN,
                                file_name=f"{paper['title'][:35].strip()}.pdf"
                            )
                            success = True
                            posted_ids.add(paper["id"])
                            sent_in_this_run += 1

                            if paper.get("is_arabic"):
                                state["arabic_posts"] = state.get("arabic_posts", 0) + 1
                            else:
                                state["english_posts"] = state.get("english_posts", 0) + 1

                            # إرسال إعلان الأدوات كل 30 منشور
                            if total_posts % 30 == 0:
                                await asyncio.sleep(3)
                                await app.send_message(CHANNEL_USERNAME, promo_text, reply_markup=promo_markup, parse_mode=ParseMode.MARKDOWN)
                                state["promo_posts"] = state.get("promo_posts", 0) + 1

                        except FloodWait as e:
                            logger.warning(f"انتظار FloodWait: {e.value} ثانية...")
                            await asyncio.sleep(e.value + 2)
                        except Exception as e:
                            logger.error(f"خطأ الإرسال: {e}")
                            break
                        finally:
                            if os.path.exists(temp_file):
                                os.remove(temp_file)

                    await asyncio.sleep(25)

    state["total_posts"] = total_posts
    state["posted_ids"] = list(posted_ids)

    await send_admin_report(app, state, sent_in_this_run)
    await app.stop()
    save_state(state)

if __name__ == "__main__":
    asyncio.run(main())
