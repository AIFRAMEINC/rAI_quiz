import sqlite3
import json
import asyncio
import os
import html
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import uuid4

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cryptography.fernet import Fernet
import google.generativeai as genai

# --- Configuration ---
DATABASE_FILE = "mbti_app.db"
ENCRYPTION_KEY_FILE = "secret.key"

def load_or_generate_key():
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, "rb") as key_file:
            key = key_file.read()
    else:
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_FILE, "wb") as key_file:
            key_file.write(key)
        print(f" کلید رمزنگاری جدید ایجاد و در {ENCRYPTION_KEY_FILE} ذخیره شد. این فایل را امن نگه دارید! ")
    if len(key) != 44 or not key.endswith(b'='): # Basic check
        print(f" هشدار: محتوای {ENCRYPTION_KEY_FILE} به نظر یک کلید Fernet معتبر نیست.")
    return key

ENCRYPTION_KEY = load_or_generate_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

API_KEY = "AIzaSyCphwC83v0XsBfQIv0ac_JHkJkVopCM43M" # <<< هشدار: کلید خود را امن نگه دارید
if not API_KEY:
    raise ValueError("متغیر محیطی GEMINI_API_KEY تنظیم نشده است")
genai.configure(api_key=API_KEY)

# --- Database Setup (No changes from previous version with encrypted_mbti_percentages column) ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        encrypted_first_name BLOB NOT NULL,
        encrypted_last_name BLOB NOT NULL,
        encrypted_phone BLOB UNIQUE NOT NULL,
        age_range TEXT NOT NULL,
        registration_time TEXT NOT NULL,
        encrypted_answers BLOB,
        mbti_result TEXT,
        encrypted_mbti_percentages BLOB,
        analysis_time TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Encryption/Decryption Helpers (No changes) ---
def encrypt_data(data: str) -> Optional[bytes]:
    if data is None: return None
    return cipher_suite.encrypt(data.encode('utf-8'))

def decrypt_data(encrypted_data: Optional[bytes]) -> Optional[str]:
    if encrypted_data is None: return None
    try:
        return cipher_suite.decrypt(encrypted_data).decode('utf-8')
    except Exception:
        return "خطا در رمزگشایی"

