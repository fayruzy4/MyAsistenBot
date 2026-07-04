import os
import traceback
from html import escape

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BRANKAS_TABLE = "brankas_berkas"
BRANKAS_CHANNEL_ID = os.getenv("BRANKAS_CHANNEL_ID", "").strip() or None


def report_local_error(where: str, exc: Exception):
    print(f"🚨 Brankas error di [{where}]: {exc}")
    print(traceback.format_exc())


def _send_or_edit(bot, message, text: str, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except Exception:
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )


def _short_text(text: str, limit: int = 36) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def brankas_main_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📥 Simpan Berkas Baru", callback_data="brankas_save_menu"),
        InlineKeyboardButton("🔍 Cari Berkas", callback_data="brankas_search_start"),
    )
    kb.row(InlineKeyboardButton("🏠 Kembali ke Dashboard", callback_data="back_dashboard"))
    return kb


def brankas_save_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📁 1. Menu Dokumen", callback_data="brankas_wait_doc"),
        InlineKeyboardButton("🖼️ 2. Menu Foto", callback_data="brankas_wait_photo"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Kembali", callback_data="brankas_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def brankas_search_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⬅️ Kembali", callback_data="brankas_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def brankas_result_keyboard(rows):
    kb = InlineKeyboardMarkup()
    for row in rows:
        tipe_input = row.get("tipe_input", "")
        judul = row.get("judul_pencarian", "") or "Berkas"
        icon = "📄" if tipe_input == "dokumen" else "🖼️"
        label = f"{icon} {_short_text(judul)}"
        kb.row(InlineKeyboardButton(label, callback_data=f"brankas_open:{row['id']}"))

    kb.row(
        InlineKeyboardButton("🔎 Cari Lagi", callback_data="brankas_search_start"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def brankas_open_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🔎 Cari Lagi", callback_data="brankas_search_start"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Kembali", callback_data="brankas_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def show_brankas_menu(bot, message, edit=False):
    text = (
        "📂 <b>Brankas Berkas</b>\n\n"
        "Simpan file ke Supabase, arsipkan ke channel, lalu cari lagi kapan saja."
    )
    if edit:
        _send_or_edit(bot, message, text, brankas_main_keyboard())
    else:
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=brankas_main_keyboard(),
            parse_mode="HTML",
        )


def show_brankas_save_menu(bot, message, edit=False):
    text = (
        "📥 <b>Simpan Berkas Baru</b>\n\n"
        "Pilih jenis berkas yang ingin disimpan."
    )
    if edit:
        _send_or_edit(bot, message, text, brankas_save_keyboard())
    else:
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=brankas_save_keyboard(),
            parse_mode="HTML",
        )


def show_brankas_search_prompt(bot, message, edit=False):
    text = (
        "🔍 <b>Cari Berkas</b>\n\n"
        "Ketik kata kunci judul berkas. Hasil akan muncul sebagai tombol."
    )
    if edit:
        _send_or_edit(bot, message, text, brankas_search_keyboard())
    else:
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=brankas_search_keyboard(),
            parse_mode="HTML",
        )


def show_brankas_doc_prompt(bot, message):
    bot.send_message(
        message.chat.id,
        "📁 <b>Menu Dokumen aktif</b>\n\n"
        "Kirim file dokumen sekarang. Nama file asli dari Telegram dipakai sebagai judul pencarian.",
        reply_markup=brankas_save_keyboard(),
        parse_mode="HTML",
    )


def show_brankas_photo_prompt(bot, message):
    bot.send_message(
        message.chat.id,
        "🖼️ <b>Menu Foto aktif</b>\n\n"
        "Kirim foto + caption. Caption wajib diisi dan dipakai sebagai judul pencarian.",
        reply_markup=brankas_save_keyboard(),
        parse_mode="HTML",
    )


def _upsert_brankas_record(supabase, payload: dict):
    return (
        supabase.table(BRANKAS_TABLE)
        .upsert(payload, on_conflict="file_id")
        .execute()
    )


