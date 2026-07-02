import traceback
import math
from datetime import datetime, timedelta, timezone
from html import escape
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Zona Waktu WIB (UTC+7)
WIB = timezone(timedelta(hours=7))

# ================= KONFIGURASI LOGIKA RPG =================

DIFFICULTY_XP = {
    "mudah": 10,
    "sedang": 20,
    "sulit": 30,
    "sangat_sulit": 50
}

DIFFICULTY_LABEL = {
    "mudah": "🟢 Mudah",
    "sedang": "🟡 Sedang",
    "sulit": "🔴 Sulit",
    "sangat_sulit": "🔥 Sangat Sulit"
}

def get_today_str():
    return datetime.now(WIB).strftime("%Y-%m-%d")

def calculate_level(xp):
    """Rumus RPG klasik: Level = floor((1 + sqrt(1 + 8 * XP / 100)) / 2)"""
    if xp < 0:
        return 1
    return math.floor((1 + math.sqrt(1 + 8 * xp / 100)) / 2)

def get_rank_name(level):
    """Pangkat militer elegan berdasarkan level."""
    if level < 3: return "Recruit"
    if level < 5: return "Cadet"
    if level < 8: return "Private"
    if level < 12: return "Corporal"
    if level < 16: return "Sergeant"
    if level < 22: return "Lieutenant"
    if level < 30: return "Captain"
    if level < 40: return "Major"
    if level < 50: return "Colonel"
    if level < 60: return "Commander"
    if level < 75: return "Senior Commander"
    if level < 90: return "General"
    if level < 100: return "Grand General"
    return "Supreme Commander"

# ================= HELPERS & UI =================

def safe_render(bot, message, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)

def get_habit_nav_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("❌ Batal", callback_data="habit_dashboard"))
    kb.row(
        InlineKeyboardButton("⬅️ Dashboard Habit", callback_data="habit_dashboard"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="back_dashboard")
    )
    return kb

# ================= FUNGSI DATABASE HABIT =================

def get_or_create_stats(supabase, user_id):
    resp = supabase.table("habit_stats").select("*").eq("user_id", str(user_id)).execute()
    if not resp.data:
        new_data = {"user_id": str(user_id), "xp": 0, "level": 1, "current_streak": 0}
        supabase.table("habit_stats").insert(new_data).execute()
        return new_data
    return resp.data[0]

def update_stats(supabase, user_id, xp_delta, streak_delta=0, update_active_date=True):
    stats = get_or_create_stats(supabase, user_id)
    new_xp = max(0, stats["xp"] + xp_delta)
    new_level = calculate_level(new_xp)
    new_streak = max(0, stats["current_streak"] + streak_delta)
    highest = max(stats["highest_streak"], new_streak)
    
    update_data = {
        "xp": new_xp,
        "level": new_level,
        "current_streak": new_streak,
        "highest_streak": highest
    }
    
    if update_active_date:
        update_data["last_active_date"] = get_today_str()
        
    supabase.table("habit_stats").update(update_data).eq("user_id", str(user_id)).execute()
    return update_data

# ================= DASHBOARD & CHECKLIST =================

