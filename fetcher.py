import os
import sys
import asyncio
import logging
import urllib.parse
import xml.etree.ElementTree as ET
import aiohttp
from pyrogram import Client
from pyrogram.enums import ParseMode

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")

if not all([API_ID, API_HASH, BOT_TOKEN, CHANNEL_USERNAME]):
    logger.error("خطأ: تأكد من إضافة جميع الأسرار (Secrets) في GitHub.")
    sys.exit(1)

API_ID = int(API_ID)

ARXIV_QUERY = "cat:cs.AI OR cat:cs.LG OR cat:cs.CL"
ARXIV_URL = f"https://export.arxiv.org/api/query?search_query={urllib.parse.quote(ARXIV_QUERY)}&sortBy=submittedDate&sortOrder=descending&max_results=1"

async def fetch_latest_paper(session):
    async with session.get(ARXIV_URL) as response:
        if response.status != 200:
            return None
        xml_data = await response.text()

    root = ET.fromstring(xml_data)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    entry = root.find('atom:entry', ns)
    if entry is None:
        return None

    title = entry.find('atom:title', ns).text.strip().replace("\n", " ")
    summary = entry.find('atom:summary', ns).text.strip().replace("\n", " ")
    
    authors = [a.find('atom:name', ns).text.strip() for a in entry.findall('atom:author', ns)]
    authors_str = ", ".join(authors[:3]) + (" وآخرون" if len(authors) > 3 else "")

    pdf_url = None
    for link in entry.findall('atom:link', ns):
        if link.attrib.get('title') == 'pdf' or link.attrib.get('type') == 'application/pdf':
            pdf_url = link.attrib.get('href')
            break

    if not pdf_url:
        id_elem = entry.find('atom:id', ns)
        if id_elem is not None:
            paper_id = id_elem.text.split('/')[-1]
            pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"

    if pdf_url and pdf_url.startswith("http://"):
        pdf_url = pdf_url.replace("http://", "https://")

    if len(summary) > 400:
        summary = summary[:400].strip() + "..."

    return {
        "title": title,
        "authors": authors_str,
        "summary": summary,
        "category": "الذكاء الاصطناعي وتعلّم الآلة",
        "pdf_url": pdf_url
    }

async def download_pdf(session, url, file_path):
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            with open(file_path, 'wb') as f:
                while True:
                    chunk = await resp.content.read(1024 * 64)
                    if not chunk:
                        break
                    f.write(chunk)
            return True
    return False

async def main():
    pdf_filename = "latest_paper.pdf"
    
    async with aiohttp.ClientSession() as session:
        paper = await fetch_latest_paper(session)
        if not paper or not paper.get("pdf_url"):
            logger.error("تعذر العثور على بحث جديد.")
            return

        success = await download_pdf(session, paper["pdf_url"], pdf_filename)
        if not success:
            logger.error("فشل تنزيل ملف الـ PDF.")
            return

    caption = (
        f"📄 **{paper['title']}**\n\n"
        f"👥 **الباحثون:** {paper['authors']}\n"
        f"🏷 **التصنيف:** {paper['category']}\n\n"
        f"📝 **ملخص البحث:**\n{paper['summary']}\n\n"
        f"#أبحاث_علمية #ذكاء_اصطناعي #تقنية"
    )

    app = Client(
        name="pdf_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )

    try:
        await app.start()
        await app.send_document(
            chat_id=CHANNEL_USERNAME,
            document=pdf_filename,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            file_name=f"{paper['title'][:40]}.pdf"
        )
        logger.info("تم إرسال الملف للقناة بنجاح!")
    except Exception as e:
        logger.error(f"خطأ: {e}")
    finally:
        await app.stop()
        if os.path.exists(pdf_filename):
            os.remove(pdf_filename)

if __name__ == "__main__":
    asyncio.run(main())
