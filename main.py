import sqlite3
import json
import asyncio
import aiofiles
import os
import html
import secrets
import string
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import uuid4
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, Form, HTTPException, Depends, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse
from fastapi import Request, status
from starlette.exceptions import HTTPException as StarletteHTTPException

from cryptography.fernet import Fernet
import google.generativeai as genai

# --- Configuration ---
DATABASE_FILE = "mbti_app.db"
ENCRYPTION_KEY_FILE = "secret.key"
SESSION_SECRET = "44fd32cf2bbad6ff823421b4cb11aad61ffb7a8583264ade63381353e6ae2d0e"

# --- Configuration ---
DATABASE_FILE = "mbti_app.db"
ADVISOR_DATABASE_FILE = "advisor_users.db"  # دیتابیس جدید برای مشاوران
ENCRYPTION_KEY_FILE = "secret.key"  # این فایل دیگر استفاده نمی‌شود، اما برای سازگاری نگه داشته شده
SESSION_SECRET = "44fd32cf2bbad6ff823421b4cb11aad61ffb7a8583264ade63381353e6ae2d0e"

# Thread pool for CPU-intensive tasks
executor = ThreadPoolExecutor(max_workers=4)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def load_or_generate_key():
    """Async key loading/generation"""
    try:
        if os.path.exists(ENCRYPTION_KEY_FILE):
            async with aiofiles.open(ENCRYPTION_KEY_FILE, "rb") as key_file:
                key = await key_file.read()
        else:
            key = Fernet.generate_key()
            async with aiofiles.open(ENCRYPTION_KEY_FILE, "wb") as key_file:
                await key_file.write(key)
            logger.info(f"کلید رمزنگاری جدید ایجاد و در {ENCRYPTION_KEY_FILE} ذخیره شد.")
        
        if len(key) != 44 or not key.endswith(b'='):
            logger.warning(f"هشدار: محتوای {ENCRYPTION_KEY_FILE} به نظر یک کلید Fernet معتبر نیست.")
        
        return key
    except Exception as e:
        logger.error(f"خطا در بارگذاری کلید: {e}")
        raise

# Initialize encryption key asynchronously
ENCRYPTION_KEY = None
cipher_suite = None

async def init_encryption():
    """Initialize encryption components"""
    global ENCRYPTION_KEY, cipher_suite
    ENCRYPTION_KEY = await load_or_generate_key()
    cipher_suite = Fernet(ENCRYPTION_KEY)

# Create directories
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions with custom error pages"""
    
    # 404 Error
    if exc.status_code == 404:
        return templates.TemplateResponse("error_404.html", {
            "request": request
        }, status_code=404)
    
    # 401 Error (Unauthorized)
    elif exc.status_code == 401:
        return templates.TemplateResponse("error_401.html", {
            "request": request
        }, status_code=401)
    
    # 500 Error (Internal Server Error)
    elif exc.status_code == 500:
        return templates.TemplateResponse("error_500.html", {
            "request": request
        }, status_code=500)
    
    # برای سایر خطاها از پیغام پیش‌فرض استفاده می‌کنیم
    else:
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html lang="fa" dir="rtl">
                <head>
                    <meta charset="UTF-8">
                    <title>خطا {exc.status_code}</title>
                    <style>
                        @import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;600;700&display=swap');
                        body {{
                            font-family: 'Vazirmatn', sans-serif;
                            background: #1a1a1a;
                            color: white;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                        }}
                        .error-box {{
                            text-align: center;
                            padding: 40px;
                            background: rgba(255,255,255,0.1);
                            border-radius: 20px;
                            backdrop-filter: blur(10px);
                            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
                        }}
                        h1 {{ 
                            font-size: 4em; 
                            margin: 0 0 20px 0;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            -webkit-background-clip: text;
                            -webkit-text-fill-color: transparent;
                        }}
                        p {{ 
                            font-size: 1.2em;
                            margin-bottom: 30px;
                            opacity: 0.9;
                        }}
                        a {{
                            display: inline-block;
                            margin-top: 20px;
                            padding: 12px 30px;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                            text-decoration: none;
                            border-radius: 15px;
                            transition: 0.3s;
                            font-weight: 600;
                            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
                        }}
                        a:hover {{ 
                            transform: translateY(-2px);
                            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
                        }}
                    </style>
                </head>
                <body>
                    <div class="error-box">
                        <h1>خطا {exc.status_code}</h1>
                        <p>{exc.detail}</p>
                        <a href="/">بازگشت به صفحه اصلی</a>
                    </div>
                </body>
            </html>
            """,
            status_code=exc.status_code
        )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions"""
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    
    return templates.TemplateResponse("error_500.html", {
        "request": request
    }, status_code=500)


API_KEY = "AIzaSyCphwC83v0XsBfQIv0ac_JHkJkVopCM43M"
if not API_KEY:
    raise ValueError("متغیر محیطی GEMINI_API_KEY تنظیم نشده است")
genai.configure(api_key=API_KEY)

# --- Async Database Operations ---
class AsyncDBManager:
    def __init__(self, db_file):
        self.db_file = db_file
        self._lock = asyncio.Lock()
    
    async def execute_query(self, query: str, params: tuple = (), fetch: bool = False):
        """Execute database query asynchronously"""
        async with self._lock:
            def _execute():
                conn = sqlite3.connect(self.db_file)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                try:
                    cursor.execute(query, params)
                    if fetch:
                        result = cursor.fetchall()
                        conn.close()
                        return result
                    else:
                        conn.commit()
                        conn.close()
                        return cursor.lastrowid
                except Exception as e:
                    conn.close()
                    raise e
            
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(executor, _execute)
    
    async def execute_many(self, query: str, params_list: List[tuple]):
        """Execute multiple queries asynchronously"""
        async with self._lock:
            def _execute():
                conn = sqlite3.connect(self.db_file)
                cursor = conn.cursor()
                try:
                    cursor.executemany(query, params_list)
                    conn.commit()
                    conn.close()
                    return cursor.rowcount
                except Exception as e:
                    conn.close()
                    raise e
            
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(executor, _execute)

# Initialize database manager
db_manager = AsyncDBManager(DATABASE_FILE)

# دیتابیس جدید برای مشاوران
class AsyncAdvisorDBManager:
    def __init__(self, db_file):
        self.db_file = db_file
        self._lock = asyncio.Lock()
    
    async def execute_query(self, query: str, params: tuple = (), fetch: bool = False):
        """Execute advisor database query asynchronously"""
        async with self._lock:
            def _execute():
                conn = sqlite3.connect(self.db_file)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                try:
                    cursor.execute(query, params)
                    if fetch:
                        result = cursor.fetchall()
                        conn.close()
                        return result
                    else:
                        conn.commit()
                        conn.close()
                        return cursor.lastrowid
                except Exception as e:
                    conn.close()
                    raise e
            
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(executor, _execute)

# Initialize database managers
db_manager = AsyncDBManager(DATABASE_FILE)
advisor_db_manager = AsyncAdvisorDBManager(ADVISOR_DATABASE_FILE)

async def init_db():
    """Initialize databases asynchronously"""
    # Initialize user database
    await db_manager.execute_query("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            encrypted_first_name BLOB NOT NULL,
            encrypted_last_name BLOB NOT NULL,
            encrypted_phone BLOB UNIQUE NOT NULL,
            encrypted_password BLOB NOT NULL,
            age_range TEXT NOT NULL,
            registration_time TEXT NOT NULL
        )
    """)
    
    await db_manager.execute_query("""
        CREATE TABLE IF NOT EXISTS test_results (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            test_name TEXT NOT NULL,
            encrypted_answers BLOB,
            mbti_result TEXT,
            encrypted_mbti_percentages BLOB,
            analysis_time TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    await db_manager.execute_query("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Initialize advisor database
    await advisor_db_manager.execute_query("""
        CREATE TABLE IF NOT EXISTS advisors (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL
        )
    """)

    await db_manager.execute_query("""
        CREATE TABLE IF NOT EXISTS advisor_sessions (
            session_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES advisors (username)
        )
    """)

# --- Async Authentication & Session Management ---
async def hash_password(password: str) -> str:
    """Hash password asynchronously"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: hashlib.sha256(password.encode('utf-8')).hexdigest())

async def verify_password(password: str, hashed: str) -> bool:
    """Verify password asynchronously"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: hashlib.sha256(password.encode('utf-8')).hexdigest() == hashed)

async def generate_password(length: int = 12) -> str:
    """Generate random password asynchronously"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: ''.join(secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*") for _ in range(length)))

async def create_session(user_id: str) -> str:
    """Create user session asynchronously"""
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now().timestamp() + (24 * 60 * 60)  # 24 hours
    
    await db_manager.execute_query("""
        INSERT INTO sessions (session_id, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    """, (session_id, user_id, datetime.now().isoformat(), expires_at))
    
    return session_id

async def create_advisor_session(username: str) -> str:
    """Create advisor session asynchronously"""
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now().timestamp() + (24 * 60 * 60)  # 24 hours
    
    await db_manager.execute_query("""
        INSERT INTO advisor_sessions (session_id, username, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    """, (session_id, username, datetime.now().isoformat(), expires_at))
    
    return session_id

async def get_current_user(session_id: str = Cookie(None)) -> Optional[Dict]:
    """Get current user asynchronously"""
    if not session_id:
        return None
    
    result = await db_manager.execute_query("""
        SELECT s.user_id, u.encrypted_first_name, u.encrypted_last_name, u.encrypted_phone, u.age_range
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_id = ? AND s.expires_at > ?
    """, (session_id, datetime.now().timestamp()), fetch=True)
    
    if result:
        user_data = result[0]
        return {
            'id': user_data['user_id'],
            'first_name': await decrypt_data(user_data['encrypted_first_name']),
            'last_name': await decrypt_data(user_data['encrypted_last_name']),
            'phone': await decrypt_data(user_data['encrypted_phone']),
            'age_range': user_data['age_range']
        }
    return None

async def get_current_advisor(advisor_session_id: str = Cookie(None)) -> Optional[Dict]:
    """Get current advisor asynchronously"""
    if not advisor_session_id:
        return None
    
    result = await db_manager.execute_query("""
        SELECT username FROM advisor_sessions
        WHERE session_id = ? AND expires_at > ?
    """, (advisor_session_id, datetime.now().timestamp()), fetch=True)
    
    if result:
        return {'username': result[0]['username'], 'type': 'advisor'}
    return None

async def require_login(user = Depends(get_current_user)):
    """Require user login dependency"""
    if not user:
        raise HTTPException(status_code=401, detail="لطفاً وارد شوید")
    return user

async def require_advisor_login(advisor = Depends(get_current_advisor)):
    """Require advisor login dependency"""
    if not advisor:
        raise HTTPException(status_code=401, detail="لطفاً وارد شوید")
    return advisor

# --- Async Encryption/Decryption Helpers ---
async def encrypt_data(data: str) -> Optional[bytes]:
    """Encrypt data asynchronously"""
    if data is None or not cipher_suite:
        return None
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: cipher_suite.encrypt(data.encode('utf-8')))

async def decrypt_data(encrypted_data: Optional[bytes]) -> str:
    """Decrypt data asynchronously - Fixed to return empty string instead of None"""
    if encrypted_data is None or not cipher_suite:
        return ""  # Return empty string instead of None
    
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, lambda: cipher_suite.decrypt(encrypted_data).decode('utf-8'))
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return "خطا در رمزگشایی"

