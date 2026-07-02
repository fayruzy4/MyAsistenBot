import os
import time
import traceback
import threading
from html import escape

from dotenv import load_dotenv
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client

from features.catat import (
    start_transaction,
    process_transaction_input,
    show_last_transactions,
    delete_last_transaction,
    show_graph_report,
)
from features.target import (
    show_target_menu,
    show_target_detail,
    start_add_target,
    process_add_target,
    delete_last_target,
)
from features.habit import (
    show_habit_dashboard,
    handle_habit_toggle,
    start_add_habit,
    process_add_habit_difficulty,
    process_add_habit_name,
    show_habit_delete_list,
    process_delete_habit_confirm,
    show_habit_stats,
    show_habit_manage_list,
    show_habit_manage_options,
    start_edit_habit_name,
    process_edit_habit_name,
    process_edit_habit_difficulty,
    show_habit_achievements,
)
from features.gemini import GeminiAI
from features.groq import GroqAI

load_dotenv()

TOKEN_BOT = os.getenv("TOKEN_BOT", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))

if not TOKEN_BOT:
    raise RuntimeError("TOKEN_BOT belum dikonfigurasi di environment variables.")
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL belum dikonfigurasi di environment variables.")
if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY belum dikonfigurasi di environment variables.")

bot = telebot.TeleBot(TOKEN_BOT, parse_mode="HTML")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

pending_actions = {}

try:
    gemini_ai = GeminiAI(supabase)
except Exception as exc:
    print(f"[WARN] GeminiAI tidak aktif: {exc}")
    gemini_ai = None

try:
    groq_ai = GroqAI(supabase, bot)
except Exception as exc:
    print(f"[WARN] GroqAI tidak aktif: {exc}")
    groq_ai = None


def report_error_to_console(where: str, exc: Exception):
    print(f"🚨 BUG TERDETEKSI di [{where}]: {exc}")
    print(traceback.format_exc())


def safe_edit_or_send(message, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" not in str(e).lower():
            bot.send_message(message.chat.id, text, reply_markup=reply_markup)
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)


def clear_user_state(user_id: int):
    pending_actions.pop(user_id, None)


def set_ai_mode(user_id: int, mode: str):
    pending_actions[user_id] = {"kind": mode}


def reset_ai_memory(user_id: int):
    supabase.table("ai_chat_memory").delete().eq("user_id", str(user_id)).execute()


def ai_mode_status_text(mode: str) -> str:
    if mode == "ai_gemini":
        return (
            "🤖 <b>Mode Gemini aktif</b>\n\n"
            "Kirim pertanyaan teks. Gemini memakai memori dari database."
        )
    if mode == "ai_groq":
        return (
            "🎙 <b>Mode Groq aktif</b>\n\n"
            "Kirim teks atau voice note. Voice akan ditranskripsi dulu."
        )
    return "Mode AI aktif."


def ai_mode_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⬅️ Kembali ke Dashboard", callback_data="back_dashboard"),
        InlineKeyboardButton("🚪 Keluar Mode", callback_data="exit_mode"),
    )
    return kb


def dashboard_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("💼 Menu Keuangan", callback_data="finance_menu"),
        InlineKeyboardButton("🎯 Habit Tracker", callback_data="habit_dashboard"),
    )
    kb.row(
        InlineKeyboardButton("🤖 Gemini AI", callback_data="ai_gemini"),
        InlineKeyboardButton("🎙 Groq AI", callback_data="ai_groq"),
    )
    return kb


def finance_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("💰 Tambah Saldo", callback_data="txn_add_income"),
        InlineKeyboardButton("💸 Kurang Saldo", callback_data="txn_add_expense"),
    )
    kb.row(
        InlineKeyboardButton("📊 Grafik 7 Hari", callback_data="graph_7"),
        InlineKeyboardButton("📊 Grafik 30 Hari", callback_data="graph_30"),
    )
    kb.row(
        InlineKeyboardButton("🎯 Target Tabungan", callback_data="target_menu"),
        InlineKeyboardButton("📋 Riwayat Transaksi", callback_data="txn_recent"),
    )
    kb.row(InlineKeyboardButton("🏠 Kembali ke Dashboard", callback_data="back_dashboard"))
    return kb


def ai_menu_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🤖 Gemini AI", callback_data="ai_gemini"),
        InlineKeyboardButton("🎙 Groq AI", callback_data="ai_groq"),
    )
    kb.row(InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"))
    return kb


def show_dashboard(message, edit=False):
    text = (
        "✨ <b>Halo! Selamat datang di Asisten Pribadimu</b> ✨\n\n"
        "Di sini kamu bisa mengatur <b>Keuangan</b>, memantau <b>Habit</b>, "
        "dan memakai <b>AI</b> untuk ngobrol atau transkripsi voice.\n\n"
        "Pilih menu di bawah ini."
    )
    if edit:
        safe_edit_or_send(message, text, dashboard_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=dashboard_keyboard())


def show_ai_menu(message, edit=False):
    text = (
        "🤖 <b>Menu AI</b>\n\n"
        "Pilih Gemini untuk chat teks dengan memori.\n"
        "Pilih Groq untuk teks dan voice note."
    )
    if edit:
        safe_edit_or_send(message, text, ai_menu_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=ai_menu_keyboard())


def show_finance_menu(message):
    text = (
        "💼 <b>Menu Keuangan Utama</b>\n\n"
        "Pilih aktivitas yang ingin kamu catat hari ini."
    )
    safe_edit_or_send(message, text, finance_keyboard())


@app.route("/", methods=["GET"])
def home():
    return "🚀 Asisten Bot Aktif dan Berjalan Baik!", 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)


