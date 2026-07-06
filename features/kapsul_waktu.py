import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

BASE_DIR = Path("/root/MyAsistenBot")
CAPSULE_ROOT = BASE_DIR / "capsules"
DRAFT_ROOT = CAPSULE_ROOT / "_drafts"
JAKARTA = ZoneInfo("Asia/Jakarta")

MONTHS_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

ALLOWED_MEDIA = {
    "text": "Text",
    "photo": "Photo",
    "video": "Video",
    "voice": "Voice",
    "document": "Document",
    "multi": "Multi File",
}

def ensure_capsule_dirs():
    CAPSULE_ROOT.mkdir(parents=True, exist_ok=True)
    DRAFT_ROOT.mkdir(parents=True, exist_ok=True)

def now_jkt():
    return datetime.now(JAKARTA)

def to_jkt(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JAKARTA)
    return dt.astimezone(JAKARTA)

def parse_db_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return to_jkt(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return to_jkt(datetime.fromisoformat(text))
    except Exception:
        return None

def format_id_datetime(dt):
    dt = to_jkt(dt)
    return f"{dt.day:02d} {MONTHS_ID[dt.month - 1]} {dt.year} {dt:%H:%M}"

def format_remaining(unlock_at):
    unlock_at = to_jkt(unlock_at)
    delta = unlock_at - now_jkt()
    if delta.total_seconds() <= 0:
        return "0 Hari"
    days = max(0, delta.days)
    if days == 0:
        return "Kurang dari 1 Hari"
    return f"{days} Hari"

def parse_unlock_text(text):
    raw = text.strip()
    patterns = [
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%dT%H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
    ]
    for fmt in patterns:
        try:
            dt = datetime.strptime(raw, fmt)
            if "%H:%M" not in fmt:
                dt = dt.replace(hour=0, minute=0)
            return dt.replace(tzinfo=JAKARTA)
        except Exception:
            continue
    return None

def sanitize_filename(name):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "file"

def get_user_identity(message):
    u = message.from_user
    return {
        "telegram_user_id": int(u.id),
        "username": u.username,
        "first_name": u.first_name,
        "last_name": u.last_name,
    }

def ensure_account_row(supabase, message):
    ident = get_user_identity(message)
    payload = {
        "telegram_user_id": ident["telegram_user_id"],
        "username": ident["username"],
        "first_name": ident["first_name"],
        "last_name": ident["last_name"],
    }
    try:
        supabase.table("kapsul_accounts").upsert(payload, on_conflict="telegram_user_id").execute()
    except Exception:
        # fallback: kalau upsert tidak cocok di environment tertentu, lanjut select
        pass

    resp = (
        supabase.table("kapsul_accounts")
        .select("*")
        .eq("telegram_user_id", ident["telegram_user_id"])
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]
    raise RuntimeError("Gagal memastikan akun kapsul.")

def ensure_account_row_by_user(supabase, user):
    payload = {
        "telegram_user_id": int(user.id),
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
    }
    try:
        supabase.table("kapsul_accounts").upsert(payload, on_conflict="telegram_user_id").execute()
    except Exception:
        pass
    resp = (
        supabase.table("kapsul_accounts")
        .select("*")
        .eq("telegram_user_id", int(user.id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None

def get_capsule_account_id(supabase, message):
    account = ensure_account_row(supabase, message)
    return account["id"], account

def get_state(pending_actions, user_id):
    return pending_actions.get(user_id)

def set_state(pending_actions, user_id, state):
    pending_actions[user_id] = state

def clear_capsule_state(pending_actions, user_id):
    state = pending_actions.get(user_id, {})
    if str(state.get("kind", "")).startswith("kapsul_"):
        pending_actions.pop(user_id, None)

def build_menu_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✍️ Buat Kapsul", callback_data="kapsul_create"),
        InlineKeyboardButton("📬 Kotak Kapsul", callback_data="kapsul_inbox"),
    )
    kb.row(
        InlineKeyboardButton("📅 Akan Datang", callback_data="kapsul_upcoming"),
        InlineKeyboardButton("📖 Riwayat", callback_data="kapsul_history"),
    )
    kb.row(
        InlineKeyboardButton("📊 Statistik", callback_data="kapsul_stats"),
        InlineKeyboardButton("⚙ Pengaturan", callback_data="kapsul_settings"),
    )
    kb.row(
        InlineKeyboardButton("⬅ Dashboard", callback_data="back_dashboard"),
    )
    return kb

def build_media_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📝 Text", callback_data="kapsul_pick:text"),
        InlineKeyboardButton("🖼 Photo", callback_data="kapsul_pick:photo"),
    )
    kb.row(
        InlineKeyboardButton("🎥 Video", callback_data="kapsul_pick:video"),
        InlineKeyboardButton("🎤 Voice", callback_data="kapsul_pick:voice"),
    )
    kb.row(
        InlineKeyboardButton("📄 Document", callback_data="kapsul_pick:document"),
        InlineKeyboardButton("📁 Multi File", callback_data="kapsul_pick:multi"),
    )
    kb.row(
        InlineKeyboardButton("⬅ Menu Kapsul", callback_data="kapsul_menu"),
    )
    return kb

def build_media_stage_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Selesai Upload", callback_data="kapsul_done_media"),
        InlineKeyboardButton("❌ Batal", callback_data="kapsul_cancel"),
    )
    return kb

def build_confirm_keyboard(capsule_id):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📬 Simpan Kapsul", callback_data=f"kapsul_confirm:{capsule_id}"),
        InlineKeyboardButton("❌ Batal", callback_data="kapsul_cancel"),
    )
    return kb