# --- MBTI Questions Database ---
QUESTIONS_DB = {
    "13-15": [
        {
            "question": "آیا شناختن شما برای دیگران آسان است؟",
            "options": [
                "خیلی آسان است",
                "تا حدی آسان است",
                "تا حدی دشوار است",
                "خیلی دشوار است"
            ]
        },
        {
            "question": "وقتی با دیگران صحبت می‌کنی، چطور رفتار می‌کنی؟",
            "options": [
                "با همه خیلی راحت صحبت می‌کنم",
                "با بعضی‌ها راحت صحبت می‌کنم",
                "فقط با دوستان نزدیک راحت هستم",
                "ترجیح می‌دم زیاد صحبت نکنم"
            ]
        },
        {
            "question": "معمولاً در جمع دوستانت چطور هستی؟",
            "options": [
                "خیلی پرهیجان و سرزنده",
                "کمی پرهیجان و شاد",
                "آرام و ساکت",
                "خیلی آرام و کم‌حرف"
            ]
        },
        {
            "question": "روابط دوستانه‌ات بیشتر چطوره؟",
            "options": [
                "با تعداد کمی دوست، ولی خیلی صمیمی",
                "با چند دوست صمیمی",
                "با افراد زیادی، ولی نه خیلی نزدیک",
                "با خیلی‌ها دوست هستم، ولی سطحی"
            ]
        },
        {
            "question": "می‌تونی با هر کسی صحبت کنی؟",
            "options": [
                "بله، با همه راحت صحبت می‌کنم",
                "بله، ولی فقط درباره چیزهای خاص",
                "فقط با کسانی که می‌شناسم",
                "خیر، فقط با افراد خیلی خاص"
            ]
        },
        {
            "question": "وقتی با دوستات حرف می‌زنی، مسائل شخصیت رو می‌گی؟",
            "options": [
                "همیشه مسائل شخصی رو می‌گم",
                "گاهی مسائل شخصی رو می‌گم",
                "به ندرت مسائل شخصی رو می‌گم",
                "هرگز مسائل شخصی رو نمی‌گم"
            ]
        },
        {
            "question": "احساساتت رو به دیگران نشون می‌دی؟",
            "options": [
                "همیشه احساساتم رو نشون می‌دم",
                "اغلب احساساتم رو نشون می‌دم",
                "به ندرت احساساتم رو نشون می‌دم",
                "هرگز احساساتم رو نشون نمی‌دم"
            ]
        },
        {
            "question": "وقتی غریبه‌ها بهت توجه می‌کنن، چه حسی داری؟",
            "options": [
                "کاملاً راحت هستم",
                "تا حدی راحت هستم",
                "کمی ناراحت می‌شم",
                "خیلی ناراحت می‌شم"
            ]
        },
        {
            "question": "به چند نفر اعتماد داری؟",
            "options": [
                "به خیلی‌ها اعتماد می‌کنم",
                "به چند نفر اعتماد می‌کنم",
                "فقط به یکی دو نفر اعتماد می‌کنم",
                "به هیچ‌کس اعتماد نمی‌کنم"
            ]
        },
        {
            "question": "دوستات می‌دونن که دوستشون داری؟",
            "options": [
                "همه می‌دونن که دوستشون دارم",
                "بعضی‌هاشون می‌دونن",
                "فقط دوستان نزدیکم می‌دونن",
                "کسی نمی‌دونه که دوستش دارم"
            ]
        },
        {
            "question": "وقتی با یه گروه هستی، ترجیح می‌دی چی کار کنی؟",
            "options": [
                "با همه گروه صحبت کنم",
                "با چند نفر خاص صحبت کنم",
                "فقط با یک نفر صحبت کنم",
                "اصلاً صحبت نکنم"
            ]
        },
        {
            "question": "اخبار و شایعات رو زود می‌شنوی؟",
            "options": [
                "همیشه اولین نفرم که می‌شنوم",
                "معمولاً زود می‌شنوم",
                "گاهی دیر می‌شنوم",
                "همیشه آخرین نفرم"
            ]
        },
        {
            "question": "توی مهمونی‌ها چطور رفتار می‌کنی؟",
            "options": [
                "با همه گروه می‌جوشم",
                "با چند نفر گپ می‌زنم",
                "با یک نفر خاص حرف می‌زنم",
                "ترجیح می‌دم تنها باشم"
            ]
        },
        {
            "question": "توی مهمونی‌ها بهت خوش می‌گذره؟",
            "options": [
                "همیشه خیلی خوش می‌گذره",
                "اغلب خوش می‌گذره",
                "گاهی کسل‌کننده است",
                "معمولاً کسل‌کننده است"
            ]
        },
        {
            "question": "کدام کلمه رو بیشتر دوست داری؟",
            "options": [
                "صحبت کردن",
                "گوش دادن",
                "نوشتن",
                "خواندن"
            ]
        },
        {
            "question": "کدام کلمه به نظرت بهتره؟",
            "options": [
                "سرزنده",
                "آرام",
                "فعال",
                "ساکت"
            ]
        },
        {
            "question": "کدام کلمه رو ترجیح می‌دی؟",
            "options": [
                "پر حرف",
                "ساکت",
                "شاد",
                "متفکر"
            ]
        },
        {
            "question": "توی مهمونی دوست داری چی کار کنی؟",
            "options": [
                "کمک کنم مهمونی خوب برگزار بشه",
                "با همه گپ بزنم",
                "با یک نفر صمیمی حرف بزنم",
                "فقط لذت ببرم"
            ]
        },
        {
            "question": "وقتی با دوستای نزدیکت هستی، چطور صحبت می‌کنی؟",
            "options": [
                "بیشتر از همه حرف می‌زنم",
                "به اندازه بقیه حرف می‌زنم",
                "کمتر از بقیه حرف می‌زنم",
                "تقریباً حرف نمی‌زنم"
            ]
        },
        {
            "question": "توی گروه‌های بزرگ، معمولاً چی کار می‌کنی؟",
            "options": [
                "دیگران رو معرفی می‌کنم",
                "خودم رو معرفی می‌کنم",
                "منتظر می‌مونم تا منو معرفی کنن",
                "سعی می‌کنم اصلاً معرفی نشم"
            ]
        },
        {
            "question": "وقتی به یه اتفاق فکر می‌کنی، ترجیح می‌دی چی کار کنی؟",
            "options": [
                "با یکی دربارش حرف بزنم",
                "خودم روش فکر کنم",
                "اول فکر کنم، بعد حرف بزنم",
                "فقط عمل کنم"
            ]
        },
        {
            "question": "دیگران چقدر سریع می‌فهمن به چی علاقه داری؟",
            "options": [
                "خیلی سریع می‌فهمن",
                "بعد از مدتی می‌فهمن",
                "فقط دوستای نزدیکم می‌فهمن",
                "کسی نمی‌فهمه"
            ]
        },
        {
            "question": "منظورت از چیزی که می‌گی چیه؟",
            "options": [
                "همیشه بیشتر از چیزیه که می‌گم",
                "گاهی بیشتره",
                "همیشه همونه که می‌گم",
                "معمولاً کمتر از چیزیه که می‌گم"
            ]
        },
        {
            "question": "وقتی غریبه‌ها رو می‌بینی، چه حسی داری؟",
            "options": [
                "خیلی راحت و خوشحال می‌شم",
                "تا حدی راحت هستم",
                "کمی اذیت می‌شم",
                "خیلی اذیت می‌شم"
            ]
        },
        {
            "question": "اگه تو جلسه‌ای نظری داشته باشی، چی کار می‌کنی؟",
            "options": [
                "حتماً می‌گم",
                "اگه مهم باشه می‌گم",
                "شاید بگم، شاید نگم",
                "معمولاً نمی‌گم"
            ]
        },
        {
            "question": "دوست داری دیگران تو رو چطور ببینن؟",
            "options": [
                "یه آدم عمل‌گرا",
                "یه آدم خلاق",
                "یه آدم منطقی",
                "یه آدم مهربون"
            ]
        },
        {
            "question": "وقتی چیزی می‌خونی، چی رو بیشتر دوست داری؟",
            "options": [
                "داستان‌های واقعی و ساده",
                "داستان‌های خلاقانه و عجیب",
                "مطالب علمی و دقیق",
                "مطالب احساسی و عمیق"
            ]
        },
        {
            "question": "اگه معلم بودی، چی رو دوست داشتی یاد بدی؟",
            "options": [
                "چیزهای واقعی و کاربردی",
                "ایده‌ها و نظریه‌ها",
                "مهارت‌های عملی",
                "ارزش‌های انسانی"
            ]
        },
        {
            "question": "با چه آدمایی راحت‌تر ارتباط برقرار می‌کنی؟",
            "options": [
                "آدمای واقع‌بین",
                "آدمای تخیلی",
                "آدمای منطقی",
                "آدمای احساسی"
            ]
        },
        {
            "question": "وقتی کاری رو انجام می‌دی، ترجیح می‌دی چطور باشه؟",
            "options": [
                "مثل همه، طبق روش معمول",
                "با یه روش جدید و خاص",
                "با برنامه‌ریزی دقیق",
                "با آزادی و انعطاف"
            ]
        }
    ],
    "15-18": [
        {
            "question": "آیا دیگران به راحتی می‌توانند شما را بشناسند؟",
            "options": [
                "بله، کاملاً آسان است",
                "تا حدی آسان است",
                "تا حدی دشوار است",
                "خیر، بسیار دشوار است"
            ]
        },
        {
            "question": "در موقعیت‌های اجتماعی، معمولاً چطور رفتار می‌کنید؟",
            "options": [
                "با همه به راحتی گپ می‌زنم",
                "با برخی افراد راحت صحبت می‌کنم",
                "فقط با افراد نزدیک صمیمی‌ام",
                "ترجیح می‌دهم ساکت بمانم"
            ]
        },
        {
            "question": "در جمع‌های دوستانه، معمولاً چه حالتی دارید؟",
            "options": [
                "بسیار پرانرژی و سرزنده",
                "تا حدی پرانرژی و فعال",
                "آرام و متفکر",
                "بسیار ساکت و درون‌گرا"
            ]
        },
        {
            "question": "روابط دوستانه شما بیشتر چه ویژگی‌ای دارند؟",
            "options": [
                "صمیمی با تعداد کمی دوست",
                "نزدیک با چند دوست",
                "گسترده با افراد زیاد",
                "سطحی با تعداد زیادی"
            ]
        },
        {
            "question": "آیا می‌توانید با هر کسی به راحتی صحبت کنید؟",
            "options": [
                "بله، با همه راحت صحبت می‌کنم",
                "بله، درباره موضوعات خاص",
                "فقط با کسانی که می‌شناسم",
                "فقط با افراد خیلی خاص"
            ]
        },
        {
            "question": "وقتی با دوستان نزدیک صحبت می‌کنید، مسائل شخصی را مطرح می‌کنید؟",
            "options": [
                "همیشه مسائل شخصی را می‌گویم",
                "گاهی مسائل شخصی را مطرح می‌کنم",
                "به ندرت مسائل شخصی را می‌گویم",
                "هرگز مسائل شخصی را نمی‌گویم"
            ]
        },
        {
            "question": "آیا احساسات خود را به دیگران نشان می‌دهید؟",
            "options": [
                "همیشه احساساتم را بیان می‌کنم",
                "اغلب احساساتم را نشان می‌دهم",
                "به ندرت احساساتم را نشان می‌دهم",
                "هرگز احساساتم را نشان نمی‌دهم"
            ]
        },
        {
            "question": "وقتی غریبه‌ها به شما توجه می‌کنند، چه احساسی دارید؟",
            "options": [
                "کاملاً راحت و خوشحال می‌شوم",
                "تا حدی راحت هستم",
                "کمی معذب می‌شوم",
                "بسیار معذب می‌شوم"
            ]
        },
        {
            "question": "به چه تعداد افراد اعتماد دارید؟",
            "options": [
                "به افراد زیادی اعتماد دارم",
                "به چند نفر خاص اعتماد دارم",
                "فقط به یک یا دو نفر",
                "به هیچ‌کس اعتماد ندارم"
            ]
        },
        {
            "question": "آیا دوستانتان می‌دانند که به آن‌ها علاقه دارید؟",
            "options": [
                "همه می‌دانند که دوستشان دارم",
                "بعضی‌ها می‌دانند",
                "فقط دوستان نزدیکم می‌دانند",
                "کسی نمی‌داند"
            ]
        },
        {
            "question": "در گروه‌های اجتماعی، ترجیح می‌دهید چه کنید؟",
            "options": [
                "با کل گروه صحبت کنم",
                "با چند نفر خاص گپ بزنم",
                "فقط با یک نفر صحبت کنم",
                "اصلاً صحبت نکنم"
            ]
        },
        {
            "question": "اخبار و اطلاعات جدید را چطور دریافت می‌کنید؟",
            "options": [
                "همیشه اولین نفرم که می‌شنوم",
                "معمولاً زود مطلع می‌شوم",
                "گاهی دیرتر از بقیه می‌شنوم",
                "اغلب آخرین نفرم"
            ]
        },
        {
            "question": "در یک مهمانی یا جمع اجتماعی، چطور رفتار می‌کنید؟",
            "options": [
                "با گروه اصلی می‌جوشم",
                "با چند نفر خاص گپ می‌زنم",
                "با یک نفر خاص صحبت می‌کنم",
                "ترجیح می‌دهم تنها باشم"
            ]
        },
        {
            "question": "در مهمانی‌ها معمولاً چه احساسی دارید؟",
            "options": [
                "همیشه به من خیلی خوش می‌گذرد",
                "اغلب اوقات لذت می‌برم",
                "گاهی اوقات کسل می‌شوم",
                "معمولاً کسل‌کننده است"
            ]
        },
        {
            "question": "کدام فعالیت را بیشتر ترجیح می‌دهید؟",
            "options": [
                "صحبت کردن با دیگران",
                "گوش دادن به دیگران",
                "نوشتن ایده‌هایم",
                "خواندن و فکر کردن"
            ]
        },
        {
            "question": "کدام ویژگی را در خودتان بیشتر دوست دارید؟",
            "options": [
                "سرزندگی و انرژی",
                "آرامش و متانت",
                "فعالیت و تحرک",
                "سکوت و تفکر"
            ]
        },
        {
            "question": "کدام صفت برای شما جذاب‌تر است؟",
            "options": [
                "پر حرف و اجتماعی",
                "ساکت و متفکر",
                "شاد و پرانرژی",
                "عمیق و آرام"
            ]
        },
        {
            "question": "در یک مهمانی، ترجیح می‌دهید چه نقشی داشته باشید؟",
            "options": [
                "کمک کنم مهمانی خوب برگزار شود",
                "با همه گپ بزنم و خوش بگذرانم",
                "با یک نفر صمیمی صحبت کنم",
                "فقط از فضا لذت ببرم"
            ]
        },
        {
            "question": "وقتی با دوستان نزدیک هستید، چقدر صحبت می‌کنید؟",
            "options": [
                "بیشتر از همه صحبت می‌کنم",
                "به اندازه دیگران صحبت می‌کنم",
                "کمتر از دیگران صحبت می‌کنم",
                "تقریباً صحبت نمی‌کنم"
            ]
        },
        {
            "question": "در جمع‌های بزرگ، معمولاً چه نقشی دارید؟",
            "options": [
                "دیگران را به هم معرفی می‌کنم",
                "خودم را معرفی می‌کنم",
                "منتظر می‌مانم تا معرفی شوم",
                "سعی می‌کنم توجه کسی را جلب نکنم"
            ]
        },
        {
            "question": "وقتی به موضوعی فکر می‌کنید، ترجیح می‌دهید چه کنید؟",
            "options": [
                "با کسی درباره‌اش صحبت کنم",
                "خودم به تنهایی فکر کنم",
                "اول فکر کنم، بعد بحث کنم",
                "سریعاً اقدام کنم"
            ]
        },
        {
            "question": "دیگران چقدر سریع متوجه علایق شما می‌شوند؟",
            "options": [
                "خیلی سریع متوجه می‌شوند",
                "بعد از مدتی می‌فهمند",
                "فقط دوستان نزدیکم می‌فهمند",
                "کسی متوجه نمی‌شود"
            ]
        },
        {
            "question": "منظورتان از حرف‌هایتان چیست؟",
            "options": [
                "همیشه بیشتر از گفته‌هایم است",
                "گاهی بیشتر از گفته‌هایم است",
                "دقیقاً همان چیزی است که می‌گویم",
                "معمولاً کمتر از گفته‌هایم است"
            ]
        },
        {
            "question": "دیدار با غریبه‌ها برایتان چه حسی دارد؟",
            "options": [
                "کاملاً خوشایند و راحت است",
                "تا حدی خوشایند است",
                "کمی آزاردهنده است",
                "بسیار آزاردهنده است"
            ]
        },
        {
            "question": "اگر در جلسه‌ای نظری داشته باشید، چه می‌کنید؟",
            "options": [
                "حتماً آن را مطرح می‌کنم",
                "اگر مهم باشد، مطرح می‌کنم",
                "شاید مطرح کنم، شاید نه",
                "معمولاً مطرح نمی‌کنم"
            ]
        },
        {
            "question": "دوست دارید دیگران شما را چگونه ببینند؟",
            "options": [
                "فردی عمل‌گرا و نتیجه‌محور",
                "فردی خلاق و نوآور",
                "فردی منطقی و تحلیل‌گر",
                "فردی مهربان و همدل"
            ]
        },
        {
            "question": "وقتی برای سرگرمی مطالعه می‌کنید، چه چیزی را ترجیح می‌دهید؟",
            "options": [
                "داستان‌های واقعی و واضح",
                "مطالب خلاقانه و تخیلی",
                "مطالب علمی و دقیق",
                "مطالب احساسی و عمیق"
            ]
        },
        {
            "question": "اگر معلم بودید، ترجیح می‌دادید چه چیزی تدریس کنید؟",
            "options": [
                "واقعیت‌ها و مهارت‌های عملی",
                "نظریه‌ها و ایده‌های بزرگ",
                "مهارت‌های کاربردی و روزمره",
                "ارزش‌ها و مفاهیم انسانی"
            ]
        },
        {
            "question": "با چه نوع افرادی راحت‌تر ارتباط برقرار می‌کنید؟",
            "options": [
                "افراد واقع‌بین و عمل‌گرا",
                "افراد تخیلی و خلاق",
                "افراد منطقی و تحلیل‌گر",
                "افراد احساسی و همدل"
            ]
        },
        {
            "question": "وقتی کاری را انجام می‌دهید، چگونه عمل می‌کنید؟",
            "options": [
                "طبق روش‌های معمول و پذیرفته‌شده",
                "با روشی جدید و خلاقانه",
                "با برنامه‌ریزی دقیق و منظم",
                "با انعطاف و آزادی عمل"
            ]
        }
    ]
}

