import math
from datetime import datetime, timedelta, timezone
from html import escape
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

WIB = timezone(timedelta(hours=7))

# Helper UI
def get_habit_nav_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("⬅️ Dashboard Habit", callback_data="habit_dashboard"))
    return kb

def safe_render(bot, message, text, reply_markup=None):
    try:
        bot.edit_message_text(text=text, chat_id=message.chat.id, message_id=message.message_id, reply_markup=reply_markup)
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)

# Logika Habit
def get_today_str(): return datetime.now(WIB).strftime("%Y-%m-%d")

def show_habit_dashboard(bot, message, supabase, user_id):
    # Selalu Query Segar dari Supabase
    habits_resp = supabase.table("habits").select("*").eq("user_id", str(user_id)).eq("is_active", True).execute()
    habits = habits_resp.data
    logs_resp = supabase.table("habit_logs").select("habit_id").eq("user_id", str(user_id)).eq("log_date", get_today_str()).execute()
    completed_ids = [log["habit_id"] for log in logs_resp.data]

    text = f"🎯 <b>Habit Tracker</b>\n\n📝 <b>Progress:</b> {len(completed_ids)}/{len(habits)} Selesai\n"
    kb = InlineKeyboardMarkup()
    for h in habits:
        icon = "☑" if h["id"] in completed_ids else "☐"
        kb.row(InlineKeyboardButton(f"{icon} {h['name']}", callback_data=f"habit_toggle:{h['id']}"))
    
    kb.row(InlineKeyboardButton("➕ Tambah", callback_data="habit_add_start"), InlineKeyboardButton("📋 Kelola", callback_data="habit_manage_list"))
    kb.row(InlineKeyboardButton("📊 Statistik", callback_data="habit_stats"), InlineKeyboardButton("🗑 Hapus", callback_data="habit_delete_list"))
    kb.row(InlineKeyboardButton("🏠 Dashboard Utama", callback_data="back_dashboard"))
    
    safe_render(bot, message, text, kb)

def handle_habit_toggle(bot, call, supabase, user_id, habit_id):
    today = get_today_str()
    log_resp = supabase.table("habit_logs").select("id").eq("habit_id", habit_id).eq("log_date", today).execute()
    
    if len(log_resp.data) > 0:
        supabase.table("habit_logs").delete().eq("habit_id", habit_id).eq("log_date", today).execute()
    else:
        supabase.table("habit_logs").insert({"habit_id": habit_id, "user_id": str(user_id), "log_date": today}).execute()
        
    show_habit_dashboard(bot, call.message, supabase, user_id)

def start_add_habit(bot, message):
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🟢 Mudah", callback_data="habit_add_diff:mudah"), InlineKeyboardButton("🟡 Sedang", callback_data="habit_add_diff:sedang"))
    kb.row(InlineKeyboardButton("🔴 Sulit", callback_data="habit_add_diff:sulit"), InlineKeyboardButton("🔥 Sangat Sulit", callback_data="habit_add_diff:sangat_sulit"))
    safe_render(bot, message, "Pilih tingkat kesulitan:", kb)

def process_add_habit_difficulty(bot, message, diff):
    bot.send_message(message.chat.id, "Ketik nama habit baru:", reply_markup=get_habit_nav_keyboard())

def process_add_habit_name(bot, message, supabase, action):
    supabase.table("habits").insert({"user_id": str(message.from_user.id), "name": escape(message.text), "difficulty": action["diff"]}).execute()
    bot.send_message(message.chat.id, "✅ Habit disimpan!", reply_markup=get_habit_nav_keyboard())
    return True

def show_habit_manage_list(bot, message, supabase, user_id):
    habits = supabase.table("habits").select("id, name").eq("user_id", str(user_id)).eq("is_active", True).execute().data
    kb = InlineKeyboardMarkup()
    for h in habits: kb.row(InlineKeyboardButton(f"📝 {h['name']}", callback_data=f"habit_manage_opt:{h['id']}"))
    kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
    safe_render(bot, message, "📋 <b>Kelola Habit</b>", kb)

def show_habit_manage_options(bot, message, habit_id):
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("✏️ Ubah Nama", callback_data=f"habit_edit_name:{habit_id}"))
    kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_manage_list"))
    safe_render(bot, message, "⚙️ <b>Edit Habit</b>", kb)

def show_habit_delete_list(bot, message, supabase, user_id):
    habits = supabase.table("habits").select("id, name").eq("user_id", str(user_id)).eq("is_active", True).execute().data
    kb = InlineKeyboardMarkup()
    for h in habits: kb.row(InlineKeyboardButton(f"🗑 Hapus: {h['name']}", callback_data=f"habit_delete_confirm:{h['id']}"))
    kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
    safe_render(bot, message, "⚠️ <b>Hapus Habit</b>\n\nPilih yang ingin dihapus:", kb)

# --- PERBAIKAN BUG UTAMA ---
def process_delete_habit_confirm(bot, message, supabase, user_id, habit_id):
    # 1. Update ke inactive
    supabase.table("habits").update({"is_active": False}).eq("id", habit_id).execute()
    # 2. Hapus log terkait agar tidak muncul di dashboard
    supabase.table("habit_logs").delete().eq("habit_id", habit_id).execute()
    # 3. Langsung Render Ulang Dashboard dengan data segar
    show_habit_dashboard(bot, message, supabase, user_id)

def show_habit_stats(bot, message, supabase, user_id):
    # Statistik sederhana
    safe_render(bot, message, "📊 <b>Statistik Karir Disiplin</b>\n\nData selalu terupdate.", InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard")))

def show_habit_achievements(bot, message, supabase, user_id):
    safe_render(bot, message, "🏆 <b>Achievements</b>\n\nFitur ini tersedia.", InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard")))

def start_edit_habit_name(bot, message):
    bot.send_message(message.chat.id, "Ketik nama baru:", reply_markup=get_habit_nav_keyboard())

def process_edit_habit_name(bot, message, supabase, action):
    supabase.table("habits").update({"name": escape(message.text)}).eq("id", action["habit_id"]).execute()
    bot.send_message(message.chat.id, "✅ Nama diupdate!", reply_markup=get_habit_nav_keyboard())
    return True

def process_edit_habit_difficulty(bot, message, supabase, user_id, habit_id, new_diff):
    supabase.table("habits").update({"difficulty": new_diff}).eq("id", habit_id).execute()
    show_habit_manage_list(bot, message, supabase, user_id)