@bot.message_handler(commands=["start"])
def handle_start(message):
    try:
        clear_user_state(message.from_user.id)
        show_dashboard(message, edit=False)
    except Exception as exc:
        report_error_to_console("handle_start", exc)
        bot.send_message(message.chat.id, "Ups! Terjadi kendala teknis saat memuat dashboard. 😔")


@bot.message_handler(commands=["ai"])
def handle_ai_menu(message):
    try:
        clear_user_state(message.from_user.id)
        show_ai_menu(message, edit=False)
    except Exception as exc:
        report_error_to_console("handle_ai_menu", exc)
        bot.send_message(message.chat.id, "Gagal membuka menu AI.")


@bot.message_handler(commands=["gemini"])
def handle_gemini_mode(message):
    try:
        if gemini_ai is None:
            bot.send_message(message.chat.id, "Layanan Gemini belum aktif.")
            return
        set_ai_mode(message.from_user.id, "ai_gemini")
        bot.send_message(
            message.chat.id,
            ai_mode_status_text("ai_gemini"),
            reply_markup=ai_mode_keyboard(),
        )
    except Exception as exc:
        report_error_to_console("handle_gemini_mode", exc)
        bot.send_message(message.chat.id, "Gagal mengaktifkan mode Gemini.")


@bot.message_handler(commands=["groq"])
def handle_groq_mode(message):
    try:
        if groq_ai is None:
            bot.send_message(message.chat.id, "Layanan Groq belum aktif.")
            return
        set_ai_mode(message.from_user.id, "ai_groq")
        bot.send_message(
            message.chat.id,
            ai_mode_status_text("ai_groq"),
            reply_markup=ai_mode_keyboard(),
        )
    except Exception as exc:
        report_error_to_console("handle_groq_mode", exc)
        bot.send_message(message.chat.id, "Gagal mengaktifkan mode Groq.")


@bot.message_handler(commands=["reset"])
def handle_reset(message):
    try:
        user_id = message.from_user.id
        reset_ai_memory(user_id)
        clear_user_state(user_id)
        bot.send_message(message.chat.id, "Memori AI Anda telah berhasil direset!")
    except Exception as exc:
        report_error_to_console("handle_reset", exc)
        bot.send_message(message.chat.id, "Gagal mereset memori AI.")


