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

# --- MBTI Questions, Descriptions (No changes) ---
QUESTIONS_DB = {
    "13-15": [
        "فرض کن یه روز تعطیله و هیچ کار خاصی نداری. دوست داری اون روز رو چطوری بگذرونی؟ تنهایی یا با دوستات؟ چه کارایی می‌کنی که بهت خوش بگذره؟ (حداقل یک پاراگراف توضیح بده)",
        "اگه یه دوستت مشکلی داشته باشه و بیاد پیش تو، تو معمولاً چطوری بهش کمک می‌کنی؟ بیشتر دلداریش میدی یا سعی می‌کنی براش راه حل پیدا کنی؟ یه مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        "وقتی می‌خوای یه کار جدید یاد بگیری (مثلاً یه بازی یا یه مهارت)، ترجیح میدی اول کلی در موردش بخونی و فکر کنی، یا اینکه سریع شروع کنی به امتحان کردنش؟ چرا؟ (حداقل یک پاراگراف توضیح بده)",
        "فرض کن معلمتون یه پروژه گروهی داده. تو دوست داری چه نقشی توی گروه داشته باشی؟ کسی که کارها رو برنامه‌ریزی می‌کنه، کسی که ایده‌های جدید میده یا کسی که به بقیه کمک می‌کنه؟ توضیح بده. (حداقل یک پاراگراف توضیح بده)"
    ],
    "15-18": [
        "آخر هفته‌ها یا اوقات فراغتت رو چطور می‌گذرونی؟ ترجیح میدی برنامه‌ریزی شده باشه یا هرچی پیش بیاد؟ معمولاً با کی وقت می‌گذرونی و چه فعالیت‌هایی رو انتخاب می‌کنی؟ (حداقل یک پاراگراف توضیح بده)",
        "وقتی با یه تصمیم مهم روبرو میشی (مثلاً انتخاب رشته یا یه فعالیت فوق برنامه)، چطوری تصمیم می‌گیری؟ بیشتر به منطق و واقعیت‌ها توجه می‌کنی یا به احساسات و ارزش‌های شخصیت؟ یه مثال بزن. (حداقل یک پاراگراف توضیح بده)",
        "اگه بخوای یه موضوع پیچیده رو یاد بگیری، روشت چیه؟ آیا دوست داری جزئیات دقیق و کاربردی رو بدونی یا بیشتر به دنبال فهمیدن ایده کلی و ارتباطش با چیزای دیگه هستی؟ (حداقل یک پاراگراف توضیح بده)",
        "در یک تیم یا گروه، خودت رو چطور توصیف می‌کنی؟ آیا تمایل داری رهبری کنی، ایده‌پردازی کنی، نظم بدی، یا از دیگران حمایت کنی؟ لطفاً توضیح بده چرا. (حداقل یک پاراگراف توضیح بده)"
    ]
}
MBTI_DESCRIPTIONS = {
    "ESTJ": {"title": "ESTJ - مدیر اجرایی", "nickname": "مدیر", "description": "افراد ESTJ عمل‌گرا، قاطع و سازمان‌یافته هستند...", "strengths": "رهبری قوی, سازمان‌دهی عالی, تعهد به وظایف, صداقت و صراحت, توانایی مدیریت پروژه‌های بزرگ", "weaknesses": "ممکن است بیش از حد سخت‌گیر باشند, انعطاف‌پذیری کم در برابر تغییرات, تمرکز بیش از حد بر قوانین, گاهی اوقات بی‌توجه به احساسات دیگران"},
    "ISTJ": {"title": "ISTJ - لجستیک‌دان", "nickname": "بازرس", "description": "ISTJها مسئولیت‌پذیر، دقیق و متعهد به سنت‌ها هستند...", "strengths": "وظیفه‌شناسی, دقت در جزئیات, قابلیت اطمینان, وفاداری, برنامه‌ریزی قوی", "weaknesses": "مقاومت در برابر تغییر, بیش از حد جدی, عدم انعطاف‌پذیری, تمرکز بیش از حد بر قوانین"},
    "ESFJ": {"title": "ESFJ - کنسول", "nickname": "مراقب", "description": "ESFJها گرم، همدل و مردم‌محور هستند...", "strengths": "همدلی بالا, مهارت‌های اجتماعی قوی, وفاداری, سازمان‌دهی خوب, توانایی ایجاد انگیزه در دیگران", "weaknesses": "وابستگی به تأیید دیگران, حساسیت به انتقاد, مشکل در پذیرش تغییرات, گاهی بیش از حد سنتی"},
    "ISFJ": {"title": "ISFJ - مدافع", "nickname": "حامی", "description": "ISFJها مهربان، وظیفه‌شناس و فداکار هستند...", "strengths": "فداکاری, دقت در جزئیات, وفاداری, مهارت‌های عملی, توانایی گوش دادن", "weaknesses": "مقاومت در برابر تغییر, مشکل در بیان نیازهای خود, حساسیت بیش از حد, تمایل به اجتناب از تعارض"},
    "ENTJ": {"title": "ENTJ - فرمانده", "nickname": "رهبر", "description": "ENTJها با اعتماد به نفس، استراتژیک و هدف‌محور هستند...", "strengths": "رهبری قاطع, تفکر استراتژیک, اعتماد به نفس, انگیزه بالا, توانایی حل مسائل پیچیده", "weaknesses": "ممکن است بیش از حد سلطه‌جو باشند, بی‌صبری با ناکارآمدی, بی‌توجهی به احساسات دیگران, تمرکز بیش از حد بر اهداف"},
    "INTJ": {"title": "INTJ - معمار", "nickname": "مغز متفکر", "description": "INTJها متفکران استراتژیک و آینده‌نگر هستند...", "strengths": "تفکر استراتژیک, استقلال, خلاقیت, تعهد به اهداف, توانایی تحلیل عمیق", "weaknesses": "ممکن است بیش از حد انتقادی باشند, مشکل در برقراری ارتباط عاطفی, کمال‌گرایی, بی‌صبری با دیگران"},
    "ENFJ": {"title": "ENFJ - قهرمان", "nickname": "مربی", "description": "ENFJها کاریزماتیک، همدل و الهام‌بخش هستند...", "strengths": "همدلی بالا, مهارت‌های ارتباطی قوی, توانایی الهام‌بخشی, سازمان‌دهی خوب, کاریزما", "weaknesses": "تمایل به نادیده گرفتن نیازهای خود, حساسیت به انتقاد, مشکل در تصمیم‌گیری‌های منطقی, وابستگی به تأیید دیگران"},
    "INFJ": {"title": "INFJ - وکیل", "nickname": "مشاور", "description": "INFJها ایده‌آلیست، بصیر و همدل هستند...", "strengths": "همدلی عمیق, بصیرت, خلاقیت, تعهد به ارزش‌ها, توانایی گوش دادن", "weaknesses": "بیش از حد ایده‌آلیستی, مشکل در پذیرش انتقاد, تمایل به انزوا, کمال‌گرایی"},
    "ESTP": {"title": "ESTP - کارآفرین", "nickname": "ماجراجو", "description": "ESTPها پرانرژی، عمل‌گرا و انعطاف‌پذیر هستند...", "strengths": "انعطاف‌پذیری, مهارت‌های عملی, اعتماد به نفس, توانایی حل بحران, انرژی بالا", "weaknesses": "بی‌صبری, مشکل در برنامه‌ریزی بلندمدت, بی‌توجهی به جزئیات, گاهی بیش از حد ریسک‌پذیر"},
    "ISTP": {"title": "ISTP - صنعت‌گر", "nickname": "مکانیک", "description": "ISTPها تحلیل‌گر، مستقل و عمل‌گرا هستند...", "strengths": "مهارت‌های فنی, استقلال, انعطاف‌پذیری, توانایی حل مسائل, خونسردی در بحران", "weaknesses": "تمایل به انزوا, مشکل در تعهد بلندمدت, بی‌توجهی به احساسات دیگران, گاهی بیش از حد محتاط"},
    "ESFP": {"title": "ESFP - سرگرم‌کننده", "nickname": "اجراکننده", "description": "ESFPها پرانرژی، اجتماعی و شاد هستند...", "strengths": "انرژی بالا, مهارت‌های اجتماعی قوی, انعطاف‌پذیری, همدلی, توانایی ایجاد شادی", "weaknesses": "مشکل در برنامه‌ریزی بلندمدت, حساسیت به انتقاد, بی‌توجهی به جزئیات, گاهی بیش از حد تکانشی"},
    "ISFP": {"title": "ISFP - ماجراجو", "nickname": "هنرمند", "description": "ISFPها مهربان، خلاق و انعطاف‌پذیر هستند...", "strengths": "خلاقیت, همدلی, انعطاف‌پذیری, وفاداری به ارزش‌ها, توانایی ایجاد هماهنگی", "weaknesses": "تمایل به انزوا, مشکل در برنامه‌ریزی بلندمدت, حساسیت به انتقاد, گاهی بیش از حد محتاط"},
    "ENTP": {"title": "ENTP - مبتکر", "nickname": "مخترع", "description": "ENTPها خلاق، کنجکاو و نوآور هستند...", "strengths": "خلاقیت, مهارت‌های کلامی قوی, انعطاف‌پذیری, توانایی حل مسائل, کنجکاوی", "weaknesses": "مشکل در تمرکز, بی‌صبری با کارهای روتین, گاهی بیش از حد بحث‌برانگیز, مشکل در تعهد"},
    "INTP": {"title": "INTP - متفکر", "nickname": "فیلسوف", "description": "INTPها تحلیل‌گر، کنجکاو و مستقل هستند...", "strengths": "تحلیل عمیق, خلاقیت, استقلال, کنجکاوی, توانایی حل مسائل پیچیده", "weaknesses": "تمایل به انزوا, مشکل در عمل‌گرایی, بی‌توجهی به جزئیات عملی, گاهی بیش از حد انتقادی"},
    "ENFP": {"title": "ENFP - مبارز", "nickname": "الهام‌بخش", "description": "ENFPها پرانرژی، خلاق و ایده‌آلیست هستند...", "strengths": "خلاقیت, همدلی, انعطاف‌پذیری, مهارت‌های ارتباطی قوی, توانایی الهام‌بخشی", "weaknesses": "مشکل در تمرکز, حساسیت به انتقاد, بی‌نظمی, گاهی بیش از حد ایده‌آلیستی"},
    "INFP": {"title": "INFP - میانجی", "nickname": "شفابخش", "description": "INFPها ایده‌آلیست، خلاق و همدل هستند...", "strengths": "همدلی بالا, خلاقیت, وفاداری به ارزش‌ها, توانایی الهام‌بخشی, انعطاف‌پذیری", "weaknesses": "بیش از حد ایده‌آلیستی, مشکل در مواجهه با انتقاد, تمایل به انزوا, دشواری در تصمیم‌گیری‌های منطقی"}
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
                # تغییر نوع از INTEGER به NUMBER
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

# --- Gemini Interaction Functions ---
def determine_mbti_from_gemini_args(extraversion_introversion, sensing_intuition, thinking_feeling, judging_perceiving):
    return {"mbti_type": f"{extraversion_introversion}{sensing_intuition}{thinking_feeling}{judging_perceiving}"}

def create_prompt_for_mbti(questions: List[str], answers: List[str]) -> str:
    prompt = "بر اساس پاسخ‌های کاربر به سوالات زیر، تیپ شخصیتی MBTI او را تشخیص بده... سپس تابع 'determine_mbti' را فراخوانی کن.\n\n"
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q)}\n**پاسخ {i+1}:** {html.escape(a)}\n\n"
    return prompt

