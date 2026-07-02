import os
import time
import traceback
import threading

from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client

# Import fitur
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
    show_habit_achievements
)

# Konfigurasi Environment
TOKEN_BOT = os.environ.get("TOKEN_BOT", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
PORT = int(os.environ.get("PORT", "10000"))

if not TOKEN_BOT or not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Konfigurasi environment (TOKEN_BOT, SUPABASE_URL, SUPABASE_KEY) belum lengkap.")

# Inisialisasi
bot = telebot.TeleBot(TOKEN_BOT, parse_mode="HTML")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)
pending_actions = {}

def report_error_to_console(where: str, exc: Exception):
    print(f"🚨 BUG TERDETEKSI di [{where}]: {exc}")
    print(traceback.format_exc())

def show_dashboard(message, edit=False):
    text = (
        "✨ <b>Asisten Pribadi</b> ✨\n\n"
        "Pilih menu untuk mengelola keuangan atau habit disiplinmu."
    )
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("💼 Menu Keuangan", callback_data="finance_menu"))
    kb.row(InlineKeyboardButton("🎯 Habit Tracker", callback_data="habit_dashboard"))
    if edit:
        try:
            bot.edit_message_text(text=text, chat_id=message.chat.id, message_id=message.message_id, reply_markup=kb)
        except:
            bot.send_message(message.chat.id, text, reply_markup=kb)
    else:
        bot.send_message(message.chat.id, text, reply_markup=kb)

@app.route("/", methods=["GET"])
def home(): return "OK", 200

def run_flask(): app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)

@bot.message_handler(commands=["start"])
def handle_start(message):
    pending_actions.pop(message.from_user.id, None)
    show_dashboard(message)

@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        user_id = message.from_user.id
        action = pending_actions.get(user_id)
        if not action: return
        
        success = False
        if action.get("kind") == "habit_add_name": success = process_add_habit_name(bot, message, supabase, action)
        elif action.get("kind") == "habit_edit_name": success = process_edit_habit_name(bot, message, supabase, action)
        elif action.get("kind") in ("income", "expense"): success = process_transaction_input(bot, message, supabase, action)
        
        if success: pending_actions.pop(user_id, None)
    except Exception as exc: report_error_to_console("handle_text", exc)

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data or ""
    try:
        if not data.startswith("habit_toggle"): bot.answer_callback_query(call.id)
        if data in ["finance_menu", "back_dashboard", "habit_dashboard"]: pending_actions.pop(user_id, None)

        if data == "finance_menu":
            kb = InlineKeyboardMarkup()
            kb.row(InlineKeyboardButton("💰 Tambah Saldo", callback_data="txn_add_income"), InlineKeyboardButton("💸 Kurang Saldo", callback_data="txn_add_expense"))
            kb.row(InlineKeyboardButton("⬅️ Kembali", callback_data="back_dashboard"))
            bot.edit_message_text("💼 Menu Keuangan", call.message.chat.id, call.message.message_id, reply_markup=kb)
        elif data == "back_dashboard": show_dashboard(call.message, edit=True)
        
        # Habit Logic
        elif data == "habit_dashboard": show_habit_dashboard(bot, call.message, supabase, user_id)
        elif data.startswith("habit_toggle:"): handle_habit_toggle(bot, call, supabase, user_id, data.split(":")[1])
        elif data == "habit_add_start": start_add_habit(bot, call.message)
        elif data.startswith("habit_add_diff:"):
            pending_actions[user_id] = {"kind": "habit_add_name", "diff": data.split(":")[1]}
            process_add_habit_difficulty(bot, call.message, data.split(":")[1])
        elif data == "habit_delete_list": show_habit_delete_list(bot, call.message, supabase, user_id)
        elif data.startswith("habit_delete_confirm:"): process_delete_habit_confirm(bot, call.message, supabase, user_id, data.split(":")[1])
        elif data == "habit_manage_list": show_habit_manage_list(bot, call.message, supabase, user_id)
        elif data.startswith("habit_manage_opt:"): show_habit_manage_options(bot, call.message, data.split(":")[1])
        elif data.startswith("habit_edit_name:"):
            pending_actions[user_id] = {"kind": "habit_edit_name", "habit_id": data.split(":")[1]}
            start_edit_habit_name(bot, call.message)
        elif data == "habit_stats": show_habit_stats(bot, call.message, supabase, user_id)
        
    except Exception as exc: report_error_to_console("callback", exc)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
