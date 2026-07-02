import os
import time
import traceback
import threading

from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client

# Import fitur (Tanpa fitur AI)
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

# Konfigurasi Environment
TOKEN_BOT = os.environ.get("TOKEN_BOT", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
PORT = int(os.environ.get("PORT", "10000"))

if not TOKEN_BOT:
    raise RuntimeError("TOKEN_BOT belum dikonfigurasi di environment variables.")
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL belum dikonfigurasi di environment variables.")
if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY belum dikonfigurasi di environment variables.")

# Inisialisasi Bot, Database, dan Flask
bot = telebot.TeleBot(TOKEN_BOT, parse_mode="HTML")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

# State management sederhana untuk input user
pending_actions = {}


def report_error_to_console(where: str, exc: Exception):
    """Fungsi helper untuk mencetak error tanpa membuat bot crash."""
    print(f"🚨 BUG TERDETEKSI di [{where}]: {exc}")
    print(traceback.format_exc())


def safe_edit_or_send(message, text, reply_markup=None):
    """Menghindari error saat mengedit pesan yang sama persis."""
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


# ================= KUMPULAN KEYBOARD UTAMA =================

def dashboard_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("💼 Masuk Menu Keuangan", callback_data="finance_menu"))
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


# ================= TAMPILAN MENU =================

def show_dashboard(message, edit=False):
    text = (
        "✨ <b>Halo! Selamat datang di MyAsistenBot</b> ✨\n\n"
        "Mulai sekarang, mencatat pengeluaran, pemasukan, hingga memantau "
        "target tabungan impianmu jadi lebih mudah dan rapi.\n\n"
        "Yuk, pilih menu di bawah untuk mulai mengatur keuanganmu hari ini! 👇"
    )
    if edit:
        safe_edit_or_send(message, text, dashboard_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=dashboard_keyboard())


def show_finance_menu(message):
    text = (
        "💼 <b>Menu Keuangan Utama</b>\n\n"
        "Pilih aktivitas yang ingin kamu catat hari ini. "
        "Jangan lupa catat pengeluaran kecil agar keuangan tetap aman! ✅"
    )
    safe_edit_or_send(message, text, finance_keyboard())


# ================= FLASK ROUTES (KEEP ALIVE) =================

@app.route("/", methods=["GET"])
def home():
    return "🚀 Asisten Bot Keuangan Aktif dan Berjalan Baik!", 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)


# ================= HANDLERS =================

@bot.message_handler(commands=["start"])
def handle_start(message):
    try:
        pending_actions.pop(message.from_user.id, None)
        show_dashboard(message, edit=False)
    except Exception as exc:
        report_error_to_console("handle_start", exc)
        bot.send_message(message.chat.id, "Ups! Terjadi kendala teknis saat memuat dashboard. 😔")


@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        if not message.text or message.text.startswith("/"):
            return

        user_id = message.from_user.id
        action = pending_actions.get(user_id)

        if not action:
            return  # Jika tidak ada state input, abaikan chat biasa

        kind = action.get("kind")
        success = False

        if kind in ("income", "expense"):
            success = process_transaction_input(
                bot=bot,
                message=message,
                supabase_client=supabase,
                action=action,
            )
        elif kind == "add_target":
            success = process_add_target(
                bot=bot,
                message=message,
                supabase_client=supabase,
            )

        # Jika sukses diproses, hapus state agar tidak stuck
        if success:
            pending_actions.pop(user_id, None)

    except Exception as exc:
        report_error_to_console("handle_text", exc)
        bot.send_message(message.chat.id, "Waduh, ada sedikit gangguan saat mencatat data kamu. Coba lagi ya! 🙏")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data or ""
    
    try:
        # Panggil answer_callback_query di awal untuk mencegah error "query is too old"
        bot.answer_callback_query(call.id)

        # Hapus state input apapun jika user menekan tombol navigasi
        if data in ["finance_menu", "back_dashboard", "cancel_input"]:
            pending_actions.pop(user_id, None)

        if data == "finance_menu" or data == "cancel_input":
            show_finance_menu(call.message)

        elif data == "back_dashboard":
            show_dashboard(call.message, edit=True)

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

    except Exception as exc:
        report_error_to_console("handle_callback", exc)
        bot.send_message(call.message.chat.id, "Ups, fitur ini sedang mengalami gangguan sementara. 🛠️")


if __name__ == "__main__":
    # Jalankan Flask Server di thread terpisah agar port cloud binding tidak timeout
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(1)

    print("🤖 Bot Keuangan berhasil diaktifkan dan sedang berjalan...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as exc:
            report_error_to_console("bot.infinity_polling", exc)
            time.sleep(5)

