import os
import time
import uuid
import traceback
from html import escape
from urllib.parse import urlparse

try:
    import yt_dlp
except Exception:
    yt_dlp = None

DOWNLOADS_DIR = "downloads"
SUPPORTED_PLATFORMS = ("tiktok", "youtube", "instagram", "facebook", "universal")
SUPPORTED_FORMATS = ("video", "audio", "photo", "auto")


def report_local_error(where: str, exc: Exception):
    print(f"🚨 Downloader error di [{where}]: {exc}")
    print(traceback.format_exc())


def ensure_download_dir():
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)


def is_valid_http_url(text: str) -> bool:
    try:
        parsed = urlparse((text or "").strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def detect_platform_from_url(url: str) -> str:
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]

        if "youtu.be" in host or "youtube.com" in host:
            return "youtube"
        if "tiktok.com" in host:
            return "tiktok"
        if "instagram.com" in host:
            return "instagram"
        if "facebook.com" in host or "fb.watch" in host or "fb.com" in host:
            return "facebook"
        return "universal"
    except Exception:
        return "universal"


def downloader_platform_label(platform: str) -> str:
    labels = {
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "instagram": "Instagram",
        "facebook": "Facebook",
        "universal": "Universal",
    }
    return labels.get(platform, "Downloader")


def downloader_format_label(format_choice: str) -> str:
    labels = {
        "video": "Video",
        "audio": "Audio MP3",
        "photo": "Foto",
        "auto": "Auto",
    }
    return labels.get(format_choice, "Video")


def default_downloader_format(platform: str) -> str:
    if platform == "universal":
        return "auto"
    if platform in SUPPORTED_PLATFORMS:
        return "video"
    return "video"


def set_downloader_mode(pending_actions: dict, user_id: int, platform: str, format_choice: str = "video"):
    pending_actions[user_id] = {
        "kind": f"downloader_{platform}",
        "platform": platform,
        "format": format_choice,
    }


def clear_downloader_state(pending_actions: dict, user_id: int):
    pending_actions.pop(user_id, None)


def safe_edit_or_send(bot, message, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        try:
            bot.send_message(message.chat.id, text, reply_markup=reply_markup)
        except Exception:
            pass


def downloader_menu_keyboard():
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🎵 TikTok", callback_data="downloader_platform:tiktok"),
        InlineKeyboardButton("📷 Instagram", callback_data="downloader_platform:instagram"),
    )
    kb.row(
        InlineKeyboardButton("▶️ YouTube", callback_data="downloader_platform:youtube"),
        InlineKeyboardButton("🎬 Facebook", callback_data="downloader_platform:facebook"),
    )
    kb.row(
        InlineKeyboardButton("🌐 Universal", callback_data="downloader_platform:universal"),
    )
    kb.row(
        InlineKeyboardButton("🏠 Kembali ke Dashboard", callback_data="back_dashboard"),
    )
    return kb


def downloader_platform_keyboard(platform: str):
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    kb = InlineKeyboardMarkup()

    if platform == "tiktok":
        kb.row(
            InlineKeyboardButton("🎬 Video", callback_data="downloader_format:tiktok:video"),
            InlineKeyboardButton("🎵 Audio", callback_data="downloader_format:tiktok:audio"),
        )
    elif platform == "youtube":
        kb.row(
            InlineKeyboardButton("🎥 Video", callback_data="downloader_format:youtube:video"),
            InlineKeyboardButton("🎵 Audio MP3", callback_data="downloader_format:youtube:audio"),
        )
    elif platform == "instagram":
        kb.row(
            InlineKeyboardButton("🎥 Video", callback_data="downloader_format:instagram:video"),
            InlineKeyboardButton("🖼 Foto", callback_data="downloader_format:instagram:photo"),
        )
    elif platform == "facebook":
        kb.row(
            InlineKeyboardButton("🎥 Video", callback_data="downloader_format:facebook:video"),
        )
    elif platform == "universal":
        pass

    kb.row(
        InlineKeyboardButton("⬅️ Kembali", callback_data="downloader_menu"),
        InlineKeyboardButton("🚪 Keluar Mode", callback_data="exit_mode"),
    )
    return kb

def show_downloader_menu(bot, message, edit=False):
    text = (
        "📥 <b>Downloader</b>\n\n"
        "Silakan pilih platform yang ingin digunakan.\n"
        "Pilih salah satu platform di bawah ini."
    )
    if edit:
        safe_edit_or_send(bot, message, text, downloader_menu_keyboard())
    else:
        bot.send_message(message.chat.id, text, reply_markup=downloader_menu_keyboard())


