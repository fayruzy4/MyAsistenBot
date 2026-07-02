import math
from datetime import datetime, timedelta, timezone
from html import escape
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

WIB = timezone(timedelta(hours=7))

def get_today_str(): return datetime.now(WIB).strftime("%Y-%m-%d")

# ================= REFRESH ENGINE =================

def show_habit_dashboard(bot, message, supabase, user_id):
    """Fungsi utama untuk me-render dashboard dengan data FRESH dari Supabase."""
    
    # 1. Fetch data terbaru dari DB
    habits = supabase.table("habits").select("*").eq("user_id", str(user_id)).eq("is_active", True).execute().data
    logs = supabase.table("habit_logs").select("habit_id").eq("user_id", str(user_id)).eq("log_date", get_today_str()).execute().data
    stats = supabase.table("habit_stats").select("*").eq("user_id", str(user_id)).execute().data
    
    s = stats[0] if stats else {"xp": 0, "level": 1, "current_streak": 0}
    completed_ids = [l["habit_id"] for l in logs]
    
    # 2. Hitung progress dinamis
    total = len(habits)
    done = len(completed_ids)
    
    text = (
        f"🎯 <b>Habit Tracker</b>\n\n"
        f"🔥 <b>Streak:</b> {s['current_streak']} Hari | ⭐ <b>Lv:</b> {s['level']}\n"
        f"⚡ <b>XP:</b> {s['xp']}\n"
        f"📈 <b>Progress:</b> {done}/{total} Selesai\n\n"
    )
    
    kb = InlineKeyboardMarkup()
    for h in habits:
        icon = "☑" if h["id"] in completed_ids else "☐"
        kb.row(InlineKeyboardButton(f"{icon} {h['name']}", callback_data=f"habit_toggle:{h['id']}"))
    
    kb.row(InlineKeyboardButton("➕ Tambah", callback_data="habit_add_start"), InlineKeyboardButton("📋 Kelola", callback_data="habit_manage_list"))
    kb.row(InlineKeyboardButton("🗑 Hapus Habit", callback_data="habit_delete_list"), InlineKeyboardButton("📊 Stat", callback_data="habit_stats"))
    kb.row(InlineKeyboardButton("🏠 Dashboard Utama", callback_data="back_dashboard"))
    
    try:
        bot.edit_message_text(text=text, chat_id=message.chat.id, message_id=message.message_id, reply_markup=kb)
    except:
        bot.send_message(message.chat.id, text, reply_markup=kb)

# ================= DELETE LOGIC =================

def process_delete_habit_confirm(bot, message, supabase, user_id, habit_id):
    """Memastikan sinkronisasi data setelah penghapusan."""
    
    # 1. Hapus seluruh data yang berelasi dengan habit ini
    supabase.table("habit_logs").delete().eq("habit_id", habit_id).execute()
    supabase.table("habits").update({"is_active": False}).eq("id", habit_id).execute()
    
    # 2. Kirim notifikasi konfirmasi
    try:
        bot.answer_callback_query(message.id, "Habit dihapus!")
    except: pass
    
    # 3. Paksa Dashboard memuat ulang dari nol (Query Supabase baru)
    show_habit_dashboard(bot, message, supabase, user_id)

# ================= TOGGLE LOGIC =================

def handle_habit_toggle(bot, call, supabase, user_id, habit_id):
    today = get_today_str()
    log = supabase.table("habit_logs").select("id").eq("habit_id", habit_id).eq("log_date", today).execute().data
    
    if log:
        supabase.table("habit_logs").delete().eq("id", log[0]["id"]).execute()
        # XP Reduction if needed, or simply pass
    else:
        supabase.table("habit_logs").insert({"habit_id": habit_id, "user_id": str(user_id), "log_date": today}).execute()
        # XP Addition logic here...
        
    # Refresh dashboard setelah perubahan
    show_habit_dashboard(bot, call.message, supabase, user_id)

# ================= HELPER FUNGSI LAINNYA =================

def start_add_habit(bot, message):
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🟢 Mudah", callback_data="habit_add_diff:mudah"), InlineKeyboardButton("🟡 Sedang", callback_data="habit_add_diff:sedang"))
    bot.edit_message_text("Pilih tingkat kesulitan:", message.chat.id, message.message_id, reply_markup=kb)

def process_add_habit_difficulty(bot, message, diff):
    bot.send_message(message.chat.id, "Ketik nama habit:")

def process_add_habit_name(bot, message, supabase, action):
    supabase.table("habits").insert({"user_id": str(message.from_user.id), "name": message.text, "difficulty": action["diff"]}).execute()
    return True

def show_habit_manage_list(bot, message, supabase, user_id):
    habits = supabase.table("habits").select("id, name").eq("user_id", str(user_id)).eq("is_active", True).execute().data
    kb = InlineKeyboardMarkup()
    for h in habits: kb.row(InlineKeyboardButton(f"📝 {h['name']}", callback_data=f"habit_manage_opt:{h['id']}"))
    kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
    safe_render(bot, message, "📋 <b>Kelola Habit</b>", kb)

def show_habit_manage_options(bot, message, habit_id):
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_manage_list"))
    safe_render(bot, message, "⚙️ <b>Opsi Habit</b>", kb)

def show_habit_delete_list(bot, message, supabase, user_id):
    habits = supabase.table("habits").select("id, name").eq("user_id", str(user_id)).eq("is_active", True).execute().data
    kb = InlineKeyboardMarkup()
    for h in habits: kb.row(InlineKeyboardButton(f"🗑 {h['name']}", callback_data=f"habit_delete_confirm:{h['id']}"))
    kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
    safe_render(bot, message, "⚠️ <b>Pilih Habit untuk Dihapus:</b>", kb)

def show_habit_stats(bot, message, supabase, user_id):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
    safe_render(bot, message, "📊 <b>Statistik</b>\n\nData telah sinkron dengan database.", kb)

def show_habit_achievements(bot, message, supabase, user_id):
    pass

def start_edit_habit_name(bot, message):
    bot.send_message(message.chat.id, "Ketik nama baru:")

def process_edit_habit_name(bot, message, supabase, action):
    supabase.table("habits").update({"name": message.text}).eq("id", action["habit_id"]).execute()
    return True

def process_edit_habit_difficulty(bot, message, supabase, user_id, habit_id, new_diff):
    pass