async def get_mbti_type_from_gemini(questions: List[str], answers: List[str]) -> str:
    prompt = create_prompt_for_mbti(questions, answers)
    try:
        response = await gemini_model_for_type.generate_content_async(prompt)
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call and part.function_call.name == "determine_mbti":
                    args = dict(part.function_call.args)
                    result = determine_mbti_from_gemini_args(**args) # Unpack args
                    return result["mbti_type"]
        print("Gemini تابع determine_mbti را فراخوانی نکرد یا پاسخ معتبر نبود.")
        return "خطا: عدم تشخیص نوع MBTI"
    except Exception as e:
        print(f"خطا در get_mbti_type_from_gemini: {e}")
        return f"خطا در پردازش نوع: {str(e)[:100]}" # Truncate long errors

def create_prompt_for_all_percentages(questions: List[str], answers: List[str], mbti_type: str) -> str:
    prompt = (
        f"کاربر به سوالات زیر پاسخ داده و تیپ شخصیتی او {mbti_type} تشخیص داده شده است. "
        "لطفاً بر اساس پاسخ‌ها، برای هر یک از هشت ترجیح MBTI (برونگرایی، درونگرایی، حسی، شهودی، منطقی، احساسی، قضاوتی، ادراکی) "
        "یک درصد تخمینی (بین ۰ تا ۱۰۰) ارائه دهید. "
        "مجموع درصدها برای هر زوج مخالف (مثلاً برونگرایی + درونگرایی) باید ۱۰۰ شود. "
        "سپس تابع 'estimate_all_eight_mbti_preferences' را با این هشت درصد فراخوانی کن.\n\n"
    )
    for i, (q, a) in enumerate(zip(questions, answers)):
        prompt += f"**سوال {i+1}:** {html.escape(q)}\n**پاسخ {i+1}:** {html.escape(a)}\n\n"
    return prompt

