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

# جلب المفاتيح من متغيرات البيئة
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")

STATE_FILE = "state.json"
MAX_FILE_SIZE_MB = 45  # حد تليجرام للبوتات 50 ميغابايت، نعتمد 45 كحد أقصى للأمان
BATCH_COUNT = 10       # عدد الملفات المرسلة في كل ساعة

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total_posts": 0, "posted_ids": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def translate_to_arabic(text: str) -> str:
    """ترجمة النصوص الإنجليزية إلى العربية تلقائياً"""
    try:
        if not text:
            return ""
        return GoogleTranslator(source='auto', target='ar').translate(text[:1200])
    except Exception as e:
        logger.warning(f"تعذر الترجمة: {e}")
        return text

# ----------------- جلب المصادر العربية (80%) -----------------
ARABIC_KEYWORDS = [
    "تاريخ", "رسالة ماجستير", "بحوث جامعية", "تقرير جامعي", 
    "أطروحة دكتوراه", "حضارة إسلامية", "فلسفة وفكر", "علوم ولغة عربية", "دراسات تاريخية"
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

                # الاستعلام عن ملف PDF المباشر
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
                        if fname.lower().endswith(".pdf") and fsize <= (MAX_FILE_SIZE_MB * 1024 * 1024) and fsize > 100000:
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
                            "title": title[:100],
                            "authors": author if isinstance(author, str) else ", ".join(author[:2]),
                            "category": f"دراسات عربية وأكاديمية ({keyword})",
                            "summary": desc[:350].strip() + "...",
                            "pdf_url": pdf_url,
                            "type": "arabic"
                        }
    except Exception as e:
        logger.error(f"خطأ أثناء جلب المصدر العربي: {e}")
    return None

# ----------------- جلب المصادر الإنجليزية (20%) -----------------
async def fetch_english_source(session, posted_ids):
    categories = ["cs.AI", "cs.LG", "stat.ML", "physics.soc-ph"]
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

            # ترجمة المحتوى إلى العربية
            ar_title = translate_to_arabic(en_title)
            ar_summary = translate_to_arabic(en_summary[:500])

            pdf_url = f"https://arxiv.org/pdf/{raw_id}.pdf"
            return {
                "id": raw_id,
                "title": ar_title[:100],
                "authors": ", ".join(authors[:2]) + (" وآخرون" if len(authors) > 2 else ""),
                "category": "أبحاث عالمية مترجمة (علوم حديثة وتقنية)",
                "summary": ar_summary[:350].strip() + "...",
                "pdf_url": pdf_url,
                "type": "english"
            }
    except Exception as e:
        logger.error(f"خطأ أثناء جلب البحث الإنجليزي: {e}")
    return None

# ----------------- تنزيل الملف وحفظه -----------------
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

# ----------------- الإرسال الرئيسي -----------------
async def main():
    state = load_state()
    total_posts = state.get("total_posts", 0)
    posted_ids = set(state.get("posted_ids", []))

    app = Client(
        name="academia_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )
    await app.start()

    promo_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("الانضمام إلى قناة الأدوات 🚀", url="https://t.me/AKADEME_GG")]
    ])
    promo_text = (
        "🚀 **منظومة أدوات أكاديميا | Academia بين يديك!**\n\n"
        "لا تكتفِ بالمكتبة فقط، احصل على تجربة أكاديمية كاملة مع أدواتنا الذكية:\n"
        "🔍 **مُعِين** — للبحث في البحوث والمصادر.\n"
        "📄 **غِلاف** — لإنشاء غلاف تقريرك الجامعي بثوانٍ والكثير أيضاً.\n\n"
        "تابع كل جديد عبر القناة المخصصة للأدوات بالضغط على الزر أدناه 👇"
    )

    sent_in_this_run = 0
    async with aiohttp.ClientSession() as session:
        for _ in range(BATCH_COUNT):
            # توزيع 80% عربي و 20% إنجليزي
            is_arabic = random.random() < 0.8
            paper = None
            if is_arabic:
                paper = await fetch_arabic_source(session, posted_ids)
            if not paper:
                paper = await fetch_english_source(session, posted_ids)

            if not paper:
                continue

            temp_filename = f"temp_doc_{sent_in_this_run}.pdf"
            if await download_file(session, paper["pdf_url"], temp_filename):
                total_posts += 1
                caption = (
                    f"📚 **المنشور #{total_posts}**\n\n"
                    f"📖 **العنوان:** {paper['title']}\n"
                    f"✍️ **المؤلف / الباحث:** {paper['authors']}\n"
                    f"🏷 **التصنيف:** {paper['category']}\n\n"
                    f"📝 **الملخص بالعربية:**\n{paper['summary']}\n\n"
                    f"#منشور_{total_posts} #أكاديميا #كتب #بحوث_جامعية #رسائل_ماجستير #تاريخ"
                )

                try:
                    await app.send_document(
                        chat_id=CHANNEL_USERNAME,
                        document=temp_filename,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN,
                        file_name=f"{paper['title'][:35].strip()}.pdf"
                    )
                    logger.info(f"تم إرسال المنشور #{total_posts} بنجاح!")
                    posted_ids.add(paper["id"])
                    sent_in_this_run += 1

                    # التحقق من شرط المنشور الإعلاني كل 30 ملف
                    if total_posts % 30 == 0:
                        await asyncio.sleep(3)
                        await app.send_message(
                            chat_id=CHANNEL_USERNAME,
                            text=promo_text,
                            reply_markup=promo_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )
                        logger.info("📢 تم إرسال إعلان منظومة أكاديميا بنجاح!")

                except Exception as send_err:
                    logger.error(f"خطأ أثناء الإرسال لتليجرام: {send_err}")
                finally:
                    if os.path.exists(temp_filename):
                        os.remove(temp_filename)

                # فاصل زمني بسيط بين كل ملف لتجنب الحظر المؤقت
                await asyncio.sleep(5)

    await app.stop()

    # حفظ الحالة الجديدة
    state["total_posts"] = total_posts
    state["posted_ids"] = list(posted_ids)[-1000:]  # الاحتفاظ بآخر 1000 معرف لتوفير المساحة
    save_state(state)
    logger.info(f"انتهت الجولة الحالية بنجاح. إجمالي المنشورات الكلي: {total_posts}")

if __name__ == "__main__":
    asyncio.run(main())
