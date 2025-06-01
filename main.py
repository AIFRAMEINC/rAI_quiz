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
SESSION_SECRET = "your-super-secret-session-key-change-this-in-production"

DATABASE_FILE = "mbti_app.db"
ADVISOR_DATABASE_FILE = "advisor_users.db"  # دیتابیس جدید برای مشاوران
ENCRYPTION_KEY_FILE = "secret.key"
SESSION_SECRET = "your-super-secret-session-key-change-this-in-production"

# مشخصات لاگین مشاوران
ADVISOR_USERNAME = "1570760403"
ADVISOR_PASSWORD = "1570760403"

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
            encrypted_password BLOB NOT NULL
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
    return await loop.run_in_executor(executor, lambda: hashlib.sha256(password.encode()).hexdigest())

async def verify_password(password: str, hashed: str) -> bool:
    """Verify password asynchronously"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: hashlib.sha256(password.encode()).hexdigest() == hashed)

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
        "تو توی کلاس و مدرسه ترجیح میدی چطور وقت بگذرونی؟ بیشتر دوست داری با دوستات حرف بزنی و در گروه باشی، یا ترجیح میدی تنها باشی و کارهای خودت رو بکنی؟ چرا؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی یه مشکلی پیش میاد، مثلاً امتحانی که خراب شده یا دعوایی با دوستت، معمولاً چطوری فکر می‌کنی؟ بیشتر سعی می‌کنی منطقی باشی و راه حل پیدا کنی، یا اول احساساتت مهم هستن؟ یه مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        "فرض کن امروز آخر هفته است و برنامه‌ای نداری. دوست داری روزت رو چطوری بگذرونی؟ از قبل برنامه‌ریزی کنی یا ببینی چه پیش میاد؟ چه کارهایی دوست داری بکنی؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی معلم یه درس جدید تدریس می‌کنه، تو بیشتر به چه چیزی توجه می‌کنی؟ به مثال‌های عملی و واقعی که میده، یا به ایده‌های کلی و ارتباطش با چیزهای دیگه؟ چرا؟ (حداقل یک پاراگراف توضیح بده)",
        "اگه یکی از دوستات غمگین باشه و نیاز به صحبت داشته باشه، تو معمولاً چطوری کمکش می‌کنی؟ بیشتر گوش می‌دی و همدلی می‌کنی، یا سعی می‌کنی راه حل عملی پیدا کنی؟ (حداقل یک پاراگراف توضیح بده)",
        "تو نسبت به چیزهای جدید و تغییرات چه احساسی داری؟ مثلاً اگه بخوان کلاستون رو عوض کنن یا یه فعالیت جدید شروع کنید. دوست داری یا نگرانت می‌کنه؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی توی یه گروه درسی یا پروژه کار می‌کنی، معمولاً چه نقشی رو بر عهده می‌گیری؟ بیشتر رهبری می‌کنی، ایده میدی، نظم می‌دی، یا از بقیه پشتیبانی می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        "آخر هفته‌ها ترجیح میدی چه جور فعالیت‌هایی داشته باشی؟ فعالیت‌هایی که از قبل برنامه‌ریزی شده و مشخص هست، یا اینکه هرچی دلت خواست انجام بدی؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی یه کتاب یا فیلم رو انتخاب می‌کنی، معمولاً به چی بیشتر توجه می‌کنی؟ به داستان و واقعیت‌هایی که توش هست، یا به پیام‌های عمیق و معانی پنهانش؟ (حداقل یک پاراگراف توضیح بده)",
        "اگه بخوای توی یه مهمونی یا جشن شرکت کنی، ترجیح میدی چطور باشه؟ جایی که همه رو بشناسی و راحت باشی، یا جایی که آدم‌های جدید بشناسی؟ چرا؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی یه تصمیم مهم باید بگیری (مثل انتخاب یه کلاس اضافی یا فعالیت)، چطوری فکر می‌کنی؟ بیشتر فایده و ضررش رو حساب می‌کنی، یا به اینکه چه احساسی بهت میده توجه می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        "تو معمولاً چطوری یه کار جدید یاد می‌گیری؟ دوست داری اول کلی بخونی و فکر کنی، یا سریع شروع کنی به تجربه کردن و انجام دادن؟ مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        "نسبت به قوانین و مقررات مدرسه چه نظری داری؟ فکر می‌کنی باید دقیقاً رعایت بشن، یا گاهی میشه انعطاف داشت؟ چرا؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی می‌خوای با کسی صحبت کنی و حرف بزنی، معمولاً در چه شرایطی راحت‌تری؟ توی گروه با چند نفر، یا تک به تک با افراد؟ (حداقل یک پاراگراف توضیح بده)",
        "اگه بتونی آینده‌ت رو تصور کنی، چه جوری دوست داری باشه؟ آیا دوست داری همه چیز از قبل مشخص و برنامه‌ریزی شده باشه، یا ترجیح میدی خودت تصمیم بگیری که چه اتفاقی بیفته؟ (حداقل یک پاراگراف توضیح بده)"
    ],
    "15-18": [
        "در محیط‌های اجتماعی مثل مهمونی‌ها، دورهمی‌ها یا فعالیت‌های گروهی، معمولاً چطوری رفتار می‌کنی؟ بیشتر فعال هستی و با همه صحبت می‌کنی، یا ترجیح میدی با چند نفر خاص ارتباط برقرار کنی؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی یه تصمیم مهم باید بگیری (مثل انتخاب رشته یا دانشگاه)، چه عواملی برات مهم‌ترند؟ بیشتر به منطق، آمار و واقعیت‌ها توجه می‌کنی، یا به احساسات و ارزش‌های شخصیت اهمیت میدی؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی می‌خوای برای یه امتحان مهم یا پروژه درسی آماده بشی، معمولاً چطور عمل می‌کنی؟ از قبل برنامه دقیق می‌ریزی و طبق اون پیش میری، یا ترجیح میدی هر روز تصمیم بگیری چی کار کنی؟ یه مثال از یه موقعیت واقعی بزن. (حداقل یک پاراگراف توضیح بده)",
        "وقتی داری یه موضوع جدید مثل یه درس علمی یا یه مهارت جدید یاد می‌گیری، بیشتر به چی توجه می‌کنی؟ به اطلاعات واقعی و چیزهایی که می‌تونی مستقیماً استفاده کنی، یا به ایده‌های بزرگ و اینکه چطور می‌تونه به چیزهای دیگه ربط داشته باشه؟ یه مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        "اگه بخوای به یکی از همکلاسی‌هات کمک کنی که تو یه درس مشکل داره، معمولاً چطور بهش نزدیک می‌شی؟ بیشتر سعی می‌کنی راه‌حل‌های منطقی و قدم به قدم بهش یاد بدی، یا اول سعی می‌کنی بفهمی چرا ناراحته و بعد کمکش کنی؟ یه موقعیت واقعی رو توضیح بده. (حداقل یک پاراگراف توضیح بده)",
        "وقتی قراره یه فعالیت گروهی مثل اردو یا پروژه تیمی انجام بدی، ترجیح میدی همه چیز از قبل کاملاً مشخص و منظم باشه، یا دوست داری یه کم آزادی عمل داشته باشی و تو لحظه تصمیم بگیری؟ چرا؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی تو یه گروه مثل تیم ورزشی یا گروه درسی کار می‌کنی، معمولاً چه نقشی رو دوست داری داشته باشی؟ بیشتر دوست داری هدایت گروه رو به عهده بگیری و تصمیم‌ها رو بگیری، یا ترجیح میدی ایده‌های خلاقانه بدی و بذاری بقیه تصمیم بگیرن؟ یه مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        "وقتی یه پروژه یا کار مهم داری، چطوری بهش نزدیک می‌شی؟ از ابتدا همه چیز رو برنامه‌ریزی می‌کنی، یا هر مرحله رو به موقعش حل می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی داری یه موضوع یا مسئله جدید رو بررسی می‌کنی (مثل یه موضوع درسی یا یه بحث اجتماعی)، بیشتر به چی اهمیت می‌دی؟ به واقعیت‌ها و چیزهایی که ثابت شدن، یا به احتمالات و ایده‌های جدیدی که ممکنه به وجود بیان؟ یه مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        "در مناقشات و بحث‌های مختلف، معمولاً چه رویکردی داری؟ بیشتر به دنبال یافتن حقیقت و درستی هستی، یا مهمه که احساسات و نظرات همه حفظ بشه؟ (حداقل یک پاراگراف توضیح بده)",
        "انرژیت رو از کجا می‌گیری؟ از تعامل با آدم‌ها و فعالیت‌های اجتماعی، یا از زمان‌هایی که تنها هستی و فکر می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی یه اتفاق غیرمنتظره می‌افته، معمولاً چطوری واکنش نشون میدی؟ سریع خودت رو با شرایط جدید وفق میدی، یا نیاز داری کمی وقت تا بپذیریش؟ (حداقل یک پاراگراف توضیح بده)",
        "در تصمیم‌گیری‌های اخلاقی، چه چیزی برات مهم‌تره؟ اصول کلی و عدالت، یا شرایط خاص و تأثیر روی احساسات افراد؟ (حداقل یک پاراگراف توضیح بده)",
        "محیط ایده‌آل کاری یا تحصیلی برای تو چطوری باید باشه؟ ساختارمند و با قوانین مشخص، یا انعطاف‌پذیر و آزاد؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی به آینده فکر می‌کنی، بیشتر چه چیزی جذبت می‌کنه؟ امکانات و فرصت‌های جدیدی که ممکنه پیش بیاد، یا اهدافی که از الان مشخص کردی و می‌خوای بهشون برسی؟ (حداقل یک پاراگراف توضیح بده)"
    ]
}

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
        "description": "شما روحی آزاد و پرشور هستید که عاشق امکانات انسانی و ایده‌های نو هستید. با انرژی عفونی و اشتیاق واقعی، دیگران را الهام می‌کنید و محیطی خلاق و پویا ایجاد می‌کنید.", 
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

# Available tests
AVAILABLE_TESTS = {
    "mbti_personality": {
        "title": "آزمون شخصیت‌شناسی MBTI",
        "description": "کشف تیپ شخصیتی خود بر اساس استاندارد بین‌المللی MBTI",
        "duration": "15-20 دقیقه",
        "questions_count": 15,
        "icon": "🧠"
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

gemini_model_for_type = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=determine_mbti_tool)
gemini_model_for_all_percentages = genai.GenerativeModel(model_name="gemini-1.5-flash", tools=estimate_all_eight_preferences_tool)

# --- Async Gemini Interaction Functions ---
async def determine_mbti_from_gemini_args(extraversion_introversion, sensing_intuition, thinking_feeling, judging_perceiving):
    """Determine MBTI type from Gemini arguments"""
    return {"mbti_type": f"{extraversion_introversion}{sensing_intuition}{thinking_feeling}{judging_perceiving}"}

async def create_prompt_for_mbti(questions: List[str], answers: List[str], age_range: str) -> str:
    """Create prompt for MBTI analysis asynchronously"""
    age_context = {
        "13-15": "این کاربر در رده سنی 13-15 سال قرار دارد. لطفاً در تحلیل این موضوع را در نظر بگیرید که پاسخ‌ها مربوط به یک نوجوان است و ویژگی‌های رشدی این سن را در نظر بگیرید.",
        "15-18": "این کاربر در رده سنی 15-18 سال قرار دارد. لطفاً در تحلیل این موضوع را در نظر بگیرید که پاسخ‌ها مربوط به یک نوجوان-جوان است که در حال گذار به بزرگسالی است."
    }
    
    prompt = f"بر اساس پاسخ‌های کاربر به سوالات زیر، تیپ شخصیتی MBTI او را تشخیص بده.\n\n"
    prompt += f"اطلاعات مهم: {age_context.get(age_range, '')}\n\n"
    prompt += "سپس تابع 'determine_mbti' را فراخوانی کن.\n\n"
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q)}\n**پاسخ {i+1}:** {html.escape(a)}\n\n"
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

async def create_prompt_for_all_percentages(questions: List[str], answers: List[str], mbti_type: str, age_range: str) -> str:
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
        "سپس تابع 'estimate_all_eight_mbti_preferences' را با این هشت درصد فراخوانی کن.\n\n"
    )
    
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q)}\n**پاسخ {i+1}:** {html.escape(a)}\n\n"
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
        reason += f"؛ به عنوان مثال، در پاسخ اول خود به مواردی اشاره کردید که نشان‌دهنده '{html.escape(answers[0][:70])}...' بود."
    
    return reason

async def generate_html_mbti_report(test_result_id: str, mbti_type: str, user_questions: List[str], user_answers: List[str], all_percentages: Optional[Dict[str, int]]) -> str:
    """Generate HTML MBTI report asynchronously"""
    info = MBTI_DESCRIPTIONS.get(mbti_type)
    if not info:
        return f"<h1>خطا</h1><p>تیپ شخصیتی '{html.escape(mbti_type)}' یافت نشد.</p>"

    reasoning_text = await get_reasoning_for_mbti(mbti_type, user_answers)
    
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
            <p>نمودارهای زیر تخمینی از میزان تمایل شما به هر یک از ترجیحات شخصیتی‌تان را بر اساس تحلیل پاسخ‌های شما توسط هوش مصنوعی نشان می‌دهند.</p>
            
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
            "SELECT username, encrypted_password FROM advisors WHERE username = ?",
            (username,), fetch=True
        )
        
        if not advisors:
            return RedirectResponse(url="/karshenasanlogin?error=invalid_credentials", status_code=303)
        
        advisor = advisors[0]
        decrypted_password = await decrypt_data(advisor['encrypted_password'])
        if decrypted_password != password:
            return RedirectResponse(url="/karshenasanlogin?error=invalid_credentials", status_code=303)
        
        # ایجاد سشن برای مشاور
        session_id = await create_advisor_session(username)
        
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
    
    if test_id == "mbti_personality":
        age_range = user['age_range']
        if age_range not in QUESTIONS_DB:
            raise HTTPException(status_code=400, detail="رده سنی نامعتبر است.")
        
        # بررسی آیا کاربر قبلاً تست را انجام داده است
        result = await db_manager.execute_query("""
            SELECT id FROM test_results 
            WHERE user_id = ? AND test_name = ?
        """, (user['id'], "آزمون شخصیت‌شناسی MBTI"), fetch=True)
        
        if result:
            return RedirectResponse(url="/my-results?error=already_taken", status_code=303)
        
        questions_for_age = QUESTIONS_DB[age_range]
        return templates.TemplateResponse("questions.html", {
            "request": request, 
            "test_id": test_id,
            "questions": questions_for_age,
            "user": user
        })
    
    raise HTTPException(status_code=404, detail="تست پیدا نشد")

@app.post("/submit_test/{test_id}")
async def handle_test_submission(request: Request, test_id: str, user = Depends(require_login)):
    if test_id not in AVAILABLE_TESTS:
        raise HTTPException(status_code=404, detail="تست یافت نشد")
    
    if test_id == "mbti_personality":
        return await handle_mbti_test_submission(request, user)
    
    raise HTTPException(status_code=404, detail="تست پیدا نشد")

@app.get("/advisor/user_result/{user_id}", response_class=HTMLResponse)
async def show_user_result_to_advisor(request: Request, user_id: str, advisor = Depends(require_advisor_login)):
    """Show specific user's test result to advisor"""
    try:
        # Get user information and test results
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
        
        # Process user data
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
            
            if row['test_id']:  # If user has test results
                # Decrypt answers
                answers = []
                if row['encrypted_answers']:
                    decrypted_answers = await decrypt_data(row['encrypted_answers'])
                    if decrypted_answers and decrypted_answers != "خطا در رمزگشایی":
                        try:
                            answers = json.loads(decrypted_answers)
                        except json.JSONDecodeError:
                            answers = ["خطا در پارس پاسخ‌ها"]
                
                # Decrypt percentages
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
            "QUESTIONS_DB": QUESTIONS_DB
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
        answers.append(answer_value)
    
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
async def get_test_result(request: Request, phone: str, test_result_id: str, user = Depends(require_login)):
    # Check if user can access this result
    if user['phone'] != phone:
        raise HTTPException(status_code=403, detail="شما اجازه دسترسی به این نتیجه را ندارید")
    
    # Get test result (async)
    result = await db_manager.execute_query("""
        SELECT tr.*, u.age_range FROM test_results tr
        JOIN users u ON tr.user_id = u.id
        WHERE tr.id = ? AND tr.user_id = ?
    """, (test_result_id, user['id']), fetch=True)
    
    if not result:
        raise HTTPException(status_code=404, detail="نتیجه تست یافت نشد")
    
    result = result[0]
    
    # Decrypt and process result (async)
    answers = []
    percentages = None
    
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
    
    # Generate report (async)
    if "خطا" in result['mbti_result']:
        report_html = f"<h1>خطا در تحلیل</h1><p>{html.escape(result['mbti_result'])}</p>"
    else:
        questions_for_age = QUESTIONS_DB.get(result['age_range'], [])
        report_html = await generate_html_mbti_report(test_result_id, result['mbti_result'], questions_for_age, answers, percentages)

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