def _search_brankas_records(supabase, keyword: str, limit: int = 10):
    keyword = (keyword or "").strip()
    if len(keyword) < 2:
        return []

    result = (
        supabase.table(BRANKAS_TABLE)
        .select("id,tipe_input,judul_pencarian,file_id,created_at")
        .ilike("judul_pencarian", f"%{keyword}%")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def _get_brankas_record_by_id(supabase, record_id: int):
    result = (
        supabase.table(BRANKAS_TABLE)
        .select("id,tipe_input,judul_pencarian,file_id,created_at")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def _publish_to_channel(bot, supabase, row: dict):
    if not BRANKAS_CHANNEL_ID:
        return

    caption = (
        "📂 <b>Arsip Brankas</b>\n\n"
        f"<b>Judul:</b> {escape(row.get('judul_pencarian', '') or 'Berkas')}\n"
        f"<b>Tipe:</b> {escape(row.get('tipe_input', '') or '-')}"
    )

    try:
        if row.get("tipe_input") == "foto":
            sent = bot.send_photo(
                BRANKAS_CHANNEL_ID,
                row["file_id"],
                caption=caption,
                parse_mode="HTML",
            )
        else:
            sent = bot.send_document(
                BRANKAS_CHANNEL_ID,
                row["file_id"],
                caption=caption,
                parse_mode="HTML",
            )

        try:
            supabase.table(BRANKAS_TABLE).update(
                {
                    "channel_chat_id": str(BRANKAS_CHANNEL_ID),
                    "channel_message_id": sent.message_id,
                }
            ).eq("file_id", row["file_id"]).execute()
        except Exception as exc:
            report_local_error("update_channel_reference", exc)

    except Exception as exc:
        report_local_error("publish_to_channel", exc)


def _send_brankas_record(bot, chat_id: int, row: dict):
    tipe_input = row.get("tipe_input", "")
    judul = row.get("judul_pencarian", "") or "Berkas"
    file_id = row.get("file_id", "")
    caption = (
        "📂 <b>Brankas Berkas</b>\n\n"
        f"<b>Judul:</b> {escape(judul)}\n"
        f"<b>Tipe:</b> {escape(tipe_input)}"
    )

    if tipe_input == "foto":
        bot.send_photo(
            chat_id,
            file_id,
            caption=caption,
            reply_markup=brankas_open_keyboard(),
            parse_mode="HTML",
        )
    else:
        bot.send_document(
            chat_id,
            file_id,
            caption=caption,
            reply_markup=brankas_open_keyboard(),
            parse_mode="HTML",
        )


def process_brankas_callback(bot, call, supabase, pending_actions: dict, show_dashboard):
    data = call.data or ""
    user_id = call.from_user.id

    if data == "brankas_menu":
        pending_actions.pop(user_id, None)
        show_brankas_menu(bot, call.message, edit=True)
        return True

    if data == "brankas_save_menu":
        pending_actions.pop(user_id, None)
        show_brankas_save_menu(bot, call.message, edit=True)
        return True

    if data == "brankas_wait_doc":
        pending_actions[user_id] = {"kind": "brankas_wait_doc"}
        show_brankas_doc_prompt(bot, call.message)
        return True

    if data == "brankas_wait_photo":
        pending_actions[user_id] = {"kind": "brankas_wait_photo"}
        show_brankas_photo_prompt(bot, call.message)
        return True

    if data == "brankas_search_start":
        pending_actions[user_id] = {"kind": "brankas_search"}
        show_brankas_search_prompt(bot, call.message)
        return True

    if data.startswith("brankas_open:"):
        try:
            record_id = int(data.split(":", 1)[1])
        except ValueError:
            bot.send_message(call.message.chat.id, "ID berkas tidak valid.")
            return True

        row = _get_brankas_record_by_id(supabase, record_id)
        if not row:
            bot.send_message(call.message.chat.id, "Berkas tidak ditemukan di database.")
            return True

        _send_brankas_record(bot, call.message.chat.id, row)
        return True

    return False


def process_brankas_text(bot, message, supabase, pending_actions: dict):
    user_id = message.from_user.id
    action = pending_actions.get(user_id, {})
    if action.get("kind") != "brankas_search":
        return False

    keyword = (message.text or "").strip()
    if len(keyword) < 2:
        bot.send_message(
            message.chat.id,
            "Kata kunci terlalu pendek. Ketik minimal 2 karakter.",
            reply_markup=brankas_search_keyboard(),
            parse_mode="HTML",
        )
        return True

    rows = _search_brankas_records(supabase, keyword, limit=10)
    pending_actions.pop(user_id, None)

    if not rows:
        bot.send_message(
            message.chat.id,
            f"🔎 Tidak ada berkas yang cocok untuk: <b>{escape(keyword)}</b>",
            reply_markup=brankas_search_keyboard(),
            parse_mode="HTML",
        )
        return True

    bot.send_message(
        message.chat.id,
        (
            "🔎 <b>Hasil pencarian Brankas</b>\n\n"
            f"Keyword: <b>{escape(keyword)}</b>\n"
            f"Jumlah hasil: <b>{len(rows)}</b>\n\n"
            "Pilih salah satu tombol hasil di bawah ini."
        ),
        reply_markup=brankas_result_keyboard(rows),
        parse_mode="HTML",
    )
    return True


def process_brankas_document(bot, message, supabase, pending_actions: dict):
    user_id = message.from_user.id
    action = pending_actions.get(user_id, {})
    if action.get("kind") != "brankas_wait_doc":
        return False

    document = message.document
    if not document:
        return True

    file_name = (getattr(document, "file_name", None) or "").strip()
    if not file_name:
        file_name = f"document_{document.file_id[:12]}"

    row = {
        "tipe_input": "dokumen",
        "judul_pencarian": file_name,
        "file_id": document.file_id,
    }

    _upsert_brankas_record(supabase, row)
    _publish_to_channel(bot, supabase, row)
    pending_actions.pop(user_id, None)

    bot.send_message(
        message.chat.id,
        f"✅ Dokumen tersimpan ke <b>Brankas Berkas</b>\n\nJudul: <b>{escape(file_name)}</b>",
        reply_markup=brankas_main_keyboard(),
        parse_mode="HTML",
    )
    return True


def process_brankas_photo(bot, message, supabase, pending_actions: dict):
    user_id = message.from_user.id
    action = pending_actions.get(user_id, {})
    if action.get("kind") != "brankas_wait_photo":
        return False

    caption = (message.caption or "").strip()
    if not caption:
        bot.send_message(
            message.chat.id,
            "Foto wajib disertai caption. Silakan kirim ulang foto + caption.",
            reply_markup=brankas_save_keyboard(),
            parse_mode="HTML",
        )
        return True

    if not message.photo:
        bot.send_message(
            message.chat.id,
            "Foto tidak ditemukan. Silakan kirim ulang gambar yang valid.",
            reply_markup=brankas_save_keyboard(),
            parse_mode="HTML",
        )
        return True

    row = {
        "tipe_input": "foto",
        "judul_pencarian": caption,
        "file_id": message.photo[-1].file_id,
    }

    _upsert_brankas_record(supabase, row)
    _publish_to_channel(bot, supabase, row)
    pending_actions.pop(user_id, None)

    bot.send_message(
        message.chat.id,
        f"✅ Foto tersimpan ke <b>Brankas Berkas</b>\n\nJudul: <b>{escape(caption)}</b>",
        reply_markup=brankas_main_keyboard(),
        parse_mode="HTML",
    )
    return True