def show_habit_dashboard(bot, message, supabase, user_id):
    try:
        stats = get_or_create_stats(supabase, user_id)
        today = get_today_str()
        
        # Logika reset streak jika bolong
        last_active = stats.get("last_active_date")
        if last_active:
            last_date_obj = datetime.strptime(last_active, "%Y-%m-%d")
            today_obj = datetime.strptime(today, "%Y-%m-%d")
            diff_days = (today_obj - last_date_obj).days
            
            # Jika user bolong lebih dari 1 hari dan dia gak komplit di hari sebelumnya
            # (Untuk simpel, jika diff > 1 hari, streak reset)
            if diff_days > 1:
                stats = update_stats(supabase, user_id, 0, -stats["current_streak"], update_active_date=True)
            elif diff_days == 1:
                # Update last active untuk hari ini
                supabase.table("habit_stats").update({"last_active_date": today}).eq("user_id", str(user_id)).execute()
        else:
            supabase.table("habit_stats").update({"last_active_date": today}).eq("user_id", str(user_id)).execute()

        # Fetch Habits & Logs
        habits_resp = supabase.table("habits").select("*").eq("user_id", str(user_id)).eq("is_active", True).execute()
        habits = habits_resp.data

        logs_resp = supabase.table("habit_logs").select("habit_id").eq("user_id", str(user_id)).eq("log_date", today).execute()
        completed_habit_ids = [log["habit_id"] for log in logs_resp.data]

        total_habits = len(habits)
        done_habits = len(completed_habit_ids)
        
        rank = get_rank_name(stats["level"])
        
        text = (
            f"🎯 <b>Habit Tracker</b>\n\n"
            f"🔥 <b>Streak:</b> {stats['current_streak']} Hari\n"
            f"⭐ <b>Level:</b> {stats['level']} | 🏅 <b>Rank:</b> {rank}\n"
            f"⚡ <b>XP Total:</b> {stats['xp']}\n\n"
            f"📝 <b>Progress Hari Ini:</b> {done_habits}/{total_habits} Selesai\n"
        )
        
        if total_habits == 0:
            text += "\n<i>Kamu belum punya kebiasaan yang dicatat. Yuk buat sekarang!</i>"
        elif done_habits == total_habits:
            text += "\n🎉 <i>Luar biasa! Semua target hari ini tuntas!</i>"
        else:
            text += "\n<i>Ayo selesaikan targetmu hari ini!</i>"

        kb = InlineKeyboardMarkup()
        
        # Render Checklist Button
        for h in habits:
            is_done = h["id"] in completed_habit_ids
            icon = "☑" if is_done else "☐"
            btn_text = f"{icon} {h['name']}"
            kb.row(InlineKeyboardButton(btn_text, callback_data=f"habit_toggle:{h['id']}"))
            
        kb.row(
            InlineKeyboardButton("➕ Tambah", callback_data="habit_add_start"),
            InlineKeyboardButton("📋 Kelola", callback_data="habit_manage_list")
        )
        kb.row(
            InlineKeyboardButton("📊 Statistik", callback_data="habit_stats"),
            InlineKeyboardButton("🗑 Hapus", callback_data="habit_delete_list")
        )
        kb.row(InlineKeyboardButton("⬅️ Dashboard Utama", callback_data="back_dashboard"))

        safe_render(bot, message, text, kb)

    except Exception as exc:
        print(f"Error di show_habit_dashboard: {exc}")
        bot.send_message(message.chat.id, "😔 Gagal memuat Habit Tracker.")

def handle_habit_toggle(bot, call, supabase, user_id, habit_id):
    try:
        today = get_today_str()
        
        # Cek habit info
        habit_resp = supabase.table("habits").select("difficulty").eq("id", habit_id).execute()
        if not habit_resp.data:
            bot.answer_callback_query(call.id, "Habit tidak ditemukan.")
            return
            
        difficulty = habit_resp.data[0]["difficulty"]
        xp_value = DIFFICULTY_XP.get(difficulty, 10)
        
        # Cek apakah sudah dicentang
        log_resp = supabase.table("habit_logs").select("id").eq("habit_id", habit_id).eq("log_date", today).execute()
        is_already_done = len(log_resp.data) > 0
        
        if is_already_done:
            # Uncheck
            supabase.table("habit_logs").delete().eq("habit_id", habit_id).eq("log_date", today).execute()
            update_stats(supabase, user_id, -xp_value)
            bot.answer_callback_query(call.id, "Dibatalkan. Yah, semangat lagi ya!")
        else:
            # Check
            supabase.table("habit_logs").insert({
                "habit_id": habit_id,
                "user_id": str(user_id),
                "log_date": today
            }).execute()
            
            # Cek apakah ini melengkapi semua habit hari ini (Bonus Streak)
            habits_resp = supabase.table("habits").select("id").eq("user_id", str(user_id)).eq("is_active", True).execute()
            logs_resp = supabase.table("habit_logs").select("habit_id").eq("user_id", str(user_id)).eq("log_date", today).execute()
            
            is_all_done = len(logs_resp.data) == len(habits_resp.data)
            
            xp_gained = xp_value
            streak_added = 0
            msg_toast = f"+{xp_value} XP! Lanjutkan!"
            
            if is_all_done:
                xp_gained += 50  # Bonus All Done
                streak_added = 1
                msg_toast = f"🎉 Keren! Semua selesai! +{xp_gained} XP & Streak Naik!"
                
            update_stats(supabase, user_id, xp_gained, streak_added)
            bot.answer_callback_query(call.id, msg_toast)

        # Re-render dashboard
        show_habit_dashboard(bot, call.message, supabase, user_id)
        
    except Exception as exc:
        print(f"Error di handle_habit_toggle: {exc}")
        bot.answer_callback_query(call.id, "Gagal mengubah status.")

# ================= TAMBAH HABIT =================