# افزودن سوالات هدایت تحصیلی به QUESTIONS_DB
ACADEMIC_GUIDANCE_QUESTIONS = [
    # علوم تجربی
    {
        "question": "وقتی کسی در اطراف شما بیمار است، چه احساسی دارید؟",
        "options": [
            "دوست دارم به او کمک کنم و حالش را بهتر کنم",
            "ناراحت می‌شوم، اما نمی‌دانم چطور کمک کنم",
            "بیشتر کنجکاو می‌شوم که چرا بیمار شده است",
            "ترجیح می‌دهم از او فاصله بگیرم"
        ]
    },
    {
        "question": "آیا از مطالعه درباره بدن انسان و نحوه کارکرد آن لذت می‌برید؟",
        "options": [
            "خیلی زیاد، همیشه برایم جذاب است",
            "گاهی اوقات، اگر ساده توضیح داده شود",
            "نه خیلی، بیشتر به موضوعات دیگر علاقه دارم",
            "اصلاً، برایم خسته‌کننده است"
        ]
    },
    {
        "question": "اگر در یک آزمایشگاه زیست‌شناسی باشید، کدام کار را ترجیح می‌دهید؟",
        "options": [
            "بررسی نمونه‌ها زیر میکروسکوپ",
            "کمک به تمیز کردن و مرتب کردن وسایل",
            "یادداشت‌برداری از نتایج آزمایش",
            "ترجیح می‌دهم اصلاً آنجا نباشم"
        ]
    },
    {
        "question": "چقدر به کمک به دیگران در بهبود سلامتی‌شان اهمیت می‌دهید؟",
        "options": [
            "خیلی زیاد، این برایم یک اولویت است",
            "تا حدی، اگر بتوانم کمک می‌کنم",
            "زیاد مهم نیست، بیشتر به خودم فکر می‌کنم",
            "اصلاً برایم مهم نیست"
        ]
    },
    {
        "question": "آیا از یادگیری درباره گیاهان، حیوانات و محیط‌زیست لذت می‌برید؟",
        "options": [
            "بله، همیشه برایم جالب است",
            "گاهی، اگر موضوع جذابی باشد",
            "نه خیلی، بیشتر به چیزهای دیگر علاقه دارم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    {
        "question": "وقتی یک مشکل پزشکی در اخبار مطرح می‌شود، چه واکنشی نشان می‌دهید؟",
        "options": [
            "کنجکاو می‌شوم و دوست دارم بیشتر بدانم",
            "کمی توجه می‌کنم، اما سریع فراموش می‌کنم",
            "برایم مهم نیست، چون به من ربطی ندارد",
            "ترجیح می‌دهم اخبار دیگری ببینم"
        ]
    },
    {
        "question": "آیا دوست دارید در آینده شغلی داشته باشید که مستقیماً به مردم کمک کند؟",
        "options": [
            "بله، این برایم خیلی مهم است",
            "شاید، بستگی به شرایط دارد",
            "نه خیلی، بیشتر به کار مستقل علاقه دارم",
            "خیر، اصلاً به این فکر نمی‌کنم"
        ]
    },
    {
        "question": "چقدر از انجام کارهای دقیق و جزئی لذت می‌برید؟",
        "options": [
            "خیلی زیاد، جزئیات برایم جذاب هستند",
            "تا حدی، اگر لازم باشد انجام می‌دهم",
            "نه خیلی، کارهای کلی را ترجیح می‌دهم",
            "اصلاً، از جزئیات خسته می‌شوم"
        ]
    },
    {
        "question": "اگر بخواهید یک پروژه علمی انتخاب کنید، کدام را انتخاب می‌کنید؟",
        "options": [
            "بررسی تاثیر تغذیه بر سلامت انسان",
            "ساخت یک مدل ساده از یک ماشین",
            "نوشتن یک داستان درباره آینده",
            "طراحی یک پوستر تبلیغاتی"
        ]
    },
    {
        "question": "آیا از حل مسائل مرتبط با سلامت و بیماری لذت می‌برید؟",
        "options": [
            "بله، همیشه برایم چالش‌برانگیز و جذاب است",
            "گاهی، اگر موضوع ساده باشد",
            "نه خیلی، بیشتر به مسائل دیگر علاقه دارم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    # علوم انسانی
    {
        "question": "آیا از گوش دادن به مشکلات دیگران و کمک به آن‌ها لذت می‌برید؟",
        "options": [
            "بله، همیشه آماده کمک هستم",
            "گاهی، اگر وقت داشته باشم",
            "نه خیلی، ترجیح می‌دهم خودم را مشغول کنم",
            "خیر، اصلاً علاقه‌ای ندارم"
        ]
    },
    {
        "question": "چقدر به درک احساسات و رفتارهای دیگران اهمیت می‌دهید؟",
        "options": [
            "خیلی زیاد، برایم بسیار مهم است",
            "تا حدی، گاهی به آن فکر می‌کنم",
            "نه خیلی، بیشتر به کارهای دیگر فکر می‌کنم",
            "اصلاً برایم مهم نیست"
        ]
    },
    {
        "question": "آیا از بحث درباره موضوعات فلسفی مثل معنای زندگی لذت می‌برید؟",
        "options": [
            "بله، این موضوعات برایم خیلی جذاب هستند",
            "گاهی، اگر بحث جالبی باشد",
            "نه خیلی، برایم خسته‌کننده است",
            "خیر، اصلاً به این موضوعات فکر نمی‌کنم"
        ]
    },
    {
        "question": "اگر دوست شما مشکلی داشته باشد، چه کار می‌کنید؟",
        "options": [
            "سعی می‌کنم به او گوش کنم و راه‌حل پیشنهاد بدهم",
            "کمی با او صحبت می‌کنم و بعد موضوع را عوض می‌کنم",
            "ترجیح می‌دهم دخالت نکنم",
            "اصلاً به مشکلات دیگران اهمیت نمی‌دهم"
        ]
    },
    {
        "question": "آیا از مطالعه درباره تاریخ و فرهنگ‌های مختلف لذت می‌برید؟",
        "options": [
            "بله، همیشه برایم جالب است",
            "گاهی، اگر موضوع جذابی باشد",
            "نه خیلی، بیشتر به چیزهای دیگر علاقه دارم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    {
        "question": "چقدر از نوشتن داستان یا مقاله لذت می‌برید؟",
        "options": [
            "خیلی زیاد، نوشتن برایم لذت‌بخش است",
            "تا حدی، اگر موضوع جالبی باشد",
            "نه خیلی، نوشتن برایم سخت است",
            "خیر، اصلاً علاقه‌ای به نوشتن ندارم"
        ]
    },
    {
        "question": "آیا دوست دارید در آینده به مردم کمک کنید تا مشکلات شخصی‌شان را حل کنند؟",
        "options": [
            "بله، این برایم خیلی مهم است",
            "شاید، بستگی به شرایط دارد",
            "نه خیلی، بیشتر به کار مستقل علاقه دارم",
            "خیر، اصلاً به این فکر نمی‌کنم"
        ]
    },
    {
        "question": "وقتی با یک نفر صحبت می‌کنید، چقدر به احساسات او توجه می‌کنید؟",
        "options": [
            "خیلی زیاد، همیشه سعی می‌کنم او را درک کنم",
            "تا حدی، گاهی به احساساتش توجه می‌کنم",
            "نه خیلی، بیشتر به موضوع صحبت فکر می‌کنم",
            "اصلاً به احساسات او اهمیت نمی‌دهم"
        ]
    },
    {
        "question": "آیا از یادگیری درباره نحوه یادگیری و رشد کودکان لذت می‌برید؟",
        "options": [
            "بله، این موضوع برایم خیلی جذاب است",
            "گاهی، اگر ساده توضیح داده شود",
            "نه خیلی، بیشتر به موضوعات دیگر علاقه دارم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    {
        "question": "آیا دوست دارید در مورد مسائل اجتماعی و چگونگی بهبود جامعه فکر کنید؟",
        "options": [
            "بله، همیشه به این موضوعات فکر می‌کنم",
            "گاهی، اگر موضوع جالبی باشد",
            "نه خیلی، بیشتر به مسائل شخصی فکر می‌کنم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    # ریاضی‌فیزیک و فنی‌حرفه‌ای
    {
        "question": "آیا از حل مسائل ریاضی و منطقی لذت می‌برید؟",
        "options": [
            "بله، همیشه برایم چالش‌برانگیز و جذاب است",
            "گاهی، اگر مسئله ساده باشد",
            "نه خیلی، بیشتر به مسائل دیگر علاقه دارم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    {
        "question": "آیا از کار با کامپیوتر و یادگیری برنامه‌نویسی لذت می‌برید؟",
        "options": [
            "بله، خیلی برایم جذاب است",
            "تا حدی، اگر ساده باشد",
            "نه خیلی، بیشتر به کارهای دیگر علاقه دارم",
            "خیر، اصلاً علاقه‌ای ندارم"
        ]
    },
    {
        "question": "اگر بخواهید چیزی بسازید، کدام را انتخاب می‌کنید؟",
        "options": [
            "یک مدل ساده از یک پل یا ساختمان",
            "یک نقاشی یا پوستر زیبا",
            "یک داستان یا شعر",
            "ترجیح می‌دهم چیزی نسازم"
        ]
    },
    {
        "question": "چقدر از کار با ابزار و وسایل فنی لذت می‌برید؟",
        "options": [
            "خیلی زیاد، همیشه برایم جذاب است",
            "تا حدی، اگر لازم باشد انجام می‌دهم",
            "نه خیلی، کارهای دیگر را ترجیح می‌دهم",
            "خیر، اصلاً علاقه‌ای ندارم"
        ]
    },
    {
        "question": "آیا از یادگیری درباره نحوه کار ماشین‌ها و دستگاه‌ها لذت می‌برید؟",
        "options": [
            "بله، همیشه برایم جالب است",
            "گاهی، اگر موضوع جذابی باشد",
            "نه خیلی، بیشتر به چیزهای دیگر علاقه دارم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    {
        "question": "وقتی یک دستگاه خراب می‌شود، چه واکنشی نشان می‌دهید؟",
        "options": [
            "کنجکاو می‌شوم و دوست دارم آن را تعمیر کنم",
            "کمی نگاه می‌کنم، اما کاری نمی‌کنم",
            "برایم مهم نیست، از یکی دیگر استفاده می‌کنم",
            "ترجیح می‌دهم یکی دیگر آن را درست کند"
        ]
    },
    {
        "question": "آیا دوست دارید در آینده شغلی داشته باشید که با طراحی و ساخت چیزهای جدید مرتبط باشد؟",
        "options": [
            "بله، این برایم خیلی جذاب است",
            "شاید، بستگی به شرایط دارد",
            "نه خیلی، بیشتر به کارهای دیگر علاقه دارم",
            "خیر، اصلاً به این فکر نمی‌کنم"
        ]
    },
    {
        "question": "چقدر از حل پازل‌ها و بازی‌های فکری لذت می‌برید؟",
        "options": [
            "خیلی زیاد، همیشه برایم سرگرم‌کننده است",
            "تا حدی، اگر ساده باشد",
            "نه خیلی، کارهای دیگر را ترجیح می‌دهم",
            "خیر، اصلاً علاقه‌ای ندارم"
        ]
    },
    {
        "question": "آیا از یادگیری درباره فناوری‌های جدید مثل هوش مصنوعی لذت می‌برید؟",
        "options": [
            "بله، خیلی برایم جذاب است",
            "گاهی، اگر ساده توضیح داده شود",
            "نه خیلی، بیشتر به موضوعات دیگر علاقه دارم",
            "خیر، اصلاً به این موضوعات علاقه ندارم"
        ]
    },
    {
        "question": "اگر بخواهید یک پروژه انتخاب کنید، کدام را انتخاب می‌کنید؟",
        "options": [
            "طراحی یک اپلیکیشن ساده",
            "نوشتن یک مقاله درباره یک موضوع اجتماعی",
            "انجام یک آزمایش زیست‌شناسی",
            "برگزاری یک برنامه گروهی"
        ]
    }
]

MBTI_DESCRIPTIONS = {
    "ESTJ": {
        "title": "ESTJ - مدیر اجرایی", 
        "nickname": "مدیر", 
        "description": "شما یک رهبر طبیعی هستید که به نظم، ساختار و کارایی اهمیت می‌دهید. با قاطعیت تصمیم می‌گیرید و دیگران را به سمت اهداف مشترک هدایت می‌کنید. برای شما سنت‌ها، قوانین و مسئولیت‌پذیری بسیار اهمیت دارد.", 
        "strengths": "رهبری قوی و کاریزماتیک, سازمان‌دهی و برنامه‌ریزی عالی, تعهد قوی به وظایف و مسئولیت‌ها, صداقت و صراحت در برخوردها, توانایی مدیریت پروژه‌های بزرگ و پیچیده", 
        "weaknesses": "گاهی بیش از حد سخت‌گیر و انتقادی, انعطاف‌پذیری کم در برابر تغییرات ناگهانی, تمرکز بیش از حد بر قوانین و مقررات, ممکن است به احساسات دیگران توجه کافی نکند"
    },
    "ISTJ": {
        "title": "ISTJ - لجستیک‌دان", 
        "nickname": "بازرس", 
        "description": "شما فردی وظیفه‌شناس، قابل اعتماد و دقیق هستید. به تفصیل، نظم و روش‌های آزمایش شده اهمیت می‌دهید. برای شما ثبات، امنیت و پیروی از اصول مهم است.", 
        "strengths": "وظیفه‌شناسی و مسئولیت‌پذیری بالا, دقت فوق‌العاده در جزئیات, قابلیت اطمینان در همه شرایط, وفاداری و پایبندی به تعهدات, برنامه‌ریزی دقیق و عملی", 
        "weaknesses": "مقاومت شدید در برابر تغییرات, گاهی بیش از حد جدی و کمتر انعطاف‌پذیر, تمایل به چسبیدن به روش‌های سنتی, ممکن است در ابراز احساسات مشکل داشته باشد"
    },
    "ESFJ": {
        "title": "ESFJ - کنسول", 
        "nickname": "مراقب", 
        "description": "شما فردی گرم، مهربان و مراقب دیگران هستید. رفاه و خوشحالی اطرافیانتان برایتان بسیار مهم است. همیشه سعی می‌کنید محیطی هماهنگ و دوستانه ایجاد کنید.", 
        "strengths": "همدلی و درک عمیق احساسات دیگران, مهارت‌های اجتماعی و ارتباطی قوی, وفاداری و حمایت از دوستان و خانواده, توانایی ایجاد انگیزه و الهام در دیگران, سازمان‌دهی فعالیت‌های اجتماعی", 
        "weaknesses": "وابستگی زیاد به تأیید و قبول دیگران, حساسیت بالا نسبت به انتقاد, مشکل در پذیرش تغییرات سریع, گاهی بیش از حد نگران رضایت همه"
    },
    "ISFJ": {
        "title": "ISFJ - مدافع", 
        "nickname": "حامی", 
        "description": "شما فردی فداکار، مهربان و حمایت‌گر هستید. همیشه آماده کمک به دیگران و ایجاد محیطی امن و آرام برای اطرافیانتان هستید. ارزش‌های انسانی برایتان بسیار مهم است.", 
        "strengths": "فداکاری و از خودگذشتگی برای دیگران, دقت شگفت‌انگیز در جزئیات مهم, وفاداری عمیق به دوستان و خانواده, مهارت‌های عملی و کاربردی, توانایی گوش دادن فعال و همدلانه", 
        "weaknesses": "مقاومت در برابر تغییرات مهم, مشکل در بیان نیازها و خواسته‌های شخصی, حساسیت بیش از حد به انتقاد, تمایل به اجتناب از درگیری و تعارض"
    },
    "ENTJ": {
        "title": "ENTJ - فرمانده", 
        "nickname": "رهبر", 
        "description": "شما رهبری طبیعی و قدرتمند هستید که با اعتماد به نفس بالا و تفکر استراتژیک، دیگران را به سمت موفقیت هدایت می‌کنید. برای شما رسیدن به اهداف بلندمدت اولویت دارد.", 
        "strengths": "رهبری قاطع و الهام‌بخش, تفکر استراتژیک و بلندمدت, اعتماد به نفس بالا و قدرت تصمیم‌گیری, انگیزه و پشتکار فوق‌العاده, توانایی حل مسائل پیچیده و چالش‌برانگیز", 
        "weaknesses": "گاهی بیش از حد قاطع و سلطه‌گر, بی‌صبری با ناکارآمدی و کندی, کمتر توجه به احساسات و نیازهای عاطفی دیگران, تمرکز بیش از حد بر نتایج"
    },
    "INTJ": {
        "title": "INTJ - معمار", 
        "nickname": "مغز متفکر", 
        "description": "شما متفکری استراتژیک و آینده‌نگر هستید که عاشق ایده‌های نوآورانه و سیستم‌های پیچیده‌اید. همیشه به دنبال بهبود و کمال هستید و برای رسیدن به اهدافتان برنامه‌ریزی دقیق می‌کنید.", 
        "strengths": "تفکر استراتژیک و تحلیلی عمیق, استقلال قوی در فکر و عمل, خلاقیت و نوآوری در حل مسائل, تعهد قوی به اهداف شخصی, توانایی دیدن الگوها و ارتباطات پیچیده", 
        "weaknesses": "گاهی بیش از حد انتقادی و کمال‌طلب, مشکل در برقراری ارتباطات عاطفی عمیق, بی‌صبری با افرادی که سریع نمی‌فهمند, تمایل به انزوا در مواقع استرس"
    },
    "ENFJ": {
        "title": "ENFJ - قهرمان", 
        "nickname": "مربی", 
        "description": "شما رهبری الهام‌بخش هستید که به رشد و توسعه دیگران اهمیت می‌دهید. با کاریزما و همدلی بالا، محیطی مثبت ایجاد می‌کنید و دیگران را به بهترین نسخه خودشان تبدیل می‌کنید.", 
        "strengths": "همدلی عمیق و درک احساسات دیگران, مهارت‌های ارتباطی و سخنرانی فوق‌العاده, توانایی الهام‌بخشی و انگیزه‌دهی, کاریزما و جذابیت شخصی, سازمان‌دهی و رهبری تیم", 
        "weaknesses": "تمایل به نادیده گرفتن نیازهای شخصی, حساسیت زیاد به انتقاد و عدم تأیید, مشکل در تصمیم‌گیری‌های سخت و منطقی, نگرانی بیش از حد برای رضایت همه"
    },
    "INFJ": {
        "title": "INFJ - وکیل", 
        "nickname": "مشاور", 
        "description": "شما فردی ایده‌آلیست، بصیر و عمیقاً همدل هستید. به دنبال معنا و هدف در زندگی‌اید و می‌خواهید دنیا را به مکانی بهتر تبدیل کنید. بصیرت شما در درک انسان‌ها فوق‌العاده است.", 
        "strengths": "همدلی عمیق و درک شهودی دیگران, بصیرت و توانایی دیدن آینده, خلاقیت و تخیل قوی, تعهد قوی به ارزش‌ها و اعتقادات, توانایی گوش دادن و مشاوره عمیق", 
        "weaknesses": "بیش از حد ایده‌آلیستی و انتظارات بالا, مشکل در پذیرش انتقاد سازنده, تمایل به انزوا در مواقع استرس, کمال‌گرایی که گاهی مانع عمل می‌شود"
    },
    "ESTP": {
        "title": "ESTP - کارآفرین", 
        "nickname": "ماجراجو", 
        "description": "شما فردی پرانرژی، عمل‌گرا و عاشق زندگی هستید. در لحظه زندگی می‌کنید و از تجربیات جدید و هیجان‌انگیز لذت می‌برید. با انعطاف‌پذیری بالا سریع با شرایط جدید سازگار می‌شوید.", 
        "strengths": "انعطاف‌پذیری فوق‌العاده در شرایط مختلف, مهارت‌های عملی و کاربردی قوی, اعتماد به نفس و جسارت در عمل, توانایی حل سریع بحران‌ها و مشکلات, انرژی بالا و قدرت انگیزه‌دهی", 
        "weaknesses": "بی‌صبری با کارهای طولانی‌مدت, مشکل در برنامه‌ریزی بلندمدت و دقیق, گاهی بی‌توجه به جزئیات مهم, ممکن است بیش از حد ریسک‌پذیر باشد"
    },
    "ISTP": {
        "title": "ISTP - صنعت‌گر", 
        "nickname": "مکانیک", 
        "description": "شما تحلیل‌گری عملی هستید که عاشق کشف نحوه کارکرد چیزها هستید. با دست‌های ماهر و ذهن تحلیلی، مشکلات عملی را به بهترین شکل حل می‌کنید. استقلال برایتان بسیار مهم است.", 
        "strengths": "مهارت‌های فنی و عملی بی‌نظیر, استقلال قوی در فکر و عمل, انعطاف‌پذیری در حل مسائل, خونسردی و آرامش در شرایط بحرانی, توانایی تحلیل دقیق و منطقی", 
        "weaknesses": "تمایل به انزوا و کاهش تعاملات اجتماعی, مشکل در تعهدات طولانی‌مدت, کمتر توجه به احساسات خود و دیگران, گاهی بیش از حد محتاط در تصمیم‌گیری"
    },
    "ESFP": {
        "title": "ESFP - سرگرم‌کننده", 
        "nickname": "اجراکننده", 
        "description": "شما روح مهمانی هستید! پرانرژی، خودجوش و عاشق تعامل با دیگران. همیشه سعی می‌کنید لحظات شاد و به‌یادماندنی خلق کنید و دیگران را با انرژی مثبتتان متأثر می‌سازید.", 
        "strengths": "انرژی فوق‌العاده و قدرت شادی‌آفرینی, مهارت‌های اجتماعی طبیعی و جذاب, انعطاف‌پذیری بالا در شرایط مختلف, همدلی و درک احساسات دیگران, توانایی ایجاد جو مثبت و دوستانه", 
        "weaknesses": "مشکل در برنامه‌ریزی و تفکر بلندمدت, حساسیت زیاد به انتقاد و عدم پذیرش, گاهی بی‌توجه به جزئیات مهم, ممکن است بیش از حد تکانشی عمل کند"
    },
    "ISFP": {
        "title": "ISFP - ماجراجو", 
        "nickname": "هنرمند", 
        "description": "شما روحی هنرمند دارید و عمیقاً به زیبایی، هماهنگی و ارزش‌های انسانی اهمیت می‌دهید. آرام و مهربان هستید، اما دارای اعتقادات قوی و عمیق. آزادی بیان برایتان مهم است.", 
        "strengths": "خلاقیت و تخیل هنری قوی, همدلی عمیق و درک احساسات, انعطاف‌پذیری و سازگاری بالا, وفاداری عمیق به ارزش‌ها و باورها, توانایی ایجاد هماهنگی و زیبایی", 
        "weaknesses": "تمایل به انزوا و کاهش تعاملات اجتماعی, مشکل در برنامه‌ریزی و تصمیم‌گیری بلندمدت, حساسیت بالا نسبت به انتقاد, گاهی بیش از حد محتاط و ریسک‌گریز"
    },
    "ENTP": {
        "title": "ENTP - مبتکر", 
        "nickname": "مخترع", 
        "description": "شما نوآور و کنجکاوی که عاشق ایده‌های جدید و امکانات بی‌نهایت هستید. با هوش سریع و قدرت ابتکار، همیشه به دنبال راه‌های جدید و خلاقانه برای حل مسائل می‌گردید.", 
        "strengths": "خلاقیت و نوآوری بی‌نظیر, مهارت‌های کلامی و قدرت متقاعدسازی, انعطاف‌پذیری ذهنی فوق‌العاده, توانایی حل مسائل پیچیده, کنجکاوی و اشتیاق به یادگیری", 
        "weaknesses": "مشکل در تمرکز طولانی‌مدت روی یک موضوع, بی‌صبری با کارهای روتین و تکراری, گاهی بیش از حد بحث‌برانگیز و چالش‌کننده, مشکل در تعهد و پیگیری طولانی‌مدت"
    },
    "INTP": {
        "title": "INTP - متفکر", 
        "nickname": "فیلسوف", 
        "description": "شما متفکری عمیق و کنجکاو هستید که عاشق تحلیل، درک و کشف حقیقت‌اید. با ذهنی مستقل و منطقی، همیشه سعی می‌کنید سیستم‌ها و ایده‌ها را درک کنید و بهبود دهید.", 
        "strengths": "قدرت تحلیل و تفکر منطقی عمیق, خلاقیت در حل مسائل پیچیده, استقلال فکری و عدم تأثیرپذیری از دیگران, کنجکاوی بی‌پایان برای یادگیری, توانایی درک الگوها و سیستم‌های پیچیده", 
        "weaknesses": "تمایل به انزوا و کاهش تعاملات اجتماعی, مشکل در عملی کردن ایده‌ها, بی‌توجهی به جزئیات عملی و روزمره, گاهی بیش از حد انتقادی و کمال‌طلب"
    },
    "ENFP": {
        "title": "ENFP - مبارز", 
        "nickname": "الهام‌بخش", 
        "description": "شما روحی آزاد و پرشور هستید که عاشق امکانات انسانی و ایده‌های نو هستید. با انرژی و اشتیاق واقعی، دیگران را الهام می‌کنید و محیطی خلاق و پویا ایجاد می‌کنید.", 
        "strengths": "خلاقیت و تخیل فوق‌العاده, همدلی عمیق و درک انسان‌ها, انعطاف‌پذیری و سازگاری بالا, مهارت‌های ارتباطی طبیعی و جذاب, توانایی الهام‌بخشی و انگیزه‌دهی", 
        "weaknesses": "مشکل در تمرکز و تکمیل پروژه‌ها, حساسیت بالا نسبت به انتقاد, بی‌نظمی و مشکل در سازمان‌دهی, گاهی بیش از حد ایده‌آلیستی و غیرواقع‌بین"
    },
    "INFP": {
        "title": "INFP - میانجی", 
        "nickname": "شفابخش", 
        "description": "شما روحی عمیق و معنویت‌گرا دارید که به دنبال هدف و معنای واقعی در زندگی‌اید. با ارزش‌هایی قوی و عمیق، همیشه سعی می‌کنید دنیا را به مکانی بهتر و عدالت‌محورتر تبدیل کنید.", 
        "strengths": "همدلی عمیق و درک احساسات انسانی, خلاقیت هنری و ادبی بالا, وفاداری قوی به ارزش‌ها و اعتقادات, توانایی الهام‌بخشی از طریق آثار و کلمات, انعطاف‌پذیری و پذیرش تفاوت‌ها", 
        "weaknesses": "بیش از حد ایده‌آلیست و انتظارات بالا از خود و دیگران, مشکل در مواجهه با انتقاد مستقیم, تمایل قوی به انزوا در مواقع سخت, دشواری در تصمیم‌گیری‌های منطقی و عملی"
    }
}

ACADEMIC_FIELD_DESCRIPTIONS = {
    "علوم تجربی": {
        "title": "علوم تجربی",
        "description": "شما به مطالعه طبیعت، سلامت و موجودات زنده علاقه‌مند هستید. این شاخه برای افرادی مناسب است که به تحقیق علمی، پزشکی و محیط‌زیست علاقه دارند.",
        "strengths": [
            "دقت بالا در مشاهده و تحلیل داده‌های علمی",
            "علاقه شدید به حل مسائل مرتبط با سلامت و زیست‌شناسی",
            "کنجکاوی علمی و تمایل به کشف ناشناخته‌ها",
            "مهارت در انجام آزمایش‌های دقیق و کنترل‌شده",
            "همدلی و تمایل به کمک به دیگران در بهبود سلامتی"
        ],
        "weaknesses": [
            "ممکن است در برابر کارهای غیرعلمی یا انتزاعی کم‌صبر باشید",
            "تمرکز بیش از حد روی جزئیات و غفلت از کلیات",
            "نیاز به محیط‌های منظم که ممکن است انعطاف‌پذیری را کاهش دهد",
            "گاهی اوقات دشواری در ارتباط با موضوعات غیرعلمی",
            "استرس در مواجهه با موقعیت‌های غیرقابل پیش‌بینی"
        ]
    },
    "علوم انسانی": {
        "title": "علوم انسانی",
        "description": "شما به درک رفتارها، احساسات و مسائل اجتماعی علاقه‌مند هستید. این شاخه برای افرادی مناسب است که به روانشناسی، مشاوره و فلسفه علاقه دارند.",
        "strengths": [
            "همدلی بالا و توانایی درک عمیق احساسات دیگران",
            "مهارت‌های ارتباطی قوی در گفت‌وگو و مشاوره",
            "خلاقیت در حل مسائل اجتماعی و انسانی",
            "تفکر انتقادی در تحلیل موضوعات فلسفی و فرهنگی",
            "انعطاف‌پذیری در مواجهه با دیدگاه‌های متنوع"
        ],
        "weaknesses": [
            "ممکن است در برابر مسائل منطقی و فنی کم‌علاقه باشید",
            "تصمیم‌گیری احساسی که گاهی منطق را تحت‌الشعاع قرار می‌دهد",
            "نیاز به زمان زیاد برای تحلیل عمیق موضوعات",
            "حساسیت بیش از حد به انتقاد یا اختلاف‌نظر",
            "دشواری در مدیریت وظایف با ساختار سختگیرانه"
        ]
    },
    "ریاضی‌فیزیک و فنی‌حرفه‌ای": {
        "title": "ریاضی‌فیزیک و فنی‌حرفه‌ای",
        "description": "شما به حل مسائل منطقی و ساخت چیزهای جدید علاقه‌مند هستید. این شاخه برای افرادی مناسب است که به مهندسی، برنامه‌نویسی و طراحی علاقه دارند.",
        "strengths": [
            "مهارت‌های منطقی و تحلیلی قوی در حل مسائل پیچیده",
            "خلاقیت در طراحی و ساخت ابزارها و فناوری‌های جدید",
            "تمرکز بالا در انجام کارهای فنی و دقیق",
            "علاقه به یادگیری فناوری‌های نوین مانند هوش مصنوعی",
            "استقلال در انجام پروژه‌های فنی"
        ],
        "weaknesses": [
            "ممکن است در تعاملات اجتماعی کم‌صبر باشید",
            "تمرکز بیش از حد روی مسائل فنی و غفلت از جنبه‌های انسانی",
            "نیاز به زمان زیاد برای یادگیری مهارت‌های جدید",
            "دشواری در کار گروهی با افراد غیرفنی",
            "گاهی اوقات مقاومت در برابر تغییر روش‌های سنتی"
        ]
    }
}

# Available tests
AVAILABLE_TESTS = {
    "mbti_personality": {
        "title": "آزمون شخصیت‌شناسی MBTI",
        "description": "کشف تیپ شخصیتی خود بر اساس استاندارد بین‌المللی MBTI",
        "duration": "15-20 دقیقه",
        "questions_count": 15,
        "icon": "🧠"
    },
    "academic_guidance": {
        "title": "آزمون هدایت تحصیلی",
        "description": "کشف شاخه‌های تحصیلی مناسب بر اساس علایق و ویژگی‌های شخصیتی",
        "duration": "10-15 دقیقه", 
        "questions_count": 30,
        "icon": "🎓"
    }
}

# --- Gemini Tools & Model ---
determine_mbti_tool = [{
    "function_declarations": [{
        "name": "determine_mbti",
        "description": "Determines the MBTI personality type.",
        "parameters": {
            "type": "OBJECT", "properties": {
                "extraversion_introversion": {"type": "STRING", "enum": ["E", "I"]},
                "sensing_intuition": {"type": "STRING", "enum": ["S", "N"]},
                "thinking_feeling": {"type": "STRING", "enum": ["T", "F"]},
                "judging_perceiving": {"type": "STRING", "enum": ["J", "P"]}
            }, "required": ["extraversion_introversion", "sensing_intuition", "thinking_feeling", "judging_perceiving"]
        }
    }]
}]

estimate_all_eight_preferences_tool = [{
    "function_declarations": [{
        "name": "estimate_all_eight_mbti_preferences",
        "description": "Estimates the percentage for each of the eight MBTI preferences. Each opposing pair should sum to 100.",
        "parameters": {
            "type": "OBJECT", "properties": {
                "extraversion_percentage": {"type": "NUMBER", "description": "Extraversion percentage (0-100)."},
                "introversion_percentage": {"type": "NUMBER", "description": "Introversion percentage (0-100)."},
                "sensing_percentage": {"type": "NUMBER", "description": "Sensing percentage (0-100)."},
                "intuition_percentage": {"type": "NUMBER", "description": "Intuition percentage (0-100)."},
                "thinking_percentage": {"type": "NUMBER", "description": "Thinking percentage (0-100)."},
                "feeling_percentage": {"type": "NUMBER", "description": "Feeling percentage (0-100)."},
                "judging_percentage": {"type": "NUMBER", "description": "Judging percentage (0-100)."},
                "perceiving_percentage": {"type": "NUMBER", "description": "Perceiving percentage (0-100)."}
            },
            "required": [
                "extraversion_percentage", "introversion_percentage", "sensing_percentage", "intuition_percentage",
                "thinking_percentage", "feeling_percentage", "judging_percentage", "perceiving_percentage"
            ]
        }
    }]
}]

# تعریف ابزارهای Gemini برای هدایت تحصیلی
determine_academic_fields_tool = [{
    "function_declarations": [{
        "name": "determine_academic_fields",
        "description": "Determines the most suitable academic fields based on user answers.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "recommended_fields": {
                    "type": "ARRAY",
                    "items": {
                        "type": "STRING",
                        "enum": ["علوم تجربی", "علوم انسانی", "ریاضی‌فیزیک و فنی‌حرفه‌ای"]
                    },
                    "description": "List of recommended academic fields based on user preferences."
                }
            },
            "required": ["recommended_fields"]
        }
    }]
}]

estimate_field_preferences_tool = [{
    "function_declarations": [{
        "name": "estimate_field_preferences",
        "description": "Estimates the percentage preference for each academic field. Percentages should sum to 100.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "experimental_sciences_percentage": {"type": "NUMBER", "description": "Percentage preference for Experimental Sciences (0-100)."},
                "humanities_percentage": {"type": "NUMBER", "description": "Percentage preference for Humanities (0-100)."},
                "math_technical_percentage": {"type": "NUMBER", "description": "Percentage preference for Math-Physics/Technical (0-100)."}
            },
            "required": ["experimental_sciences_percentage", "humanities_percentage", "math_technical_percentage"]
        }
    }]
}]

prioritize_fields_tool = [{
    "function_declarations": [{
        "name": "prioritize_academic_fields",
        "description": "Prioritizes the recommended academic fields based on user answers.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prioritized_fields": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "field": {
                                "type": "STRING",
                                "enum": ["علوم تجربی", "علوم انسانی", "ریاضی‌فیزیک و فنی‌حرفه‌ای"]
                            },
                            "priority": {
                                "type": "INTEGER",
                                "description": "Priority rank (1=first, 2=second, 3=third)"
                            }
                        },
                        "required": ["field", "priority"]
                    },
                    "description": "List of academic fields with their priority ranks."
                }
            },
            "required": ["prioritized_fields"]
        }
    }]
}]

