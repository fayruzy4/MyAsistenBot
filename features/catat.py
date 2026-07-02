import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


# ================= UTILITIES & HELPERS =================

def format_rupiah(value):
    """Format angka menjadi string Rupiah."""
    try:
        return f"Rp{int(value):,}".replace(",", ".")
    except Exception:
        return "Rp0"


def generate_ascii_bar(current, maximum, width=10):
    """Membuat progress bar ASCII yang rapi untuk grafik."""
    if maximum <= 0:
        return "░" * width
    percent = max(0.0, min(1.0, current / maximum))
    filled = int(round(percent * width))
    return "█" * filled + "░" * (width - filled)


def safe_render(bot, message, text, reply_markup=None):
    """Merender ulang message, fall back ke kirim pesan baru jika edit gagal."""
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)


def get_navigation_keyboard(tipe_transaksi=None):
    """Membuat tombol navigasi standar (Batal, Menu Keuangan, Dashboard)."""
    kb = InlineKeyboardMarkup()
    if tipe_transaksi:
        kb.row(
            InlineKeyboardButton("➕ Tambah Lagi", callback_data=f"txn_add_{tipe_transaksi}"),
            InlineKeyboardButton("📋 Riwayat", callback_data="txn_recent")
        )
    else:
        kb.row(InlineKeyboardButton("❌ Batal", callback_data="cancel_input"))
        
    kb.row(
        InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard")
    )
    return kb


# ================= FITUR TRANSAKSI =================

def start_transaction(bot, message, tipe):
    try:
        if tipe == "income":
            judul = "💰 Tambah Saldo (Pemasukan)"
            contoh = "50000#Gaji Bulanan"
        else:
            judul = "💸 Kurang Saldo (Pengeluaran)"
            contoh = "25000#Beli Makan Siang"

        teks = (
            f"<b>{judul}</b>\n\n"
            f"Silakan ketik nominal dan keterangan transaksimu.\n\n"
            f"<b>Format:</b>\n"
            f"<code>Nominal#Keterangan</code>\n\n"
            f"<b>Contoh:</b>\n"
            f"<code>{contoh}</code>\n\n"
            f"<i>💡 Kalau kamu berubah pikiran, tinggal tekan tombol <b>Batal</b> di bawah ya.</i> 👇"
        )
        # Mengirim sebagai pesan baru karena user akan mengetik balasan
        bot.send_message(message.chat.id, teks, reply_markup=get_navigation_keyboard())
    except Exception as exc:
        print(f"Error di start_transaction: {exc}")


def process_transaction_input(bot, message, supabase_client, action):
    try:
        raw_text = (message.text or "").strip()
        
        # Validasi format dasar
        if "#" not in raw_text:
            bot.send_message(
                message.chat.id,
                "🤔 Ups! Formatnya kurang pas nih.\n\n"
                "Coba gunakan format ini ya:\n"
                "<code>Nominal#Keterangan</code>\n\n"
                "Contoh: <code>50000#Uang Makan</code>",
                reply_markup=get_navigation_keyboard()
            )
            return False

        nominal_raw, keterangan = raw_text.split("#", 1)
        nominal_clean = nominal_raw.strip().replace(".", "").replace(",", "")
        keterangan = keterangan.strip()

        # Validasi angka
        if not nominal_clean.isdigit():
            bot.send_message(
                message.chat.id, 
                "⚠️ Nominalnya harus berupa angka bulat ya. Yuk, coba perbaiki!",
                reply_markup=get_navigation_keyboard()
            )
            return False

        nominal = int(nominal_clean)
        if nominal <= 0:
            bot.send_message(
                message.chat.id, 
                "⚠️ Nominal tidak boleh nol atau minus ya.",
                reply_markup=get_navigation_keyboard()
            )
            return False

        tipe = action.get("kind")
        if tipe not in ("income", "expense"):
            return False

        # Simpan ke Supabase (menggunakan syntax versi terbaru supabase-py)
        row_data = {
            "user_id": str(message.from_user.id),
            "tipe": tipe,
            "nominal": nominal,
            "keterangan": keterangan,
        }
        supabase_client.table("transactions").insert(row_data).execute()

        # Feedback Sukses
        header = "Pemasukan Berhasil Dicatat!" if tipe == "income" else "Pengeluaran Berhasil Dicatat!"
        icon = "✅ 💰" if tipe == "income" else "✅ 💸"
        
        teks_sukses = (
            f"{icon} <b>{header}</b>\n\n"
            f"<b>Nominal:</b> {format_rupiah(nominal)}\n"
            f"<b>Keterangan:</b> 📝 {escape(keterangan)}\n\n"
            f"Pilih langkah berikutnya di bawah ini! 👇"
        )

        bot.send_message(
            message.chat.id,
            teks_sukses,
            reply_markup=get_navigation_keyboard(tipe_transaksi=tipe)
        )
        return True

    except Exception as exc:
        print(f"Error di process_transaction_input: {exc}")
        bot.send_message(
            message.chat.id, 
            "😔 Maaf, gagal menyimpan datamu. Coba beberapa saat lagi ya.",
            reply_markup=get_navigation_keyboard()
        )
        return False