def start_add_habit(bot, message):
    text = (
        "➕ <b>Tambah Kebiasaan Baru</b>\n\n"
        "Seberapa sulit kebiasaan ini untuk kamu lakukan setiap hari?"
    )
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🟢 Mudah", callback_data="habit_add_diff:mudah"),
        InlineKeyboardButton("🟡 Sedang", callback_data="habit_add_diff:sedang")
    )
    kb.row(
        InlineKeyboardButton("🔴 Sulit", callback_data="habit_add_diff:sulit"),
        InlineKeyboardButton("🔥 Sangat Sulit", callback_data="habit_add_diff:sangat_sulit")
    )
    kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
    
    safe_render(bot, message, text, kb)

def process_add_habit_difficulty(bot, message, diff):
    text = (
        f"Kategori: {DIFFICULTY_LABEL.get(diff, diff)}\n\n"
        "Silakan ketik <b>Nama Kebiasaan</b> yang ingin kamu bangun!\n"
        "<i>Contoh: Sholat Subuh Tepat Waktu, Lari 15 Menit, dsb.</i>\n\n"
        "Ketik dan kirim ke bot sekarang. 👇"
    )
    # Kirim sebagai pesan baru agar user sadar harus ngetik
    bot.send_message(message.chat.id, text, reply_markup=get_habit_nav_keyboard())

def process_add_habit_name(bot, message, supabase, action):
    try:
        name = (message.text or "").strip()
        diff = action.get("diff", "mudah")
        
        if len(name) < 3:
            bot.send_message(
                message.chat.id, 
                "⚠️ Nama habit terlalu pendek. Coba ketik yang lebih jelas ya!",
                reply_markup=get_habit_nav_keyboard()
            )
            return False

        supabase.table("habits").insert({
            "user_id": str(message.from_user.id),
            "name": escape(name),
            "difficulty": diff
        }).execute()
        
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("⬅️ Kembali ke Habit Dashboard", callback_data="habit_dashboard"))
        
        bot.send_message(
            message.chat.id,
            f"✅ <b>Berhasil Disimpan!</b>\n\nHabit <b>{escape(name)}</b> sekarang siap untuk ditaklukkan setiap hari! 💪",
            reply_markup=kb
        )
        return True
    except Exception as exc:
        print(f"Error process_add_habit_name: {exc}")
        return False

# ================= KELOLA & EDIT HABIT =================

def show_habit_manage_list(bot, message, supabase, user_id):
    try:
        resp = supabase.table("habits").select("id, name, difficulty").eq("user_id", str(user_id)).eq("is_active", True).execute()
        habits = resp.data
        
        kb = InlineKeyboardMarkup()
        if not habits:
            kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
            safe_render(bot, message, "Belum ada habit untuk dikelola.", kb)
            return

        for h in habits:
            kb.row(InlineKeyboardButton(f"📝 {h['name']}", callback_data=f"habit_manage_opt:{h['id']}"))
            
        kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
        safe_render(bot, message, "📋 <b>Kelola Habit</b>\n\nPilih habit yang ingin kamu edit pengaturannya:", kb)
    except Exception:
        bot.send_message(message.chat.id, "Gagal memuat kelola habit.")

def show_habit_manage_options(bot, message, habit_id):
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("✏️ Ubah Nama", callback_data=f"habit_edit_name:{habit_id}"))
    kb.row(
        InlineKeyboardButton("🟢 Mudah", callback_data=f"habit_edit_diff:{habit_id}:mudah"),
        InlineKeyboardButton("🟡 Sedang", callback_data=f"habit_edit_diff:{habit_id}:sedang")
    )
    kb.row(
        InlineKeyboardButton("🔴 Sulit", callback_data=f"habit_edit_diff:{habit_id}:sulit"),
        InlineKeyboardButton("🔥 Epic", callback_data=f"habit_edit_diff:{habit_id}:sangat_sulit")
    )
    kb.row(InlineKeyboardButton("⬅️ Batal / Kembali", callback_data="habit_manage_list"))
    
    text = "⚙️ <b>Edit Habit</b>\n\nPilih tindakan yang ingin kamu lakukan atau pilih langsung kesulitan barunya:"
    safe_render(bot, message, text, kb)

def start_edit_habit_name(bot, message):
    text = "Silakan ketik <b>Nama Baru</b> untuk habit ini dan kirim ke bot:"
    bot.send_message(message.chat.id, text, reply_markup=get_habit_nav_keyboard())

def process_edit_habit_name(bot, message, supabase, action):
    try:
        new_name = (message.text or "").strip()
        habit_id = action.get("habit_id")
        
        if len(new_name) < 3:
            bot.send_message(message.chat.id, "Nama terlalu pendek.", reply_markup=get_habit_nav_keyboard())
            return False
            
        supabase.table("habits").update({"name": escape(new_name)}).eq("id", habit_id).execute()
        
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("⬅️ Lanjut Kelola", callback_data="habit_manage_list"))
        bot.send_message(message.chat.id, f"✅ Nama berhasil diubah menjadi: <b>{escape(new_name)}</b>", reply_markup=kb)
        return True
    except Exception:
        return False