def show_downloader_mode(bot, message, platform: str, format_choice: str, edit=False):
    if platform == "tiktok":
        text = (
            "🎵 <b>Downloader TikTok Aktif</b>\n\n"
            "Silakan kirim link TikTok.\n"
            "Bot akan mengunduh video secara otomatis.\n\n"
            f"<b>Mode aktif:</b> {escape(downloader_format_label(format_choice))}"
        )
    elif platform == "youtube":
        text = (
            "▶️ <b>Downloader YouTube Aktif</b>\n\n"
            "Silakan kirim URL.\n\n"
            f"<b>Mode aktif:</b> {escape(downloader_format_label(format_choice))}"
        )
    elif platform == "instagram":
        text = (
            "📷 <b>Downloader Instagram Aktif</b>\n\n"
            "Silakan kirim URL Reel atau Post.\n\n"
            f"<b>Mode aktif:</b> {escape(downloader_format_label(format_choice))}"
        )
    elif platform == "facebook":
        text = (
            "🎬 <b>Downloader Facebook Aktif</b>\n\n"
            "Silakan kirim URL Facebook.\n\n"
            f"<b>Mode aktif:</b> {escape(downloader_format_label(format_choice))}"
        )
    elif platform == "universal":
        text = (
            "🌐 <b>Downloader Universal</b>\n\n"
            "Tempel URL.\n"
            "Bot akan mendeteksi platform secara otomatis."
        )
    else:
        text = "Downloader aktif."

    if edit:
        safe_edit_or_send(bot, message, text, downloader_platform_keyboard(platform))
    else:
        bot.send_message(message.chat.id, text, reply_markup=downloader_platform_keyboard(platform))


def process_downloader_callback(bot, call, pending_actions: dict):
    data = call.data or ""
    user_id = call.from_user.id

    if data == "downloader_menu":
        show_downloader_menu(bot, call.message, edit=True)
        return True

    if data.startswith("downloader_platform:"):
        platform = data.split(":", 1)[1].strip()
        if platform not in SUPPORTED_PLATFORMS:
            bot.send_message(call.message.chat.id, "Platform downloader tidak dikenal.")
            return True

        default_format = default_downloader_format(platform)
        set_downloader_mode(pending_actions, user_id, platform, default_format)
        show_downloader_mode(bot, call.message, platform, default_format, edit=True)
        return True

    if data.startswith("downloader_format:"):
        parts = data.split(":")
        if len(parts) < 3:
            bot.send_message(call.message.chat.id, "Format downloader tidak valid.")
            return True

        platform = parts[1].strip()
        format_choice = parts[2].strip()

        if platform not in SUPPORTED_PLATFORMS:
            bot.send_message(call.message.chat.id, "Platform downloader tidak dikenal.")
            return True

        if format_choice not in SUPPORTED_FORMATS:
            bot.send_message(call.message.chat.id, "Format downloader tidak dikenal.")
            return True

        set_downloader_mode(pending_actions, user_id, platform, format_choice)
        show_downloader_mode(bot, call.message, platform, format_choice, edit=True)
        return True

    return False


def build_ytdlp_options(download_tag: str, platform: str, format_choice: str):
    ensure_download_dir()

    outtmpl = os.path.join(DOWNLOADS_DIR, f"{download_tag}_%(id)s.%(ext)s")

    options = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "merge_output_format": "mp4",
    }

    if format_choice == "audio":
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    elif format_choice == "photo":
        options["format"] = "best"
    elif format_choice == "auto":
        options["format"] = "bestvideo+bestaudio/best"
    else:
        options["format"] = "bestvideo+bestaudio/best"

    return options


def find_downloaded_file(download_tag: str, format_choice: str = "video", fallback_path: str = "") -> str:
    video_exts = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".3gp")
    audio_exts = (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac")
    image_exts = (".jpg", ".jpeg", ".png", ".webp")

    candidates = []

    if os.path.isdir(DOWNLOADS_DIR):
        for name in os.listdir(DOWNLOADS_DIR):
            if not name.startswith(download_tag):
                continue
            if name.endswith((".part", ".ytdl", ".tmp")):
                continue

            path = os.path.join(DOWNLOADS_DIR, name)

            if os.path.isfile(path):
                candidates.append(path)

    if candidates:

        def priority(path):
            ext = os.path.splitext(path)[1].lower()
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)

            if format_choice == "audio":
                rank = 0 if ext in audio_exts else 1
            elif format_choice == "photo":
                rank = 0 if ext in image_exts else 1
            else:
                rank = 0 if ext in video_exts else 1

            return (rank, -size, -mtime)

        candidates.sort(key=priority)
        return candidates[0]

    if fallback_path and os.path.exists(fallback_path):
        return fallback_path

    return ""