def build_capsule_view_keyboard(capsule_id, can_open):
    kb = InlineKeyboardMarkup()
    if can_open:
        kb.row(
            InlineKeyboardButton("📬 Buka Sekarang", callback_data=f"kapsul_open:{capsule_id}"),
        )
    kb.row(
        InlineKeyboardButton("⬅ Menu Kapsul", callback_data="kapsul_menu"),
    )
    return kb

def build_list_keyboard(capsules, open_prefix="kapsul_open"):
    kb = InlineKeyboardMarkup()
    for row in capsules[:8]:
        title = row.get("title") or f"Kapsul #{row['id']}"
        kb.row(
            InlineKeyboardButton(
                f"📦 {title[:32]}",
                callback_data=f"{open_prefix}:{row['id']}",
            )
        )
    kb.row(InlineKeyboardButton("⬅ Menu Kapsul", callback_data="kapsul_menu"))
    return kb

def capsule_status_emoji(status):
    return {
        "LOCKED": "🔒",
        "READY": "📬",
        "OPENED": "✅",
        "DELETED": "🗑️",
    }.get(status, "📦")

def capsule_status_text(row):
    status = row.get("status", "LOCKED")
    unlock_at = parse_db_datetime(row.get("unlock_at"))
    text = [
        f"📦 <b>KAPSUL</b>",
        "",
        f"Status: {capsule_status_emoji(status)} <b>{status}</b>",
    ]
    if unlock_at:
        text.append(f"Tanggal Buka: <b>{format_id_datetime(unlock_at)}</b>")
        text.append(f"Sisa: <b>{format_remaining(unlock_at)}</b>")
    return "\n".join(text)

def capsule_detail_text(row):
    title = escape(row.get("title") or "-")
    description = escape(row.get("description") or "-")
    status = row.get("status", "LOCKED")
    unlock_at = parse_db_datetime(row.get("unlock_at"))
    created_at = parse_db_datetime(row.get("created_at"))
    opened_at = parse_db_datetime(row.get("opened_at"))
    text = [
        "📦 <b>KAPSUL</b>",
        "",
        f"Judul: <b>{title}</b>",
        f"Deskripsi: {description}",
        f"Status: <b>{status}</b>",
    ]
    if unlock_at:
        text.append(f"Buka: <b>{format_id_datetime(unlock_at)}</b>")
        text.append(f"Sisa: <b>{format_remaining(unlock_at)}</b>")
    if created_at:
        text.append(f"Dibuat: <b>{format_id_datetime(created_at)}</b>")
    if opened_at:
        text.append(f"Dibuka: <b>{format_id_datetime(opened_at)}</b>")
    return "\n".join(text)

def show_kapsul_inbox(bot, message, supabase, user_id, edit=False):
    account = (
        supabase.table("kapsul_accounts")
        .select("*")
        .eq("telegram_user_id", int(user_id))
        .limit(1)
        .execute()
        .data
    )
    if not account:
        bot.send_message(message.chat.id, "Belum ada kapsul.")
        return

    account_id = account[0]["id"]

    # FIX:
    # sebelumnya hanya READY dan OPENED,
    # jadi kapsul LOCKED tidak kelihatan di kotak kapsul.
    rows = _fetch_capsules_for_user(
        supabase,
        account_id,
        statuses=["LOCKED", "READY", "OPENED"],
    )
    rows = sorted(rows, key=lambda x: parse_db_datetime(x.get("created_at")) or now_jkt(), reverse=True)

    text = "📬 <b>KOTAK KAPSUL</b>\n\n"
    if not rows:
        text += "Belum ada kapsul."
        kb = build_menu_keyboard()
    else:
        parts = []
        for r in rows[:10]:
            title = escape(r.get("title") or "Tanpa Judul")
            status = r.get("status", "LOCKED")
            unlock_at = parse_db_datetime(r.get("unlock_at"))
            line = f"• {capsule_status_emoji(status)} <b>{title}</b> — <b>{status}</b>"
            if unlock_at:
                line += f"\n  <i>{format_id_datetime(unlock_at)}</i>"
            parts.append(line)
        text += "\n".join(parts)
        kb = build_list_keyboard(rows)

    if edit:
        try:
            bot.edit_message_text(
                text,
                message.chat.id,
                message.message_id,
                reply_markup=kb,
            )
        except Exception:
            bot.send_message(message.chat.id, text, reply_markup=kb)
    else:
        bot.send_message(message.chat.id, text, reply_markup=kb)
