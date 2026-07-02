import traceback
from html import escape
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


# ================= UTILITIES =================

def format_rupiah(value):
    """Format angka menjadi string Rupiah."""
    try:
        return f"Rp{int(value):,}".replace(",", ".")
    except Exception:
        return "Rp0"


def get_navigation_keyboard(is_success=False):
    """Keyboard navigasi dengan opsi kembali atau batal."""
    kb = InlineKeyboardMarkup()
    if is_success:
        kb.row(
            InlineKeyboardButton("➕ Tambah Target Lain", callback_data="target_add"),
            InlineKeyboardButton("🎯 Lihat Target", callback_data="target_menu")
        )
    else:
        kb.row(InlineKeyboardButton("❌ Batal", callback_data="cancel_input"))
        
    kb.row(
        InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard")
    )
    return kb


def generate_target_bar(current, total, length=12):
    """Membuat progress bar ASCII persentase target tabungan."""
    if total <= 0:
        return "░" * length
    percent = max(0.0, min(1.0, float(current) / float(total)))
    filled = int(round(percent * length))
    return "█" * filled + "░" * (length - filled)


def safe_render(bot, message, text, reply_markup=None):
    """Helper render yang aman."""
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)


# ================= FITUR TARGET TABUNGAN =================

def start_add_target(bot, message):
    try:
        teks = (
            "🎯 <b>Buat Target Tabungan Baru</b>\n\n"
            "Punya impian barang yang mau dibeli? Mari wujudkan pelan-pelan!\n\n"
            "Ketik detail target tabunganmu dengan format ini:\n"
            "<b>Format:</b>\n"
            "<code>Nama Impian#Total Kebutuhan#Sudah Terkumpul</code>\n\n"
            "<b>Contoh:</b>\n"
            "<code>Laptop Baru#10000000#2500000</code>\n\n"
            "<i>💡 Jika kamu belum mulai menabung untuk target ini, isi bagian akhirnya dengan angka 0.</i>\n\n"
            "Tekan <b>Batal</b> di bawah kalau kamu ingin kembali."
        )
        bot.send_message(message.chat.id, teks, reply_markup=get_navigation_keyboard())
    except Exception as exc:
        print(f"Error di start_add_target: {exc}")


def process_add_target(bot, message, supabase_client):
    try:
        raw_text = (message.text or "").strip()
        parts = [p.strip() for p in raw_text.split("#")]

        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "🤔 Formatnya belum lengkap nih.\n\n"
                "Gunakan format:\n"
                "<code>Nama#Total#Terkumpul</code>\n\n"
                "Contoh: <code>Laptop#10000000#0</code>",
                reply_markup=get_navigation_keyboard()
            )
            return False

        name = parts[0]
        total_raw = parts[1].replace(".", "").replace(",", "")

        if not total_raw.isdigit():
            bot.send_message(
                message.chat.id, 
                "⚠️ Total target wajib berupa angka bulat ya. Yuk perbaiki!",
                reply_markup=get_navigation_keyboard()
            )
            return False

        goal_amount = int(total_raw)
        saved_amount = 0

        # Jika bagian ke-3 (terkumpul) disisipkan
        if len(parts) >= 3 and parts[2]:
            saved_raw = parts[2].replace(".", "").replace(",", "")
            if not saved_raw.isdigit():
                bot.send_message(
                    message.chat.id, 
                    "⚠️ Jumlah terkumpul harus berupa angka bulat. Coba lagi ya!",
                    reply_markup=get_navigation_keyboard()
                )
                return False
            saved_amount = int(saved_raw)

        if goal_amount <= 0:
            bot.send_message(
                message.chat.id, 
                "⚠️ Wah, total target impian harus lebih dari 0 dong.",
                reply_markup=get_navigation_keyboard()
            )
            return False

        if saved_amount < 0:
            bot.send_message(
                message.chat.id, 
                "⚠️ Uang terkumpul masa minus? Diperbaiki ya.",
                reply_markup=get_navigation_keyboard()
            )
            return False

        # Insert ke Supabase
        row_data = {
            "user_id": str(message.from_user.id),
            "name": name,
            "goal_amount": goal_amount,
            "saved_amount": saved_amount,
        }
        supabase_client.table("targets").insert(row_data).execute()

        # Feedback sukses
        teks_sukses = (
            f"✅ 🎯 <b>Asyik! Target Impianmu Sudah Dicatat</b>\n\n"
            f"<b>Nama Impian:</b> ✨ {escape(name)}\n"
            f"<b>Total Target:</b> {format_rupiah(goal_amount)}\n"
            f"<b>Sudah Ada:</b> {format_rupiah(saved_amount)}\n\n"
            f"Semoga cepat terkumpul ya! 💪 Pilih menu selanjutnya di bawah ini."
        )

        bot.send_message(
            message.chat.id, 
            teks_sukses, 
            reply_markup=get_navigation_keyboard(is_success=True)
        )
        return True

    except Exception as exc:
        print(f"Error di process_add_target: {exc}")
        bot.send_message(
            message.chat.id, 
            "😔 Maaf, sistem sedang kesulitan menyimpan targetmu.",
            reply_markup=get_navigation_keyboard()
        )
        return False


