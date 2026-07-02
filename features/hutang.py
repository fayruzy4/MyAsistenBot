from __future__ import annotations

import os
import tempfile
import traceback
from html import escape
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
try:
    import matplotlib.pyplot as plt
except Exception:
    plt = None

from telebot.types import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _shorten_label(value: str, max_len: int = 28) -> str:
    value = _clean_text(value)
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


def _parse_money(value: Any, default: int = 0) -> int:
    try:
        cleaned = str(value).strip().lower().replace("rp", "").replace(".", "").replace(",", "").replace(" ", "")
        if cleaned == "":
            return default
        return int(cleaned)
    except Exception:
        return default


def _parse_percent(value: Any, default: float = 0.0) -> float:
    try:
        cleaned = str(value).strip().replace("%", "").replace(",", ".")
        if cleaned == "":
            return default
        return float(cleaned)
    except Exception:
        return default


def format_rupiah(value: Any) -> str:
    try:
        return f"Rp{int(float(value)):,}".replace(",", ".")
    except Exception:
        return "Rp0"


def _safe_render(bot, message, text: str, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        bot.send_message(message.chat.id, text, reply_markup=reply_markup)


def _safe_send(bot, chat_id: int, text: str, reply_markup=None):
    bot.send_message(chat_id, text, reply_markup=reply_markup)


def _force_reply():
    return ForceReply(selective=True)


def _ascii_bar(value: int, maximum: int, length: int = 20) -> str:
    if maximum <= 0:
        return "░" * length
    ratio = max(0.0, min(1.0, float(value) / float(maximum)))
    filled = int(round(ratio * length))
    return "█" * filled + "░" * (length - filled)


def _get_rows(supabase, user_id: int, tipe_utang: Optional[str] = None, lunas: Optional[bool] = None) -> List[Dict[str, Any]]:
    query = (
        supabase.table("hutang")
        .select("*")
        .eq("user_id", user_id)
    )

    if tipe_utang is not None:
        query = query.eq("tipe_utang", tipe_utang)
    if lunas is not None:
        query = query.eq("lunas", lunas)

    response = query.order("created_at", desc=False).execute()
    return response.data or []


def _row_name(row: Dict[str, Any]) -> str:
    return (
        row.get("nama_pihak")
        or row.get("nama_lembaga")
        or row.get("nama")
        or "Tanpa Nama"
    )


def _row_remaining(row: Dict[str, Any]) -> int:
    for key in ("sisa_nominal", "total_tagihan", "nominal_pokok", "nominal"):
        if row.get(key) not in (None, "", 0, "0"):
            return _parse_money(row.get(key))
    return 0


def _row_total(row: Dict[str, Any]) -> int:
    for key in ("total_tagihan", "nominal_pokok", "sisa_nominal", "nominal"):
        if row.get(key) not in (None, "", 0, "0"):
            return _parse_money(row.get(key))
    return 0


def _row_interest_rate(row: Dict[str, Any]) -> float:
    return _parse_percent(row.get("bunga_persen_per_bulan"), 0.0)


def _row_tenor(row: Dict[str, Any]) -> int:
    try:
        return int(row.get("tenor_bulan") or 0)
    except Exception:
        return 0


def _row_installment(row: Dict[str, Any]) -> int:
    if row.get("cicilan_bulanan") not in (None, "", 0, "0"):
        return _parse_money(row.get("cicilan_bulanan"))

    pokok = _parse_money(row.get("nominal_pokok"))
    tenor = max(_row_tenor(row), 1)
    bunga = pokok * (_row_interest_rate(row) / 100.0)
    return int(round((pokok / tenor) + bunga))


def _insert_hutang(supabase, payload: Dict[str, Any]) -> None:
    supabase.table("hutang").insert(payload).execute()


def _mark_lunas(supabase, debt_id: Any, user_id: int) -> None:
    supabase.table("hutang").update(
        {
            "lunas": True,
            "sisa_nominal": 0,
        }
    ).eq("id", debt_id).eq("user_id", user_id).execute()


def build_hutang_main_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("👤 Hutang Perorangan", callback_data="hutang_perorangan"),
    )
    kb.row(
        InlineKeyboardButton("🏛️ Hutang Lembaga (Cicilan/Pinjol)", callback_data="hutang_lembaga"),
    )
    kb.row(
        InlineKeyboardButton("📈 Grafik & Ringkasan Laporan", callback_data="hutang_grafik"),
    )
    kb.row(
        InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="menu_utama_kembali"),
    )
    return kb