@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        if not message.text:
            return

        text = message.text.strip()
        if text.startswith("/"):
            return

        user_id = message.from_user.id
        action = pending_actions.get(user_id, {})
        kind = action.get("kind")

        # Chat biasa tidak masuk AI kalau tidak sedang di mode AI.
        if kind == "ai_gemini":
            if gemini_ai is None:
                bot.send_message(message.chat.id, "Layanan Gemini belum aktif.")
                return
            answer = gemini_ai.ask(user_id, text, history_limit=16)
            bot.send_message(message.chat.id, escape(answer), reply_markup=ai_mode_keyboard())
            return

        if kind == "ai_groq":
            if groq_ai is None:
                bot.send_message(message.chat.id, "Layanan Groq belum aktif.")
                return
            answer = groq_ai.ask_text(user_id, text, history_limit=16)
            bot.send_message(message.chat.id, escape(answer), reply_markup=ai_mode_keyboard())
            return

        success = False

        if kind in ("income", "expense"):
            success = process_transaction_input(bot, message, supabase, action)
        elif kind == "add_target":
            success = process_add_target(bot, message, supabase)
        elif kind == "habit_add_name":
            success = process_add_habit_name(bot, message, supabase, action)
        elif kind == "habit_edit_name":
            success = process_edit_habit_name(bot, message, supabase, action)

        if success:
            clear_user_state(user_id)

    except Exception as exc:
        report_error_to_console("handle_text", exc)
        bot.send_message(message.chat.id, "Ada gangguan saat memproses pesan Anda.")


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    try:
        user_id = message.from_user.id
        action = pending_actions.get(user_id, {})
        kind = action.get("kind")

        if kind != "ai_groq":
            bot.send_message(message.chat.id, "Aktifkan mode Groq dulu lewat /groq atau tombol Groq AI.")
            return

        if groq_ai is None:
            bot.send_message(message.chat.id, "Layanan Groq belum aktif.")
            return

        transcript, answer = groq_ai.ask_voice(user_id, message.voice.file_id, history_limit=16)

        if transcript:
            bot.send_message(message.chat.id, f"📝 <b>Transkrip:</b> {escape(transcript)}")
        bot.send_message(message.chat.id, escape(answer), reply_markup=ai_mode_keyboard())

    except Exception as exc:
        report_error_to_console("handle_voice", exc)
        bot.send_message(message.chat.id, "Gagal memproses voice note.")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data or ""

    try:
        if not data.startswith("habit_toggle"):
            bot.answer_callback_query(call.id)

        if data in ["finance_menu", "back_dashboard", "cancel_input", "habit_dashboard", "exit_mode"]:
            clear_user_state(user_id)

        if data == "finance_menu":
            show_finance_menu(call.message)

        elif data == "cancel_input":
            if pending_actions.get(user_id, {}).get("kind", "").startswith("habit_"):
                clear_user_state(user_id)
                show_habit_dashboard(bot, call.message, supabase, user_id)
            else:
                clear_user_state(user_id)
                show_dashboard(call.message, edit=True)

        elif data == "back_dashboard":
            show_dashboard(call.message, edit=True)

        elif data == "exit_mode":
            show_dashboard(call.message, edit=True)

        elif data == "ai_gemini":
            if gemini_ai is None:
                bot.send_message(call.message.chat.id, "Layanan Gemini belum aktif.")
            else:
                set_ai_mode(user_id, "ai_gemini")
                bot.send_message(
                    call.message.chat.id,
                    ai_mode_status_text("ai_gemini"),
                    reply_markup=ai_mode_keyboard(),
                )

        elif data == "ai_groq":
            if groq_ai is None:
                bot.send_message(call.message.chat.id, "Layanan Groq belum aktif.")
            else:
                set_ai_mode(user_id, "ai_groq")
                bot.send_message(
                    call.message.chat.id,
                    ai_mode_status_text("ai_groq"),
                    reply_markup=ai_mode_keyboard(),
                )

        # Sisanya biarkan sama persis seperti main.py Anda sekarang.
        elif data == "txn_add_income":
            pending_actions[user_id] = {"kind": "income"}
            start_transaction(bot, call.message, "income")
        elif data == "txn_add_expense":
            pending_actions[user_id] = {"kind": "expense"}
            start_transaction(bot, call.message, "expense")
        elif data == "txn_recent":
            show_last_transactions(bot, call.message, supabase, user_id)
        elif data == "txn_delete_last":
            delete_last_transaction(bot, call.message, supabase, user_id)
        elif data == "graph_7":
            show_graph_report(bot, call.message, supabase, user_id, 7)
        elif data == "graph_30":
            show_graph_report(bot, call.message, supabase, user_id, 30)
        elif data == "target_menu":
            show_target_menu(bot, call.message, supabase, user_id)
        elif data.startswith("target_detail:"):
            target_id = data.split(":", 1)[1]
            show_target_detail(bot, call.message, supabase, user_id, target_id)
        elif data == "target_add":
            pending_actions[user_id] = {"kind": "add_target"}
            start_add_target(bot, call.message)
        elif data == "target_delete_last":
            delete_last_target(bot, call.message, supabase, user_id)

        elif data == "habit_dashboard":
            show_habit_dashboard(bot, call.message, supabase, user_id)
        elif data.startswith("habit_toggle:"):
            habit_id = data.split(":", 1)[1]
            handle_habit_toggle(bot, call, supabase, user_id, habit_id)
        elif data == "habit_add_start":
            start_add_habit(bot, call.message)
        elif data.startswith("habit_add_diff:"):
            diff = data.split(":", 1)[1]
            pending_actions[user_id] = {"kind": "habit_add_name", "diff": diff}
            process_add_habit_difficulty(bot, call.message, diff)
        elif data == "habit_delete_list":
            show_habit_delete_list(bot, call.message, supabase, user_id)
        elif data.startswith("habit_delete_confirm:"):
            habit_id = data.split(":", 1)[1]
            process_delete_habit_confirm(bot, call.message, supabase, user_id, habit_id)
        elif data == "habit_manage_list":
            show_habit_manage_list(bot, call.message, supabase, user_id)
        elif data.startswith("habit_manage_opt:"):
            habit_id = data.split(":", 1)[1]
            show_habit_manage_options(bot, call.message, habit_id)
        elif data.startswith("habit_edit_name:"):
            habit_id = data.split(":", 1)[1]
            pending_actions[user_id] = {"kind": "habit_edit_name", "habit_id": habit_id}
            start_edit_habit_name(bot, call.message)
        elif data.startswith("habit_edit_diff:"):
            parts = data.split(":")
            habit_id = parts[1]
            diff = parts[2]
            process_edit_habit_difficulty(bot, call.message, supabase, user_id, habit_id, diff)
        elif data == "habit_stats":
            show_habit_stats(bot, call.message, supabase, user_id)
        elif data == "habit_achievements":
            show_habit_achievements(bot, call.message, supabase, user_id)

    except Exception as exc:
        report_error_to_console("handle_callback", exc)
        bot.send_message(call.message.chat.id, "Ups, sistem sedang memproses terlalu banyak permintaan. Coba lagi ya! 🛠️")


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(1)

    print("🤖 Bot Asisten berhasil diaktifkan dan sedang berjalan...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as exc:
            report_error_to_console("bot.infinity_polling", exc)
            time.sleep(5)