generate_traits_tool = [{
    "function_declarations": [{
        "name": "generate_field_traits",
        "description": "Generates strengths and weaknesses for academic fields based on user answers.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "fields_traits": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "field": {
                                "type": "STRING",
                                "enum": ["علوم تجربی", "علوم انسانی", "ریاضی‌فیزیک و فنی‌حرفه‌ای"]
                            },
                            "strengths": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                                "description": "List of at least 5 strengths for the field"
                            },
                            "weaknesses": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                                "description": "List of at least 5 weaknesses for the field"
                            }
                        },
                        "required": ["field", "strengths", "weaknesses"]
                    },
                    "description": "Traits for each recommended academic field."
                }
            },
            "required": ["fields_traits"]
        }
    }]
}]

# تعریف مدل Gemini برای تولید ویژگی‌ها
gemini_model_for_traits = genai.GenerativeModel(model_name="gemini-1-5-flash", tools=generate_traits_tool)

# تعریف مدل Gemini برای اولویت‌بندی
gemini_model_for_priority = genai.GenerativeModel(model_name="gemini-1-5-flash", tools=prioritize_fields_tool)
# تعریف مدل‌های Gemini برای آزمون هدایت تحصیلی
gemini_model_for_academic_fields = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=determine_academic_fields_tool)
gemini_model_for_field_percentages = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=estimate_field_preferences_tool)