def build_hutang_perorangan_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📝 Tambah Catatan Baru", callback_data="hutang_perorangan_tambah"),
    )
    kb.row(
        InlineKeyboardButton("💸 Lihat Semua Daftar Hutang", callback_data="hutang_perorangan_daftar"),
    )
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="hutang_kembali"),
    )
    return kb


def build_hutang_lembaga_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📝 Input Pinjaman Lembaga Baru", callback_data="hutang_lembaga_tambah"),
    )
    kb.row(
        InlineKeyboardButton("📊 Lihat Jadwal Cicilan Bulanan", callback_data="hutang_lembaga_jadwal"),
    )
    kb.row(
        InlineKeyboardButton("🧾 Perbarui Status Pembayaran", callback_data="hutang_lembaga_lunas"),
    )
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="hutang_kembali"),
    )
    return kb


def build_hutang_grafik_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🔄 Perbarui Data", callback_data="hutang_grafik_refresh"),
        InlineKeyboardButton("🔙 Kembali", callback_data="hutang_kembali"),
    )
    return kb


def _build_hutang_lunas_keyboard(rows: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()

    if rows:
        for row in rows:
            label = f"✅ {_shorten_label(_row_name(row))} | {format_rupiah(_row_remaining(row))}"
            kb.row(InlineKeyboardButton(label, callback_data=f"hutang_lunas_set:{row['id']}"))
    else:
        kb.row(InlineKeyboardButton("Tidak ada data aktif", callback_data="hutang_kembali"))

    kb.row(InlineKeyboardButton("🔙 Kembali", callback_data="hutang_kembali"))
    return kb


def _force_reply_text_bayarke() -> str:
    return (
        "📝 <b>Tambah Catatan Hutang Perorangan</b>\n\n"
        "Gunakan format berikut pada pesan balasan:\n"
        "<code>/bayarke [Nama_Pihak] [Nominal] [Keterangan]</code>\n\n"
        "<b>Contoh:</b>\n"
        "<code>/bayarke Andi 150000 Pinjam makan malam</code>"
    )


def _force_reply_text_pinjol() -> str:
    return (
        "📝 <b>Input Pinjaman Lembaga Baru</b>\n\n"
        "Gunakan format berikut pada pesan balasan:\n"
        "<code>/pinjol [Nama_Lembaga] [Nominal_Pokok] [Bunga_%_Per_Bulan] [Tenor_Bulan] [Keterangan]</code>\n\n"
        "<b>Contoh:</b>\n"
        "<code>/pinjol BankX 5000000 2.5 12 Cicilan motor</code>\n\n"
        "Sistem akan menghitung simulasi cicilan bulanan secara otomatis."
    )


def show_hutang_menu(bot, message) -> None:
    text = (
        "📊 <b>Sistem Manajemen Hutang</b>\n"
        "Di sini kamu bisa mencatat dan memantau seluruh kewajiban finansial yang harus diselesaikan, baik kepada perorangan maupun lembaga formal. Silakan pilih menu manajemen di bawah ini:"
    )
    _safe_render(bot, message, text, build_hutang_main_keyboard())


def show_hutang_perorangan_menu(bot, message) -> None:
    text = (
        "👤 <b>Hutang Perorangan</b>\n\n"
        "Kelola kewajiban finansial kepada perorangan secara tertib. "
        "Gunakan menu di bawah untuk menambah catatan atau melihat daftar aktif."
    )
    _safe_render(bot, message, text, build_hutang_perorangan_keyboard())


def show_hutang_lembaga_menu(bot, message) -> None:
    text = (
        "🏛️ <b>Hutang Lembaga</b>\n\n"
        "Kelola kewajiban finansial kepada lembaga formal, cicilan, atau pinjaman daring. "
        "Gunakan menu di bawah untuk menambah pinjaman, melihat jadwal cicilan, atau memperbarui status pembayaran."
    )
    _safe_render(bot, message, text, build_hutang_lembaga_keyboard())


def show_hutang_perorangan_daftar(bot, message, supabase, user_id: int) -> None:
    rows = _get_rows(supabase, user_id, tipe_utang="perorangan", lunas=False)

    lines = ["👤 <b>Daftar Hutang Perorangan Aktif</b>", ""]
    if not rows:
        lines.append("Tidak ada hutang perorangan aktif saat ini.")
    else:
        total = 0
        for idx, row in enumerate(rows, start=1):
            nama = _row_name(row)
            sisa = _row_remaining(row)
            total += sisa
            keterangan = _clean_text(row.get("keterangan")) or "-"
            lines.extend([
                f"{idx}. <b>{escape(nama)}</b>",
                f"   Sisa : {format_rupiah(sisa)}",
                f"   Ket. : {escape(keterangan)}",
            ])
        lines.extend(["", f"<b>Total aktif:</b> {format_rupiah(total)}"])

    text = "\n".join(lines)
    _safe_render(bot, message, text, build_hutang_perorangan_keyboard())


def show_hutang_lembaga_jadwal(bot, message, supabase, user_id: int) -> None:
    rows = _get_rows(supabase, user_id, tipe_utang="lembaga", lunas=False)

    lines = ["🏛️ <b>Jadwal Cicilan Bulanan</b>", ""]
    if not rows:
        lines.append("Tidak ada pinjaman lembaga aktif saat ini.")
    else:
        total = 0
        for idx, row in enumerate(rows, start=1):
            nama = _row_name(row)
            pokok = _parse_money(row.get("nominal_pokok"))
            tenor = max(_row_tenor(row), 1)
            bunga = _row_interest_rate(row)
            cicilan = _row_installment(row)
            sisa = _row_remaining(row)
            total += sisa

            pokok_bulanan = int(round(pokok / tenor))
            bunga_bulanan = int(round(pokok * (bunga / 100.0)))

            lines.extend([
                f"{idx}. <b>{escape(nama)}</b>",
                f"   Pokok        : {format_rupiah(pokok)}",
                f"   Bunga / bln  : {bunga:.2f}%",
                f"   Tenor        : {tenor} bulan",
                f"   Cicilan / bln: {format_rupiah(cicilan)}",
                f"   Sisa         : {format_rupiah(sisa)}",
                f"   Breakdown    : {format_rupiah(pokok_bulanan)} + {format_rupiah(bunga_bulanan)}",
            ])
        lines.extend(["", f"<b>Total aktif:</b> {format_rupiah(total)}"])

    text = "\n".join(lines)
    _safe_render(bot, message, text, build_hutang_lembaga_keyboard())


def show_hutang_lembaga_lunas_options(bot, message, supabase, user_id: int) -> None:
    rows = _get_rows(supabase, user_id, tipe_utang="lembaga", lunas=False)

    if not rows:
        text = (
            "🧾 <b>Perbarui Status Pembayaran</b>\n\n"
            "Tidak ada hutang lembaga aktif yang perlu diperbarui."
        )
        _safe_render(bot, message, text, build_hutang_lembaga_keyboard())
        return

    text = (
        "🧾 <b>Perbarui Status Pembayaran</b>\n\n"
        "Pilih pinjaman yang sudah lunas agar statusnya diperbarui."
    )
    _safe_render(bot, message, text, _build_hutang_lunas_keyboard(rows))


def _build_hutang_grafik_file(supabase, user_id: int) -> Tuple[Optional[str], Dict[str, Any]]:
    perorangan_rows = _get_rows(supabase, user_id, tipe_utang="perorangan", lunas=False)
    lembaga_rows = _get_rows(supabase, user_id, tipe_utang="lembaga", lunas=False)

    perorangan_total = sum(_row_remaining(row) for row in perorangan_rows)
    lembaga_total = sum(_row_remaining(row) for row in lembaga_rows)
    total = perorangan_total + lembaga_total

    payload = {
        "perorangan_total": perorangan_total,
        "lembaga_total": lembaga_total,
        "total": total,
        "perorangan_count": len(perorangan_rows),
        "lembaga_count": len(lembaga_rows),
    }

    if plt is None:
        return None, payload

    fd, image_path = tempfile.mkstemp(prefix="hutang_grafik_", suffix=".png")
    os.close(fd)

    try:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))

        labels = ["Perorangan", "Lembaga"]
        values = [perorangan_total, lembaga_total]

        axes[0].bar(labels, values)
        axes[0].set_title("Total Hutang Aktif")
        axes[0].set_ylabel("Rupiah")

        if total > 0:
            axes[1].pie(values, labels=labels, autopct="%1.1f%%")
        else:
            axes[1].pie([1, 1], labels=labels, autopct="%1.1f%%")

        axes[1].set_title("Komposisi Beban Hutang")
        plt.tight_layout()
        plt.savefig(image_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return image_path, payload
    except Exception:
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
        except Exception:
            pass
        raise


def show_hutang_grafik(bot, message, supabase, user_id: int, refresh: bool = False) -> None:
    image_path = None
    try:
        image_path, payload = _build_hutang_grafik_file(supabase, user_id)

        perorangan_total = payload["perorangan_total"]
        lembaga_total = payload["lembaga_total"]
        total = payload["total"]
        perorangan_count = payload["perorangan_count"]
        lembaga_count = payload["lembaga_count"]

        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as photo_file:
                bot.send_photo(
                    message.chat.id,
                    photo_file,
                    caption="📈 Grafik Hutang Aktif",
                )
        else:
            _safe_send(
                bot,
                message.chat.id,
                "📈 <b>Grafik Hutang Aktif</b>\n\nMatplotlib belum tersedia di server, sehingga grafik tidak dapat ditampilkan.",
            )

        max_total = max(perorangan_total, lembaga_total, 1)
        summary_lines = [
            "📈 <b>Ringkasan Laporan Hutang Aktif</b>",
            "",
            f"👤 Hutang Perorangan : {format_rupiah(perorangan_total)}",
            f"🏛️ Hutang Lembaga    : {format_rupiah(lembaga_total)}",
            f"🧮 Total Akumulasi    : {format_rupiah(total)}",
            "",
            f"Perorangan : {_ascii_bar(perorangan_total, max_total, 20)}",
            f"Lembaga    : {_ascii_bar(lembaga_total, max_total, 20)}",
            "",
            f"<b>Jumlah catatan aktif:</b>",
            f"• Perorangan = {perorangan_count}",
            f"• Lembaga    = {lembaga_count}",
        ]
        summary_text = "\n".join(summary_lines)

        _safe_send(bot, message.chat.id, summary_text, build_hutang_grafik_keyboard())

    except Exception as exc:
        print(f"[hutang.show_hutang_grafik] {exc}")
        print(traceback.format_exc())
        _safe_send(
            bot,
            message.chat.id,
            "Gagal memuat grafik hutang.",
            build_hutang_grafik_keyboard(),
        )
    finally:
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
            except Exception:
                pass


def process_hutang_bayarke(bot, message, supabase, pending_actions: Dict[str, Any]) -> bool:
    raw = _clean_text(getattr(message, "text", ""))
    if not raw.startswith("/bayarke"):
        return False

    parts = raw.split(maxsplit=3)
    if len(parts) < 4:
        bot.send_message(
            message.chat.id,
            (
                "Format belum lengkap.\n\n"
                "Gunakan:\n"
                "<code>/bayarke [Nama_Pihak] [Nominal] [Keterangan]</code>\n\n"
                "Contoh:\n"
                "<code>/bayarke Andi 150000 Pinjam makan malam</code>"
            ),
            reply_markup=_force_reply(),
        )
        return False

    _, nama_pihak, nominal_raw, keterangan = parts
    nominal = _parse_money(nominal_raw, 0)
    if nominal <= 0:
        bot.send_message(
            message.chat.id,
            "Nominal harus berupa angka bulat lebih dari 0.",
            reply_markup=_force_reply(),
        )
        return False

    user_id = message.from_user.id
    payload = {
        "user_id": user_id,
        "tipe_utang": "perorangan",
        "nama_pihak": nama_pihak,
        "nama_lembaga": None,
        "nominal_pokok": nominal,
        "sisa_nominal": nominal,
        "bunga_persen_per_bulan": 0,
        "tenor_bulan": 0,
        "cicilan_bulanan": 0,
        "total_tagihan": nominal,
        "keterangan": keterangan,
        "lunas": False,
    }

    _insert_hutang(supabase, payload)
    pending_actions.pop(user_id, None)

    bot.send_message(
        message.chat.id,
        (
            f"✅ Catatan hutang perorangan tersimpan.\n\n"
            f"<b>Nama Pihak:</b> {escape(nama_pihak)}\n"
            f"<b>Nominal:</b> {format_rupiah(nominal)}\n"
            f"<b>Keterangan:</b> {escape(keterangan)}"
        ),
        reply_markup=build_hutang_perorangan_keyboard(),
    )
    return True


def process_hutang_pinjol(bot, message, supabase, pending_actions: Dict[str, Any]) -> bool:
    raw = _clean_text(getattr(message, "text", ""))
    if not raw.startswith("/pinjol"):
        return False

    parts = raw.split(maxsplit=5)
    if len(parts) < 6:
        bot.send_message(
            message.chat.id,
            (
                "Format belum lengkap.\n\n"
                "Gunakan:\n"
                "<code>/pinjol [Nama_Lembaga] [Nominal_Pokok] [Bunga_%_Per_Bulan] [Tenor_Bulan] [Keterangan]</code>\n\n"
                "Contoh:\n"
                "<code>/pinjol BankX 5000000 2.5 12 Cicilan motor</code>"
            ),
            reply_markup=_force_reply(),
        )
        return False

    _, nama_lembaga, nominal_raw, bunga_raw, tenor_raw, keterangan = parts

    nominal_pokok = _parse_money(nominal_raw, 0)
    bunga_persen = _parse_percent(bunga_raw, 0.0)
    tenor_bulan = _parse_money(tenor_raw, 0)

    if nominal_pokok <= 0 or tenor_bulan <= 0 or bunga_persen < 0:
        bot.send_message(
            message.chat.id,
            "Pastikan nominal, bunga, dan tenor valid.",
            reply_markup=_force_reply(),
        )
        return False

    pokok_per_bulan = nominal_pokok / tenor_bulan
    bunga_bulanan = nominal_pokok * (bunga_persen / 100.0)
    cicilan_bulanan = int(round(pokok_per_bulan + bunga_bulanan))
    total_tagihan = int(round(cicilan_bulanan * tenor_bulan))

    user_id = message.from_user.id
    payload = {
        "user_id": user_id,
        "tipe_utang": "lembaga",
        "nama_pihak": None,
        "nama_lembaga": nama_lembaga,
        "nominal_pokok": nominal_pokok,
        "sisa_nominal": total_tagihan,
        "bunga_persen_per_bulan": bunga_persen,
        "tenor_bulan": tenor_bulan,
        "cicilan_bulanan": cicilan_bulanan,
        "total_tagihan": total_tagihan,
        "keterangan": keterangan,
        "lunas": False,
    }

    _insert_hutang(supabase, payload)
    pending_actions.pop(user_id, None)

    bot.send_message(
        message.chat.id,
        (
            f"✅ Pinjaman lembaga tersimpan.\n\n"
            f"<b>Nama Lembaga:</b> {escape(nama_lembaga)}\n"
            f"<b>Pokok:</b> {format_rupiah(nominal_pokok)}\n"
            f"<b>Bunga / Bulan:</b> {bunga_persen:.2f}%\n"
            f"<b>Tenor:</b> {tenor_bulan} bulan\n"
            f"<b>Cicilan / Bulan:</b> {format_rupiah(cicilan_bulanan)}\n"
            f"<b>Total Tagihan:</b> {format_rupiah(total_tagihan)}\n"
            f"<b>Keterangan:</b> {escape(keterangan)}"
        ),
        reply_markup=build_hutang_lembaga_keyboard(),
    )
    return True


def process_hutang_callback(bot, call, supabase, pending_actions: Dict[str, Any], show_dashboard_callback) -> bool:
    data = call.data or ""
    user_id = call.from_user.id

    try:
        if data == "hutang_menu":
            pending_actions.pop(user_id, None)
            show_hutang_menu(bot, call.message)
            return True

        if data == "hutang_perorangan":
            pending_actions.pop(user_id, None)
            show_hutang_perorangan_menu(bot, call.message)
            return True

        if data == "hutang_lembaga":
            pending_actions.pop(user_id, None)
            show_hutang_lembaga_menu(bot, call.message)
            return True

        if data == "hutang_grafik":
            pending_actions.pop(user_id, None)
            show_hutang_grafik(bot, call.message, supabase, user_id)
            return True

        if data == "hutang_grafik_refresh":
            show_hutang_grafik(bot, call.message, supabase, user_id, refresh=True)
            return True

        if data == "menu_utama_kembali":
            pending_actions.pop(user_id, None)
            show_dashboard_callback(call.message, edit=True)
            return True

        if data == "hutang_kembali":
            pending_actions.pop(user_id, None)
            show_hutang_menu(bot, call.message)
            return True

        if data == "hutang_perorangan_tambah":
            pending_actions[user_id] = {"kind": "hutang_perorangan_input"}
            bot.send_message(
                call.message.chat.id,
                _force_reply_text_bayarke(),
                reply_markup=_force_reply(),
            )
            return True

        if data == "hutang_perorangan_daftar":
            show_hutang_perorangan_daftar(bot, call.message, supabase, user_id)
            return True

        if data == "hutang_lembaga_tambah":
            pending_actions[user_id] = {"kind": "hutang_lembaga_input"}
            bot.send_message(
                call.message.chat.id,
                _force_reply_text_pinjol(),
                reply_markup=_force_reply(),
            )
            return True

        if data == "hutang_lembaga_jadwal":
            show_hutang_lembaga_jadwal(bot, call.message, supabase, user_id)
            return True

        if data == "hutang_lembaga_lunas":
            show_hutang_lembaga_lunas_options(bot, call.message, supabase, user_id)
            return True

        if data.startswith("hutang_lunas_set:"):
            debt_id = data.split(":", 1)[1]
            _mark_lunas(supabase, debt_id, user_id)
            pending_actions.pop(user_id, None)
            bot.send_message(
                call.message.chat.id,
                "✅ Status pembayaran telah diperbarui menjadi lunas.",
            )
            show_hutang_lembaga_menu(bot, call.message)
            return True

        if data.startswith("hutang_"):
            bot.send_message(
                call.message.chat.id,
                "Menu hutang sedang diproses.",
            )
            return True

        return False

    except Exception as exc:
        print(f"[hutang.process_hutang_callback] {exc}")
        print(traceback.format_exc())
        bot.send_message(call.message.chat.id, "Gagal memproses menu hutang.")
        return True