def start_create_flow(bot, message, pending_actions, user_id):
    set_state(
        pending_actions,
        user_id,
        {
            "kind": "kapsul_choose_media",
            "draft_id": uuid.uuid4().hex,
            "draft_items": [],
            "media_mode": None,
            "title": None,
            "description": None,
            "unlock_at": None,
        },
    )
    bot.send_message(
        message.chat.id,
        "Pilih jenis media untuk kapsul ini.",
        reply_markup=build_media_keyboard(),
    )

def set_media_mode(bot, message, pending_actions, user_id, mode):
    state = get_state(pending_actions, user_id)
    if not state or not str(state.get("kind", "")).startswith("kapsul_"):
        return
    state["media_mode"] = mode
    if mode == "text":
        state["kind"] = "kapsul_wait_text"
        bot.send_message(
            message.chat.id,
            "Kirim isi teks untuk kapsul ini.",
            reply_markup=build_media_stage_keyboard(),
        )
    else:
        state["kind"] = "kapsul_wait_media"
        bot.send_message(
            message.chat.id,
            (
                f"Mode <b>{ALLOWED_MEDIA[mode]}</b> aktif.\n\n"
                "Kirim media sekarang.\n"
                "Kalau sudah selesai, tekan <b>Selesai Upload</b>."
            ),
            reply_markup=build_media_stage_keyboard(),
        )
    pending_actions[user_id] = state

def _download_file(bot, file_id, dest_path):
    file_info = bot.get_file(file_id)
    data = bot.download_file(file_info.file_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)

def _append_item(state, item):
    state.setdefault("draft_items", []).append(item)

def _temp_item_name(kind, index, ext):
    return f"{index:03d}_{kind}{ext}"

def _guess_document_ext(document):
    if document and document.file_name:
        suffix = Path(document.file_name).suffix
        if suffix:
            return suffix.lower()
    return ".bin"

def _save_text_to_draft(state, text):
    draft_dir = DRAFT_ROOT / state["draft_id"]
    draft_dir.mkdir(parents=True, exist_ok=True)
    index = len(state.get("draft_items", [])) + 1
    file_name = _temp_item_name("text", index, ".txt")
    file_path = draft_dir / file_name
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    _append_item(
        state,
        {
            "order": index,
            "kind": "text",
            "draft_name": file_name,
            "temp_path": str(file_path),
            "original_name": "text.txt",
            "caption": None,
        },
    )

def _save_binary_item(state, bot, file_id, kind, ext, original_name=None, caption=None):
    draft_dir = DRAFT_ROOT / state["draft_id"]
    draft_dir.mkdir(parents=True, exist_ok=True)
    index = len(state.get("draft_items", [])) + 1
    file_name = _temp_item_name(kind, index, ext)
    file_path = draft_dir / file_name
    _download_file(bot, file_id, file_path)
    _append_item(
        state,
        {
            "order": index,
            "kind": kind,
            "draft_name": file_name,
            "temp_path": str(file_path),
            "original_name": original_name,
            "caption": caption,
        },
    )

def _advance_after_single_media(bot, message, pending_actions, user_id):
    state = get_state(pending_actions, user_id)
    if not state:
        return
    state["kind"] = "kapsul_wait_title"
    pending_actions[user_id] = state
    bot.send_message(
        message.chat.id,
        "Kirim <b>Judul Kapsul</b> sekarang.",
        reply_markup=build_media_stage_keyboard(),
    )

def _ask_more_or_finish(bot, message):
    bot.send_message(
        message.chat.id,
        "Media tersimpan. Kirim lagi atau tekan <b>Selesai Upload</b>.",
        reply_markup=build_media_stage_keyboard(),
    )

def _reset_capsule_state_to_start(pending_actions, user_id):
    pending_actions.pop(user_id, None)

def _show_title_prompt(bot, message):
    bot.send_message(message.chat.id, "Kirim <b>Judul Kapsul</b> sekarang.")

def _show_description_prompt(bot, message):
    bot.send_message(
        message.chat.id,
        "Kirim <b>Deskripsi</b> sekarang. Kirim <code>-</code> jika ingin kosong.",
    )

def _show_unlock_prompt(bot, message):
    bot.send_message(
        message.chat.id,
        (
            "Kirim tanggal & waktu pembukaan.\n\n"
            "Format yang diterima:\n"
            "<code>YYYY-MM-DD HH:MM</code>\n"
            "<code>DD/MM/YYYY HH:MM</code>\n"
            "<code>DD-MM-YYYY HH:MM</code>\n\n"
            "Contoh: <code>2030-01-01 08:00</code>"
        ),
    )

