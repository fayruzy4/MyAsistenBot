import os
import traceback
from html import escape
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

BRANKAS_TABLE = "brankas_berkas"
BRANKAS_CHANNEL_ID_RAW = os.getenv("BRANKAS_CHANNEL_ID", "").strip()
GEMINI_EMBEDDING_KEY = os.getenv("GEMINI_EMBEDDING_KEY", "").strip()

BRANKAS_EMBEDDING_DIM = int(os.getenv("BRANKAS_EMBEDDING_DIM", "768"))
BRANKAS_MATCH_THRESHOLD = float(os.getenv("BRANKAS_MATCH_THRESHOLD", "0.78"))
BRANKAS_MATCH_COUNT = int(os.getenv("BRANKAS_MATCH_COUNT", "10"))

def _normalize_channel_id(value: str):
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return value

BRANKAS_CHANNEL_ID = _normalize_channel_id(BRANKAS_CHANNEL_ID_RAW)

try:
    _EMBED_CLIENT = genai.Client(api_key=GEMINI_EMBEDDING_KEY) if GEMINI_EMBEDDING_KEY else None
except Exception as exc:
    _EMBED_CLIENT = None
    print(f"🚨 Brankas embedding client gagal diinisialisasi: {exc}")

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

def _vector_literal(values: List[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"

def _guess_mime_from_filename(filename: str) -> Optional[str]:
    ext = os.path.splitext((filename or "").lower())[1]
    if ext == ".pdf":
        return "application/pdf"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return None

def _download_telegram_file_bytes(bot, file_id: str) -> bytes:
    file_info = bot.get_file(file_id)
    return bot.download_file(file_info.file_path)

def _embed_query_text(query: str) -> List[float]:
    if _EMBED_CLIENT is None:
        raise RuntimeError("GEMINI_EMBEDDING_KEY belum dikonfigurasi.")

    response = _EMBED_CLIENT.models.embed_content(
        model="gemini-embedding-2",
        contents=f"task: search result | query: {query}",
        config=types.EmbedContentConfig(output_dimensionality=BRANKAS_EMBEDDING_DIM),
    )
    [embedding_obj] = response.embeddings
    return [float(v) for v in embedding_obj.values]

def _embed_document_text(title: str, text: str, file_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> List[float]:
    if _EMBED_CLIENT is None:
        raise RuntimeError("GEMINI_EMBEDDING_KEY belum dikonfigurasi.")

    title = (title or "none").strip() or "none"
    text = (text or title).strip() or "none"

    contents: Any
    if file_bytes and mime_type:
        contents = [
            f"title: {title} | text: {text}",
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
        ]
    else:
        contents = f"title: {title} | text: {text}"

    response = _EMBED_CLIENT.models.embed_content(
        model="gemini-embedding-2",
        contents=contents,
        config=types.EmbedContentConfig(output_dimensionality=BRANKAS_EMBEDDING_DIM),
    )
    [embedding_obj] = response.embeddings
    return [float(v) for v in embedding_obj.values]

def _store_row_with_embedding(supabase, row: dict, embedding_values: Optional[List[float]]):
    payload = {
        "p_owner_id": row["owner_id"],
        "p_tipe_input": row["tipe_input"],
        "p_judul_pencarian": row["judul_pencarian"],
        "p_file_id": row["file_id"],
        "p_embedding": _vector_literal(embedding_values) if embedding_values else None,
        "p_channel_chat_id": row.get("channel_chat_id"),
        "p_channel_message_id": row.get("channel_message_id"),
    }
    return supabase.rpc("add_brankas_berkas", payload).execute()

def _archive_to_channel(bot, supabase, row: dict):
    if not BRANKAS_CHANNEL_ID:
        return None

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

        return sent

    except Exception as exc:
        report_local_error("publish_to_channel", exc)
        return None

def _search_rows(supabase, owner_id: int, query: str, limit: int = BRANKAS_MATCH_COUNT):
    query_embedding = _embed_query_text(query)
    result = supabase.rpc(
        "match_brankas_berkas",
        {
            "p_owner_id": owner_id,
            "p_query_embedding": _vector_literal(query_embedding),
            "p_match_threshold": BRANKAS_MATCH_THRESHOLD,
            "p_match_count": limit,
        },
    ).execute()
    return result.data or []

def _get_row_by_id(supabase, record_id: int):
    result = (
        supabase.table(BRANKAS_TABLE)
        .select("id,owner_id,tipe_input,judul_pencarian,file_id,channel_chat_id,channel_message_id,created_at")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None

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
        similarity = row.get("similarity")
        percent = ""
        if isinstance(similarity, (int, float)):
            percent = f" ({int(round(float(similarity) * 100))}%)"
        icon = "📄" if tipe_input == "dokumen" else "🖼️"
        label = f"{icon} {_short_text(judul)}{percent}"
        kb.row(InlineKeyboardButton(label, callback_data=f"brankas_open:{row['id']}"))

    kb.row(
        InlineKeyboardButton("🔎 Cari Lagi", callback_data="brankas_search_start"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb

def brankas_open_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔎 Cari Lagi", callback_data="brankas_search_start"))
    kb.row(
        InlineKeyboardButton("⬅️ Kembali", callback_data="brankas_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb

def show_brankas_menu(bot, message, edit=False):
    text = (
        "📂 <b>Brankas Berkas</b>\n\n"
        "Simpan file ke Supabase, arsipkan ke channel, lalu cari dengan semantic search."
    )
    if edit:
        _send_or_edit(bot, message, text, brankas_main_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=brankas_main_keyboard(), parse_mode="HTML")

def show_brankas_save_menu(bot, message, edit=False):
    text = (
        "📥 <b>Simpan Berkas Baru</b>\n\n"
        "Pilih jenis berkas yang ingin disimpan."
    )
    if edit:
        _send_or_edit(bot, message, text, brankas_save_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=brankas_save_keyboard(), parse_mode="HTML")

def show_brankas_search_prompt(bot, message, edit=False):
    text = (
        "🔍 <b>Cari Berkas</b>\n\n"
        "Ketik kata kunci atau kalimat bebas. Hasil dicari pakai makna, bukan nama file saja."
    )
    if edit:
        _send_or_edit(bot, message, text, brankas_search_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=brankas_search_keyboard(), parse_mode="HTML")

def show_brankas_doc_prompt(bot, message):
    bot.send_message(
        message.chat.id,
        "📁 <b>Menu Dokumen aktif</b>\n\n"
        "Kirim file dokumen sekarang. Nama file asli Telegram akan dipakai sebagai judul.",
        reply_markup=brankas_save_keyboard(),
        parse_mode="HTML",
    )

def show_brankas_photo_prompt(bot, message):
    bot.send_message(
        message.chat.id,
        "🖼️ <b>Menu Foto aktif</b>\n\n"
        "Kirim foto + caption. Caption wajib diisi dan dipakai sebagai metadata utama.",
        reply_markup=brankas_save_keyboard(),
        parse_mode="HTML",
    )

def _send_brankas_file(bot, chat_id: int, row: dict):
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

def _prepare_document_embedding(bot, document_file_id: str, file_name: str) -> Optional[List[float]]:
    title = file_name or "none"
    ext = os.path.splitext((file_name or "").lower())[1]
    if ext == ".pdf":
        try:
            pdf_bytes = _download_telegram_file_bytes(bot, document_file_id)
            return _embed_document_text(title=title, text=title, file_bytes=pdf_bytes, mime_type="application/pdf")
        except Exception as exc:
            report_local_error("document_pdf_embedding", exc)
    try:
        return _embed_document_text(title=title, text=title)
    except Exception as exc:
        report_local_error("document_text_embedding", exc)
        return None

def _prepare_photo_embedding(bot, photo_file_id: str, caption: str) -> Optional[List[float]]:
    title = caption or "none"
    try:
        image_bytes = _download_telegram_file_bytes(bot, photo_file_id)
        return _embed_document_text(title=title, text=title, file_bytes=image_bytes, mime_type="image/jpeg")
    except Exception as exc:
        report_local_error("photo_image_embedding", exc)
    try:
        return _embed_document_text(title=title, text=title)
    except Exception as exc:
        report_local_error("photo_text_embedding", exc)
        return None

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

        row = _get_row_by_id(supabase, record_id)
        if not row:
            bot.send_message(call.message.chat.id, "Berkas tidak ditemukan di database.")
            return True

        _send_brankas_file(bot, call.message.chat.id, row)
        return True

    return False

def process_brankas_text(bot, message, supabase, pending_actions: dict):
    user_id = message.from_user.id
    action = pending_actions.get(user_id, {})
    if action.get("kind") != "brankas_search":
        return False

    query = (message.text or "").strip()
    if len(query) < 2:
        bot.send_message(
            message.chat.id,
            "Kata kunci terlalu pendek. Ketik minimal 2 karakter.",
            reply_markup=brankas_search_keyboard(),
            parse_mode="HTML",
        )
        return True

    try:
        rows = _search_rows(supabase, user_id, query, limit=BRANKAS_MATCH_COUNT)
    except Exception as exc:
        report_local_error("brankas_search", exc)
        bot.send_message(
            message.chat.id,
            f"❌ Pencarian gagal:\n<code>{escape(str(exc))}</code>",
            reply_markup=brankas_search_keyboard(),
            parse_mode="HTML",
        )
        return True

    pending_actions.pop(user_id, None)

    if not rows:
        bot.send_message(
            message.chat.id,
            f"🔎 Tidak ada berkas yang cocok untuk: <b>{escape(query)}</b>",
            reply_markup=brankas_search_keyboard(),
            parse_mode="HTML",
        )
        return True

    bot.send_message(
        message.chat.id,
        (
            "🔎 <b>Hasil pencarian Brankas</b>\n\n"
            f"Query: <b>{escape(query)}</b>\n"
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

    embedding = _prepare_document_embedding(bot, document.file_id, file_name)

    row = {
        "owner_id": user_id,
        "tipe_input": "dokumen",
        "judul_pencarian": file_name,
        "file_id": document.file_id,
    }

    try:
        _store_row_with_embedding(supabase, row, embedding)
        _archive_to_channel(bot, supabase, row)
    except Exception as exc:
        report_local_error("process_brankas_document_store", exc)
        bot.send_message(
            message.chat.id,
            f"❌ Gagal menyimpan dokumen:\n<code>{escape(str(exc))}</code>",
            reply_markup=brankas_main_keyboard(),
            parse_mode="HTML",
        )
        return True

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

    photo_file_id = message.photo[-1].file_id
    embedding = _prepare_photo_embedding(bot, photo_file_id, caption)

    row = {
        "owner_id": user_id,
        "tipe_input": "foto",
        "judul_pencarian": caption,
        "file_id": photo_file_id,
    }

    try:
        _store_row_with_embedding(supabase, row, embedding)
        _archive_to_channel(bot, supabase, row)
    except Exception as exc:
        report_local_error("process_brankas_photo_store", exc)
        bot.send_message(
            message.chat.id,
            f"❌ Gagal menyimpan foto:\n<code>{escape(str(exc))}</code>",
            reply_markup=brankas_main_keyboard(),
            parse_mode="HTML",
        )
        return True

    pending_actions.pop(user_id, None)

    bot.send_message(
        message.chat.id,
        f"✅ Foto tersimpan ke <b>Brankas Berkas</b>\n\nJudul: <b>{escape(caption)}</b>",
        reply_markup=brankas_main_keyboard(),
        parse_mode="HTML",
    )
    return True