# --- MBTI Questions Database - Updated with 15 questions for each age range ---
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
        
        "وقتی یه تصمیم مهم باید بگیری (مثل انتخاب یه کلاس اضافی یا فعالیت), چطوری فکر می‌کنی؟ بیشتر فایده و ضررش رو حساب می‌کنی، یا به اینکه چه احساسی بهت میده توجه می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        
        "تو معمولاً چطوری یه کار جدید یاد می‌گیری؟ دوست داری اول کلی بخونی و فکر کنی، یا سریع شروع کنی به تجربه کردن و انجام دادن؟ مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        
        "نسبت به قوانین و مقررات مدرسه چه نظری داری؟ فکر می‌کنی باید دقیقاً رعایت بشن، یا گاهی میشه انعطاف داشت؟ چرا؟ (حداقل یک پاراگراف توضیح بده)",
        
        "وقتی میخوای با کسی صحبت کنی و حرف بزنی، معمولاً در چه شرایطی راحت‌تری؟ توی گروه با چند نفر، یا تک به تک با افراد؟ (حداقل یک پاراگراف توضیح بده)",
        
        "اگه بتونی آینده‌ت رو تصور کنی، چه جوری دوست داری باشه؟ آیا دوست داری همه چیز از قبل مشخص و برنامه‌ریزی شده باشه، یا ترجیح میدی خودت تصمیم بگیری که چه اتفاقی بیفته؟ (حداقل یک پاراگراف توضیح بده)"
    ],
    
    "15-18": [
        "در محیط‌های اجتماعی مثل مهمونی‌ها، دورهمی‌ها یا فعالیت‌های گروهی، معمولاً چطوری رفتار می‌کنی؟ بیشتر فعال هستی و با همه صحبت می‌کنی، یا ترجیح میدی با چند نفر خاص ارتباط برقرار کنی؟ (حداقل یک پاراگراف توضیح بده)",
        
        "وقتی یه تصمیم مهم باید بگیری (مثل انتخاب رشته یا دانشگاه), چه عواملی برات مهم‌ترند؟ بیشتر به منطق، آمار و واقعیت‌ها توجه می‌کنی، یا به احساسات و ارزش‌های شخصیت اهمیت میدی؟ (حداقل یک پاراگراف توضیح بده)",
        
        "آخر هفته‌ها و اوقات فراغت رو چطوری می‌گذرونی؟ ترجیح میدی برنامه‌های از پیش تعیین شده داشته باشی، یا بر اساس حال و حوصله‌ت تصمیم بگیری؟ (حداقل یک پاراگراف توضیح بده)",
        
        "وقتی موضوع پیچیده‌ای رو یاد می‌گیری، بیشتر به چه چیزی توجه می‌کنی؟ به جزئیات دقیق و کاربرد عملی‌اش، یا به ایده کلی و ارتباطش با مفاهیم دیگه؟ (حداقل یک پاراگراف توضیح بده)",
        
        "در روابط دوستی و خانوادگی، وقتی کسی مشکلی داره، معمولاً چطوری واکنش نشون میدی؟ بیشتر سعی می‌کنی راه حل منطقی ارائه بدی، یا اول به احساساتش توجه می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        
        "نسبت به تغییرات و موقعیت‌های جدید چه برخوردی داری؟ آیا انطباق‌پذیری و تغییر برات آسانه، یا ترجیح میدی چیزها ثابت و قابل پیش‌بینی باشن؟ (حداقل یک پاراگراف توضیح بده)",
        
        "در کارهای گروهی و تیمی، خودت رو چطوری توصیف می‌کنی؟ آیا تمایل داری رهبری کنی، ایده‌پردازی کنی، سازماندهی کنی، یا از دیگران حمایت کنی؟ (حداقل یک پاراگراف توضیح بده)",
        
        "وقتی یه پروژه یا کار مهم داری، چطوری بهش نزدیک میشی؟ از ابتدا همه چیز رو برنامه‌ریزی می‌کنی، یا هر مرحله رو به موقعش حل می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        
        "کتاب‌ها، فیلم‌ها یا مطالبی که مطالعه می‌کنی معمولاً چه ویژگی‌هایی دارن؟ بیشتر واقع‌گرا و عملی هستن، یا تخیلی و پر از ایده‌های انتزاعی؟ (حداقل یک پاراگراف توضیح بده)",
        
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

# --- Gemini Interaction Functions - Updated to include age range ---
def determine_mbti_from_gemini_args(extraversion_introversion, sensing_intuition, thinking_feeling, judging_perceiving):
    return {"mbti_type": f"{extraversion_introversion}{sensing_intuition}{thinking_feeling}{judging_perceiving}"}

def create_prompt_for_mbti(questions: List[str], answers: List[str], age_range: str) -> str:
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
    prompt = create_prompt_for_mbti(questions, answers, age_range)
    try:
        response = await gemini_model_for_type.generate_content_async(prompt)
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "determine_mbti":
                    args = dict(part.function_call.args)
                    result = determine_mbti_from_gemini_args(**args)
                    return result["mbti_type"]
        print("Gemini تابع determine_mbti را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return "خطا: عدم تشخیص نوع MBTI"
    except Exception as e:
        print(f"خطا در get_mbti_type_from_gemini: {e}")
        return f"خطا در پردازش نوع: {str(e)[:100]}"

def create_prompt_for_all_percentages(questions: List[str], answers: List[str], mbti_type: str, age_range: str) -> str:
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
    if "خطا" in mbti_type:
        return None
        
    prompt = create_prompt_for_all_percentages(questions, answers, mbti_type, age_range)
    try:
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
                            print(f"مقدار غیرعددی یا گمشده برای زوج: {p1_key}={p1_val_orig}, {p2_key}={p2_val_orig}")
                            all_pairs_valid = False
                            break
                        
                        p1_int = round(float(p1_val_orig))
                        p2_int = round(float(p2_val_orig))

                        if not (0 <= p1_int <= 100 and 0 <= p2_int <= 100):
                            print(f"درصد خارج از محدوده برای زوج: {p1_key}={p1_int}, {p2_key}={p2_int}")
                            all_pairs_valid = False
                            break
                        
                        if not (98 <= (p1_int + p2_int) <= 102):
                            print(f"مجموع زوج درصدها برای {p1_key} و {p2_key} برابر ۱۰۰ نیست: {p1_int} + {p2_int} = {p1_int + p2_int}")
                            all_pairs_valid = False 
                            break
                        
                        valid_percentages_int[p1_key] = p1_int
                        valid_percentages_int[p2_key] = p2_int
                    
                    if all_pairs_valid and len(valid_percentages_int) == 8:
                        return valid_percentages_int
                    else:
                        print(f"اعتبارسنجی درصدها ناموفق بود. داده دریافتی: {percentages_from_gemini}. داده معتبر شده: {valid_percentages_int}")
                        return None 
        print("Gemini تابع estimate_all_eight_mbti_preferences را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return None
    except Exception as e:
        print(f"خطا در get_all_eight_mbti_percentages_from_gemini: {e}")
        return None

def get_reasoning_for_mbti(mbti_type: str, answers: List[str]) -> str:
    base_reasoning = {
        "ESTJ": "نظم، مسئولیت‌پذیری و تصمیم‌گیری منطقی", "ISTJ": "دقت، وظیفه‌شناسی و پایبندی به قوانین",
        "ESFJ": "همدلی، مهارت‌های اجتماعی و توجه به نیازهای دیگران", "ISFJ": "فداکاری، دقت و حمایت از دیگران",
        "ENTJ": "رهبری قاطع، تفکر استراتژیک و هدف‌محوری", "INTJ": "تفکر استراتژیک، استقلال و خلاقیت",
        "ENFJ": "همدلی، کاریزما و الهام‌بخشی به دیگران", "INFJ": "همدلی عمیق، بصیرت و تعهد به ارزش‌ها",
        "ESTP": "انعطاف‌پذیری، عمل‌گرایی و انرژی بالا", "ISTP": "مهارت‌های فنی، استقلال و خونسردی در بحران",
        "ESFP": "انرژی بالا، مهارت‌های اجتماعی و زندگی در لحظه", "ISFP": "خلاقیت، همدلی و وفاداری به ارزش‌ها",
        "ENTP": "خلاقیت، کنجکاوی و نوآوری", "INTP": "تحلیل عمیق، کنجکاوی و استقلال",
        "ENFP": "خلاقیت، همدلی و اشتیاق به امکانات جدید", "INFP": "همدلی، خلاقیت و ارزش‌های شخصی قوی"
    }
    default_reason = "ویژگی‌های کلیدی متناسب با پاسخ‌های شما"
    reason = base_reasoning.get(mbti_type, default_reason)
    if answers and answers[0]:
        reason += f"؛ به عنوان مثال، در پاسخ اول خود به مواردی اشاره کردید که نشان‌دهنده '{html.escape(answers[0][:70])}...' بود."
    return reason

def generate_html_mbti_report(user_id: str, mbti_type: str, user_questions: List[str], user_answers: List[str], all_percentages: Optional[Dict[str, int]]) -> str:
    info = MBTI_DESCRIPTIONS.get(mbti_type)
    if not info:
        return f"<h1>خطا</h1><p>تیپ شخصیتی '{html.escape(mbti_type)}' یافت نشد.</p>"

    reasoning_text = get_reasoning_for_mbti(mbti_type, user_answers)
    
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

# --- FastAPI Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def get_registration_page(request: Request, error: str = None, phone: str = None):
    error_message = None
    if error == "phone_exists" and phone:
        error_message = f"شماره همراه {html.escape(phone)} قبلاً ثبت شده."
    elif error == "check_failed":
        error_message = "خطا در بررسی اطلاعات. لطفاً دوباره تلاش کنید."
    return templates.TemplateResponse("register.html", {"request": request, "error_message": error_message})

@app.post("/register", response_class=RedirectResponse)
async def handle_registration(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str = Form(...),
    age_range: str = Form(...)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    is_duplicate = False
    try:
        cursor.execute("SELECT encrypted_phone FROM users")
        all_encrypted_phones = cursor.fetchall()
        for row in all_encrypted_phones:
            decrypted_phone = decrypt_data(row['encrypted_phone'])
            if decrypted_phone == phone:
                is_duplicate = True
                break
    except Exception as e:
        print(f"خطا در بررسی یکتایی شماره تلفن: {e}")
        conn.close()
        return RedirectResponse(url=f"/?error=check_failed", status_code=303)

    if is_duplicate:
        conn.close()
        return RedirectResponse(url=f"/?error=phone_exists&phone={phone}", status_code=303)

    user_id = str(uuid4())
    encrypted_fname = encrypt_data(first_name)
    encrypted_lname = encrypt_data(last_name)
    encrypted_phone_val = encrypt_data(phone)
    
    try:
        cursor.execute("""
            INSERT INTO users (id, encrypted_first_name, encrypted_last_name, encrypted_phone, age_range, registration_time)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, encrypted_fname, encrypted_lname, encrypted_phone_val, age_range, datetime.now().isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return RedirectResponse(url=f"/?error=phone_exists&phone={phone}", status_code=303)
    finally:
        conn.close()
    
    return RedirectResponse(url=f"/questions?user_id={user_id}&age_range={age_range}", status_code=303)

@app.get("/questions", response_class=HTMLResponse)
async def get_questions_page(request: Request, user_id: str, age_range: str, error_message: str = None, previous_answers: str = None):
    if age_range not in QUESTIONS_DB:
        raise HTTPException(status_code=400, detail="رده سنی نامعتبر است.")
    
    questions_for_age = QUESTIONS_DB[age_range]
    parsed_previous_answers = {}
    if previous_answers:
        try:
            parsed_previous_answers = json.loads(previous_answers)
        except json.JSONDecodeError:
            pass 

    return templates.TemplateResponse("questions.html", {
        "request": request, 
        "user_id": user_id,
        "age_range": age_range,
        "questions": questions_for_age,
        "error_message": error_message,
        "previous_answers": parsed_previous_answers
    })

@app.post("/submit_answers")
async def handle_answers_submission(request: Request):
    form_data = await request.form()
    user_id = form_data.get("user_id")
    age_range = form_data.get("age_range")

    if not user_id or not age_range:
        raise HTTPException(status_code=400, detail="شناسه کاربری یا رده سنی ارسال نشده است.")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")

    if age_range not in QUESTIONS_DB:
        conn.close()
        raise HTTPException(status_code=400, detail="رده سنی نامعتبر برای سوالات.")

    questions_for_user = QUESTIONS_DB[age_range]
    answers = []
    raw_answers_for_repopulation = {}
    for i in range(len(questions_for_user)):
        answer_key = f"answer_{i}"
        answer_value = form_data.get(answer_key)
        raw_answers_for_repopulation[answer_key] = answer_value
        if not answer_value or not answer_value.strip():
            conn.close()
            prev_ans_json = json.dumps(raw_answers_for_repopulation)
            error_msg_val = f"لطفاً به سوال {i+1} پاسخ کامل دهید."
            return RedirectResponse(
                url=f"/questions?user_id={user_id}&age_range={age_range}&error_message={error_msg_val}&previous_answers={prev_ans_json}",
                status_code=303
            )
        answers.append(answer_value)
    
    encrypted_answers_json_blob = encrypt_data(json.dumps(answers))
    
    # Updated to pass age_range to AI analysis
    mbti_type_result = await get_mbti_type_from_gemini(questions_for_user, answers, age_range)
    
    all_mbti_percentages_dict = None
    encrypted_percentages_blob = None
    if not "خطا" in mbti_type_result:
        all_mbti_percentages_dict = await get_all_eight_mbti_percentages_from_gemini(questions_for_user, answers, mbti_type_result, age_range)
        if all_mbti_percentages_dict:
            encrypted_percentages_blob = encrypt_data(json.dumps(all_mbti_percentages_dict))
        else:
            print(f"هشدار: درصدها برای کاربر {user_id} با تیپ {mbti_type_result} دریافت نشد.")

    try:
        cursor.execute("""
            UPDATE users
            SET encrypted_answers = ?, mbti_result = ?, encrypted_mbti_percentages = ?, analysis_time = ?
            WHERE id = ?
        """, (encrypted_answers_json_blob, mbti_type_result, encrypted_percentages_blob, datetime.now().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"خطا در به‌روزرسانی دیتابیس: {e}")
    finally:
        conn.close()

    if "خطا" in mbti_type_result:
        report_html = f"<h1>خطا در تحلیل</h1><p>{html.escape(mbti_type_result)}</p><a href='/' class='button'>بازگشت به صفحه اصلی</a>"
    else:
        report_html = generate_html_mbti_report(user_id, mbti_type_result, questions_for_user, answers, all_mbti_percentages_dict)

    return templates.TemplateResponse("result.html", {
        "request": request,
        "user_id": user_id,
        "report_content": report_html,
        "mbti_type": mbti_type_result
    })

@app.get("/show_data", response_class=HTMLResponse)
async def show_data_page(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, encrypted_first_name, encrypted_last_name, encrypted_phone, age_range, registration_time, encrypted_answers, mbti_result, encrypted_mbti_percentages, analysis_time FROM users")
    rows = cursor.fetchall()
    conn.close()

    users_list = []
    for row_data in rows:
        user_dict = dict(row_data)
        user_dict['first_name'] = decrypt_data(user_dict.pop('encrypted_first_name'))
        user_dict['last_name'] = decrypt_data(user_dict.pop('encrypted_last_name'))
        user_dict['phone'] = decrypt_data(user_dict.pop('encrypted_phone'))
        
        decrypted_answers_list = []
        encrypted_ans_blob = user_dict.pop('encrypted_answers')
        if encrypted_ans_blob:
            dec_ans_json = decrypt_data(encrypted_ans_blob)
            if dec_ans_json and dec_ans_json != "خطا در رمزگشایی":
                try: decrypted_answers_list = json.loads(dec_ans_json)
                except json.JSONDecodeError: decrypted_answers_list = ["خطا در پارس JSON پاسخ‌ها"]
            elif dec_ans_json == "خطا در رمزگشایی": decrypted_answers_list = ["خطا در رمزگشایی پاسخ‌ها"]
        user_dict['answers'] = decrypted_answers_list

        decrypted_percentages_dict = None
        encrypted_perc_blob = user_dict.pop('encrypted_mbti_percentages')
        if encrypted_perc_blob:
            dec_perc_json = decrypt_data(encrypted_perc_blob)
            if dec_perc_json and dec_perc_json != "خطا در رمزگشایی":
                try: decrypted_percentages_dict = json.loads(dec_perc_json)
                except json.JSONDecodeError: decrypted_percentages_dict = {"error": "خطا در پارس JSON درصدها"}
            elif dec_perc_json == "خطا در رمزگشایی": decrypted_percentages_dict = {"error": "خطا در رمزگشایی درصدها"}
        user_dict['mbti_percentages'] = decrypted_percentages_dict
            
        users_list.append(user_dict)
        
    return templates.TemplateResponse("debug_data.html", {"request": request, "users_data": users_list})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)