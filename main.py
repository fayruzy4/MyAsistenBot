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
# Import fitur Habit Tracker
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
    show_habit_achievements
)

# Konfigurasi Environment
TOKEN_BOT = os.environ.get("TOKEN_BOT", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
PORT = int(os.environ.get("PORT", "10000"))

if not TOKEN_BOT or not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Konfigurasi environment (TOKEN_BOT, SUPABASE_URL, SUPABASE_KEY) belum lengkap.")

# Inisialisasi Bot, Database, dan Flask
bot = telebot.TeleBot(TOKEN_BOT, parse_mode="HTML")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

# State management
pending_actions = {}


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
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)


# ================= DASHBOARD & MENU KEUANGAN =================

def dashboard_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("💼 Menu Keuangan", callback_data="finance_menu"))
    kb.row(InlineKeyboardButton("🎯 Habit Tracker (Disiplin)", callback_data="habit_dashboard"))
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


def show_dashboard(message, edit=False):
    text = (
        "✨ <b>Halo! Selamat datang di Asisten Pribadimu</b> ✨\n\n"
        "Di sini kamu bisa mengatur <b>Keuangan</b> agar tetap stabil, "
        "dan memantau <b>Habit (Kebiasaan)</b> agar hidupmu makin disiplin.\n\n"
        "Yuk, pilih menu di bawah ini! 👇"
    )
    if edit:
        safe_edit_or_send(message, text, dashboard_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=dashboard_keyboard())


def show_finance_menu(message):
    text = ("💼 <b>Menu Keuangan Utama</b>\n\nPilih aktivitas yang ingin kamu catat hari ini. ✅")
    safe_edit_or_send(message, text, finance_keyboard())


# ================= FLASK & HANDLERS =================

@app.route("/", methods=["GET"])
def home():
    return "🚀 Bot Aktif", 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)


@bot.message_handler(commands=["start"])
def handle_start(message):
    try:
        pending_actions.pop(message.from_user.id, None)
        show_dashboard(message, edit=False)
    except Exception as exc:
        report_error_to_console("handle_start", exc)


@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        if not message.text or message.text.startswith("/"): return
        user_id = message.from_user.id
        action = pending_actions.get(user_id)
        if not action: return

        success = False
        if action.get("kind") in ("income", "expense"):
            success = process_transaction_input(bot, message, supabase, action)
        elif action.get("kind") == "add_target":
            success = process_add_target(bot, message, supabase)
        elif action.get("kind") == "habit_add_name":
            success = process_add_habit_name(bot, message, supabase, action)
        elif action.get("kind") == "habit_edit_name":
            success = process_edit_habit_name(bot, message, supabase, action)

        if success: pending_actions.pop(user_id, None)
    except Exception as exc:
        report_error_to_console("handle_text", exc)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data or ""
    try:
        if not data.startswith("habit_toggle"): bot.answer_callback_query(call.id)
        if data in ["finance_menu", "back_dashboard", "cancel_input", "habit_dashboard"]: pending_actions.pop(user_id, None)

        if data == "finance_menu": show_finance_menu(call.message)
        elif data == "cancel_input":
            if pending_actions.get(user_id, {}).get("kind", "").startswith("habit_"):
                pending_actions.pop(user_id, None)
                show_habit_dashboard(bot, call.message, supabase, user_id)
            else:
                show_dashboard(call.message, edit=True)
        elif data == "back_dashboard": show_dashboard(call.message, edit=True)
        
        # Keuangan
        elif data == "txn_add_income":
            pending_actions[user_id] = {"kind": "income"}
            start_transaction(bot, call.message, "income")
        elif data == "txn_add_expense":
            pending_actions[user_id] = {"kind": "expense"}
            start_transaction(bot, call.message, "expense")
        elif data == "txn_recent": show_last_transactions(bot, call.message, supabase, user_id)
        elif data == "txn_delete_last": delete_last_transaction(bot, call.message, supabase, user_id)
        elif data == "graph_7": show_graph_report(bot, call.message, supabase, user_id, 7)
        elif data == "graph_30": show_graph_report(bot, call.message, supabase, user_id, 30)
        elif data == "target_menu": show_target_menu(bot, call.message, supabase, user_id)
        elif data.startswith("target_detail:"): show_target_detail(bot, call.message, supabase, user_id, data.split(":")[1])
        elif data == "target_add":
            pending_actions[user_id] = {"kind": "add_target"}
            start_add_target(bot, call.message)
        elif data == "target_delete_last": delete_last_target(bot, call.message, supabase, user_id)

        # Habit Tracker
        elif data == "habit_dashboard": show_habit_dashboard(bot, call.message, supabase, user_id)
        elif data.startswith("habit_toggle:"): handle_habit_toggle(bot, call, supabase, user_id, data.split(":")[1])
        elif data == "habit_add_start": start_add_habit(bot, call.message)
        elif data.startswith("habit_add_diff:"):
            diff = data.split(":")[1]
            pending_actions[user_id] = {"kind": "habit_add_name", "diff": diff}
            process_add_habit_difficulty(bot, call.message, diff)
        elif data == "habit_delete_list": show_habit_delete_list(bot, call.message, supabase, user_id)
        elif data.startswith("habit_delete_confirm:"): process_delete_habit_confirm(bot, call.message, supabase, user_id, data.split(":")[1])
        elif data == "habit_manage_list": show_habit_manage_list(bot, call.message, supabase, user_id)
        elif data.startswith("habit_manage_opt:"): show_habit_manage_options(bot, call.message, data.split(":")[1])
        elif data.startswith("habit_edit_name:"):
            habit_id = data.split(":")[1]
            pending_actions[user_id] = {"kind": "habit_edit_name", "habit_id": habit_id}
            start_edit_habit_name(bot, call.message)
        elif data.startswith("habit_edit_diff:"):
            parts = data.split(":")
            process_edit_habit_difficulty(bot, call.message, supabase, user_id, parts[1], parts[2])
        elif data == "habit_stats": show_habit_stats(bot, call.message, supabase, user_id)
        elif data == "habit_achievements": show_habit_achievements(bot, call.message, supabase, user_id)

    except Exception as exc:
        report_error_to_console("handle_callback", exc)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    bot.infinity_polling(skip_pending=True)