def show_last_transactions(bot, message, supabase_client, user_id):
    try:
        response = (
            supabase_client.table("transactions")
            .select("id, user_id, tipe, nominal, keterangan, created_at")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        rows = response.data

        kb = InlineKeyboardMarkup()
        
        if not rows:
            text = "📋 <b>Riwayat Transaksi</b>\n\nBelum ada transaksi yang dicatat nih. Yuk, mulai mencatat! ✨"
            kb.row(
                InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
                InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard")
            )
            safe_render(bot, message, text, kb)
            return

        lines = ["📋 <b>5 Transaksi Terakhir Kamu</b>\n"]
        for item in rows:
            icon = "💰" if item.get("tipe") == "income" else "💸"
            nominal = format_rupiah(item.get("nominal", 0))
            ket = escape(item.get("keterangan", "-"))
            waktu = str(item.get("created_at", ""))[:16].replace("T", " ")
            
            lines.append(f"{icon} <b>{nominal}</b>")
            lines.append(f"📝 {ket}")
            lines.append(f"🕒 <i>{waktu}</i>\n")

        kb.row(InlineKeyboardButton("❌ Hapus Transaksi Terakhir", callback_data="txn_delete_last"))
        kb.row(
            InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
            InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard")
        )

        safe_render(bot, message, "\n".join(lines).strip(), kb)

    except Exception as exc:
        print(f"Error di show_last_transactions: {exc}")
        bot.send_message(message.chat.id, "😔 Gagal mengambil riwayat transaksimu.")


def delete_last_transaction(bot, message, supabase_client, user_id):
    try:
        response = (
            supabase_client.table("transactions")
            .select("id")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data

        if not rows:
            bot.send_message(message.chat.id, "Tidak ada transaksi yang bisa dihapus. 🤷‍♂️")
            return

        tx_id = rows[0]["id"]
        supabase_client.table("transactions").delete().eq("id", tx_id).execute()
        
        # Panggil kembali fungsi list untuk merender ulang
        show_last_transactions(bot, message, supabase_client, user_id)

    except Exception as exc:
        print(f"Error di delete_last_transaction: {exc}")
        bot.send_message(message.chat.id, "😔 Gagal menghapus transaksi terakhir.")


def show_graph_report(bot, message, supabase_client, user_id, days):
    try:
        start_dt = datetime.now(timezone.utc) - timedelta(days=days)
        start_iso = start_dt.isoformat()

        response = (
            supabase_client.table("transactions")
            .select("tipe, nominal, created_at")
            .eq("user_id", str(user_id))
            .gte("created_at", start_iso)
            .order("created_at", desc=False)
            .execute()
        )
        rows = response.data

        title = f"📊 <b>Laporan Keuangan ({days} Hari Terakhir)</b>\n\n"
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("⬅️ Menu Keuangan", callback_data="finance_menu"),
            InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard")
        )

        if not rows:
            safe_render(bot, message, title + "Belum ada aktivitas di periode ini. 📉", kb)
            return

        income = 0
        expense = 0
        daily_net = defaultdict(int)

        for item in rows:
            nominal = int(item.get("nominal", 0))
            tipe = item.get("tipe")
            created_date = str(item.get("created_at", ""))[:10]

            if tipe == "income":
                income += nominal
                daily_net[created_date] += nominal
            else:
                expense += nominal
                daily_net[created_date] -= nominal

        net = income - expense
        max_daily_value = max([abs(v) for v in daily_net.values()] + [1])

        # Status kesehatan
        if net > 0:
            status = "✅ Sehat (Surplus)"
        elif net < 0:
            status = "⚠️ Boncos (Defisit)"
        else:
            status = "⚖️ Impas"

        lines = [
            title.rstrip(),
            f"💰 <b>Total Pemasukan:</b> {format_rupiah(income)}",
            f"💸 <b>Total Pengeluaran:</b> {format_rupiah(expense)}",
            f"✨ <b>Saldo Bersih:</b> {format_rupiah(net)}",
            f"📈 <b>Status Keuangan:</b> {status}",
            "",
            "📅 <b>Grafik Harian:</b>",
        ]

        # Buat grafik ASCII harian
        for day in sorted(daily_net.keys()):
            val = daily_net[day]
            sign = "+" if val >= 0 else "-"
            emoji = "🟢" if val >= 0 else "🔴"
            bar = generate_ascii_bar(abs(val), max_daily_value, width=10)
            lines.append(f"{emoji} <code>{day}</code>")
            lines.append(f"<code>{bar}</code> {sign}{format_rupiah(abs(val))}\n")

        safe_render(bot, message, "\n".join(lines).strip(), kb)

    except Exception as exc:
        print(f"Error di show_graph_report: {exc}")
        bot.send_message(message.chat.id, "😔 Gagal memproses laporan grafik keuangan.")