def cleanup_download_artifacts(download_tag: str):
    try:
        if not os.path.isdir(DOWNLOADS_DIR):
            return
        for name in os.listdir(DOWNLOADS_DIR):
            if not name.startswith(download_tag):
                continue
            path = os.path.join(DOWNLOADS_DIR, name)
            try:
                os.remove(path)
            except Exception:
                pass
    except Exception:
        pass


def download_with_ytdlp(url: str, platform: str, format_choice: str):
    if yt_dlp is None:
        raise RuntimeError("Library yt-dlp belum terpasang.")

    download_tag = f"{platform}_{format_choice}_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    options = build_ytdlp_options(download_tag, platform, format_choice)

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)

        title = info.get("title") or "download"

        try:
            fallback_path = ydl.prepare_filename(info)
        except Exception:
            fallback_path = ""

    file_path = find_downloaded_file(download_tag, format_choice, fallback_path)

    if not file_path:
        raise RuntimeError("File hasil download tidak ditemukan.")

    return title, file_path, download_tag
def send_downloaded_file(bot, chat_id, file_path, title, platform, format_choice):
    ext = os.path.splitext(file_path)[1].lower()

caption = (
    "✅ <b>Unduhan selesai</b>\n\n"
    f"<b>Platform:</b> {escape(platform_label)}\n"
    f"<b>Format:</b> {escape(format_label)}\n"
    f"<b>Judul:</b> {escape(title)}"
)

keyboard = downloader_platform_keyboard(platform)
       
    with open(file_path, "rb") as f:

        try:
            if ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".3gp"):
                bot.send_video(
                    chat_id,
                    f,
                    caption=caption,
                    supports_streaming=True,
                )
                return

            if ext in (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac"):
                bot.send_audio(
                    chat_id,
                    f,
                    caption=caption,
                )
                return

            if ext in (".jpg", ".jpeg", ".png", ".webp"):
                bot.send_photo(
                    chat_id,
                    f,
                    caption=caption,
                )
                return

            bot.send_document(
                chat_id,
                f,
                caption=caption,
            )

        except Exception as e:
            report_local_error("send_downloaded_file", e)

            f.seek(0)

            bot.send_document(
                chat_id,
                f,
                caption=caption,
            )
def process_downloader_message(bot, message, pending_actions: dict):
    try:
        if not message.text:
            return False

        user_id = message.from_user.id
        action = pending_actions.get(user_id, {})
        kind = action.get("kind", "")

        if not kind.startswith("downloader_"):
            return False

        url = message.text.strip()

        if not is_valid_http_url(url):
            bot.send_message(
                message.chat.id,
                "URL tidak valid. Kirim link http/https yang benar sesuai platform yang dipilih.",
            )
            return True

        platform = action.get("platform", "universal")
        format_choice = action.get("format", default_downloader_format(platform))

        detected_platform = detect_platform_from_url(url)

        if platform != "universal" and detected_platform != platform:
            bot.send_message(
                message.chat.id,
                f"URL yang dikirim tidak sesuai mode {downloader_platform_label(platform)}.\n"
                f"Silakan kirim URL {downloader_platform_label(platform)} yang benar.",
            )
            return True

        selected_platform = (
            detected_platform if platform == "universal" else platform
        )

        if selected_platform not in SUPPORTED_PLATFORMS:
            bot.send_message(
                message.chat.id,
                "Platform URL belum didukung oleh mode ini.",
            )
            return True

        if selected_platform == "universal":
            selected_platform = detected_platform

        bot.send_message(
            message.chat.id,
            (
                "⏳ <b>Memproses unduhan...</b>\n\n"
                f"Platform: {escape(downloader_platform_label(selected_platform))}\n"
                f"Mode: {escape(downloader_format_label(format_choice))}"
            ),
            reply_markup=downloader_platform_keyboard(selected_platform),
            parse_mode="HTML",
        )

        title = ""
        file_path = ""
        download_tag = ""

        try:
            title, file_path, download_tag = download_with_ytdlp(
                url,
                selected_platform,
                format_choice,
            )

            send_downloaded_file(
                bot,
                message.chat.id,
                file_path,
                title,
                selected_platform,
                format_choice,
            )

            clear_downloader_state(pending_actions, user_id)
            return True

        except Exception as exc:
            import traceback
            traceback.print_exc()

            report_local_error("process_downloader_message", exc)

            bot.send_message(
                message.chat.id,
                (
                    "❌ <b>Download gagal</b>\n\n"
                    f"<code>{escape(str(exc))}</code>"
                ),
                parse_mode="HTML",
            )
            return True

        finally:
            if download_tag:
                cleanup_download_artifacts(download_tag)

    except Exception as exc:
        report_local_error("process_downloader_message_outer", exc)
        return False