def _build_final_capsule_payload(account_id, title, description, folder_name, unlock_at):
    return {
        "account_id": account_id,
        "title": title,
        "description": description,
        "folder_name": folder_name,
        "unlock_at": unlock_at.astimezone(timezone.utc).isoformat(),
        "status": "LOCKED",
    }

def _write_metadata_file(final_dir, capsule_row, items):
    metadata = {
        "capsule": capsule_row,
        "items": items,
    }
    with open(final_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

def _move_draft_to_final(state, final_dir):
    draft_dir = DRAFT_ROOT / state["draft_id"]
    final_dir.mkdir(parents=True, exist_ok=False)
    final_items = []
    for item in sorted(state.get("draft_items", []), key=lambda x: x["order"]):
        src = Path(item["temp_path"])
        ext = src.suffix.lower() or ".bin"
        safe_kind = item["kind"]
        final_name = f"{safe_kind}_{item['order']}{ext}"
        dst = final_dir / final_name
        shutil.move(str(src), str(dst))
        final_items.append(
            {
                "order": item["order"],
                "kind": item["kind"],
                "file_name": final_name,
                "original_name": item.get("original_name"),
                "caption": item.get("caption"),
            }
        )
    try:
        if draft_dir.exists():
            shutil.rmtree(draft_dir, ignore_errors=True)
    except Exception:
        pass
    return final_items

def _fetch_capsules_for_user(supabase, account_id, statuses=None):
    q = supabase.table("kapsul_capsules").select("*").eq("account_id", account_id)
    if statuses:
        # Supabase python client tidak selalu stabil untuk in_ di semua versi,
        # jadi filter di Python bila perlu.
        resp = q.execute()
        rows = resp.data or []
        return [r for r in rows if r.get("status") in set(statuses)]
    resp = q.execute()
    return resp.data or []

def _get_capsule_by_id(supabase, capsule_id):
    resp = supabase.table("kapsul_capsules").select("*").eq("id", capsule_id).limit(1).execute()
    return resp.data[0] if resp.data else None

def _get_account_by_id(supabase, account_id):
    resp = supabase.table("kapsul_accounts").select("*").eq("id", account_id).limit(1).execute()
    return resp.data[0] if resp.data else None

def show_kapsul_inbox(bot, message, supabase, user_id, edit=False):
    account = (
        supabase.table("kapsul_accounts")
        .select("*")
        .eq("telegram_user_id", int(user_id))
        .limit(1)
        .execute()
        .data
    )
    if not account:
        bot.send_message(message.chat.id, "Belum ada kapsul.")
        return
    account_id = account[0]["id"]
    rows = _fetch_capsules_for_user(supabase, account_id, statuses=["READY", "OPENED"])
    text = "📬 <b>KOTAK KAPSUL</b>\n\n"
    if not rows:
        text += "Belum ada kapsul yang siap dibuka."
        kb = build_menu_keyboard()
    else:
        text += "\n".join(
            f"• {capsule_status_emoji(r.get('status'))} <b>{escape(r.get('title') or 'Tanpa Judul')}</b>"
            for r in rows[:8]
        )
        kb = build_list_keyboard(rows)
    if edit:
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=kb)
        except Exception:
            bot.send_message(message.chat.id, text, reply_markup=kb)
    else:
        bot.send_message(message.chat.id, text, reply_markup=kb)

def show_kapsul_upcoming(bot, message, supabase, user_id, edit=False):
    account = (
        supabase.table("kapsul_accounts")
        .select("*")
        .eq("telegram_user_id", int(user_id))
        .limit(1)
        .execute()
        .data
    )
    if not account:
        bot.send_message(message.chat.id, "Belum ada kapsul.")
        return
    account_id = account[0]["id"]
    rows = _fetch_capsules_for_user(supabase, account_id, statuses=["LOCKED"])
    rows = sorted(rows, key=lambda x: parse_db_datetime(x.get("unlock_at")) or now_jkt())
    text = "📅 <b>AKAN DATANG</b>\n\n"
    if not rows:
        text += "Tidak ada kapsul yang masih terkunci."
        kb = build_menu_keyboard()
    else:
        parts = []
        for r in rows[:8]:
            unlock_at = parse_db_datetime(r.get("unlock_at"))
            parts.append(
                f"• 🔒 <b>{escape(r.get('title') or 'Tanpa Judul')}</b> — {format_id_datetime(unlock_at) if unlock_at else '-'}"
            )
        text += "\n".join(parts)
        kb = build_list_keyboard(rows, open_prefix="kapsul_open")
    if edit:
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=kb)
        except Exception:
            bot.send_message(message.chat.id, text, reply_markup=kb)
    else:
        bot.send_message(message.chat.id, text, reply_markup=kb)