def show_target_menu(bot, message, supabase_client, user_id):
    try:
        response = (
            supabase_client.table("targets")
            .select("id, name, goal_amount, saved_amount")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .execute()
        )
        rows = response.data

        kb = InlineKeyboardMarkup()

        if rows:
            for item in rows:
                btn_text = f"📌 {item.get('name', 'Target Impian')}"
                kb.add(InlineKeyboardButton(btn_text, callback_data=f"target_detail:{item['id']}"))

        kb.row(
            InlineKeyboardButton("➕ Tambah Target", callback_data="target_add"),
            InlineKeyboardButton("❌ Hapus Target Terakhir", callback_data="target_delete_last"),
        )
        kb.row(
            InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
            InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard")
        )

        text = (
            "🎯 <b>Daftar Target Tabunganmu</b>\n\n"
            "Di sini kamu bisa melihat daftar impian yang sedang kamu kejar.\n"
            "Pilih salah satu target di bawah untuk melihat detail kemajuannya ya! ✨"
        )

        safe_render(bot, message, text, kb)

    except Exception as exc:
        print(f"Error di show_target_menu: {exc}")
        bot.send_message(message.chat.id, "😔 Gagal memuat daftar target tabungan.")


def show_target_detail(bot, message, supabase_client, user_id, target_id):
    try:
        response = (
            supabase_client.table("targets")
            .select("id, name, goal_amount, saved_amount")
            .eq("user_id", str(user_id))
            .eq("id", target_id)
            .limit(1)
            .execute()
        )
        rows = response.data

        if not rows:
            bot.send_message(message.chat.id, "Waduh, target ini tidak ditemukan. 🤷‍♂️")
            return

        item = rows[0]
        name = item.get("name", "Tanpa Nama")
        goal_amount = int(item.get("goal_amount", 0))
        saved_amount = int(item.get("saved_amount", 0))
        remaining = max(goal_amount - saved_amount, 0)
        
        percent = 0.0 if goal_amount <= 0 else min(100.0, (saved_amount / goal_amount) * 100)
        ascii_bar = generate_target_bar(saved_amount, goal_amount, length=15)

        text = (
            f"🎯 <b>Detail Progress Targetmu</b>\n\n"
            f"<b>Impian:</b> ✨ {escape(name)}\n\n"
            f"💰 <b>Total Dibutuhkan:</b> {format_rupiah(goal_amount)}\n"
            f"✅ <b>Sudah Terkumpul:</b> {format_rupiah(saved_amount)}\n"
            f"📉 <b>Sisa Kurang:</b> {format_rupiah(remaining)}\n\n"
            f"<b>Progres:</b> {percent:.1f}%\n"
            f"<code>[{ascii_bar}]</code>\n\n"
        )
        
        if remaining <= 0:
            text += "🎉 <b>Luar Biasa! Tabungan impianmu sudah tercapai!</b> 🥳"
        else:
            text += "Sedikit lagi pasti bisa! Jangan menyerah menabungnya ya! 💪"

        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("⬅️ Kembali ke Daftar Target", callback_data="target_menu"))
        kb.row(
            InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
            InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard")
        )

        safe_render(bot, message, text, kb)

    except Exception as exc:
        print(f"Error di show_target_detail: {exc}")
        bot.send_message(message.chat.id, "😔 Gagal memuat detail target.")


def delete_last_target(bot, message, supabase_client, user_id):
    try:
        response = (
            supabase_client.table("targets")
            .select("id")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data

        if not rows:
            bot.send_message(message.chat.id, "Belum ada target yang bisa dihapus nih. 🤷‍♂️")
            return

        target_id = rows[0]["id"]
        supabase_client.table("targets").delete().eq("id", target_id).execute()
        
        # Merender ulang daftar
        show_target_menu(bot, message, supabase_client, user_id)

    except Exception as exc:
        print(f"Error di delete_last_target: {exc}")
        bot.send_message(message.chat.id, "😔 Gagal menghapus target terakhir.")
    
