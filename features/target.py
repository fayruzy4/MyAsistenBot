import traceback
from datetime import datetime, timezone
from html import escape

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


def rupiah(value):
    try:
        return f"Rp{int(value):,}".replace(",", ".")
    except Exception:
        return "Rp0"


def notify_bug(bot, where, exc, notify_owner=None):
    msg = (
        f"🚨 BUG TERDETEKSI\n\n"
        f"Lokasi: {where}\n"
        f"Error: {exc}\n\n"
        f"{traceback.format_exc()}"
    )
    print(msg)
    if callable(notify_owner):
        try:
            notify_owner(msg)
        except Exception:
            pass


def safe_render(bot, message, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)


def progress_bar(current, total, length=10):
    if total <= 0:
        return "░" * length
    percent = max(0.0, min(1.0, float(current) / float(total)))
    filled = int(round(percent * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


def start_add_target(bot, message):
    try:
        teks = (
            "<b>Tambah Target</b>\n\n"
            "Ketik format berikut:\n"
            "<code>Nama#Total#Terkumpul</code>\n\n"
            "Contoh:\n"
            "<code>Laptop#10000000#2500000</code>\n\n"
            "Kalau belum ada uang terkumpul, boleh pakai 0."
        )
        bot.send_message(message.chat.id, teks)
    except Exception as exc:
        notify_bug(bot, "start_add_target", exc)


def process_add_target(bot, message, supabase_client, action, notify_owner=None):
    try:
        raw = (message.text or "").strip()
        parts = [p.strip() for p in raw.split("#")]

        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "Format salah.\nPakai:\n<code>Nama#Total#Terkumpul</code>\nContoh: <code>Laptop#10000000#2500000</code>",
            )
            return False

        name = parts[0]
        total_raw = parts[1].replace(".", "").replace(",", "")

        if not total_raw.isdigit():
            bot.send_message(message.chat.id, "Total target harus angka bulat.")
            return False

        total_amount = int(total_raw)
        saved_amount = 0

        if len(parts) >= 3 and parts[2]:
            saved_raw = parts[2].replace(".", "").replace(",", "")
            if not saved_raw.isdigit():
                bot.send_message(message.chat.id, "Terkumpul harus angka bulat.")
                return False
            saved_amount = int(saved_raw)

        if total_amount <= 0:
            bot.send_message(message.chat.id, "Total target harus lebih dari 0.")
            return False

        if saved_amount < 0:
            bot.send_message(message.chat.id, "Terkumpul tidak boleh negatif.")
            return False

        row = {
            "user_id": str(message.from_user.id),
            "name": name,
            "goal_amount": total_amount,
            "saved_amount": saved_amount,
        }

        supabase_client.from_("targets").insert(row).execute()

        bot.send_message(
            message.chat.id,
            f"🎯 Target tersimpan.\n\nNama: {escape(name)}\nTotal: {rupiah(total_amount)}\nTerkumpul: {rupiah(saved_amount)}",
        )
        return True

    except Exception as exc:
        notify_bug(bot, "process_add_target", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal menyimpan target.")
        return False


def show_target_menu(bot, message, supabase_client, user_id, notify_owner=None):
    try:
        rows = (
            supabase_client.from_("targets")
            .select("id, name, goal_amount, saved_amount, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .execute()
            .data
        )

        kb = InlineKeyboardMarkup()

        if rows:
            for item in rows:
                title = item.get("name", "Target")
                kb.add(InlineKeyboardButton(title, callback_data=f"target_detail:{item['id']}"))

        kb.row(
            InlineKeyboardButton("➕ Tambah Target", callback_data="target_add"),
            InlineKeyboardButton("❌ Hapus Target", callback_data="target_delete_last"),
        )
        kb.row(InlineKeyboardButton("🧠 Reset Memori AI", callback_data="target_reset_ai"))
        kb.row(InlineKeyboardButton("⬅️ Kembali ke Menu Keuangan", callback_data="finance_menu"))

        text = (
            "<b>Target Tabungan</b>\n\n"
            "Pilih target yang mau dilihat.\n"
            "Kalau belum ada target, tekan tombol tambah target."
        )

        safe_render(bot, message, text, kb)

    except Exception as exc:
        notify_bug(bot, "show_target_menu", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal mengambil daftar target.")


def show_target_detail(bot, message, supabase_client, user_id, target_id, notify_owner=None):
    try:
        rows = (
            supabase_client.from_("targets")
            .select("id, name, goal_amount, saved_amount, created_at")
            .eq("user_id", str(user_id))
            .eq("id", target_id)
            .limit(1)
            .execute()
            .data
        )

        if not rows:
            bot.send_message(message.chat.id, "Target tidak ditemukan.")
            return

        item = rows[0]
        name = item.get("name", "-")
        goal_amount = int(item.get("goal_amount", 0))
        saved_amount = int(item.get("saved_amount", 0))
        remaining = max(goal_amount - saved_amount, 0)
        percent = 0 if goal_amount <= 0 else min(100, round((saved_amount / goal_amount) * 100))
        bar = progress_bar(saved_amount, goal_amount, 10)

        text = (
            f"<b>Detail Target</b>\n\n"
            f"Nama Target: {escape(name)}\n"
            f"Total Kebutuhan: {rupiah(goal_amount)}\n"
            f"Uang Terkumpul: {rupiah(saved_amount)}\n"
            f"Sisa Kekurangan: {rupiah(remaining)}\n"
            f"Persentase: {percent}%\n"
            f"Progress: [{bar}] {percent}%"
        )

        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("⬅️ Kembali ke Target", callback_data="target_menu"),
            InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
        )

        safe_render(bot, message, text, kb)

    except Exception as exc:
        notify_bug(bot, "show_target_detail", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal membuka detail target.")


def delete_last_target(bot, message, supabase_client, user_id, notify_owner=None):
    try:
        rows = (
            supabase_client.from_("targets")
            .select("id, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )

        if not rows:
            bot.send_message(message.chat.id, "Belum ada target untuk dihapus.")
            return

        target_id = rows[0]["id"]
        supabase_client.from_("targets").delete().eq("id", target_id).execute()
        bot.send_message(message.chat.id, "Target terakhir sudah dihapus.")
        show_target_menu(bot, message, supabase_client, user_id, notify_owner=notify_owner)

    except Exception as exc:
        notify_bug(bot, "delete_last_target", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal menghapus target.")


def reset_ai_memories(bot, message, supabase_client, user_id, notify_owner=None):
    try:
        supabase_client.from_("ai_memories").delete().eq("user_id", str(user_id)).execute()
        bot.send_message(message.chat.id, "🧠 Memori AI untuk user ini sudah dihapus.")
    except Exception as exc:
        notify_bug(bot, "reset_ai_memories", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal mereset memori AI.")