def show_kapsul_history(bot, message, supabase, user_id, edit=False):
    account = (
        supabase.table("kapsul_accounts")
        .select("*")
        .eq("telegram_user_id", int(user_id))
        .limit(1)
        .execute()
        .data
    )
    if not account:
        bot.send_message(message.chat.id, "Belum ada kapsul.")
        return
    account_id = account[0]["id"]
    rows = _fetch_capsules_for_user(supabase, account_id, statuses=["OPENED", "DELETED"])
    rows = sorted(rows, key=lambda x: parse_db_datetime(x.get("opened_at") or x.get("created_at")) or now_jkt(), reverse=True)
    text = "📖 <b>RIWAYAT</b>\n\n"
    if not rows:
        text += "Belum ada riwayat kapsul."
        kb = build_menu_keyboard()
    else:
        parts = []
        for r in rows[:8]:
            parts.append(
                f"• {capsule_status_emoji(r.get('status'))} <b>{escape(r.get('title') or 'Tanpa Judul')}</b>"
            )
        text += "\n".join(parts)
        kb = build_list_keyboard(rows)
    if edit:
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=kb)
        except Exception:
            bot.send_message(message.chat.id, text, reply_markup=kb)
    else:
        bot.send_message(message.chat.id, text, reply_markup=kb)

def show_kapsul_stats(bot, message, supabase, user_id, edit=False):
    account = (
        supabase.table("kapsul_accounts")
        .select("*")
        .eq("telegram_user_id", int(user_id))
        .limit(1)
        .execute()
        .data
    )
    if not account:
        bot.send_message(message.chat.id, "Belum ada kapsul.")
        return
    account_id = account[0]["id"]
    rows = _fetch_capsules_for_user(supabase, account_id)
    total = len(rows)
    locked = len([r for r in rows if r.get("status") == "LOCKED"])
    ready = len([r for r in rows if r.get("status") == "READY"])
    opened = len([r for r in rows if r.get("status") == "OPENED"])
    deleted = len([r for r in rows if r.get("status") == "DELETED"])
    text = (
        "📊 <b>STATISTIK KAPSUL</b>\n\n"
        f"Total: <b>{total}</b>\n"
        f"Locked: <b>{locked}</b>\n"
        f"Ready: <b>{ready}</b>\n"
        f"Opened: <b>{opened}</b>\n"
        f"Deleted: <b>{deleted}</b>"
    )
    kb = build_menu_keyboard()
    if edit:
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=kb)
        except Exception:
            bot.send_message(message.chat.id, text, reply_markup=kb)
    else:
        bot.send_message(message.chat.id, text, reply_markup=kb)

def show_kapsul_settings(bot, message, edit=False):
    text = (
        "⚙ <b>PENGATURAN KAPSUL</b>\n\n"
        "Zona waktu: <b>Asia/Jakarta</b>\n"
        "Penyimpanan media: <b>/root/MyAsistenBot/capsules/</b>\n"
        "Format tanggal: <code>YYYY-MM-DD HH:MM</code>"
    )
    kb = build_menu_keyboard()
    if edit:
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=kb)
        except Exception:
            bot.send_message(message.chat.id, text, reply_markup=kb)
    else:
        bot.send_message(message.chat.id, text, reply_markup=kb)

def _show_capsule_detail(bot, message, row):
    unlock_at = parse_db_datetime(row.get("unlock_at"))
    can_open = row.get("status") in ("READY",) or (row.get("status") == "LOCKED" and unlock_at and now_jkt() >= unlock_at)
    text = capsule_detail_text(row)
    if row.get("status") == "LOCKED" and unlock_at and now_jkt() < unlock_at:
        text += "\n\n⚠ Kapsul belum dapat dibuka sebelum waktu yang telah ditentukan."
        kb = build_capsule_view_keyboard(row["id"], False)
    elif row.get("status") == "READY" or can_open:
        text += "\n\n📬 Kapsul siap dibuka."
        kb = build_capsule_view_keyboard(row["id"], True)
    elif row.get("status") == "OPENED":
        text += "\n\n✅ Kapsul sudah dibuka."
        kb = build_capsule_view_keyboard(row["id"], False)
    else:
        kb = build_capsule_view_keyboard(row["id"], False)
    bot.send_message(message.chat.id, text, reply_markup=kb)

