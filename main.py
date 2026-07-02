import os
import time
import traceback
import threading

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
    reset_ai_memories,
)

TOKEN_BOT = os.environ.get("TOKEN_BOT", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "").strip()
PORT = int(os.environ.get("PORT", "10000"))

if not TOKEN_BOT:
    raise RuntimeError("TOKEN_BOT belum diisi.")
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL belum diisi.")
if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY belum diisi.")

bot = telebot.TeleBot(TOKEN_BOT, parse_mode="HTML")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)

pending_actions = {}


def notify_owner(text: str):
    try:
        if OWNER_CHAT_ID:
            bot.send_message(int(OWNER_CHAT_ID), text)
    except Exception:
        pass


def report_bug(where: str, exc: Exception):
    msg = (
        f"🚨 BUG TERDETEKSI\n\n"
        f"Lokasi: {where}\n"
        f"Error: {exc}\n\n"
        f"{traceback.format_exc()}"
    )
    print(msg)
    notify_owner(msg)


def safe_edit_or_send(message, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)


def dashboard_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📝 Masuk ke Menu Keuangan", callback_data="finance_menu"))
    return kb


def finance_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Tambah Saldo", callback_data="txn_add_income"),
        InlineKeyboardButton("➖ Kurang Saldo", callback_data="txn_add_expense"),
    )
    kb.row(
        InlineKeyboardButton("📊 Grafik 1 Hari", callback_data="graph_1"),
        InlineKeyboardButton("📊 Grafik 7 Hari", callback_data="graph_7"),
        InlineKeyboardButton("📊 Grafik 1 Bulan", callback_data="graph_30"),
    )
    kb.row(
        InlineKeyboardButton("🎯 Target Tabungan", callback_data="target_menu"),
        InlineKeyboardButton("📋 5 Transaksi Terakhir", callback_data="txn_recent"),
    )
    kb.row(InlineKeyboardButton("⬅️ Kembali ke Dashboard", callback_data="back_dashboard"))
    return kb


def show_dashboard(message, edit=False):
    text = (
        "<b>Dashboard Utama</b>\n\n"
        "Ini halaman depan bot.\n"
        "Tekan tombol di bawah untuk masuk ke menu keuangan."
    )
    if edit:
        safe_edit_or_send(message, text, dashboard_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=dashboard_keyboard())


def show_finance_menu(message):
    text = (
        "<b>Menu Utama Keuangan</b>\n\n"
        "Pilih tombol yang mau dipakai."
    )
    safe_edit_or_send(message, text, finance_keyboard())


@app.route("/ping", methods=["GET"])
def ping():
    return "OK", 200


@app.route("/", methods=["GET"])
def home():
    return "Bot aktif", 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)


@bot.message_handler(commands=["start"])
def handle_start(message):
    try:
        pending_actions.pop(message.from_user.id, None)
        show_dashboard(message, edit=False)
    except Exception as exc:
        report_bug("handle_start", exc)
        bot.send_message(message.chat.id, "Terjadi error saat membuka dashboard.")


@bot.message_handler(commands=["id"])
def handle_id(message):
    try:
        bot.send_message(
            message.chat.id,
            f"ID kamu:\n<code>{message.from_user.id}</code>\n\nChat ID:\n<code>{message.chat.id}</code>",
        )
    except Exception as exc:
        report_bug("handle_id", exc)


@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        if not message.text or message.text.startswith("/"):
            return

        user_id = message.from_user.id
        action = pending_actions.get(user_id)

        if not action:
            return

        kind = action.get("kind")

        ok = False
        if kind in ("income", "expense"):
            ok = process_transaction_input(
                bot=bot,
                message=message,
                supabase_client=supabase,
                action=action,
                notify_owner=notify_owner,
            )
        elif kind == "add_target":
            ok = process_add_target(
                bot=bot,
                message=message,
                supabase_client=supabase,
                action=action,
                notify_owner=notify_owner,
            )

        if ok:
            pending_actions.pop(user_id, None)

    except Exception as exc:
        report_bug("handle_text", exc)
        bot.send_message(message.chat.id, "Error saat memproses input.")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    try:
        data = call.data or ""
        user_id = call.from_user.id

        if data == "finance_menu":
            show_finance_menu(call.message)

        elif data == "back_dashboard":
            pending_actions.pop(user_id, None)
            show_dashboard(call.message, edit=True)

        elif data == "txn_add_income":
            pending_actions[user_id] = {"kind": "income"}
            start_transaction(bot, call.message, "income")
            bot.answer_callback_query(call.id, "Ketik format: Angka#Keterangan")

        elif data == "txn_add_expense":
            pending_actions[user_id] = {"kind": "expense"}
            start_transaction(bot, call.message, "expense")
            bot.answer_callback_query(call.id, "Ketik format: Angka#Keterangan")

        elif data == "txn_recent":
            show_last_transactions(
                bot=bot,
                message=call.message,
                supabase_client=supabase,
                user_id=user_id,
                notify_owner=notify_owner,
            )
            bot.answer_callback_query(call.id)

        elif data == "txn_delete_last":
            delete_last_transaction(
                bot=bot,
                message=call.message,
                supabase_client=supabase,
                user_id=user_id,
                notify_owner=notify_owner,
            )
            bot.answer_callback_query(call.id, "Transaksi terakhir dihapus")

        elif data == "graph_1":
            show_graph_report(bot, call.message, supabase, user_id, 1, notify_owner)
            bot.answer_callback_query(call.id)

        elif data == "graph_7":
            show_graph_report(bot, call.message, supabase, user_id, 7, notify_owner)
            bot.answer_callback_query(call.id)

        elif data == "graph_30":
            show_graph_report(bot, call.message, supabase, user_id, 30, notify_owner)
            bot.answer_callback_query(call.id)

        elif data == "target_menu":
            show_target_menu(
                bot=bot,
                message=call.message,
                supabase_client=supabase,
                user_id=user_id,
                notify_owner=notify_owner,
            )
            bot.answer_callback_query(call.id)

        elif data.startswith("target_detail:"):
            target_id = data.split(":", 1)[1]
            show_target_detail(
                bot=bot,
                message=call.message,
                supabase_client=supabase,
                user_id=user_id,
                target_id=target_id,
                notify_owner=notify_owner,
            )
            bot.answer_callback_query(call.id)

        elif data == "target_add":
            pending_actions[user_id] = {"kind": "add_target"}
            start_add_target(bot, call.message)
            bot.answer_callback_query(call.id, "Ketik format: Nama#Total#Terkumpul")

        elif data == "target_delete_last":
            delete_last_target(
                bot=bot,
                message=call.message,
                supabase_client=supabase,
                user_id=user_id,
                notify_owner=notify_owner,
            )
            bot.answer_callback_query(call.id, "Target terakhir dihapus")

        elif data == "target_reset_ai":
            reset_ai_memories(
                bot=bot,
                message=call.message,
                supabase_client=supabase,
                user_id=user_id,
                notify_owner=notify_owner,
            )
            bot.answer_callback_query(call.id, "Memori AI direset")

        else:
            bot.answer_callback_query(call.id, "Tombol tidak dikenali.")

    except Exception as exc:
        report_bug("handle_callback", exc)
        try:
            bot.answer_callback_query(call.id, "Terjadi error.")
        except Exception:
            pass


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(1)

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as exc:
            report_bug("bot.infinity_polling", exc)
            time.sleep(5)
