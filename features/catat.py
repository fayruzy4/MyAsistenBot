import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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


def start_transaction(bot, message, tipe):
    try:
        judul = "Tambah Saldo" if tipe == "income" else "Kurang Saldo"
        teks = (
            f"<b>{judul}</b>\n\n"
            "Ketik dengan format ini:\n"
            "<code>Angka#Keterangan</code>\n\n"
            "Contoh:\n"
            "<code>50000#Beli Kopi</code>"
        )
        bot.send_message(message.chat.id, teks)
    except Exception as exc:
        notify_bug(bot, "start_transaction", exc)


def process_transaction_input(bot, message, supabase_client, action, notify_owner=None):
    try:
        raw = (message.text or "").strip()
        if "#" not in raw:
            bot.send_message(
                message.chat.id,
                "Format salah.\nPakai:\n<code>Angka#Keterangan</code>\nContoh: <code>50000#Beli Kopi</code>",
            )
            return False

        nominal_raw, keterangan = raw.split("#", 1)
        nominal_raw = nominal_raw.strip().replace(".", "").replace(",", "")
        keterangan = keterangan.strip()

        if not nominal_raw.isdigit():
            bot.send_message(message.chat.id, "Nominal harus angka bulat.")
            return False

        nominal = int(nominal_raw)
        if nominal <= 0:
            bot.send_message(message.chat.id, "Nominal harus lebih dari 0.")
            return False

        tipe = action.get("kind")
        if tipe not in ("income", "expense"):
            bot.send_message(message.chat.id, "Jenis transaksi tidak valid.")
            return False

        row = {
            "user_id": str(message.from_user.id),
            "tipe": tipe,
            "nominal": nominal,
            "keterangan": keterangan,
        }

        supabase_client.from_("transactions").insert(row).execute()

        icon = "➕" if tipe == "income" else "➖"
        bot.send_message(
            message.chat.id,
            f"{icon} Tersimpan.\n\nNominal: {rupiah(nominal)}\nKeterangan: {escape(keterangan)}",
        )
        return True

    except Exception as exc:
        notify_bug(bot, "process_transaction_input", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal menyimpan transaksi.")
        return False


def show_last_transactions(bot, message, supabase_client, user_id, notify_owner=None):
    try:
        rows = (
            supabase_client.from_("transactions")
            .select("id, user_id, tipe, nominal, keterangan, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(5)
            .execute()
            .data
        )

        if not rows:
            text = "📋 <b>5 TRANSAKSI TERAKHIR</b>\n\nBelum ada transaksi."
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⬅️ Kembali", callback_data="finance_menu"))
            safe_render(bot, message, text, kb)
            return

        lines = ["📋 <b>5 TRANSAKSI TERAKHIR</b>", ""]
        for item in rows:
            icon = "➕" if item.get("tipe") == "income" else "➖"
            nominal = rupiah(item.get("nominal", 0))
            ket = escape(item.get("keterangan", "-"))
            created = str(item.get("created_at", ""))[:19].replace("T", " ")
            lines.append(f"{icon} {nominal} | {ket}")
            lines.append(f"   {created}")
            lines.append("")

        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("🗑 Hapus Transaksi Terakhir", callback_data="txn_delete_last"),
            InlineKeyboardButton("⬅️ Kembali", callback_data="finance_menu"),
        )

        safe_render(bot, message, "\n".join(lines).strip(), kb)

    except Exception as exc:
        notify_bug(bot, "show_last_transactions", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal mengambil transaksi terakhir.")


def delete_last_transaction(bot, message, supabase_client, user_id, notify_owner=None):
    try:
        rows = (
            supabase_client.from_("transactions")
            .select("id, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )

        if not rows:
            bot.send_message(message.chat.id, "Belum ada transaksi untuk dihapus.")
            return

        tx_id = rows[0]["id"]
        supabase_client.from_("transactions").delete().eq("id", tx_id).execute()
        bot.send_message(message.chat.id, "Transaksi terakhir sudah dihapus.")
        show_last_transactions(bot, message, supabase_client, user_id, notify_owner=notify_owner)

    except Exception as exc:
        notify_bug(bot, "delete_last_transaction", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal menghapus transaksi terakhir.")


def _bar(current, maximum, width=12):
    if maximum <= 0:
        return "░" * width
    filled = int(round((current / maximum) * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def show_graph_report(bot, message, supabase_client, user_id, days, notify_owner=None):
    try:
        start_dt = datetime.now(timezone.utc) - timedelta(days=days)
        start_iso = start_dt.isoformat()

        rows = (
            supabase_client.from_("transactions")
            .select("tipe, nominal, created_at")
            .eq("user_id", str(user_id))
            .gte("created_at", start_iso)
            .order("created_at", desc=False)
            .execute()
            .data
        )

        title = f"📊 <b>GRAFIK {days} HARI</b>\n\n"

        if not rows:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⬅️ Kembali", callback_data="finance_menu"))
            safe_render(bot, message, title + "Belum ada data transaksi.", kb)
            return

        income = 0
        expense = 0
        daily_net = defaultdict(int)

        for item in rows:
            nominal = int(item.get("nominal", 0))
            tipe = item.get("tipe")
            created = str(item.get("created_at", ""))[:10]

            if tipe == "income":
                income += nominal
                daily_net[created] += nominal
            else:
                expense += nominal
                daily_net[created] -= nominal

        net = income - expense
        max_abs = max([abs(v) for v in daily_net.values()] + [1])

        lines = [
            title.rstrip(),
            f"Pemasukan : {rupiah(income)}",
            f"Pengeluaran: {rupiah(expense)}",
            f"Saldo Bersih: {rupiah(net)}",
            "",
            "<b>Ringkasan Harian</b>",
        ]

        for day in sorted(daily_net.keys()):
            val = daily_net[day]
            sign = "+" if val >= 0 else "-"
            lines.append(f"{day} | {_bar(abs(val), max_abs)} {sign}{rupiah(abs(val))}")

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ Kembali", callback_data="finance_menu"))

        safe_render(bot, message, "\n".join(lines), kb)

    except Exception as exc:
        notify_bug(bot, "show_graph_report", exc, notify_owner=notify_owner)
        bot.send_message(message.chat.id, "Gagal membuat grafik.")