def _send_capsule_contents(bot, chat_id, capsule_row):
    final_dir = CAPSULE_ROOT / capsule_row["folder_name"]
    metadata_file = final_dir / "metadata.json"
    if not metadata_file.exists():
        raise RuntimeError("metadata.json tidak ditemukan.")
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    items = sorted(metadata.get("items", []), key=lambda x: x.get("order", 0))
    for item in items:
        kind = item.get("kind")
        file_name = item.get("file_name")
        file_path = final_dir / file_name
        caption = item.get("caption")
        if kind == "text":
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            bot.send_message(chat_id, text or " ")
        elif kind == "photo":
            with open(file_path, "rb") as f:
                bot.send_photo(chat_id, f, caption=caption or None)
        elif kind == "video":
            with open(file_path, "rb") as f:
                bot.send_video(chat_id, f, caption=caption or None)
        elif kind == "voice":
            with open(file_path, "rb") as f:
                bot.send_voice(chat_id, f, caption=caption or None)
        elif kind == "document":
            with open(file_path, "rb") as f:
                bot.send_document(chat_id, f, caption=caption or None)
        else:
            with open(file_path, "rb") as f:
                bot.send_document(chat_id, f, caption=caption or None)

def _finalize_capsule(bot, message, supabase, pending_actions, user_id):
    state = get_state(pending_actions, user_id)
    if not state:
        bot.send_message(message.chat.id, "State kapsul tidak ditemukan.")
        return

    title = (state.get("title") or "").strip()
    unlock_at = state.get("unlock_at")
    if not title or not unlock_at:
        bot.send_message(message.chat.id, "Judul atau waktu buka belum lengkap.")
        return

    account_id, _account = get_capsule_account_id(supabase, message)
    payload = _build_final_capsule_payload(
        account_id=account_id,
        title=title,
        description=(state.get("description") or "").strip() or None,
        folder_name="PENDING",
        unlock_at=unlock_at,
    )

    inserted = supabase.table("kapsul_capsules").insert(payload).execute()
    if not inserted.data:
        raise RuntimeError("Gagal membuat kapsul di database.")
    row = inserted.data[0]
    capsule_id = row["id"]
    folder_name = f"CP{capsule_id:06d}"
    final_dir = CAPSULE_ROOT / folder_name

    try:
        final_items = _move_draft_to_final(state, final_dir)
        capsule_row = {
            **row,
            "folder_name": folder_name,
        }
        _write_metadata_file(final_dir, capsule_row, final_items)
        supabase.table("kapsul_capsules").update({"folder_name": folder_name}).eq("id", capsule_id).execute()
    except Exception:
        try:
            supabase.table("kapsul_capsules").delete().eq("id", capsule_id).execute()
        except Exception:
            pass
        raise

    _reset_capsule_state_to_start(pending_actions, user_id)
    bot.send_message(
        message.chat.id,
        (
            "📦 <b>KAPSUL BERHASIL DIBUAT</b>\n\n"
            f"Judul: <b>{escape(title)}</b>\n"
            f"Folder: <code>{folder_name}</code>\n"
            f"Status: <b>LOCKED</b>"
        ),
        reply_markup=build_menu_keyboard(),
    )

def process_kapsul_text(bot, message, supabase, pending_actions):
    if not message.text:
        return False
    user_id = message.from_user.id
    state = get_state(pending_actions, user_id)
    if not state:
        return False

    kind = state.get("kind")
    text = message.text.strip()

    if kind == "kapsul_wait_text":
        if text.startswith("/"):
            return True
        ensure_capsule_dirs()
        _save_text_to_draft(state, text)
        if state.get("media_mode") == "multi":
            state["kind"] = "kapsul_wait_media"
            pending_actions[user_id] = state
            _ask_more_or_finish(bot, message)
        else:
            pending_actions[user_id] = state
            _advance_after_single_media(bot, message, pending_actions, user_id)
        return True

    if kind == "kapsul_wait_title":
        if text.startswith("/"):
            return True
        state["title"] = text
        state["kind"] = "kapsul_wait_description"
        pending_actions[user_id] = state
        _show_description_prompt(bot, message)
        return True

    if kind == "kapsul_wait_description":
        if text.startswith("/"):
            return True
        state["description"] = "" if text in ("-", "skip", "SKIP", "Skip", "kosong", "Kosong") else text
        state["kind"] = "kapsul_wait_unlock"
        pending_actions[user_id] = state
        _show_unlock_prompt(bot, message)
        return True

    if kind == "kapsul_wait_unlock":
        dt = parse_unlock_text(text)
        if not dt:
            bot.send_message(
                message.chat.id,
                "Format tanggal tidak valid. Contoh: <code>2030-01-01 08:00</code>",
            )
            return True
        state["unlock_at"] = dt
        state["kind"] = "kapsul_confirm"
        pending_actions[user_id] = state

        preview = (
            "📦 <b>KONFIRMASI KAPSUL</b>\n\n"
            f"Judul: <b>{escape(state.get('title') or '-')}</b>\n"
            f"Deskripsi: {escape(state.get('description') or '-')}\n"
            f"Buka: <b>{format_id_datetime(dt)}</b>\n"
            f"Media: <b>{len(state.get('draft_items', []))}</b> item\n\n"
            "Tekan simpan jika sudah benar."
        )
        bot.send_message(message.chat.id, preview, reply_markup=build_confirm_keyboard("draft"))
        return True

    return False