gemini_model_for_type = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=determine_mbti_tool)
gemini_model_for_all_percentages = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=estimate_all_eight_preferences_tool)

# --- Async Gemini Interaction Functions ---
async def determine_mbti_from_gemini_args(extraversion_introversion, sensing_intuition, thinking_feeling, judging_perceiving):
    """Determine MBTI type from Gemini arguments"""
    return {"mbti_type": f"{extraversion_introversion}{sensing_intuition}{thinking_feeling}{judging_perceiving}"}

async def create_prompt_for_mbti(questions: List[Dict[str, Any]], answers: List[int], age_range: str) -> str:
    """Create prompt for MBTI analysis asynchronously"""
    age_context = {
        "13-15": "این کاربر در رده سنی 13-15 سال قرار دارد. لطفاً در تحلیل این موضوع را در نظر بگیرید که پاسخ‌ها مربوط به یک نوجوان است و ویژگی‌های رشدی این سن را در نظر بگیرید.",
        "15-18": "این کاربر در رده سنی 15-18 سال قرار دارد. لطفاً در تحلیل این موضوع را در نظر بگیرید که پاسخ‌ها مربوط به یک نوجوان-جوان است که در حال گذار به بزرگسالی است."
    }
    
    prompt = f"بر اساس پاسخ‌های کاربر به سوالات زیر، تیپ شخصیتی MBTI او را تشخیص بده.\n\n"
    prompt += f"اطلاعات مهم: {age_context.get(age_range, '')}\n\n"
    prompt += "پاسخ‌ها به صورت عددی (1=گزینه الف، 2=گزینه ب، 3=گزینه ج، 4=گزینه د) ارائه شده‌اند.\n"
    prompt += "سپس تابع 'determine_mbti' را با چهار بعد شخصیتی (E/I, S/N, T/F, J/P) فراخوانی کن.\n\n"
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q['question'])}\n"
        prompt += f"**گزینه‌ها:**\n"
        for j, opt in enumerate(q['options'], 1):
            prompt += f"{j}. {html.escape(opt)}\n"
        prompt += f"**پاسخ کاربر:** {a}\n\n"
    
    return prompt

async def get_mbti_type_from_gemini(questions: List[str], answers: List[str], age_range: str) -> str:
    """Get MBTI type from Gemini asynchronously"""
    try:
        prompt = await create_prompt_for_mbti(questions, answers, age_range)
        response = await gemini_model_for_type.generate_content_async(prompt)
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "determine_mbti":
                    args = dict(part.function_call.args)
                    result = await determine_mbti_from_gemini_args(**args)
                    return result["mbti_type"]
        
        logger.warning("Gemini تابع determine_mbti را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return "خطا: عدم تشخیص نوع MBTI"
    except Exception as e:
        logger.error(f"خطا در get_mbti_type_from_gemini: {e}")
        return f"خطا در پردازش نوع: {str(e)[:100]}"

async def create_prompt_for_all_percentages(questions: List[Dict[str, Any]], answers: List[int], mbti_type: str, age_range: str) -> str:
    """Create prompt for percentage analysis asynchronously"""
    age_context = {
        "13-15": "این کاربر در رده سنی 13-15 سال قرار دارد. لطفاً در تحلیل درصدها، ویژگی‌های رشدی این سن و عدم کامل بودن شخصیت در این سن را در نظر بگیرید.",
        "15-18": "این کاربر در رده سنی 15-18 سال قرار دارد. لطفاً در تحلیل درصدها، این را در نظر بگیرید که شخصیت او در حال تکمیل است و ممکن است تغییرات بیشتری داشته باشد."
    }
    
    prompt = (
        f"کاربر به سوالات زیر پاسخ داده و تیپ شخصیتی او {mbti_type} تشخیص داده شده است. "
        f"{age_context.get(age_range, '')} "
        "لطفاً بر اساس پاسخ‌ها، برای هر یک از هشت ترجیح MBTI (برونگرایی، درونگرایی، حسی، شهودی، منطقی، احساسی، قضاوتی، ادراکی) "
        "یک درصد تخمینی (بین ۰ تا ۱۰۰) ارائه دهید. "
        "مجموع درصدها برای هر زوج مخالف (مثلاً برونگرایی + درونگرایی) باید ۱۰۰ شود. "
        "پاسخ‌ها به صورت عددی (1=گزینه الف، 2=گزینه ب، 3=گزینه ج، 4=گزینه د) ارائه شده‌اند. "
        "سپس تابع 'estimate_all_eight_mbti_preferences' را با این هشت درصد فراخوانی کن.\n\n"
    )
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q['question'])}\n"
        prompt += f"**گزینه‌ها:**\n"
        for j, opt in enumerate(q['options'], 1):
            prompt += f"{j}. {html.escape(opt)}\n"
        prompt += f"**پاسخ کاربر:** {a}\n\n"
    
    return prompt

async def get_all_eight_mbti_percentages_from_gemini(questions: List[str], answers: List[str], mbti_type: str, age_range: str) -> Optional[Dict[str, int]]:
    """Get all eight MBTI percentages from Gemini asynchronously"""
    if "خطا" in mbti_type:
        return None
        
    try:
        prompt = await create_prompt_for_all_percentages(questions, answers, mbti_type, age_range)
        response = await gemini_model_for_all_percentages.generate_content_async(prompt)
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "estimate_all_eight_mbti_preferences":
                    percentages_from_gemini = dict(part.function_call.args)
                    
                    valid_percentages_int = {}
                    pairs_to_check = [
                        ("extraversion_percentage", "introversion_percentage"),
                        ("sensing_percentage", "intuition_percentage"),
                        ("thinking_percentage", "feeling_percentage"),
                        ("judging_percentage", "perceiving_percentage")
                    ]
                    all_pairs_valid = True

                    for p1_key, p2_key in pairs_to_check:
                        p1_val_orig = percentages_from_gemini.get(p1_key)
                        p2_val_orig = percentages_from_gemini.get(p2_key)

                        if not (isinstance(p1_val_orig, (int, float)) and isinstance(p2_val_orig, (int, float))):
                            logger.warning(f"مقدار غیرعددی یا گمشده برای زوج: {p1_key}={p1_val_orig}, {p2_key}={p2_val_orig}")
                            all_pairs_valid = False
                            break
                        
                        p1_int = round(float(p1_val_orig))
                        p2_int = round(float(p2_val_orig))

                        if not (0 <= p1_int <= 100 and 0 <= p2_int <= 100):
                            logger.warning(f"درصد خارج از محدوده برای زوج: {p1_key}={p1_int}, {p2_key}={p2_int}")
                            all_pairs_valid = False
                            break
                        
                        if not (98 <= (p1_int + p2_int) <= 102):
                            logger.warning(f"مجموع زوج درصدها برای {p1_key} و {p2_key} برابر ۱۰۰ نیست: {p1_int} + {p2_int} = {p1_int + p2_int}")
                            all_pairs_valid = False 
                            break
                        
                        valid_percentages_int[p1_key] = p1_int
                        valid_percentages_int[p2_key] = p2_int
                    
                    if all_pairs_valid and len(valid_percentages_int) == 8:
                        return valid_percentages_int
                    else:
                        logger.warning(f"اعتبارسنجی درصدها ناموفق بود. داده دریافتی: {percentages_from_gemini}")
                        return None 
        
        logger.warning("Gemini تابع estimate_all_eight_mbti_preferences را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return None
    except Exception as e:
        logger.error(f"خطا در get_all_eight_mbti_percentages_from_gemini: {e}")
        return None

async def get_reasoning_for_mbti(mbti_type: str, answers: List[str]) -> str:
    """Get reasoning for MBTI result asynchronously"""
    base_reasoning = {
        "ESTJ": "نظم، مسئولیت‌پذیری و تصمیم‌گیری منطقی", 
        "ISTJ": "دقت، وظیفه‌شناسی و پایبندی به قوانین",
        "ESFJ": "همدلی، مهارت‌های اجتماعی و توجه به نیازهای دیگران", 
        "ISFJ": "فداکاری، دقت و حمایت از دیگران",
        "ENTJ": "رهبری قاطع، تفکر استراتژیک و هدف‌محوری", 
        "INTJ": "تفکر استراتژیک، استقلال و خلاقیت",
        "ENFJ": "همدلی، کاریزما و الهام‌بخشی به دیگران", 
        "INFJ": "همدلی عمیق، بصیرت و تعهد به ارزش‌ها",
        "ESTP": "انعطاف‌پذیری، عمل‌گرایی و انرژی بالا", 
        "ISTP": "مهارت‌های فنی، استقلال و خونسردی در بحران",
        "ESFP": "انرژی بالا، مهارت‌های اجتماعی و زندگی در لحظه", 
        "ISFP": "خلاقیت، همدلی و وفاداری به ارزش‌ها",
        "ENTP": "خلاقیت، کنجکاوی و نوآوری", 
        "INTP": "تحلیل عمیق، کنجکاوی و استقلال",
        "ENFP": "خلاقیت، همدلی و اشتیاق به امکانات جدید", 
        "INFP": "همدلی، خلاقیت و ارزش‌های شخصی قوی"
    }
    
    default_reason = "ویژگی‌های کلیدی متناسب با پاسخ‌های شما"
    reason = base_reasoning.get(mbti_type, default_reason)
    
    if answers and answers[0]:
        reason += f"؛ به عنوان مثال، در پاسخ اول خود شما گزینه '{html.escape(answers[0][:70])}' را انتخاب کردید که نشان‌دهنده این ویژگی‌هاست."
    
    return reason

async def determine_academic_fields_from_gemini_args(recommended_fields):
    """Determine academic fields from Gemini arguments"""
    return {"recommended_fields": recommended_fields}

async def create_prompt_for_academic_fields(questions: List[Dict[str, Any]], answers: List[int]) -> str:
    """Create prompt for academic field analysis"""
    prompt = (
        "بر اساس پاسخ‌های کاربر به سوالات زیر، شاخه‌های تحصیلی مناسب (علوم تجربی، علوم انسانی، ریاضی‌فیزیک و فنی‌حرفه‌ای) را تشخیص دهید. "
        "پاسخ‌ها به صورت عددی (1=گزینه الف، 2=گزینه ب، 3=گزینه ج، 4=گزینه د) ارائه شده‌اند. "
        "سپس تابع 'determine_academic_fields' را با لیست شاخه‌های پیشنهادی فراخوانی کنید.\n\n"
    )
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q['question'])}\n"
        prompt += f"**گزینه‌ها:**\n"
        for j, opt in enumerate(q['options'], 1):
            prompt += f"{j}. {html.escape(opt)}\n"
        prompt += f"**پاسخ کاربر:** {a}\n\n"
    
    return prompt

async def get_academic_fields_from_gemini(questions: List[Dict[str, Any]], answers: List[int]) -> List[str]:
    """Get recommended academic fields from Gemini"""
    try:
        prompt = await create_prompt_for_academic_fields(questions, answers)
        response = await gemini_model_for_academic_fields.generate_content_async(prompt)
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "determine_academic_fields":
                    args = dict(part.function_call.args)
                    return args.get("recommended_fields", [])
        
        logger.warning("Gemini تابع determine_academic_fields را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return ["خطا: عدم تشخیص شاخه‌های تحصیلی"]
    except Exception as e:
        logger.error(f"خطا در get_academic_fields_from_gemini: {e}")
        return [f"خطا در پردازش: {str(e)[:100]}"]

async def create_prompt_for_field_percentages(questions: List[Dict[str, Any]], answers: List[int], recommended_fields: List[str]) -> str:
    """Create prompt for field preference percentages"""
    prompt = (
        f"کاربر به سوالات زیر پاسخ داده و شاخه‌های تحصیلی پیشنهادی او {recommended_fields} هستند. "
        "بر اساس پاسخ‌ها، درصد تمایل به هر شاخه تحصیلی (علوم تجربی، علوم انسانی، ریاضی‌فیزیک و فنی‌حرفه‌ای) را تخمین بزنید. "
        "مجموع درصدها باید ۱۰۰ باشد. "
        "پاسخ‌ها به صورت عددی (1=گزینه الف، 2=گزینه ب، 3=گزینه ج، 4=گزینه د) ارائه شده‌اند. "
        "سپس تابع 'estimate_field_preferences' را با این درصدها فراخوانی کنید.\n\n"
    )
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q['question'])}\n"
        prompt += f"**گزینه‌ها:**\n"
        for j, opt in enumerate(q['options'], 1):
            prompt += f"{j}. {html.escape(opt)}\n"
        prompt += f"**پاسخ کاربر:** {a}\n\n"
    
    return prompt

async def create_prompt_for_field_priority(questions: List[Dict[str, Any]], answers: List[int], recommended_fields: List[str]) -> str:
    """Create prompt for prioritizing academic fields"""
    prompt = (
        f"بر اساس پاسخ‌های کاربر به سوالات زیر و شاخه‌های پیشنهادی {recommended_fields}، شاخه‌های تحصیلی را اولویت‌بندی کنید (اولویت 1=بالاترین، 2=متوسط، 3=پایین‌ترین). "
        "پاسخ‌ها به صورت عددی (1=گزینه الف، 2=گزینه ب، 3=گزینه ج، 4=گزینه د) ارائه شده‌اند. "
        "سپس تابع 'prioritize_academic_fields' را با لیست شاخه‌ها و اولویت‌های آن‌ها فراخوانی کنید.\n\n"
    )
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q['question'])}\n"
        prompt += f"**گزینه‌ها:**\n"
        for j, opt in enumerate(q['options'], 1):
            prompt += f"{j}. {html.escape(opt)}\n"
        prompt += f"**پاسخ کاربر:** {a}\n\n"
    
    return prompt

async def get_field_priorities_from_gemini(questions: List[Dict[str, Any]], answers: List[int], recommended_fields: List[str]) -> List[Dict[str, Any]]:
    """Get prioritized academic fields from Gemini"""
    try:
        prompt = await create_prompt_for_field_priority(questions, answers, recommended_fields)
        response = await gemini_model_for_priority.generate_content_async(prompt)
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "prioritize_academic_fields":
                    prioritized_fields = []
                    for item in part.function_call.args.get("prioritized_fields", []):
                        field = item.get("field")
                        priority = int(item.get("priority", 0))
                        if field in ["علوم تجربی", "علوم انسانی", "ریاضی‌فیزیک و فنی‌حرفه‌ای"] and priority in [1, 2, 3]:
                            prioritized_fields.append({"field": field, "priority": priority})
                    return prioritized_fields
        
        logger.warning("Gemini تابع prioritize_academic_fields را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return []
    except Exception as e:
        logger.error(f"خطا در get_field_priorities_from_gemini: {e}")
        return []

async def get_field_percentages_from_gemini(questions: List[Dict[str, Any]], answers: List[int], recommended_fields: List[str]) -> Optional[Dict[str, int]]:
    """Get academic field preference percentages from Gemini"""
    try:
        prompt = await create_prompt_for_field_percentages(questions, answers, recommended_fields)
        response = await gemini_model_for_field_percentages.generate_content_async(prompt)
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "estimate_field_preferences":
                    percentages = dict(part.function_call.args)
                    
                    valid_percentages = {}
                    keys = ["experimental_sciences_percentage", "humanities_percentage", "math_technical_percentage"]
                    total = 0
                    
                    for key in keys:
                        value = percentages.get(key)
                        if not isinstance(value, (int, float)) or value < 0 or value > 100:
                            logger.warning(f"مقدار نامعتبر برای {key}: {value}")
                            return None
                        valid_percentages[key] = round(float(value))
                        total += valid_percentages[key]
                    
                    if 98 <= total <= 102:
                        return valid_percentages
                    else:
                        logger.warning(f"مجموع درصدها برابر ۱۰۰ نیست: {total}")
                        return None
        
        logger.warning("Gemini تابع estimate_field_preferences را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return None
    except Exception as e:
        logger.error(f"خطا در get_field_percentages_from_gemini: {e}")
        return None

async def create_prompt_for_traits(questions: List[Dict[str, Any]], answers: List[int], recommended_fields: List[str]) -> str:
    """Create prompt for generating field-specific traits"""
    prompt = (
        f"بر اساس پاسخ‌های کاربر به سوالات زیر و شاخه‌های پیشنهادی {recommended_fields}، برای هر شاخه حداقل 5 نقطه قوت و 5 نقطه ضعف تولید کنید. "
        "نقاط قوت و ضعف باید مرتبط با پاسخ‌های کاربر و ویژگی‌های شاخه باشند. "
        "پاسخ‌ها به صورت عددی (1=گزینه الف، 2=گزینه ب، 3=گزینه ج، 4=گزینه د) ارائه شده‌اند. "
        "سپس تابع 'generate_field_traits' را با ویژگی‌های تولیدشده فراخوانی کنید.\n\n"
    )
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q['question'])}\n"
        prompt += f"**گزینه‌ها:**\n"
        for j, opt in enumerate(q['options'], 1):
            prompt += f"{j}. {html.escape(opt)}\n"
        prompt += f"**پاسخ کاربر:** {a}\n\n"
    
    return prompt

async def get_field_traits_from_gemini(questions: List[Dict[str, Any]], answers: List[int], recommended_fields: List[str]) -> Dict[str, Dict[str, List[str]]]:
    """Get field-specific strengths and weaknesses from Gemini"""
    try:
        prompt = await create_prompt_for_traits(questions, answers, recommended_fields)
        response = await gemini_model_for_traits.generate_content_async(prompt)
        
        traits_dict = {}
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "generate_field_traits":
                    for item in part.function_call.args.get("fields_traits", []):
                        field = item.get("field")
                        strengths = item.get("strengths", [])
                        weaknesses = item.get("weaknesses", [])
                        if field in recommended_fields and len(strengths) >= 5 and len(weaknesses) >= 5:
                            traits_dict[field] = {"strengths": strengths, "weaknesses": weaknesses}
        
        # اگر Gemini ویژگی‌ها را تولید نکرد، از دیکشنری پیش‌فرض استفاده شود
        for field in recommended_fields:
            if field not in traits_dict:
                traits_dict[field] = {
                    "strengths": ACADEMIC_FIELD_DESCRIPTIONS[field]["strengths"],
                    "weaknesses": ACADEMIC_FIELD_DESCRIPTIONS[field]["weaknesses"]
                }
        
        return traits_dict
    except Exception as e:
        logger.error(f"خطا در get_field_traits_from_gemini: {e}")
        # در صورت خطا، از دیکشنری پیش‌فرض استفاده شود
        return {field: {
            "strengths": ACADEMIC_FIELD_DESCRIPTIONS[field]["strengths"],
            "weaknesses": ACADEMIC_FIELD_DESCRIPTIONS[field]["weaknesses"]
        } for field in recommended_fields}

async def generate_html_mbti_report(test_result_id: str, mbti_type: str, user_questions: List[Dict[str, Any]], user_answers: List[int], all_percentages: Optional[Dict[str, int]]) -> str:
    """Generate HTML MBTI report asynchronously"""
    info = MBTI_DESCRIPTIONS.get(mbti_type)
    if not info:
        return f"<h1>خطا</h1><p>تیپ شخصیتی '{html.escape(mbti_type)}' یافت نشد.</p>"

    # Convert numeric answers to their corresponding option texts
    answer_texts = []
    for i, answer in enumerate(user_answers):
        if i < len(user_questions) and answer in [1, 2, 3, 4]:
            answer_texts.append(user_questions[i]['options'][answer-1])
        else:
            answer_texts.append("پاسخ نامعتبر")

    # Get reasoning text with text answers
    reasoning_text = await get_reasoning_for_mbti(mbti_type, answer_texts)
    
    # Prepare data for charts if percentages are available
    pie_chart_data_js = "null"
    bar_charts_data_js = "null"
    radar_chart_data_js = "null"

    if all_percentages:
        # Pie Chart Data (Dominant Preferences)
        ei_pref_char = mbti_type[0]
        sn_pref_char = mbti_type[1]
        tf_pref_char = mbti_type[2]
        jp_pref_char = mbti_type[3]

        pie_data_values = [
            all_percentages.get(f"{'extraversion' if ei_pref_char == 'E' else 'introversion'}_percentage", 50),
            all_percentages.get(f"{'sensing' if sn_pref_char == 'S' else 'intuition'}_percentage", 50),
            all_percentages.get(f"{'thinking' if tf_pref_char == 'T' else 'feeling'}_percentage", 50),
            all_percentages.get(f"{'judging' if jp_pref_char == 'J' else 'perceiving'}_percentage", 50),
        ]
        pie_labels = [
            f"{ei_pref_char} ({'برونگرایی' if ei_pref_char == 'E' else 'درونگرایی'})",
            f"{sn_pref_char} ({'حسی' if sn_pref_char == 'S' else 'شهودی'})",
            f"{tf_pref_char} ({'منطقی' if tf_pref_char == 'T' else 'احساسی'})",
            f"{jp_pref_char} ({'قضاوتی' if jp_pref_char == 'J' else 'ادراکی'})",
        ]
        pie_chart_data_js = json.dumps({"labels": pie_labels, "data": pie_data_values, "mbtiType": mbti_type})

        # Bar Charts Data (Each Dichotomy)
        bar_charts_data = {
            "energy": {
                "labels": ["برونگرایی (E)", "درونگرایی (I)"],
                "data": [all_percentages.get("extraversion_percentage",0), all_percentages.get("introversion_percentage",0)]
            },
            "information": {
                "labels": ["حسی (S)", "شهودی (N)"],
                "data": [all_percentages.get("sensing_percentage",0), all_percentages.get("intuition_percentage",0)]
            },
            "decision": {
                "labels": ["منطقی (T)", "احساسی (F)"],
                "data": [all_percentages.get("thinking_percentage",0), all_percentages.get("feeling_percentage",0)]
            },
            "lifestyle": {
                "labels": ["قضاوتی (J)", "ادراکی (P)"],
                "data": [all_percentages.get("judging_percentage",0), all_percentages.get("perceiving_percentage",0)]
            }
        }
        bar_charts_data_js = json.dumps(bar_charts_data)

        # Radar Chart Data (All 8 preferences)
        radar_labels = ["برونگرایی", "درونگرایی", "حسی", "شهودی", "منطقی", "احساسی", "قضاوتی", "ادراکی"]
        radar_data_values = [
            all_percentages.get("extraversion_percentage",0), all_percentages.get("introversion_percentage",0),
            all_percentages.get("sensing_percentage",0), all_percentages.get("intuition_percentage",0),
            all_percentages.get("thinking_percentage",0), all_percentages.get("feeling_percentage",0),
            all_percentages.get("judging_percentage",0), all_percentages.get("perceiving_percentage",0)
        ]
        radar_chart_data_js = json.dumps({"labels": radar_labels, "data": radar_data_values, "mbtiType": mbti_type})

    charts_html_section = ""
    if all_percentages:
        charts_html_section = f"""
        <div class="charts-dashboard">
            <h2>📊 تحلیل گرافیکی ترجیحات شما</h2>
            <p>نمودارهای زیر تخمینی از میزان تمایل شما به هر یک از ترجیحات شخصیتی‌تان را بر اساس تحلیل پاسخ‌های شما توسط الگوریتم نشان می‌دهند.</p>
            
            <div class="chart-row">
                <div class="chart-container pie-chart-container">
                    <h3>نمودار کلی ترجیحات غالب</h3>
                    <canvas id="mbtiPieChart"></canvas>
                </div>
                <div class="chart-container radar-chart-container">
                    <h3>پروفایل کلی ۸ ترجیح</h3>
                    <canvas id="mbtiRadarChart"></canvas>
                </div>
            </div>

            <h3>تفکیک دوگانگی‌ها</h3>
            <div class="chart-row bar-charts-grid">
                <div class="chart-container bar-chart-container">
                    <canvas id="energyBarChart"></canvas>
                </div>
                <div class="chart-container bar-chart-container">
                    <canvas id="informationBarChart"></canvas>
                </div>
                <div class="chart-container bar-chart-container">
                    <canvas id="decisionBarChart"></canvas>
                </div>
                <div class="chart-container bar-chart-container">
                    <canvas id="lifestyleBarChart"></canvas>
                </div>
            </div>
        </div>
        """

    # Generate answers display section
    answers_html = ""
    for i, (q, a) in enumerate(zip(user_questions, user_answers)):
        if a in [1, 2, 3, 4]:
            selected_option = q['options'][a-1]
            answers_html += f"""
            <div class="answer-item">
                <h4>سوال {i+1}: {html.escape(q['question'])}</h4>
                <p><strong>پاسخ شما:</strong> {html.escape(selected_option)}</p>
            </div>
            """

    html_content = f"""
    <!-- Main Content -->
    <div class="main-content">
        <div class="mbti-header">
            <div class="mbti-badge">{html.escape(mbti_type)}</div>
            <h1>{html.escape(info['title'])}</h1>
            <p class="mbti-nickname">"{html.escape(info['nickname'])}"</p>
        </div>
        
        <div class="description-section">
            <h2>✨ توضیحات تیپ شخصیتی شما</h2>
            <p class="description-text">{html.escape(info['description'])}</p>
        </div>
        
        <div class="traits-grid">
            <div class="trait-card strengths">
                <h3>💪 نقاط قوت شما</h3>
                <ul>{''.join(f'<li>{html.escape(strength.strip())}</li>' for strength in info['strengths'].split(','))}</ul>
            </div>
            <div class="trait-card weaknesses">
                <h3>🎯 نکات قابل توجه</h3>
                <ul>{''.join(f'<li>{html.escape(weakness.strip())}</li>' for weakness in info['weaknesses'].split(','))}</ul>
            </div>
        </div>
        
        <div class="reasoning-section">
            <h3>🤔 چرا این تیپ برای شما انتخاب شد؟</h3>
            <p>بر اساس تحلیل پاسخ‌های شما، ویژگی‌هایی که بیشترین تطابق را با شخصیت شما داشتند عبارتند از: <strong>{reasoning_text}</strong></p>
        </div>
        
        <div class="answers-section">
            <h2>📋 پاسخ‌های شما</h2>
            {answers_html}
        </div>
    </div>
    
    <!-- Charts Section -->
    {charts_html_section}
    
    <script>
        const pieChartData = {pie_chart_data_js};
        const barChartsData = {bar_charts_data_js};
        const radarChartData = {radar_chart_data_js};
    </script>
    """
    return html_content

async def generate_html_academic_report(test_result_id: str, recommended_fields: List[str], user_questions: List[Dict[str, Any]], user_answers: List[int], field_percentages: Optional[Dict[str, int]], field_traits: Dict[str, Dict[str, List[str]]], prioritized_fields: List[Dict[str, Any]]) -> str:
    """Generate HTML academic guidance report"""
    if not recommended_fields or "خطا" in recommended_fields[0]:
        return f"<h1>خطا</h1><p>{html.escape(recommended_fields[0])}</p>"

    # Convert numeric answers to text
    answer_texts = [user_questions[i]['options'][a-1] if i < len(user_questions) and a in [1, 2, 3, 4] else "پاسخ نامعتبر" for i, a in enumerate(user_answers)]
    
    # Generate field descriptions with traits
    fields_html = ""
    for field in recommended_fields:
        info = ACADEMIC_FIELD_DESCRIPTIONS.get(field, {})
        traits = field_traits.get(field, {"strengths": [], "weaknesses": []})
        fields_html += f"""
        <div class="field-section">
            <h2>{html.escape(info['title'])}</h2>
            <p>{html.escape(info['description'])}</p>
            <div class="traits-grid">
                <div class="trait-card strengths">
                    <h3>💪 نقاط قوت</h3>
                    <ul>{''.join(f'<li>{html.escape(strength)}</li>' for strength in traits['strengths'])}</ul>
                </div>
                <div class="trait-card weaknesses">
                    <h3>🎯 نکات قابل توجه</h3>
                    <ul>{''.join(f'<li>{html.escape(weakness)}</li>' for weakness in traits['weaknesses'])}</ul>
                </div>
            </div>
        </div>
        """

    # Generate priority table
    priority_html = "<h2>📋 اولویت‌بندی شاخه‌های پیشنهادی</h2><table class='priority-table'><tr><th>شاخه</th><th>اولویت</th></tr>"
    for item in prioritized_fields:
        priority_html += f"<tr><td>{html.escape(item['field'])}</td><td>{item['priority']}</td></tr>"
    priority_html += "</table>"

    # Generate charts if percentages are available
    charts_html_section = ""
    if field_percentages:
        pie_labels = ["علوم تجربی", "علوم انسانی", "ریاضی‌فیزیک و فنی‌حرفه‌ای"]
        pie_data_values = [
            field_percentages.get("experimental_sciences_percentage", 33),
            field_percentages.get("humanities_percentage", 33),
            field_percentages.get("math_technical_percentage", 34)
        ]
        pie_chart_data_js = json.dumps({"labels": pie_labels, "data": pie_data_values})

        bar_chart_data = {
            "fields": {
                "labels": pie_labels,
                "data": pie_data_values
            }
        }
        bar_chart_data_js = json.dumps(bar_chart_data)

        charts_html_section = f"""
        <div class="charts-dashboard">
            <h2>📊 تحلیل گرافیکی ترجیحات شما</h2>
            <p>نمودار زیر میزان تمایل شما به هر شاخه تحصیلی را بر اساس تحلیل پاسخ‌های شما نشان می‌دهد.</p>
            <div class="chart-row">
                <div class="chart-container bar-chart-container">
                    <h3>تفکیک شاخه‌ها</h3>
                    <canvas id="academicBarChart"></canvas>
                </div>
            </div>
        </div>
        """

    # Generate answers display
    answers_html = "".join(
        f"""
        <div class="answer-item">
            <h4>سوال {i+1}: {html.escape(q['question'])}</h4>
            <p><strong>پاسخ شما:</strong> {html.escape(answer_texts[i])}</p>
        </div>
        """ for i, q in enumerate(user_questions)
    )

    html_content = f"""
    <div class="main-content">
        <div class="academic-header">
            <h1>نتایج هدایت تحصیلی شما</h1>
            <p class="academic-subtitle">شاخه‌های پیشنهادی: {html.escape(', '.join(recommended_fields))}</p>
        </div>
        {fields_html}
        {priority_html}
        <div class="answers-section">
            <h2>📋 پاسخ‌های شما</h2>
            {answers_html}
        </div>
    </div>
    {charts_html_section}
    <style>
        .priority-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        .priority-table th, .priority-table td {{ border: 1px solid #ddd; padding: 8px; text-align: center; }}
        .priority-table th {{ background-color: #f2f2f2; }}
    </style>
    <script>
        const academicBarChartData = {bar_chart_data_js};
    </script>
    """
    return html_content

# --- FastAPI Endpoints (All Async) ---
@app.get("/", response_class=HTMLResponse)
async def get_home_page(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)  
async def get_register_page(request: Request, error: str = None, user=Depends(get_current_user)):
    # اگر کاربر از قبل وارد شده باشد، به داشبورد هدایت شود
    if user:
        return RedirectResponse(url="/quiz", status_code=303)

    error_message = None
    if error == "phone_exists":
        error_message = "این شماره همراه قبلاً ثبت شده است."
    elif error == "passwords_mismatch":
        error_message = "رمزهای وارد شده مطابقت ندارند."
    elif error == "weak_password":
        error_message = "رمز باید حداقل 8 کاراکتر باشد."
    elif error == "registration_failed":
        error_message = "خطا در ثبت نام. لطفاً دوباره تلاش کنید."
        
    return templates.TemplateResponse("register.html", {"request": request, "error_message": error_message})

@app.post("/register")
async def handle_registration(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    age_range: str = Form(...)
):
    # Validation
    if password != confirm_password:
        return RedirectResponse(url="/register?error=passwords_mismatch", status_code=303)
    
    if len(password) < 8:
        return RedirectResponse(url="/register?error=weak_password", status_code=303)
    
    # Check if phone exists (async)
    try:
        all_users = await db_manager.execute_query("SELECT encrypted_phone FROM users", fetch=True)
        
        is_duplicate = False
        for user in all_users:
            decrypted_phone = await decrypt_data(user['encrypted_phone'])
            if decrypted_phone == phone:
                is_duplicate = True
                break

        if is_duplicate:
            return RedirectResponse(url="/register?error=phone_exists", status_code=303)

        # Create user (all async operations)
        user_id = str(uuid4())
        encrypted_fname = await encrypt_data(first_name)
        encrypted_lname = await encrypt_data(last_name)
        encrypted_phone_val = await encrypt_data(phone)
        hashed_password = await hash_password(password)
        encrypted_password = await encrypt_data(hashed_password)
        
        await db_manager.execute_query("""
            INSERT INTO users (id, encrypted_first_name, encrypted_last_name, encrypted_phone, encrypted_password, age_range, registration_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, encrypted_fname, encrypted_lname, encrypted_phone_val, encrypted_password, age_range, datetime.now().isoformat()))
        
        # Create session
        session_id = await create_session(user_id)
        
        # Redirect to quiz dashboard
        response = RedirectResponse(url="/quiz", status_code=303)
        response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=86400)  # 24 hours
        return response
        
    except Exception as e:
        logger.error(f"خطا در ایجاد کاربر: {e}")
        return RedirectResponse(url="/register?error=registration_failed", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def get_login_page(request: Request, error: str = None, user=Depends(get_current_user)):
    # اگر کاربر از قبل وارد شده باشد، به داشبورد هدایت شود
    if user:
        return RedirectResponse(url="/quiz", status_code=303)

    error_message = None
    if error == "invalid_credentials":
        error_message = "شماره همراه یا رمز اشتباه است."
    elif error == "login_failed":
        error_message = "خطا در ورود. لطفاً دوباره تلاش کنید."
        
    return templates.TemplateResponse("login.html", {"request": request, "error_message": error_message})

@app.post("/login")
async def handle_login(
    request: Request,
    phone: str = Form(...),
    password: str = Form(...)
):
    try:
        # Find user by phone (async)
        all_users = await db_manager.execute_query("SELECT id, encrypted_phone, encrypted_password FROM users", fetch=True)
        
        user_id = None
        for user in all_users:
            decrypted_phone = await decrypt_data(user['encrypted_phone'])
            if decrypted_phone == phone:
                stored_password_hash = await decrypt_data(user['encrypted_password'])
                if await verify_password(password, stored_password_hash):
                    user_id = user['id']
                    break
        
        if not user_id:
            return RedirectResponse(url="/login?error=invalid_credentials", status_code=303)
        
        # Create session
        session_id = await create_session(user_id)
        
        response = RedirectResponse(url="/quiz", status_code=303)
        response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=86400)
        return response
        
    except Exception as e:
        logger.error(f"خطا در ورود: {e}")
        return RedirectResponse(url="/login?error=login_failed", status_code=303)

@app.get("/karshenasanlogin", response_class=HTMLResponse)
async def get_advisor_login_page(request: Request, error: str = None, advisor=Depends(get_current_advisor)):
    """Get advisor login page"""
    if advisor:
        return RedirectResponse(url="/advisor/show_data", status_code=303)
        
    error_message = None
    if error == "invalid_credentials":
        error_message = "نام کاربری یا رمز عبور اشتباه است."
    elif error == "login_failed":
        error_message = "خطا در ورود. لطفاً دوباره تلاش کنید."
        
    return templates.TemplateResponse("advisor_login.html", {"request": request, "error_message": error_message})

@app.post("/karshenasanlogin")
async def handle_advisor_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Handle advisor login"""
    try:
        # بررسی نام کاربری و رمز عبور در دیتابیس مشاوران
        advisors = await advisor_db_manager.execute_query(
            "SELECT username, password FROM advisors WHERE username = ?",
            (username,), fetch=True
        )
        
        if not advisors:
            logger.warning(f"لاگین ناموفق: نام کاربری {username} یافت نشد")
            return RedirectResponse(url="/karshenasanlogin?error=invalid_credentials", status_code=303)
        
        advisor = advisors[0]
        if not await verify_password(password, advisor['password']):
            logger.warning(f"لاگین ناموفق: رمز عبور برای {username} اشتباه است")
            return RedirectResponse(url="/karshenasanlogin?error=invalid_credentials", status_code=303)
        
        # ایجاد سشن برای مشاور
        session_id = await create_advisor_session(username)
        logger.info(f"لاگین موفق برای مشاور: {username}")
        
        response = RedirectResponse(url="/advisor/show_data", status_code=303)
        response.set_cookie(key="advisor_session_id", value=session_id, httponly=True, max_age=86400)
        return response
        
    except Exception as e:
        logger.error(f"خطا در ورود مشاور: {e}")
        return RedirectResponse(url="/karshenasanlogin?error=login_failed", status_code=303)

@app.get("/advisor/show_data", response_class=HTMLResponse)
async def show_advisor_data_page(request: Request, advisor = Depends(require_advisor_login)):
    """Show all data page for advisors"""
    try:
        rows = await db_manager.execute_query("""
            SELECT u.id, u.encrypted_first_name, u.encrypted_last_name, u.encrypted_phone, u.age_range, u.registration_time,
                   tr.test_name, tr.encrypted_answers, tr.mbti_result, tr.encrypted_mbti_percentages, tr.analysis_time
            FROM users u
            LEFT JOIN test_results tr ON u.id = tr.user_id
            ORDER BY u.registration_time DESC
        """, fetch=True)

        users_list = []
        
        # Process all user data concurrently for better performance
        async def process_user_row(row_data):
            user_dict = dict(row_data)
            
            # Decrypt user data with proper null handling
            user_dict['first_name'] = await decrypt_data(user_dict.pop('encrypted_first_name')) or ""
            user_dict['last_name'] = await decrypt_data(user_dict.pop('encrypted_last_name')) or ""
            user_dict['phone'] = await decrypt_data(user_dict.pop('encrypted_phone')) or ""
            
            # Ensure mbti_result is never None
            if user_dict.get('mbti_result') is None:
                user_dict['mbti_result'] = ""
            
            # Process answers
            decrypted_answers_list = []
            encrypted_ans_blob = user_dict.pop('encrypted_answers')
            if encrypted_ans_blob:
                dec_ans_json = await decrypt_data(encrypted_ans_blob)
                if dec_ans_json and dec_ans_json != "خطا در رمزگشایی":
                    try: 
                        decrypted_answers_list = json.loads(dec_ans_json)
                    except json.JSONDecodeError: 
                        decrypted_answers_list = ["خطا در پارس JSON پاسخ‌ها"]
                elif dec_ans_json == "خطا در رمزگشایی": 
                    decrypted_answers_list = ["خطا در رمزگشایی پاسخ‌ها"]
            user_dict['answers'] = decrypted_answers_list

            # Process percentages
            decrypted_percentages_dict = None
            encrypted_perc_blob = user_dict.pop('encrypted_mbti_percentages')
            if encrypted_perc_blob:
                dec_perc_json = await decrypt_data(encrypted_perc_blob)
                if dec_perc_json and dec_perc_json != "خطا در رمزگشایی":
                    try: 
                        decrypted_percentages_dict = json.loads(dec_perc_json)
                    except json.JSONDecodeError: 
                        decrypted_percentages_dict = {"error": "خطا در پارس JSON درصدها"}
                elif dec_perc_json == "خطا در رمزگشایی": 
                    decrypted_percentages_dict = {"error": "خطا در رمزگشایی درصدها"}
            user_dict['mbti_percentages'] = decrypted_percentages_dict
                
            return user_dict

        # Process all rows concurrently
        if rows:
            users_list = await asyncio.gather(*[process_user_row(row) for row in rows])
        
        return templates.TemplateResponse("advisor_data.html", {
            "request": request, 
            "users_data": users_list, 
            "advisor": advisor
        })
        
    except Exception as e:
        logger.error(f"خطا در نمایش داده‌ها: {e}")
        raise HTTPException(status_code=500, detail="خطا در بارگذاری داده‌ها")

@app.get("/advisor/logout")
async def advisor_logout(advisor_session_id: str = Cookie(None)):
    """Logout advisor"""
    if advisor_session_id:
        await db_manager.execute_query("DELETE FROM advisor_sessions WHERE session_id = ?", (advisor_session_id,))
    
    response = RedirectResponse(url="/karshenasanlogin", status_code=303)
    response.delete_cookie(key="advisor_session_id")
    return response

@app.get("/logout")
async def logout(session_id: str = Cookie(None)):
    # Delete session from database (async)
    if session_id:
        await db_manager.execute_query("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="session_id")
    return response

@app.get("/quiz", response_class=HTMLResponse)
async def get_quiz_dashboard(request: Request, user = Depends(require_login)):
    return templates.TemplateResponse("quiz_dashboard.html", {
        "request": request, 
        "user": user,
        "available_tests": AVAILABLE_TESTS
    })

@app.get("/test/{test_id}", response_class=HTMLResponse)
async def get_test_page(request: Request, test_id: str, user=Depends(require_login)):
    if test_id not in AVAILABLE_TESTS:
        raise HTTPException(status_code=404, detail="تست یافت نشد")
    
    # بررسی آیا کاربر قبلاً تست را انجام داده است
    result = await db_manager.execute_query("""
        SELECT id FROM test_results 
        WHERE user_id = ? AND test_name = ?
    """, (user['id'], AVAILABLE_TESTS[test_id]['title']), fetch=True)
    
    if result:
        return RedirectResponse(url="/my-results?error=already_taken", status_code=303)
    
    if test_id == "mbti_personality":
        age_range = user['age_range']
        if age_range not in QUESTIONS_DB:
            raise HTTPException(status_code=400, detail="رده سنی نامعتبر است.")
        
        questions_for_age = QUESTIONS_DB[age_range]
        return templates.TemplateResponse("questions.html", {
            "request": request, 
            "test_id": test_id,
            "questions": questions_for_age,
            "user": user
        })
    
    elif test_id == "academic_guidance":
        return templates.TemplateResponse("questions.html", {
            "request": request, 
            "test_id": test_id,
            "questions": ACADEMIC_GUIDANCE_QUESTIONS,
            "user": user
        })
    
    raise HTTPException(status_code=404, detail="تست پیدا نشد")

async def handle_test_submission(request: Request, test_id: str, user = Depends(require_login)):
    if test_id not in AVAILABLE_TESTS:
        raise HTTPException(status_code=404, detail="تست یافت نشد")
    
    if test_id == "mbti_personality":
        return await handle_mbti_test_submission(request, user)
    
    raise HTTPException(status_code=404, detail="تست پیدا نشد")

async def handle_academic_test_submission(request: Request, user: Dict):
    """Handle academic guidance test submission"""
    form_data = await request.form()
    questions = ACADEMIC_GUIDANCE_QUESTIONS
    answers = []
    
    for i in range(len(questions)):
        answer_key = f"answer_{i}"
        answer_value = form_data.get(answer_key)
        if not answer_value or not answer_value.strip():
            return RedirectResponse(
                url=f"/test/academic_guidance?error=incomplete&question={i+1}",
                status_code=303
            )
        try:
            answer_num = int(answer_value)
            if answer_num not in [1, 2, 3, 4]:
                raise ValueError("پاسخ نامعتبر")
            answers.append(answer_num)
        except ValueError:
            return RedirectResponse(
                url=f"/test/academic_guidance?error=invalid_answer&question={i+1}",
                status_code=303
            )
    
    # Process with AI
    recommended_fields = await get_academic_fields_from_gemini(questions, answers)
    field_percentages = None
    field_traits = None
    prioritized_fields = []
    encrypted_percentages_blob = None
    encrypted_traits_blob = None
    encrypted_priorities_blob = None
    
    if not any("خطا" in field for field in recommended_fields):
        field_percentages = await get_field_percentages_from_gemini(questions, answers, recommended_fields)
        field_traits = await get_field_traits_from_gemini(questions, answers, recommended_fields)
        prioritized_fields = await get_field_priorities_from_gemini(questions, answers, recommended_fields)
        
        if field_percentages:
            encrypted_percentages_blob = await encrypt_data(json.dumps(field_percentages))
        if field_traits:
            encrypted_traits_blob = await encrypt_data(json.dumps(field_traits))
        if prioritized_fields:
            encrypted_priorities_blob = await encrypt_data(json.dumps(prioritized_fields))
    
    # Save to database
    test_result_id = str(uuid4())
    encrypted_answers_json_blob = await encrypt_data(json.dumps(answers))
    
    try:
        await db_manager.execute_query("""
            INSERT INTO test_results (id, user_id, test_name, encrypted_answers, mbti_result, encrypted_mbti_percentages, analysis_time, encrypted_traits, encrypted_priorities)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (test_result_id, user['id'], "آزمون هدایت تحصیلی", encrypted_answers_json_blob, ", ".join(recommended_fields), encrypted_percentages_blob, datetime.now().isoformat(), encrypted_traits_blob, encrypted_priorities_blob))
    except Exception as e:
        logger.error(f"خطا در ذخیره نتیجه تست: {e}")
    
    return RedirectResponse(url=f"/result/{user['phone']}/{test_result_id}", status_code=303)

@app.post("/submit_test/{test_id}")
async def handle_test_submission(request: Request, test_id: str, user=Depends(require_login)):
    if test_id not in AVAILABLE_TESTS:
        raise HTTPException(status_code=404, detail="تست یافت نشد")
    
    if test_id == "mbti_personality":
        return await handle_mbti_test_submission(request, user)
    elif test_id == "academic_guidance":
        return await handle_academic_test_submission(request, user)
    
    raise HTTPException(status_code=404, detail="تست پیدا نشد")

@app.get("/advisor/user_result/{user_id}", response_class=HTMLResponse)
async def show_user_result_to_advisor(request: Request, user_id: str, advisor=Depends(require_advisor_login)):
    try:
        user_data = await db_manager.execute_query("""
            SELECT u.id, u.encrypted_first_name, u.encrypted_last_name, u.encrypted_phone, u.age_range, u.registration_time,
                   tr.id as test_id, tr.test_name, tr.encrypted_answers, tr.mbti_result, tr.encrypted_mbti_percentages, tr.analysis_time
            FROM users u
            LEFT JOIN test_results tr ON u.id = tr.user_id
            WHERE u.id = ?
            ORDER BY tr.analysis_time DESC
        """, (user_id,), fetch=True)
        
        if not user_data:
            raise HTTPException(status_code=404, detail="کاربر یافت نشد")
        
        processed_user = {}
        test_results = []
        
        for row in user_data:
            if not processed_user:
                processed_user = {
                    'id': row['id'],
                    'first_name': await decrypt_data(row['encrypted_first_name']) or "",
                    'last_name': await decrypt_data(row['encrypted_last_name']) or "",
                    'phone': await decrypt_data(row['encrypted_phone']) or "",
                    'age_range': row['age_range'],
                    'registration_time': row['registration_time']
                }
            
            if row['test_id']:
                answers = []
                if row['encrypted_answers']:
                    decrypted_answers = await decrypt_data(row['encrypted_answers'])
                    if decrypted_answers and decrypted_answers != "خطا در رمزگشایی":
                        try:
                            answers = json.loads(decrypted_answers)
                            if row['test_name'] == "آزمون شخصیت‌شناسی MBTI":
                                questions_for_age = QUESTIONS_DB.get(processed_user['age_range'], [])
                                answers = [
                                    questions_for_age[i]['options'][a-1] if i < len(questions_for_age) and a in [1, 2, 3, 4] else "پاسخ نامعتبر"
                                    for i, a in enumerate(answers)
                                ]
                            else:  # آزمون هدایت تحصیلی
                                answers = [
                                    ACADEMIC_GUIDANCE_QUESTIONS[i]['options'][a-1] if i < len(ACADEMIC_GUIDANCE_QUESTIONS) and a in [1, 2, 3, 4] else "پاسخ نامعتبر"
                                    for i, a in enumerate(answers)
                                ]
                        except (json.JSONDecodeError, IndexError):
                            answers = ["خطا در پارس پاسخ‌ها"]
                
                percentages = None
                if row['encrypted_mbti_percentages']:
                    decrypted_percentages = await decrypt_data(row['encrypted_mbti_percentages'])
                    if decrypted_percentages and decrypted_percentages != "خطا در رمزگشایی":
                        try:
                            percentages = json.loads(decrypted_percentages)
                        except json.JSONDecodeError:
                            percentages = {"error": "خطا در پارس درصدها"}
                
                test_results.append({
                    'test_id': row['test_id'],
                    'test_name': row['test_name'],
                    'mbti_result': row['mbti_result'] or "",
                    'analysis_time': row['analysis_time'],
                    'answers': answers,
                    'percentages': percentages
                })
        
        return templates.TemplateResponse("advisor_user_result.html", {
            "request": request,
            "user": processed_user,
            "test_results": test_results,
            "advisor": advisor,
            "MBTI_DESCRIPTIONS": MBTI_DESCRIPTIONS,
            "ACADEMIC_FIELD_DESCRIPTIONS": ACADEMIC_FIELD_DESCRIPTIONS,
            "QUESTIONS_DB": QUESTIONS_DB,
            "ACADEMIC_GUIDANCE_QUESTIONS": ACADEMIC_GUIDANCE_QUESTIONS
        })
        
    except Exception as e:
        logger.error(f"خطا در نمایش نتیجه کاربر: {e}")
        raise HTTPException(status_code=500, detail="خطا در بارگذاری اطلاعات کاربر")

async def handle_mbti_test_submission(request: Request, user: Dict):
    """Handle MBTI test submission asynchronously"""
    form_data = await request.form()
    age_range = user['age_range']

    if age_range not in QUESTIONS_DB:
        raise HTTPException(status_code=400, detail="رده سنی نامعتبر برای سوالات.")

    questions_for_user = QUESTIONS_DB[age_range]
    answers = []
    
    for i in range(len(questions_for_user)):
        answer_key = f"answer_{i}"
        answer_value = form_data.get(answer_key)
        if not answer_value or not answer_value.strip():
            return RedirectResponse(
                url=f"/test/mbti_personality?error=incomplete&question={i+1}",
                status_code=303
            )
        try:
            answer_num = int(answer_value)  # پاسخ به صورت عدد (1-4)
            if answer_num not in [1, 2, 3, 4]:
                raise ValueError("پاسخ نامعتبر")
            answers.append(answer_num)
        except ValueError:
            return RedirectResponse(
                url=f"/test/mbti_personality?error=invalid_answer&question={i+1}",
                status_code=303
            )
    
    # Process with AI (already async)
    mbti_type_result = await get_mbti_type_from_gemini(questions_for_user, answers, age_range)
    
    all_mbti_percentages_dict = None
    encrypted_percentages_blob = None
    if not "خطا" in mbti_type_result:
        all_mbti_percentages_dict = await get_all_eight_mbti_percentages_from_gemini(questions_for_user, answers, mbti_type_result, age_range)
        if all_mbti_percentages_dict:
            encrypted_percentages_blob = await encrypt_data(json.dumps(all_mbti_percentages_dict))

    # Save to database (async)
    test_result_id = str(uuid4())
    encrypted_answers_json_blob = await encrypt_data(json.dumps(answers))

    try:
        await db_manager.execute_query("""
            INSERT INTO test_results (id, user_id, test_name, encrypted_answers, mbti_result, encrypted_mbti_percentages, analysis_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (test_result_id, user['id'], "آزمون شخصیت‌شناسی MBTI", encrypted_answers_json_blob, mbti_type_result, encrypted_percentages_blob, datetime.now().isoformat()))
    except Exception as e:
        logger.error(f"خطا در ذخیره نتیجه تست: {e}")

    return RedirectResponse(url=f"/result/{user['phone']}/{test_result_id}", status_code=303)

@app.get("/result/{phone}/{test_result_id}", response_class=HTMLResponse)
async def get_test_result(request: Request, phone: str, test_result_id: str, user=Depends(require_login)):
    if user['phone'] != phone:
        raise HTTPException(status_code=403, detail="شما اجازه دسترسی به این نتیجه را ندارید")
    
    result = await db_manager.execute_query("""
        SELECT tr.*, u.age_range FROM test_results tr
        JOIN users u ON tr.user_id = u.id
        WHERE tr.id = ? AND tr.user_id = ?
    """, (test_result_id, user['id']), fetch=True)
    
    if not result:
        raise HTTPException(status_code=404, detail="نتیجه تست یافت نشد")
    
    result = result[0]
    test_name = result['test_name']
    
    answers = []
    percentages = None
    traits = None
    priorities = []
    
    if result['encrypted_answers']:
        decrypted_answers = await decrypt_data(result['encrypted_answers'])
        if decrypted_answers and decrypted_answers != "خطا در رمزگشایی":
            try:
                answers = json.loads(decrypted_answers)
            except json.JSONDecodeError:
                pass
    
    if result['encrypted_mbti_percentages']:
        decrypted_percentages = await decrypt_data(result['encrypted_mbti_percentages'])
        if decrypted_percentages and decrypted_percentages != "خطا در رمزگشایی":
            try:
                percentages = json.loads(decrypted_percentages)
            except json.JSONDecodeError:
                pass
    
    if result['encrypted_traits']:
        decrypted_traits = await decrypt_data(result['encrypted_traits'])
        if decrypted_traits and decrypted_traits != "خطا در رمزگشایی":
            try:
                traits = json.loads(decrypted_traits)
            except json.JSONDecodeError:
                pass
    
    if result['encrypted_priorities']:
        decrypted_priorities = await decrypt_data(result['encrypted_priorities'])
        if decrypted_priorities and decrypted_priorities != "خطا در رمزگشایی":
            try:
                priorities = json.loads(decrypted_priorities)
            except json.JSONDecodeError:
                pass
    
    if test_name == "آزمون شخصیت‌شناسی MBTI":
        if "خطا" in result['mbti_result']:
            report_html = f"<h1>خطا در تحلیل</h1><p>{html.escape(result['mbti_result'])}</p>"
        else:
            questions_for_age = QUESTIONS_DB.get(result['age_range'], [])
            report_html = await generate_html_mbti_report(test_result_id, result['mbti_result'], questions_for_age, answers, percentages)
    elif test_name == "آزمون هدایت تحصیلی":
        recommended_fields = result['mbti_result'].split(", ") if result['mbti_result'] else []
        report_html = await generate_html_academic_report(test_result_id, recommended_fields, ACADEMIC_GUIDANCE_QUESTIONS, answers, percentages, traits or {}, priorities)
    else:
        report_html = "<h1>خطا</h1><p>نوع تست نامعتبر است.</p>"

    return templates.TemplateResponse("result.html", {
        "request": request,
        "test_result_id": test_result_id,
        "report_content": report_html,
        "mbti_type": result['mbti_result'],
        "user": user
    })

@app.get("/my-results", response_class=HTMLResponse)
async def get_user_results(request: Request, user=Depends(require_login)):
    # دریافت نتایج کاربر از دیتابیس
    results = await db_manager.execute_query("""
        SELECT id, test_name, mbti_result, analysis_time 
        FROM test_results 
        WHERE user_id = ?
    """, (user['id'],), fetch=True)
    
    # تبدیل نتایج به لیست دیکشنری‌ها
    results = [
        {
            "id": row[0],
            "test_name": row[1],
            "mbti_result": row[2],
            "analysis_time": row[3]
        } for row in results
    ] if results else []
    
    return templates.TemplateResponse("my_results.html", {
        "request": request,
        "user": user,
        "results": results
    })


@app.post("/submit_answers")
async def handle_form_submission(request: Request, user = Depends(require_login)):
    """Handle form submission from questions page"""
    return await handle_mbti_test_submission(request, user)

@app.get("/generate-password")
async def generate_random_password():
    """Generate random password asynchronously"""
    password = await generate_password()
    return {"password": password}

# --- Application Startup ---
@app.on_event("startup")
async def startup_event():
    """Initialize application on startup"""
    logger.info("🚀 Starting MBTI Application...")
    
    # Initialize encryption
    await init_encryption()
    logger.info("🔐 Encryption initialized")
    
    # Initialize database
    await init_db()
    logger.info("💾 Database initialized")
    
    logger.info("✅ Application started successfully!")

async def require_login(user = Depends(get_current_user)):
    """Require user login dependency"""
    if not user:
        raise HTTPException(status_code=401, detail="لطفاً وارد حساب کاربری خود شوید")
    return user

async def require_advisor_login(advisor = Depends(get_current_advisor)):
    """Require advisor login dependency"""  
    if not advisor:
        raise HTTPException(status_code=401, detail="لطفاً وارد پنل مشاوران شوید")
    return advisor


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("🛑 Shutting down application...")
    executor.shutdown(wait=True)
    logger.info("✅ Application shutdown complete!")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4455)

@app.get("/{full_path:path}")
async def catch_all(request: Request, full_path: str):
    """Catch all undefined routes and show 404 page"""
    # بررسی که آیا فایل استاتیک است
    if full_path.startswith("static/"):
        raise HTTPException(status_code=404, detail="فایل یافت نشد")
    
    # نمایش صفحه 404 برای سایر مسیرها
    return templates.TemplateResponse("error_404.html", {
        "request": request
    }, status_code=404)