async def get_all_eight_mbti_percentages_from_gemini(questions: List[str], answers: List[str], mbti_type: str) -> Optional[Dict[str, int]]:
    if "خطا" in mbti_type:
        return None
        
    prompt = create_prompt_for_all_percentages(questions, answers, mbti_type)
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

                        # بررسی اینکه آیا مقادیر عددی هستند (صحیح یا اعشاری)
                        if not (isinstance(p1_val_orig, (int, float)) and isinstance(p2_val_orig, (int, float))):
                            print(f"مقدار غیرعددی یا گمشده برای زوج: {p1_key}={p1_val_orig}, {p2_key}={p2_val_orig}")
                            all_pairs_valid = False
                            break
                        
                        # تبدیل به عدد صحیح (گرد کردن) و بررسی محدوده 0-100
                        p1_int = round(float(p1_val_orig))
                        p2_int = round(float(p2_val_orig))

                        if not (0 <= p1_int <= 100 and 0 <= p2_int <= 100):
                            print(f"درصد خارج از محدوده برای زوج: {p1_key}={p1_int}, {p2_key}={p2_int}")
                            all_pairs_valid = False
                            break
                        
                        # بررسی مجموع زوج‌ها (با کمی تلورانس)
                        if not (98 <= (p1_int + p2_int) <= 102):
                            print(f"مجموع زوج درصدها برای {p1_key} و {p2_key} برابر ۱۰۰ نیست: {p1_int} + {p2_int} = {p1_int + p2_int}")
                            # اینجا می‌توانید منطق نرمال‌سازی را اضافه کنید اگر می‌خواهید
                            # برای سادگی، فعلا سخت‌گیرانه عمل می‌کنیم
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
        # import traceback
        # traceback.print_exc() # برای دیباگ بیشتر
        return None


def get_reasoning_for_mbti(mbti_type: str, answers: List[str]) -> str:
    # (No changes from previous)
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

    escaped_answers_html = ""
    for i, (q, a) in enumerate(zip(user_questions, user_answers)):
        escaped_answers_html += f"<p><strong>سوال {i+1}:</strong> {html.escape(q)}</p>"
        escaped_answers_html += f"<div class='answer'>{html.escape(a)}</div>"
    
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

        # Radar Chart Data (All 8 preferences or 4 dominant based on strength)
        # For simplicity using 8 preferences
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
        <div class="section charts-dashboard">
            <h2>تحلیل گرافیکی ترجیحات شما ({html.escape(mbti_type)})</h2>
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
    <h1>{html.escape(info['title'])} ({html.escape(info['nickname'])})</h1>
    {charts_html_section} 
    <div class="section"><h2>توضیحات تیپ شخصیتی</h2><p>{html.escape(info['description'])}</p></div>
    <div class="section"><h2>نقاط قوت</h2><ul>{''.join(f'<li>{html.escape(strength.strip())}</li>' for strength in info['strengths'].split(','))}</ul></div>
    <div class="section"><h2>نقاط ضعف</h2><ul>{''.join(f'<li>{html.escape(weakness.strip())}</li>' for weakness in info['weaknesses'].split(','))}</ul></div>
    <div class="section"><h2>چرا این تیپ برای شما انتخاب شد؟</h2>
        <p>پاسخ‌های شما به سوالات زیر تحلیل شد:</p>{escaped_answers_html}
    </div>
    <script>
        const pieChartData = {pie_chart_data_js};
        const barChartsData = {bar_charts_data_js};
        const radarChartData = {radar_chart_data_js};
    </script>
    """
    return html_content

# --- FastAPI Endpoints (Registration, Questions are similar) ---
@app.get("/", response_class=HTMLResponse)
async def get_registration_page(request: Request, error: str = None, phone: str = None):
    # (No changes)
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
    # (No changes)
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
    # (No changes)
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
    
    mbti_type_result = await get_mbti_type_from_gemini(questions_for_user, answers)
    
    all_mbti_percentages_dict = None
    encrypted_percentages_blob = None
    if not "خطا" in mbti_type_result: # Only proceed if type determination was successful
        all_mbti_percentages_dict = await get_all_eight_mbti_percentages_from_gemini(questions_for_user, answers, mbti_type_result)
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
        # Handle DB error, perhaps show an error page
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
    # (No changes from previous, should correctly display the dictionary of 8 percentages now)
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