def process_kapsul_document(bot, message, supabase, pending_actions):
    user_id = message.from_user.id
    state = get_state(pending_actions, user_id)
    if not state or state.get("kind") != "kapsul_wait_media":
        return False

    mode = state.get("media_mode")
    if mode not in ("document", "multi"):
        return False

    ensure_capsule_dirs()
    doc = message.document
    ext = _guess_document_ext(doc)
    caption = getattr(message, "caption", None)
    _save_binary_item(state, bot, doc.file_id, "document", ext, original_name=doc.file_name, caption=caption)
    pending_actions[user_id] = state

    if mode == "multi":
        _ask_more_or_finish(bot, message)
    else:
        _advance_after_single_media(bot, message, pending_actions, user_id)
    return True

def process_kapsul_photo(bot, message, supabase, pending_actions):
    user_id = message.from_user.id
    state = get_state(pending_actions, user_id)
    if not state or state.get("kind") != "kapsul_wait_media":
        return False

    mode = state.get("media_mode")
    if mode not in ("photo", "multi"):
        return False

    ensure_capsule_dirs()
    photo = message.photo[-1]
    caption = getattr(message, "caption", None)
    _save_binary_item(state, bot, photo.file_id, "photo", ".jpg", original_name="photo.jpg", caption=caption)
    pending_actions[user_id] = state

    if mode == "multi":
        _ask_more_or_finish(bot, message)
    else:
        _advance_after_single_media(bot, message, pending_actions, user_id)
    return True

def process_kapsul_video(bot, message, supabase, pending_actions):
    user_id = message.from_user.id
    state = get_state(pending_actions, user_id)
    if not state or state.get("kind") != "kapsul_wait_media":
        return False

    mode = state.get("media_mode")
    if mode not in ("video", "multi"):
        return False

    ensure_capsule_dirs()
    video = message.video
    caption = getattr(message, "caption", None)
    _save_binary_item(state, bot, video.file_id, "video", ".mp4", original_name="video.mp4", caption=caption)
    pending_actions[user_id] = state

    if mode == "multi":
        _ask_more_or_finish(bot, message)
    else:
        _advance_after_single_media(bot, message, pending_actions, user_id)
    return True

def process_kapsul_voice(bot, message, supabase, pending_actions):
    user_id = message.from_user.id
    state = get_state(pending_actions, user_id)
    if not state or state.get("kind") != "kapsul_wait_media":
        return False

    mode = state.get("media_mode")
    if mode not in ("voice", "multi"):
        return False

    ensure_capsule_dirs()
    voice = message.voice
    _save_binary_item(state, bot, voice.file_id, "voice", ".ogg", original_name="voice.ogg")
    pending_actions[user_id] = state

    if mode == "multi":
        _ask_more_or_finish(bot, message)
    else:
        _advance_after_single_media(bot, message, pending_actions, user_id)
    return True

