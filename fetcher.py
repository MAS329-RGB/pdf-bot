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
from pypdf import PdfReader
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

# بيانات الدخول
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")
ADMIN_ID = 642550263

STATE_FILE = "state.json"
MAX_FILE_SIZE_MB = 48
# الحد الأقصى للملفات في الجولة الواحدة لتفادي حدود وقت جيت هاب (50 ملفاً = قرابة 22 دقيقة)
BATCH_LIMIT_PER_RUN = 50

def load_state():
    default_state = {
        "total_posts": 0,
        "arabic_posts": 0,
        "english_posts": 0,
        "promo_posts": 0,
        "manual_posts": 0,
        "is_paused": False,
        "posted_ids": [],
        "processed_file_ids": [],
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
    # حفظ سجل 30 ألف معرف لمنع أي تكرار مستقبلي
    if len(state["posted_ids"]) > 30000:
        state["posted_ids"] = state["posted_ids"][-30000:]
    if len(state["processed_file_ids"]) > 30000:
        state["processed_file_ids"] = state["processed_file_ids"][-30000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_arabic_text(text: str) -> bool:
    if not text:
        return False
    return len(re.findall(r'[\u0600-\u06FF]', text)) > 6

async def translate_to_arabic(session: aiohttp.ClientSession, text: str) -> str:
    if not text:
        return ""
    clean_text = text.strip().replace("\n", " ")[:1200]
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ar&dt=t&q={urllib.parse.quote(clean_text)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with session.get(url, headers=headers, timeout=12) as resp:
            if resp.status == 200:
                data = await resp.json()
                translated = "".join([part[0] for part in data[0] if part and part[0]])
                if is_arabic_text(translated):
                    return translated.strip()
    except Exception:
        pass
    return ""

def auto_detect_category(text: str) -> str:
    """استنتاج التصنيف الأكاديمي تلقائياً من العنوان والنص"""
    t = text.lower()
    if any(k in t for k in ["تاريخ", "أندلس", "حضارة", "عصر", "معركة", "دولة", "تراث"]):
        return "دراسات تاريخية وحضارية"
    if any(k in t for k in ["ماجستير", "دكتوراه", "أطروحة", "رسالة", "جامعة"]):
        return "رسائل ماجستير وأطروحات جامعية"
    if any(k in t for k in ["قانون", "دستور", "قضاء", "محكمة", "جنائي", "حقوق"]):
        return "دراسات قانونية وتشريعية"
    if any(k in t for k in ["طب", "صحة", "علاج", "دواء", "سريري", "مرض"]):
        return "علوم طبية وصحية"
    if any(k in t for k in ["حاسوب", "ذكاء", "برمجة", "بيانات", "خوارزمية", "شبكات", "ai", "machine learning"]):
        return "حوسبة وذكاء اصطناعي"
    if any(k in t for k in ["اقتصاد", "محاسبة", "مالية", "تجارة", "إدارة", "تسويق"]):
        return "إدارة وأعمال واقتصاد"
    if any(k in t for k in ["فقه", "شريعة", "قرآن", "تفسير", "حديث", "إسلام"]):
        return "دراسات إسلامية وشرعية"
    if any(k in t for k in ["لغة", "بلاغة", "نحو", "أدب", "شعر", "رواية"]):
        return "لغة عربية وآدابها"
    return "بحوث ومصادر جامعية تخصصية"

async def analyze_pdf_file(session: aiohttp.ClientSession, file_path: str, orig_name: str) -> dict:
    """تحليل محتوى ملف الـ PDF وقراءة نصوصه الداخلية"""
    clean_title = ""
    author = "غير مسجل / باحثون متخصصون"
    summary = ""
    
    try:
        reader = PdfReader(file_path)
        meta = reader.metadata or {}
        if meta.title and len(meta.title.strip()) > 4:
            clean_title = meta.title.strip()
        if meta.author and len(meta.author.strip()) > 3:
            author = meta.author.strip()

        # استخراج نصوص الصفحات الأولى
        extracted = ""
        for page in reader.pages[:3]:
            txt = page.extract_text()
            if txt:
                extracted += txt + " "

        extracted = re.sub(r'\s+', ' ', extracted).strip()
        if extracted:
            summary = extracted[:450].strip()
    except Exception as e:
        logger.warning(f"ملاحظة عند استخراج نص PDF: {e}")

    # إذا لم يتوفر عنوان بالـ Metadata، ننظف اسم الملف الأصلي
    if not clean_title:
        base = os.path.splitext(orig_name)[0]
        base = re.sub(r'[_\-]+', ' ', base)
        base = re.sub(r'\[.*?\]|\(.*?\)', '', base).strip()
        clean_title = base if len(base) > 3 else "بحث ومصدر أكاديمي مرجعي"

    # فحص اللغة: إن كان إنجليزياً، نترجمه للعربية
    if not is_arabic_text(clean_title):
        ar_title = await translate_to_arabic(session, clean_title)
        if is_arabic_text(ar_title):
            clean_title = ar_title

    if summary and not is_arabic_text(summary):
        ar_summary = await translate_to_arabic(session, summary)
        if is_arabic_text(ar_summary):
            summary = ar_summary
        else:
            summary = ""

    if not summary:
        summary = "دراسة أكاديمية وبحث مرجعي تخصصي يتناول مراجع موثقة وموضوعات مفصلة تهم الباحثين والدارسين."

    category = auto_detect_category(clean_title + " " + summary)

    return {
        "title": clean_title[:110].strip(),
        "authors": author[:60].strip(),
        "category": category,
        "summary": summary[:380].strip() + "..."
    }

# ----------------- فحص الملفات المحولة من الأدمن في الخاص -----------------
async def get_queued_admin_files(app: Client, processed_ids: set) -> list:
    """جلب كل الملفات المرسلة من الأدمن وفرزها من الأقدم للأحدث بالترتيب"""
    queued_messages = []
    logger.info("جاري فحص قائمة الملفات المحولة من الأدمن في الخاص...")

    try:
        # فحص آخر 500 رسالة في محادثة الأدمن
        async for msg in app.get_chat_history(ADMIN_ID, limit=500):
            if msg.document and msg.document.file_name:
                fname = msg.document.file_name.lower()
                mime = msg.document.mime_type or ""
                if fname.endswith(".pdf") or "pdf" in mime:
                    uid = msg.document.file_unique_id
                    if uid not in processed_ids:
                        if msg.document.file_size <= (MAX_FILE_SIZE_MB * 1024 * 1024):
                            queued_messages.append(msg)

        # ترتيب الرسائل تصاعدياً بحسب تاريخ الإرسال (الأقدم أولاً كما أرسلها الأدمن)
        queued_messages.sort(key=lambda m: m.id)
        logger.info(f"تم العثور على {len(queued_messages)} ملفاً في قائمة الانتظار.")
    except Exception as e:
        logger.error(f"خطأ أثناء قراءة محادثة الأدمن: {e}")

    return queued_messages

# ----------------- جلب الأرشيف (احتياطي عند فراغ القائمة) -----------------
ARABIC_KEYWORDS = ["تاريخ", "دراسات", "رسالة", "أطروحة", "فلسفة", "علوم", "مكتبة", "بحث", "أدب", "حضارة"]

async def fetch_archive_source(session, posted_ids):
    for _ in range(5):
        kw = random.choice(ARABIC_KEYWORDS)
        url = f"https://archive.org/advancedsearch.php?q={urllib.parse.quote(f'language:(arabic OR ara) AND mediatype:(texts) AND format:(pdf) AND ({kw})')}&fl[]=identifier,title,creator,description&sort[]=publicdate desc&rows=25&page={random.randint(1, 40)}&output=json"
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200: continue
                docs = (await resp.json()).get("response", {}).get("docs", [])
                random.shuffle(docs)
                for doc in docs:
                    iid = doc.get("identifier")
                    if not iid or iid in posted_ids: continue
                    meta_url = f"https://archive.org/metadata/{iid}/files"
                    async with session.get(meta_url, timeout=15) as mresp:
                        if mresp.status != 200: continue
                        for f in (await mresp.json()).get("result", []):
                            fn = f.get("name", "")
                            fz = int(f.get("size", 0))
                            if fn.lower().endswith(".pdf") and (150000 < fz <= MAX_FILE_SIZE_MB * 1024 * 1024):
                                return {
                                    "id": iid,
                                    "url": f"https://archive.org/download/{iid}/{urllib.parse.quote(fn)}",
                                    "title": doc.get("title", "كتاب وبحث أكاديمي مرجعي")[:100],
                                    "author": doc.get("creator", "باحثون وأكاديميون متخصصون"),
                                    "category": f"كتب وبحوث عربية ({kw})",
                                    "summary": (doc.get("description", "") or "دراسة علمية محكمة وبحث أكاديمي موثق.")[:350]
                                }
        except Exception:
            continue
    return None

# ----------------- الدالة الرئيسية -----------------
async def main():
    state = load_state()
    total_posts = state["total_posts"]
    posted_ids = set(state["posted_ids"])
    processed_file_ids = set(state.get("processed_file_ids", []))

    app = Client(name="academia_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
    await app.start()

    promo_markup = InlineKeyboardMarkup([[InlineKeyboardButton("الانضمام إلى قناة الأدوات 🚀", url="https://t.me/AKADEME_GG")]])
    promo_text = (
        "🚀 **منظومة أدوات أكاديميا | Academia بين يديك!**\n\n"
        "لا تكتفِ بالمكتبة فقط، احصل على تجربة أكاديمية كاملة مع أدواتنا الذكية:\n"
        "🔍 **مُعِين** — للبحث في البحوث والمصادر.\n"
        "📄 **غِلاف** — لإنشاء غلاف تقريرك الجامعي بثوانٍ والكثير أيضاً.\n\n"
        "تابع كل جديد عبر القناة المخصصة للأدوات:"
    )

    sent_in_this_run = 0
    queued_files = await get_queued_admin_files(app, processed_file_ids)
    remaining_in_queue = max(0, len(queued_files) - BATCH_LIMIT_PER_RUN)

    async with aiohttp.ClientSession() as session:
        # المسار الأول: إذا كان هناك ملفات محولة من الأدمن، يتم نشرها أولاً وبالترتيب
        if queued_files:
            logger.info(f"بدء نشر دفعة من ملفات الأدمن المحولة (سيتم نشر حتى {BATCH_LIMIT_PER_RUN} ملفاً بالترتيب)...")
            
            for msg in queued_files[:BATCH_LIMIT_PER_RUN]:
                file_uid = msg.document.file_unique_id
                orig_filename = msg.document.file_name or "academic_document.pdf"
                temp_pdf = f"admin_doc_{sent_in_this_run}.pdf"

                try:
                    # تحميل الملف من تليجرام
                    await app.download_media(msg, file_name=temp_pdf)
                    if not os.path.exists(temp_pdf):
                        continue

                    # تحليل الملف واستخراج البيانات
                    info = await analyze_pdf_file(session, temp_pdf, orig_filename)
                    total_posts += 1

                    caption = (
                        f"📚 **المنشور #{total_posts}**\n\n"
                        f"📖 **العنوان:** {info['title']}\n"
                        f"✍️ **المؤلف / الباحث:** {info['authors']}\n"
                        f"🏷 **التصنيف:** {info['category']}\n\n"
                        f"📝 **الملخص بالعربية:**\n{info['summary']}"
                    )

                    success = False
                    while not success:
                        try:
                            await app.send_document(
                                chat_id=CHANNEL_USERNAME,
                                document=temp_pdf,
                                caption=caption,
                                parse_mode=ParseMode.MARKDOWN,
                                file_name=f"{info['title'][:35].strip()}.pdf"
                            )
                            success = True
                            processed_file_ids.add(file_uid)
                            sent_in_this_run += 1
                            state["manual_posts"] = state.get("manual_posts", 0) + 1
                            logger.info(f"تم نشر ملف محول #{total_posts}: {info['title']}")

                            # فحص إعلان الأدوات كل 30 منشور
                            if total_posts % 30 == 0:
                                await asyncio.sleep(2)
                                await app.send_message(CHANNEL_USERNAME, promo_text, reply_markup=promo_markup, parse_mode=ParseMode.MARKDOWN)
                                state["promo_posts"] = state.get("promo_posts", 0) + 1

                        except FloodWait as e:
                            logger.warning(f"انتظار تيليجرام: {e.value} ثانية...")
                            await asyncio.sleep(e.value + 2)
                        except Exception as e:
                            logger.error(f"خطأ أثناء الإرسال: {e}")
                            break

                except Exception as e:
                    logger.error(f"خطأ في معالجة الملف المحول: {e}")
                finally:
                    if os.path.exists(temp_pdf):
                        os.remove(temp_pdf)

                # فاصل زمني آمن 20 ثانية بين كل ملف
                await asyncio.sleep(20)

        # المسار الثاني: إذا لم توجد أي ملفات محولة، يجلب من الأرشيف لضمان استمرار القناة
        else:
            logger.info("لا توجد ملفات محولة من الأدمن في قائمة الانتظار، جاري الجلب من الأرشيف...")
            for _ in range(15):
                paper = await fetch_archive_source(session, posted_ids)
                if not paper: continue

                temp_file = f"archive_{sent_in_this_run}.pdf"
                try:
                    headers = {"User-Agent": "Mozilla/5.0"}
                    async with session.get(paper["url"], headers=headers, timeout=60) as resp:
                        if resp.status == 200:
                            with open(temp_file, 'wb') as f:
                                f.write(await resp.read())
                except Exception:
                    continue

                if os.path.exists(temp_file):
                    total_posts += 1
                    caption = (
                        f"📚 **المنشور #{total_posts}**\n\n"
                        f"📖 **العنوان:** {paper['title']}\n"
                        f"✍️ **المؤلف / الباحث:** {paper['author']}\n"
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
                        state["arabic_posts"] = state.get("arabic_posts", 0) + 1

                        if total_posts % 30 == 0:
                            await asyncio.sleep(2)
                            await app.send_message(CHANNEL_USERNAME, promo_text, reply_markup=promo_markup, parse_mode=ParseMode.MARKDOWN)
                            state["promo_posts"] = state.get("promo_posts", 0) + 1
                    except Exception as e:
                        logger.error(f"خطأ: {e}")
                    finally:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    await asyncio.sleep(20)

    # حفظ وتحديث الحالة
    state["total_posts"] = total_posts
    state["posted_ids"] = list(posted_ids)
    state["processed_file_ids"] = list(processed_file_ids)
    save_state(state)

    # إرسال تقرير الإحصائيات للأدمن
    next_promo = 30 - (total_posts % 30) if (total_posts % 30) != 0 else 30
    report_msg = (
        "📊 **تقرير معالجة الملفات والضخ الأكاديمي**\n"
        "────────────────────\n"
        f"🚀 **نُشر في هذه الجولة:** `{sent_in_this_run}` ملف\n"
        f"📥 **الملفات المحولة المتبقية في قائمة الانتظار:** `{remaining_in_queue}` ملف\n"
        f"📈 **إجمالي المنشورات الكلي في القناة:** `{total_posts}` منشور\n"
        "────────────────────\n"
        f"📂 **منشورات من ملفاتك الخاصة:** `{state.get('manual_posts', 0)}`\n"
        f"📚 **منشورات من الأرشيف والمصادر:** `{state.get('arabic_posts', 0)}`\n"
        f"📢 **الإعلانات المنشورة:** `{state.get('promo_posts', 0)}`\n"
        f"⏳ **المتبقي على الإعلان القادم:** `{next_promo}` منشور\n"
        "────────────────────\n"
        "💡 *يمكنك إعادة توجيه مئات الملفات للبوت في الخاص في أي وقت، وسينشرها بالترتيب تباعاً في كل جولة.*"
    )
    try:
        await app.send_message(ADMIN_ID, report_msg, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