def process_edit_habit_difficulty(bot, message, supabase, user_id, habit_id, new_diff):
    try:
        supabase.table("habits").update({"difficulty": new_diff}).eq("id", habit_id).execute()
        show_habit_manage_list(bot, message, supabase, user_id)
    except Exception:
        bot.send_message(message.chat.id, "Gagal mengubah kesulitan.")

# ================= HAPUS HABIT =================

def show_habit_delete_list(bot, message, supabase, user_id):
    try:
        resp = supabase.table("habits").select("id, name").eq("user_id", str(user_id)).eq("is_active", True).execute()
        habits = resp.data
        
        kb = InlineKeyboardMarkup()
        if not habits:
            kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
            safe_render(bot, message, "Belum ada habit untuk dihapus.", kb)
            return

        for h in habits:
            kb.row(InlineKeyboardButton(f"🗑 Hapus: {h['name']}", callback_data=f"habit_delete_confirm:{h['id']}"))
            
        kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
        safe_render(bot, message, "⚠️ <b>Hapus Habit</b>\n\nPilih habit yang ingin dihapus. Tindakan ini membuat habit tidak akan muncul lagi di checklist.", kb)
    except Exception:
        bot.send_message(message.chat.id, "Gagal memuat daftar hapus.")

def process_delete_habit_confirm(bot, message, supabase, user_id, habit_id):
    try:
        supabase.table("habits").update({"is_active": False}).eq("id", habit_id).execute()
        show_habit_delete_list(bot, message, supabase, user_id)
    except Exception:
        bot.send_message(message.chat.id, "Gagal menghapus habit.")

# ================= STATISTIK & ACHIEVEMENT =================

def show_habit_stats(bot, message, supabase, user_id):
    try:
        stats = get_or_create_stats(supabase, user_id)
        
        habits_resp = supabase.table("habits").select("id").eq("user_id", str(user_id)).eq("is_active", True).execute()
        total_habits = len(habits_resp.data)
        
        logs_resp = supabase.table("habit_logs").select("id", count="exact").eq("user_id", str(user_id)).execute()
        total_logs = logs_resp.count or 0

        rank = get_rank_name(stats["level"])
        next_level_xp = (stats["level"]) * (stats["level"]) * 100 # Rough visualization
        
        text = (
            "📊 <b>Statistik Karir Disiplinmu</b>\n\n"
            f"🏅 <b>Pangkat:</b> {rank}\n"
            f"⭐ <b>Level:</b> {stats['level']}\n"
            f"⚡ <b>Total XP:</b> {stats['xp']}\n\n"
            f"🔥 <b>Streak Saat Ini:</b> {stats['current_streak']} Hari\n"
            f"👑 <b>Streak Tertinggi:</b> {stats['highest_streak']} Hari\n\n"
            f"📋 <b>Total Habit Aktif:</b> {total_habits}\n"
            f"✅ <b>Total Checklist Diselesaikan:</b> {total_logs} kali\n\n"
            "<i>Terus pertahankan konsistensimu untuk naik pangkat! Jenderal Besar menunggu!</i>"
        )
        
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("🏆 Lihat Achievement", callback_data="habit_achievements"))
        kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="habit_dashboard"))
        
        safe_render(bot, message, text, kb)
    except Exception:
        bot.send_message(message.chat.id, "Gagal memuat statistik.")

def show_habit_achievements(bot, message, supabase, user_id):
    text = (
        "🏆 <b>Ruang Pencapaian (Achievements)</b>\n\n"
        "<i>Fitur Achievement akan otomatis terbuka berdasarkan statistik yang kamu capai!</i>\n\n"
        "🔒 <b>Prajurit Rajin:</b> Selesaikan 10 Checklist (Terkunci)\n"
        "🔒 <b>7 Hari Berturut:</b> Streak 7 Hari (Terkunci)\n"
        "🔒 <b>Master Komandan:</b> Capai Level 60 (Terkunci)\n\n"
        "<i>(Sistem Achievement akan disinkronkan secara otomatis saat kamu melakukan progress)</i>"
    )
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("⬅️ Kembali ke Statistik", callback_data="habit_stats"))
    kb.row(InlineKeyboardButton("⬅️ Dashboard Habit", callback_data="habit_dashboard"))
    
    safe_render(bot, message, text, kb)