def process_kapsul_callback(bot, call, supabase, pending_actions, show_dashboard=None):
    data = call.data or ""
    user_id = call.from_user.id

    if data == "kapsul_menu":
        clear_capsule_state(pending_actions, user_id)
        show_kapsul_menu(bot, call.message, supabase, user_id, edit=True)
        return True

    if data == "kapsul_create":
        clear_capsule_state(pending_actions, user_id)
        start_create_flow(bot, call.message, pending_actions, user_id)
        return True

    if data.startswith("kapsul_open:"):
        capsule_id = int(data.split(":", 1)[1])
        row = _get_capsule_by_id(supabase, capsule_id)
        if not row:
            bot.send_message(call.message.chat.id, "Kapsul tidak ditemukan.")
            return True

        account = _get_account_by_id(supabase, row["account_id"])
        if not account or int(account["telegram_user_id"]) != int(user_id):
            bot.send_message(call.message.chat.id, "Kapsul ini bukan milik Anda.")
            return True

        unlock_at = parse_db_datetime(row.get("unlock_at"))
        now = now_jkt()

        if row.get("status") == "DELETED":
            bot.send_message(call.message.chat.id, "Kapsul ini sudah dihapus.")
            return True

        if row.get("status") == "OPENED":
            _show_capsule_detail(bot, call.message, row)
            return True

        if unlock_at and now < unlock_at:
            _show_capsule_detail(bot, call.message, row)
            return True
        try:
            _send_capsule_contents(bot, call.message.chat.id, row)
            opened_iso = now_jkt().astimezone(timezone.utc).isoformat()
            supabase.table("kapsul_capsules").update(
                {
                    "status": "OPENED",
                    "opened_at": opened_iso,
                }
            ).eq("id", capsule_id).execute()
            row["status"] = "OPENED"
            row["opened_at"] = opened_iso
            bot.send_message(call.message.chat.id, "✅ Kapsul selesai dibuka.")
        except Exception as exc:
            bot.send_message(call.message.chat.id, f"Gagal membuka kapsul: {exc}")
            return True

        _show_capsule_detail(bot, call.message, row)
        return True
    if data == "kapsul_done_media":
        state = get_state(pending_actions, user_id)
        if not state:
            bot.send_message(call.message.chat.id, "State kapsul tidak ditemukan.")
            return True
        if not state.get("draft_items"):
            bot.send_message(call.message.chat.id, "Belum ada media yang dikirim.")
            return True
        state["kind"] = "kapsul_wait_title"
        pending_actions[user_id] = state
        _show_title_prompt(bot, call.message)
        return True

    if data == "kapsul_cancel":
        clear_capsule_state(pending_actions, user_id)
        bot.send_message(call.message.chat.id, "Pembuatan kapsul dibatalkan.")
        show_kapsul_menu(bot, call.message, supabase, user_id, edit=False)
        return True

    if data == "kapsul_inbox":
        show_kapsul_inbox(bot, call.message, supabase, user_id, edit=True)
        return True

    if data == "kapsul_upcoming":
        show_kapsul_upcoming(bot, call.message, supabase, user_id, edit=True)
        return True

    if data == "kapsul_history":
        show_kapsul_history(bot, call.message, supabase, user_id, edit=True)
        return True

    if data == "kapsul_stats":
        show_kapsul_stats(bot, call.message, supabase, user_id, edit=True)
        return True

    if data == "kapsul_settings":
        show_kapsul_settings(bot, call.message, edit=True)
        return True

    if data.startswith("kapsul_open:"):
        capsule_id = int(data.split(":", 1)[1])
        row = _get_capsule_by_id(supabase, capsule_id)
        if not row:
            bot.send_message(call.message.chat.id, "Kapsul tidak ditemukan.")
            return True
        account = _get_account_by_id(supabase, row["account_id"])
        if not account or int(account["telegram_user_id"]) != int(user_id):
            bot.send_message(call.message.chat.id, "Kapsul ini bukan milik Anda.")
            return True

        unlock_at = parse_db_datetime(row.get("unlock_at"))
        now = now_jkt()

        if row.get("status") == "DELETED":
            bot.send_message(call.message.chat.id, "Kapsul ini sudah dihapus.")
            return True

        if row.get("status") == "OPENED":
            _show_capsule_detail(bot, call.message, row)
            return True

        if unlock_at and now < unlock_at:
            _show_capsule_detail(bot, call.message, row)
            return True

        if row.get("status") == "LOCKED":
            try:
                supabase.table("kapsul_capsules").update({"status": "READY"}).eq("id", capsule_id).execute()
                row["status"] = "READY"
            except Exception:
                pass

        _show_capsule_detail(bot, call.message, row)
        return True

    if data.startswith("kapsul_confirm:"):
        state = get_state(pending_actions, user_id)
        if not state:
            bot.send_message(call.message.chat.id, "State kapsul tidak ditemukan.")
            return True
        _finalize_capsule(bot, call.message, supabase, pending_actions, user_id)
        return True

    return False

def process_kapsul_media_ready(bot, message, supabase, pending_actions):
    return (
        process_kapsul_document(bot, message, supabase, pending_actions)
        or process_kapsul_photo(bot, message, supabase, pending_actions)
        or process_kapsul_video(bot, message, supabase, pending_actions)
        or process_kapsul_voice(bot, message, supabase, pending_actions)
    )

def _notify_ready_capsule(bot, supabase, row):
    account = _get_account_by_id(supabase, row["account_id"])
    if not account:
        return

    chat_id = int(account["telegram_user_id"])
    unlock_at = parse_db_datetime(row.get("unlock_at"))
    text = (
        "📦 <b>KAPSUL SIAP DIBUKA</b>\n\n"
        f"Judul: <b>{escape(row.get('title') or '-')}</b>\n"
        f"Buka: <b>{format_id_datetime(unlock_at) if unlock_at else '-'}</b>\n\n"
        "Tekan tombol di bawah untuk membuka."
    )
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("📬 Buka Sekarang", callback_data=f"kapsul_open:{row['id']}"))
    bot.send_message(chat_id, text, reply_markup=kb)
def start_kapsul_scheduler(bot, supabase, interval_seconds=60):
    ensure_capsule_dirs()

    def worker():
        while True:
            try:
                resp = supabase.table("kapsul_capsules").select("*").eq("status", "LOCKED").execute()
                rows = resp.data or []
                now = now_jkt()
                for row in rows:
                    unlock_at = parse_db_datetime(row.get("unlock_at"))
                    if unlock_at and now >= unlock_at:
                        try:
                            supabase.table("kapsul_capsules").update({"status": "READY"}).eq("id", row["id"]).execute()
                            row["status"] = "READY"
                            _notify_ready_capsule(bot, supabase, row)
                        except Exception:
                            pass
            except Exception as exc:
                print(f"[KAPSUL] scheduler error: {exc}")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread
