from __future__ import annotations

import csv
import io
import math
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape

try:
    import yfinance as yf
except Exception:
    yf = None

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

PREFIX = "inv"
LOT_SIZE = 100
DEFAULT_CURRENCY = "IDR"
DEFAULT_REFRESH_INTERVAL = 300
ALERT_COOLDOWN_SECONDS = 600

SYMBOL_ALIAS = {"IHSG": "^JKSE"}

DEFAULT_UNIVERSE = [
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BRIS.JK",
    "TLKM.JK", "ASII.JK", "UNTR.JK", "ADRO.JK", "PTBA.JK",
    "ANTM.JK", "SMGR.JK", "UNVR.JK", "ICBP.JK", "INDF.JK",
    "MYOR.JK", "KLBF.JK", "MIKA.JK", "HEAL.JK", "GOTO.JK",
    "TPIA.JK", "AMRT.JK", "ACES.JK", "SIDO.JK",
]

SECTOR_BASKETS = {
    "Perbankan": ["BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BRIS.JK"],
    "Energi": ["ADRO.JK", "PTBA.JK", "MEDC.JK", "PGAS.JK", "AKRA.JK"],
    "Teknologi": ["GOTO.JK", "DCII.JK", "TLKM.JK", "MLPT.JK", "MTDL.JK"],
    "Consumer": ["UNVR.JK", "ICBP.JK", "INDF.JK", "MYOR.JK", "SIDO.JK"],
    "Healthcare": ["KLBF.JK", "MIKA.JK", "HEAL.JK", "SILO.JK", "PRDA.JK"],
}


def _safe_decimal(v, default=Decimal("0")):
    try:
        if isinstance(v, Decimal):
            return v
        if v is None:
            return default
        s = str(v).replace(",", "").strip()
        return Decimal(s) if s else default
    except (InvalidOperation, ValueError, TypeError):
        return default


def _money(v, currency=DEFAULT_CURRENCY, places=0):
    d = _safe_decimal(v)
    if places == 0:
        s = f"{int(d):,}"
    else:
        s = f"{d:,.{places}f}"
    return f"{currency} {s}".replace(",", ".")


def _percent(v, places=2):
    d = _safe_decimal(v)
    return f"{d:.{places}f}%".replace(".", ",", 1)


def _parse_date(text: str) -> str:
    t = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except Exception:
            pass
    raise ValueError("Format tanggal tidak valid. Gunakan YYYY-MM-DD atau DD/MM/YYYY.")


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().replace(" ", "")
    if not s:
        raise ValueError("Kode saham kosong.")
    if s in SYMBOL_ALIAS:
        return SYMBOL_ALIAS[s]
    if s.startswith("^") or "." in s:
        return s
    if s.isalnum() and len(s) <= 6:
        return f"{s}.JK"
    return s


def _pending(pending_actions, user_id):
    return pending_actions.setdefault(user_id, {})


def _clear_pending(pending_actions, user_id):
    pending_actions.pop(user_id, None)


def _send_or_edit(bot, message, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def _fmt_dt_now():
    return datetime.utcnow().isoformat()


def _nav_kb(back_cb="inv_home", show_dashboard=True):
    kb = InlineKeyboardMarkup()
    row = [InlineKeyboardButton("⬅️ Kembali", callback_data=back_cb)]
    if show_dashboard:
        row.append(InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"))
    kb.row(*row)
    return kb


def _main_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("💼 Portofolio", callback_data=f"{PREFIX}_portfolio"),
        InlineKeyboardButton("📊 Pasar", callback_data=f"{PREFIX}_market"),
    )
    kb.row(
        InlineKeyboardButton("⭐ Watchlist", callback_data=f"{PREFIX}_watchlist"),
        InlineKeyboardButton("💸 Transaksi", callback_data=f"{PREFIX}_transactions"),
    )
    kb.row(
        InlineKeyboardButton("🔔 Alert Harga", callback_data=f"{PREFIX}_alerts"),
        InlineKeyboardButton("📰 Berita", callback_data=f"{PREFIX}_news"),
    )
    kb.row(
        InlineKeyboardButton("⚙ Pengaturan", callback_data=f"{PREFIX}_settings"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _portfolio_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🔄 Refresh", callback_data=f"{PREFIX}_portfolio"),
        InlineKeyboardButton("➕ Beli", callback_data=f"{PREFIX}_buy"),
    )
    kb.row(
        InlineKeyboardButton("➖ Jual", callback_data=f"{PREFIX}_sell"),
        InlineKeyboardButton("🧾 Riwayat", callback_data=f"{PREFIX}_tx_history"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Menu", callback_data=f"{PREFIX}_home"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _market_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("IHSG", callback_data=f"{PREFIX}_market:IHSG"),
        InlineKeyboardButton("Top Gainers", callback_data=f"{PREFIX}_market:gainers"),
    )
    kb.row(
        InlineKeyboardButton("Top Losers", callback_data=f"{PREFIX}_market:losers"),
        InlineKeyboardButton("Top Volume", callback_data=f"{PREFIX}_market:volume"),
    )
    kb.row(
        InlineKeyboardButton("Perbankan", callback_data=f"{PREFIX}_sector:Perbankan"),
        InlineKeyboardButton("Energi", callback_data=f"{PREFIX}_sector:Energi"),
    )
    kb.row(
        InlineKeyboardButton("Teknologi", callback_data=f"{PREFIX}_sector:Teknologi"),
        InlineKeyboardButton("Consumer", callback_data=f"{PREFIX}_sector:Consumer"),
    )
    kb.row(
        InlineKeyboardButton("Healthcare", callback_data=f"{PREFIX}_sector:Healthcare"),
        InlineKeyboardButton("Cari Saham", callback_data=f"{PREFIX}_search"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Menu", callback_data=f"{PREFIX}_home"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _watchlist_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Tambah saham", callback_data=f"{PREFIX}_watch_add"),
        InlineKeyboardButton("➖ Hapus saham", callback_data=f"{PREFIX}_watch_remove"),
    )
    kb.row(
        InlineKeyboardButton("↕️ Urutkan", callback_data=f"{PREFIX}_watch_sort"),
        InlineKeyboardButton("💹 Realtime", callback_data=f"{PREFIX}_watch_realtime"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Menu", callback_data=f"{PREFIX}_home"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _txn_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Beli", callback_data=f"{PREFIX}_buy"),
        InlineKeyboardButton("➖ Jual", callback_data=f"{PREFIX}_sell"),
    )
    kb.row(
        InlineKeyboardButton("🧾 Riwayat", callback_data=f"{PREFIX}_tx_history"),
        InlineKeyboardButton("✏️ Edit transaksi", callback_data=f"{PREFIX}_tx_edit"),
    )
    kb.row(
        InlineKeyboardButton("🗑 Hapus transaksi", callback_data=f"{PREFIX}_tx_delete"),
        InlineKeyboardButton("📥 Import CSV", callback_data=f"{PREFIX}_tx_import"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Menu", callback_data=f"{PREFIX}_home"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _alert_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Tambah alert", callback_data=f"{PREFIX}_alert_add"),
        InlineKeyboardButton("📃 Daftar alert", callback_data=f"{PREFIX}_alert_list"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Menu", callback_data=f"{PREFIX}_home"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _news_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("IHSG", callback_data=f"{PREFIX}_news:IHSG"),
        InlineKeyboardButton("Cari saham", callback_data=f"{PREFIX}_news_search"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Menu", callback_data=f"{PREFIX}_home"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _settings_kb(settings):
    kb = InlineKeyboardMarkup()
    notif = "ON" if settings.get("notifications_enabled", True) else "OFF"
    kb.row(
        InlineKeyboardButton(
            f"⏱ Refresh: {settings.get('refresh_interval_sec', DEFAULT_REFRESH_INTERVAL)}s",
            callback_data=f"{PREFIX}_set_refresh",
        ),
        InlineKeyboardButton(
            f"💱 Mata uang: {settings.get('currency', DEFAULT_CURRENCY)}",
            callback_data=f"{PREFIX}_set_currency",
        ),
    )
    kb.row(
        InlineKeyboardButton(f"🔔 Notifikasi: {notif}", callback_data=f"{PREFIX}_set_notif"),
        InlineKeyboardButton("♻️ Reset portofolio", callback_data=f"{PREFIX}_set_reset"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Menu", callback_data=f"{PREFIX}_home"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _detail_kb(symbol):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📈 Grafik", callback_data=f"{PREFIX}_chart:{symbol}"),
        InlineKeyboardButton("📰 Berita", callback_data=f"{PREFIX}_news:{symbol}"),
    )
    kb.row(
        InlineKeyboardButton("ℹ Fundamental", callback_data=f"{PREFIX}_fund:{symbol}"),
        InlineKeyboardButton("🧾 Riwayat", callback_data=f"{PREFIX}_tx_symbol:{symbol}"),
    )
    kb.row(
        InlineKeyboardButton("⬅️ Portofolio", callback_data=f"{PREFIX}_portfolio"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _ticker(symbol):
    if yf is None:
        raise RuntimeError("yfinance belum diinstal.")
    return yf.Ticker(symbol)


def _quote(symbol):
    symbol = _normalize_symbol(symbol)
    tk = _ticker(symbol)
    fast = {}
    info = {}
    try:
        fast = dict(getattr(tk, "fast_info", {}) or {})
    except Exception:
        pass
    try:
        info = tk.info or {}
    except Exception:
        pass

    current = fast.get("lastPrice") or info.get("currentPrice")
    prev_close = fast.get("previousClose") or info.get("previousClose")
    volume = fast.get("lastVolume") or info.get("volume")
    currency = info.get("currency") or DEFAULT_CURRENCY
    name = info.get("shortName") or info.get("longName") or symbol

    if current is None:
        hist = tk.history(period="5d", interval="1d", auto_adjust=False)
        if hist is not None and not hist.empty:
            current = float(hist["Close"].iloc[-1])
            if prev_close is None and len(hist) > 1:
                prev_close = float(hist["Close"].iloc[-2])
            if volume is None:
                volume = int(hist["Volume"].iloc[-1])

    if current is None:
        raise RuntimeError(f"Gagal mengambil harga {symbol}.")

    change = None if prev_close in (None, 0) else float(current) - float(prev_close)
    pct = None if prev_close in (None, 0) else (float(change) / float(prev_close)) * 100

    return {
        "symbol": symbol,
        "name": name,
        "current": float(current),
        "prev_close": None if prev_close is None else float(prev_close),
        "change": change,
        "pct": pct,
        "volume": int(volume or 0),
        "currency": currency,
        "market_cap": info.get("marketCap"),
    }


def _history(symbol, period="6mo", interval="1d"):
    symbol = _normalize_symbol(symbol)
    return _ticker(symbol).history(period=period, interval=interval, auto_adjust=False)


def _news(symbol, limit=5):
    symbol = _normalize_symbol(symbol)
    try:
        items = _ticker(symbol).news or []
        out = []
        for item in items[:limit]:
            out.append({
                "title": item.get("title") or "",
                "publisher": item.get("publisher") or "",
                "link": item.get("link") or item.get("url") or "",
            })
        return out
    except Exception:
        return []


def _fundamental(symbol):
    symbol = _normalize_symbol(symbol)
    tk = _ticker(symbol)
    info = {}
    try:
        info = tk.info or {}
    except Exception:
        pass
    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "pe": info.get("trailingPE"),
        "pb": info.get("priceToBook"),
        "profit_margin": info.get("profitMargins"),
        "revenue_growth": info.get("revenueGrowth"),
        "debt_to_equity": info.get("debtToEquity"),
        "dividend_yield": info.get("dividendYield"),
        "beta": info.get("beta"),
    }


def _chart_text(symbol):
    hist = _history(symbol, period="6mo", interval="1d")
    if hist is None or hist.empty:
        return "Grafik belum tersedia."
    values = [float(x) for x in hist["Close"].tail(30).tolist()]
    if len(values) < 2:
        return "Grafik belum cukup data."
    mn, mx = min(values), max(values)
    if math.isclose(mn, mx):
        return "─" * len(values)
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    for v in values:
        idx = int((v - mn) / (mx - mn) * (len(blocks) - 1))
        out.append(blocks[max(0, min(idx, len(blocks) - 1))])
    return "".join(out)


def _account_id(supabase, user_id, chat_id, username="", first_name=""):
    row = (
        supabase.table("investasi_accounts")
        .select("id")
        .eq("telegram_user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if row.data and row.data.get("id"):
        supabase.table("investasi_accounts").update({
            "telegram_chat_id": str(chat_id),
            "username": username or None,
            "first_name": first_name or None,
            "updated_at": _fmt_dt_now(),
        }).eq("id", row.data["id"]).execute()
        return row.data["id"]

    inserted = supabase.table("investasi_accounts").insert({
        "telegram_user_id": str(user_id),
        "telegram_chat_id": str(chat_id),
        "username": username or None,
        "first_name": first_name or None,
    }).execute()
    return inserted.data[0]["id"]


def _settings(supabase, account_id):
    result = (
        supabase.table("investasi_settings")
        .select("*")
        .eq("account_id", account_id)
        .execute()
    )

    if result.data:
        return result.data[0]

    default = {
        "account_id": account_id,
        "refresh_interval_sec": DEFAULT_REFRESH_INTERVAL,
        "currency": DEFAULT_CURRENCY,
        "notifications_enabled": True,
    }

    supabase.table("investasi_settings").insert(default).execute()

    return default

def _set_settings(supabase, account_id, **kwargs):
    kwargs["updated_at"] = _fmt_dt_now()
    supabase.table("investasi_settings").update(kwargs).eq("account_id", account_id).execute()


def show_investasi_menu(bot, message, edit=False):
    text = (
        "📈 <b>Investasi</b>\n\n"
        "Bot membaca transaksi dari Supabase, lalu menghitung portofolio sendiri.\n"
        "Harga, berita, dan fundamental diambil dari sumber market eksternal."
    )
    if edit:
        _send_or_edit(bot, message, text, _main_kb())
    else:
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=_main_kb(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def _portfolio_snapshot(supabase, account_id, currency):
    rows = (
        supabase.table("investasi_transactions")
        .select("*")
        .eq("account_id", account_id)
        .order("trade_date")
        .order("created_at")
        .execute()
    ).data or []

    books = defaultdict(deque)

    for tx in rows:
        symbol = tx["symbol"]
        shares = int(tx.get("qty_share") or int(tx.get("qty_lot") or 0) * LOT_SIZE)
        price = _safe_decimal(tx.get("price"))
        fee = _safe_decimal(tx.get("fee"))
        unit = _safe_decimal(tx.get("cost_basis_per_share"))
        if unit <= 0 and shares > 0:
            unit = (price * shares + fee) / shares

        if tx["side"] == "BUY":
            books[symbol].append([shares, unit])
        else:
            remaining = shares
            while remaining > 0 and books[symbol]:
                lot_shares, lot_unit = books[symbol][0]
                matched = min(remaining, lot_shares)
                lot_shares -= matched
                remaining -= matched
                if lot_shares <= 0:
                    books[symbol].popleft()
                else:
                    books[symbol][0][0] = lot_shares

    holdings = []
    total_cost = Decimal("0")
    market_value = Decimal("0")
    total_shares = 0

    for sym, lotq in books.items():
        shares = sum(x[0] for x in lotq)
        if shares <= 0:
            continue
        avg_cost = sum(Decimal(x[0]) * x[1] for x in lotq) / Decimal(shares)
        try:
            q = _quote(sym)
            current = Decimal(str(q["current"]))
        except Exception:
            current = Decimal("0")
            q = None
        cost = avg_cost * Decimal(shares)
        value = current * Decimal(shares)
        pnl = value - cost
        pct = (pnl / cost * 100) if cost else Decimal("0")

        total_cost += cost
        market_value += value
        total_shares += shares
        holdings.append({
            "symbol": sym,
            "shares": shares,
            "lots": shares // LOT_SIZE,
            "avg_cost": avg_cost,
            "current": current,
            "cost": cost,
            "value": value,
            "pnl": pnl,
            "pct": pct,
            "quote": q,
        })

    realized = Decimal("0")
    for tx in rows:
        if tx["side"] == "SELL" and (tx.get("notes") or "").startswith("realized="):
            try:
                realized += Decimal((tx.get("notes") or "").split("=", 1)[1])
            except Exception:
                pass

    unrealized = market_value - total_cost
    profit = realized + unrealized
    profit_pct = (profit / total_cost * 100) if total_cost else Decimal("0")
    holdings.sort(key=lambda x: x["value"], reverse=True)

    return {
        "holdings": holdings,
        "total_cost": total_cost,
        "market_value": market_value,
        "realized": realized,
        "unrealized": unrealized,
        "profit": profit,
        "profit_pct": profit_pct,
        "total_shares": total_shares,
    }


def _portfolio_text(snapshot, currency):
    lines = [
        "💼 <b>Portofolio</b>",
        "",
        f"Total Modal: <b>{_money(snapshot['total_cost'], currency)}</b>",
        f"Nilai Portofolio: <b>{_money(snapshot['market_value'], currency)}</b>",
        f"Unrealized Profit: <b>{_money(snapshot['unrealized'], currency)}</b>",
        f"Realized Profit: <b>{_money(snapshot['realized'], currency)}</b>",
        f"Profit %: <b>{_percent(snapshot['profit_pct'])}</b>",
        f"Total Saham: <b>{snapshot['total_shares']}</b>",
        "",
        "<b>Ringkasan kepemilikan:</b>",
    ]
    if not snapshot["holdings"]:
        lines.append("Belum ada posisi terbuka.")
    else:
        for h in snapshot["holdings"][:8]:
            lines.append(
                f"• <b>{escape(h['symbol'])}</b> — {h['lots']} lot | "
                f"{_money(h['value'], currency)} | {_percent(h['pct'])}"
            )
    return "\n".join(lines)


def _detail_text(symbol, snapshot, currency):
    q = _quote(symbol)
    h = next((x for x in snapshot["holdings"] if x["symbol"] == symbol), None)
    lines = [f"📊 <b>{escape(symbol)}</b>", ""]
    if h:
        lines += [
            f"Harga beli rata-rata: <b>{_money(h['avg_cost'], currency, 2)}</b>",
            f"Harga sekarang: <b>{_money(h['current'], currency, 2)}</b>",
            f"Jumlah lot: <b>{h['lots']}</b>",
            f"Nilai modal: <b>{_money(h['cost'], currency)}</b>",
            f"Nilai sekarang: <b>{_money(h['value'], currency)}</b>",
            f"Profit/Loss: <b>{_money(h['pnl'], currency)}</b>",
            f"Persentase: <b>{_percent(h['pct'])}</b>",
        ]
    else:
        lines.append("Saham ini belum ada di portofolio. Data pasar tetap bisa ditampilkan.")
    lines += [
        "",
        f"Harga sekarang: <b>{_money(q['current'], currency, 2)}</b>",
        f"Perubahan harian: <b>{_money(q.get('change') or 0, currency, 2)}</b> ({_percent(q.get('pct') or 0)})",
    ]
    return "\n".join(lines)


def _tx_text(rows, currency):
    if not rows:
        return "Belum ada transaksi."
    lines = ["🧾 <b>Riwayat Transaksi</b>", ""]
    for tx in rows:
        lines.append(
            f"• <b>{tx['trade_date']}</b> {escape(tx['side'])} <b>{escape(tx['symbol'])}</b> — "
            f"{tx['qty_lot']} lot @ {_money(tx['price'], currency, 2)}"
        )
    return "\n".join(lines)


def _watchlist_rows(supabase, account_id):
    return (
        supabase.table("investasi_watchlist")
        .select("*")
        .eq("account_id", account_id)
        .order("sort_order")
        .order("created_at")
        .execute()
    ).data or []


def _set_watchlist(supabase, account_id, symbol, add=True):
    symbol = _normalize_symbol(symbol)
    if add:
        exists = (
            supabase.table("investasi_watchlist")
            .select("id")
            .eq("account_id", account_id)
            .eq("symbol", symbol)
            .maybe_single()
            .execute()
        )
        if exists.data:
            return
        last = (
            supabase.table("investasi_watchlist")
            .select("sort_order")
            .eq("account_id", account_id)
            .order("sort_order", desc=True)
            .limit(1)
            .execute()
        ).data or []
        order = (last[0]["sort_order"] + 1) if last else 1
        supabase.table("investasi_watchlist").insert({
            "account_id": account_id,
            "symbol": symbol,
            "sort_order": order,
        }).execute()
    else:
        supabase.table("investasi_watchlist").delete().eq("account_id", account_id).eq("symbol", symbol).execute()


def _watchlist_text(supabase, account_id, currency):
    rows = _watchlist_rows(supabase, account_id)
    lines = ["⭐ <b>Watchlist</b>", ""]
    if not rows:
        lines.append("Watchlist masih kosong.")
    else:
        for row in rows[:10]:
            sym = row["symbol"]
            try:
                q = _quote(sym)
                lines.append(f"• <b>{escape(sym)}</b> — {_money(q['current'], currency, 2)}")
            except Exception:
                lines.append(f"• <b>{escape(sym)}</b>")
    return "\n".join(lines)


def _alert_rows(supabase, account_id, only_active=True):
    q = supabase.table("investasi_alerts").select("*").eq("account_id", account_id)
    if only_active:
        q = q.eq("is_active", True)
    return q.order("created_at", desc=True).execute().data or []


def _alert_text(supabase, account_id, currency):
    rows = _alert_rows(supabase, account_id, only_active=False)
    lines = ["🔔 <b>Alert Harga</b>", ""]
    if not rows:
        lines.append("Belum ada alert.")
    else:
        for a in rows[:10]:
            lines.append(
                f"• <b>{escape(a['symbol'])}</b> {escape(a['operator'])} "
                f"{_money(a['target_price'], currency, 2)} — {'aktif' if a['is_active'] else 'nonaktif'}"
            )
    return "\n".join(lines)


def _history_rows(supabase, account_id, limit=10):
    return (
        supabase.table("investasi_transactions")
        .select("*")
        .eq("account_id", account_id)
        .order("trade_date", desc=True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def _insert_tx(supabase, account_id, symbol, side, lot, price, trade_date, fee=Decimal("0"), notes=""):
    qty_share = int(lot) * LOT_SIZE
    unit_cost = ((price * qty_share + fee) / qty_share) if qty_share else Decimal("0")
    inserted = supabase.table("investasi_transactions").insert({
        "account_id": account_id,
        "symbol": symbol,
        "side": side,
        "qty_lot": int(lot),
        "qty_share": qty_share,
        "price": str(price),
        "fee": str(fee),
        "trade_date": trade_date,
        "notes": notes or None,
        "cost_basis_per_share": str(unit_cost),
        "created_at": _fmt_dt_now(),
        "updated_at": _fmt_dt_now(),
    }).execute()
    return inserted.data[0]


def _insert_sell_and_realized(supabase, account_id, symbol, lot, price, trade_date):
    rows = (
        supabase.table("investasi_transactions")
        .select("*")
        .eq("account_id", account_id)
        .eq("symbol", symbol)
        .execute()
    ).data or []

    book = deque()
    realized = Decimal("0")

    for tx in sorted(rows, key=lambda x: (x.get("trade_date") or "", x.get("created_at") or "", x.get("id") or "")):
        shares = int(tx.get("qty_share") or int(tx.get("qty_lot") or 0) * LOT_SIZE)
        tx_price = _safe_decimal(tx.get("price"))
        tx_fee = _safe_decimal(tx.get("fee"))
        unit = _safe_decimal(tx.get("cost_basis_per_share"))
        if unit <= 0 and shares > 0:
            unit = (tx_price * shares + tx_fee) / shares

        if tx["side"] == "BUY":
            book.append([shares, unit])
        else:
            remaining = shares
            while remaining > 0 and book:
                s, u = book[0]
                m = min(remaining, s)
                s -= m
                remaining -= m
                if s <= 0:
                    book.popleft()
                else:
                    book[0][0] = s

    remaining_sell = lot * LOT_SIZE
    owned = sum(x[0] for x in book)
    if owned < remaining_sell:
        raise ValueError("Jumlah jual melebihi saham yang masih dimiliki.")

    while remaining_sell > 0 and book:
        s, u = book[0]
        m = min(remaining_sell, s)
        realized += Decimal(m) * (price - u)
        s -= m
        remaining_sell -= m
        if s <= 0:
            book.popleft()
        else:
            book[0][0] = s

    _insert_tx(supabase, account_id, symbol, "SELL", lot, price, trade_date, fee=Decimal("0"), notes=f"realized={realized}")
    return realized


def _detail_rows(supabase, account_id, symbol):
    return (
        supabase.table("investasi_transactions")
        .select("*")
        .eq("account_id", account_id)
        .eq("symbol", symbol)
        .order("trade_date", desc=True)
        .order("created_at", desc=True)
        .execute()
    ).data or []


def process_investasi_text(bot, message, supabase, pending_actions):
    user_id = message.from_user.id
    action = _pending(pending_actions, user_id)
    kind = action.get("kind")
    if not kind or not kind.startswith("inv_"):
        return False

    try:
        account_id = _account_id(
            supabase,
            user_id=user_id,
            chat_id=message.chat.id,
            username=getattr(message.from_user, "username", "") or "",
            first_name=getattr(message.from_user, "first_name", "") or "",
        )
        currency = _settings(supabase, account_id).get("currency", DEFAULT_CURRENCY)
        text = (message.text or "").strip()

        if kind == "inv_buy_symbol":
            symbol = _normalize_symbol(text)
            action.clear()
            action.update({"kind": "inv_buy_lot", "symbol": symbol})
            bot.send_message(message.chat.id, f"Masukkan jumlah lot untuk <b>{escape(symbol)}</b>.", parse_mode="HTML", reply_markup=_nav_kb())
            return True

        if kind == "inv_buy_lot":
            lot = int(text)
            if lot <= 0:
                raise ValueError("Lot harus lebih dari 0.")
            action["lot"] = lot
            action["kind"] = "inv_buy_price"
            bot.send_message(message.chat.id, "Masukkan harga beli per saham.", reply_markup=_nav_kb())
            return True

        if kind == "inv_buy_price":
            price = _safe_decimal(text)
            if price <= 0:
                raise ValueError("Harga harus lebih dari 0.")
            action["price"] = str(price)
            action["kind"] = "inv_buy_date"
            bot.send_message(message.chat.id, "Masukkan tanggal transaksi.", reply_markup=_nav_kb())
            return True

        if kind == "inv_buy_date":
            symbol = action["symbol"]
            lot = int(action["lot"])
            price = _safe_decimal(action["price"])
            _insert_tx(supabase, account_id, symbol, "BUY", lot, price, _parse_date(text))
            _clear_pending(pending_actions, user_id)
            bot.send_message(message.chat.id, f"✅ Beli <b>{escape(symbol)}</b> tersimpan.", parse_mode="HTML", reply_markup=_portfolio_kb())
            return True

        if kind == "inv_sell_symbol":
            symbol = _normalize_symbol(text)
            action.clear()
            action.update({"kind": "inv_sell_lot", "symbol": symbol})
            bot.send_message(message.chat.id, f"Masukkan jumlah lot untuk jual <b>{escape(symbol)}</b>.", parse_mode="HTML", reply_markup=_nav_kb())
            return True

        if kind == "inv_sell_lot":
            lot = int(text)
            if lot <= 0:
                raise ValueError("Lot harus lebih dari 0.")
            action["lot"] = lot
            action["kind"] = "inv_sell_price"
            bot.send_message(message.chat.id, "Masukkan harga jual per saham.", reply_markup=_nav_kb())
            return True

        if kind == "inv_sell_price":
            price = _safe_decimal(text)
            if price <= 0:
                raise ValueError("Harga harus lebih dari 0.")
            action["price"] = str(price)
            action["kind"] = "inv_sell_date"
            bot.send_message(message.chat.id, "Masukkan tanggal jual.", reply_markup=_nav_kb())
            return True

        if kind == "inv_sell_date":
            symbol = action["symbol"]
            lot = int(action["lot"])
            price = _safe_decimal(action["price"])
            realized = _insert_sell_and_realized(supabase, account_id, symbol, lot, price, _parse_date(text))
            _clear_pending(pending_actions, user_id)
            bot.send_message(
                message.chat.id,
                f"✅ Jual <b>{escape(symbol)}</b> tersimpan. Realized profit: <b>{_money(realized, currency)}</b>",
                parse_mode="HTML",
                reply_markup=_portfolio_kb(),
            )
            return True

        if kind == "inv_watch_add":
            _set_watchlist(supabase, account_id, _normalize_symbol(text), add=True)
            _clear_pending(pending_actions, user_id)
            bot.send_message(message.chat.id, "⭐ Watchlist diperbarui.", reply_markup=_watchlist_kb())
            return True

        if kind == "inv_watch_remove":
            _set_watchlist(supabase, account_id, _normalize_symbol(text), add=False)
            _clear_pending(pending_actions, user_id)
            bot.send_message(message.chat.id, "➖ Saham dihapus dari watchlist.", reply_markup=_watchlist_kb())
            return True

        if kind == "inv_search":
            results = []
            if yf is not None:
                try:
                    s = yf.Search(text)
                    results = (getattr(s, "quotes", []) or [])[:5]
                except Exception:
                    results = []
            if not results:
                results = [{"symbol": text.upper(), "shortname": text}]
            lines = ["🔎 <b>Hasil pencarian</b>", ""]
            kb = InlineKeyboardMarkup()
            for r in results:
                sym = r.get("symbol") or ""
                name = r.get("shortname") or r.get("longname") or r.get("name") or ""
                if not sym:
                    continue
                lines.append(f"• <b>{escape(sym)}</b> — {escape(name)}")
                kb.row(InlineKeyboardButton(sym, callback_data=f"{PREFIX}_detail:{sym}"))
            kb.row(
                InlineKeyboardButton("⬅️ Kembali", callback_data=f"{PREFIX}_market"),
                InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
            )
            _send_or_edit(bot, message, "\n".join(lines), kb)
            _clear_pending(pending_actions, user_id)
            return True

        if kind == "inv_alert_symbol":
            symbol = _normalize_symbol(text)
            action.clear()
            action.update({"kind": "inv_alert_op", "symbol": symbol})
            bot.send_message(message.chat.id, f"Masukkan operator alert untuk <b>{escape(symbol)}</b> (contoh: > atau <).", parse_mode="HTML", reply_markup=_nav_kb())
            return True

        if kind == "inv_alert_op":
            if text not in (">", "<"):
                raise ValueError("Operator harus > atau <.")
            action["operator"] = text
            action["kind"] = "inv_alert_price"
            bot.send_message(message.chat.id, "Masukkan harga target.", reply_markup=_nav_kb())
            return True

        if kind == "inv_alert_price":
            symbol = action["symbol"]
            op = action["operator"]
            target = _safe_decimal(text)
            supabase.table("investasi_alerts").insert({
                "account_id": account_id,
                "symbol": symbol,
                "operator": op,
                "target_price": str(target),
                "is_active": True,
                "created_at": _fmt_dt_now(),
                "updated_at": _fmt_dt_now(),
            }).execute()
            _clear_pending(pending_actions, user_id)
            bot.send_message(
                message.chat.id,
                f"🔔 Alert tersimpan: <b>{escape(symbol)}</b> {escape(op)} {_money(target, currency, 2)}",
                parse_mode="HTML",
                reply_markup=_alert_kb(),
            )
            return True

        if kind == "inv_set_refresh":
            sec = int(text)
            if sec < 30:
                raise ValueError("Refresh minimal 30 detik.")
            _set_settings(supabase, account_id, refresh_interval_sec=sec)
            _clear_pending(pending_actions, user_id)
            bot.send_message(message.chat.id, f"⏱ Refresh interval diubah ke {sec} detik.", reply_markup=_settings_kb(_settings(supabase, account_id)))
            return True

        if kind == "inv_set_currency":
            cur = text.strip().upper()
            if not cur:
                raise ValueError("Mata uang tidak boleh kosong.")
            _set_settings(supabase, account_id, currency=cur)
            _clear_pending(pending_actions, user_id)
            bot.send_message(message.chat.id, f"💱 Mata uang diubah ke {escape(cur)}.", parse_mode="HTML", reply_markup=_settings_kb(_settings(supabase, account_id)))
            return True

        if kind == "inv_tx_edit_field":
            tx_id = action["tx_id"]
            field = action["field"]
            update = {}
            if field == "lot":
                lot = int(text)
                if lot <= 0:
                    raise ValueError("Lot harus lebih dari 0.")
                update["qty_lot"] = lot
                update["qty_share"] = lot * LOT_SIZE
            elif field == "price":
                price = _safe_decimal(text)
                if price <= 0:
                    raise ValueError("Harga harus lebih dari 0.")
                update["price"] = str(price)
            elif field == "trade_date":
                update["trade_date"] = _parse_date(text)
            elif field == "notes":
                update["notes"] = text
            else:
                raise ValueError("Field edit tidak dikenali.")
            update["updated_at"] = _fmt_dt_now()
            supabase.table("investasi_transactions").update(update).eq("id", tx_id).execute()
            _clear_pending(pending_actions, user_id)
            bot.send_message(message.chat.id, "✅ Transaksi berhasil diperbarui.", reply_markup=_txn_kb())
            return True

        return False

    except Exception as exc:
        bot.send_message(message.chat.id, f"❌ {escape(str(exc))}", parse_mode="HTML", reply_markup=_nav_kb())
        return True


def process_investasi_document(bot, message, supabase, pending_actions):
    user_id = message.from_user.id
    action = _pending(pending_actions, user_id)
    if action.get("kind") != "inv_import_csv":
        return False
    try:
        account_id = _account_id(
            supabase,
            user_id=user_id,
            chat_id=message.chat.id,
            username=getattr(message.from_user, "username", "") or "",
            first_name=getattr(message.from_user, "first_name", "") or "",
        )
        file_info = bot.get_file(message.document.file_id)
        content = bot.download_file(file_info.file_path).decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        count = 0
        for row in reader:
            symbol = _normalize_symbol(row.get("symbol") or row.get("kode") or row.get("ticker") or "")
            side = (row.get("side") or row.get("aksi") or "").strip().upper()
            if side not in ("BUY", "SELL"):
                raise ValueError("Kolom side harus BUY atau SELL.")
            lot = int(row.get("lot") or row.get("qty_lot") or 0)
            price = _safe_decimal(row.get("price") or row.get("harga") or 0)
            date = _parse_date(row.get("trade_date") or row.get("tanggal") or datetime.utcnow().date().isoformat())
            fee = _safe_decimal(row.get("fee") or 0)
            notes = row.get("notes") or ""
            _insert_tx(supabase, account_id, symbol, side, lot, price, date, fee=fee, notes=notes)
            count += 1
        _clear_pending(pending_actions, user_id)
        bot.send_message(message.chat.id, f"✅ {count} transaksi berhasil diimpor.", reply_markup=_txn_kb())
        return True
    except Exception as exc:
        bot.send_message(message.chat.id, f"❌ Import CSV gagal: {escape(str(exc))}", parse_mode="HTML", reply_markup=_txn_kb())
        return True


def process_investasi_callback(bot, call, supabase, pending_actions, show_dashboard):
    data = call.data or ""
    if not data.startswith(PREFIX):
        return False

    user_id = call.from_user.id

    try:
        account_id = _account_id(
            supabase,
            user_id=user_id,
            chat_id=call.message.chat.id,
            username=getattr(call.from_user, "username", "") or "",
            first_name=getattr(call.from_user, "first_name", "") or "",
        )
        settings = _settings(supabase, account_id)
        currency = settings.get("currency", DEFAULT_CURRENCY)

        if data in (f"{PREFIX}_home", f"{PREFIX}_menu"):
            _clear_pending(pending_actions, user_id)
            show_investasi_menu(bot, call.message, edit=True)
            return True

        if data == f"{PREFIX}_portfolio":
            snapshot = _portfolio_snapshot(supabase, account_id, currency)
            _send_or_edit(bot, call.message, _portfolio_text(snapshot, currency), _portfolio_kb())
            return True

        if data.startswith(f"{PREFIX}_detail:"):
            symbol = data.split(":", 1)[1]
            snapshot = _portfolio_snapshot(supabase, account_id, currency)
            _send_or_edit(bot, call.message, _detail_text(symbol, snapshot, currency), _detail_kb(symbol))
            return True

        if data.startswith(f"{PREFIX}_tx_symbol:"):
            symbol = data.split(":", 1)[1]
            rows = _detail_rows(supabase, account_id, symbol)
            lines = ["🧾 <b>Riwayat Saham</b>", "", f"Kode: <b>{escape(symbol)}</b>", ""]
            for tx in rows[:10]:
                lines.append(f"• {tx['trade_date']} {escape(tx['side'])} {tx['qty_lot']} lot @ {_money(tx['price'], currency, 2)}")
            kb = InlineKeyboardMarkup()
            kb.row(
                InlineKeyboardButton("⬅️ Kembali", callback_data=f"{PREFIX}_detail:{symbol}"),
                InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
            )
            _send_or_edit(bot, call.message, "\n".join(lines), kb)
            return True

        if data.startswith(f"{PREFIX}_chart:"):
            symbol = data.split(":", 1)[1]
            text = f"📈 <b>{escape(symbol)}</b>\n\n{_chart_text(symbol)}"
            _send_or_edit(bot, call.message, text, _detail_kb(symbol))
            return True

        if data.startswith(f"{PREFIX}_news:"):
            symbol = data.split(":", 1)[1]
            if symbol == "IHSG":
                symbol = "^JKSE"
            items = _news(symbol, 5)
            lines = [f"📰 <b>{escape(symbol)}</b>", ""]
            if not items:
                lines.append("Tidak ada berita yang berhasil diambil.")
            else:
                for item in items:
                    lines.append(f"• <b>{escape(item['title'])}</b>")
                    if item.get("publisher"):
                        lines.append(f"  {escape(item['publisher'])}")
                    lines.append("")
            _send_or_edit(bot, call.message, "\n".join(lines).strip(), _detail_kb(symbol))
            return True

        if data.startswith(f"{PREFIX}_fund:"):
            symbol = data.split(":", 1)[1]
            try:
                f = _fundamental(symbol)
                lines = [f"ℹ <b>Fundamental {escape(symbol)}</b>", ""]
                for key, label in [
                    ("sector", "Sector"),
                    ("industry", "Industry"),
                    ("market_cap", "Market Cap"),
                    ("pe", "P/E"),
                    ("pb", "P/B"),
                    ("profit_margin", "Profit Margin"),
                    ("revenue_growth", "Revenue Growth"),
                    ("debt_to_equity", "Debt/Equity"),
                    ("dividend_yield", "Dividend Yield"),
                    ("beta", "Beta"),
                ]:
                    val = f.get(key)
                    if val is None:
                        continue
                    if key == "market_cap":
                        val = _money(val, DEFAULT_CURRENCY)
                    elif key in ("profit_margin", "revenue_growth", "dividend_yield"):
                        try:
                            val = f"{float(val) * 100:.2f}%"
                        except Exception:
                            continue
                    lines.append(f"{label}: <b>{escape(str(val))}</b>")
                _send_or_edit(bot, call.message, "\n".join(lines), _detail_kb(symbol))
            except Exception as exc:
                _send_or_edit(bot, call.message, f"Fundamental gagal dimuat: {escape(str(exc))}", _detail_kb(symbol))
            return True

        if data == f"{PREFIX}_market":
            _send_or_edit(bot, call.message, "📊 <b>Pasar</b>\n\nPilih sub menu.", _market_kb())
            return True

        if data.startswith(f"{PREFIX}_market:"):
            key = data.split(":", 1)[1]
            if key == "IHSG":
                syms = ["^JKSE"]
                title = "IHSG"
            elif key == "gainers":
                syms, title = DEFAULT_UNIVERSE, "Top Gainers"
            elif key == "losers":
                syms, title = DEFAULT_UNIVERSE, "Top Losers"
            elif key == "volume":
                syms, title = DEFAULT_UNIVERSE, "Top Volume"
            else:
                syms, title = DEFAULT_UNIVERSE, "Pasar"

            quotes = []
            for s in syms:
                try:
                    quotes.append(_quote(s))
                except Exception:
                    pass

            if key == "losers":
                quotes.sort(key=lambda x: float(x.get("pct") or 0))
            elif key == "volume":
                quotes.sort(key=lambda x: int(x.get("volume") or 0), reverse=True)
            else:
                quotes.sort(key=lambda x: float(x.get("pct") or 0), reverse=True)

            lines = [f"📊 <b>{escape(title)}</b>", ""]
            kb = InlineKeyboardMarkup()
            for q in quotes[:10]:
                lines.append(
                    f"• <b>{escape(q['symbol'])}</b> — "
                    f"{_money(q['current'], q.get('currency') or currency, 2)} ({_percent(q.get('pct') or 0)})"
                )
                kb.row(InlineKeyboardButton(q["symbol"], callback_data=f"{PREFIX}_detail:{q['symbol']}"))
            kb.row(
                InlineKeyboardButton("⬅️ Kembali", callback_data=f"{PREFIX}_home"),
                InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
            )
            _send_or_edit(bot, call.message, "\n".join(lines), kb)
            return True

        if data.startswith(f"{PREFIX}_sector:"):
            name = data.split(":", 1)[1]
            syms = SECTOR_BASKETS.get(name, DEFAULT_UNIVERSE)
            quotes = []
            for s in syms:
                try:
                    quotes.append(_quote(s))
                except Exception:
                    pass
            quotes.sort(key=lambda x: float(x.get("pct") or 0), reverse=True)
            lines = [f"📊 <b>Basket {escape(name)}</b>", ""]
            kb = InlineKeyboardMarkup()
            for q in quotes[:10]:
                lines.append(
                    f"• <b>{escape(q['symbol'])}</b> — "
                    f"{_money(q['current'], q.get('currency') or currency, 2)} ({_percent(q.get('pct') or 0)})"
                )
                kb.row(InlineKeyboardButton(q["symbol"], callback_data=f"{PREFIX}_detail:{q['symbol']}"))
            kb.row(
                InlineKeyboardButton("⬅️ Kembali", callback_data=f"{PREFIX}_market"),
                InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
            )
            _send_or_edit(bot, call.message, "\n".join(lines), kb)
            return True

        if data == f"{PREFIX}_search":
            pending_actions[user_id] = {"kind": "inv_search"}
            bot.send_message(call.message.chat.id, "Kirim kode saham atau nama emiten.", reply_markup=_nav_kb())
            return True

        if data == f"{PREFIX}_watchlist":
            _send_or_edit(bot, call.message, _watchlist_text(supabase, account_id, currency), _watchlist_kb())
            return True

        if data == f"{PREFIX}_watch_add":
            pending_actions[user_id] = {"kind": "inv_watch_add"}
            bot.send_message(call.message.chat.id, "Kirim kode saham untuk ditambahkan.", reply_markup=_watchlist_kb())
            return True

        if data == f"{PREFIX}_watch_remove":
            pending_actions[user_id] = {"kind": "inv_watch_remove"}
            bot.send_message(call.message.chat.id, "Kirim kode saham untuk dihapus.", reply_markup=_watchlist_kb())
            return True

        if data == f"{PREFIX}_watch_sort":
            rows = _watchlist_rows(supabase, account_id)
            rows.sort(key=lambda x: x["symbol"])
            for i, row in enumerate(rows, start=1):
                supabase.table("investasi_watchlist").update({"sort_order": i}).eq("id", row["id"]).execute()
            bot.send_message(call.message.chat.id, "↕️ Watchlist diurutkan alfabetis.", reply_markup=_watchlist_kb())
            return True

        if data == f"{PREFIX}_watch_realtime":
            rows = _watchlist_rows(supabase, account_id)
            if not rows:
                bot.send_message(call.message.chat.id, "Watchlist kosong.", reply_markup=_watchlist_kb())
                return True
            lines = ["💹 <b>Watchlist realtime</b>", ""]
            for r in rows:
                try:
                    q = _quote(r["symbol"])
                    lines.append(f"• <b>{escape(q['symbol'])}</b> — {_money(q['current'], currency, 2)}")
                except Exception:
                    lines.append(f"• <b>{escape(r['symbol'])}</b>")
            _send_or_edit(bot, call.message, "\n".join(lines), _watchlist_kb())
            return True

        if data == f"{PREFIX}_transactions":
            _send_or_edit(bot, call.message, "💸 <b>Transaksi</b>\n\nPilih aksi.", _txn_kb())
            return True

        if data == f"{PREFIX}_buy":
            pending_actions[user_id] = {"kind": "inv_buy_symbol"}
            bot.send_message(call.message.chat.id, "Masukkan kode saham yang dibeli.", reply_markup=_nav_kb())
            return True

        if data == f"{PREFIX}_sell":
            pending_actions[user_id] = {"kind": "inv_sell_symbol"}
            bot.send_message(call.message.chat.id, "Masukkan kode saham yang dijual.", reply_markup=_nav_kb())
            return True

        if data == f"{PREFIX}_tx_history":
            rows = _history_rows(supabase, account_id, 10)
            lines = _tx_text(rows, currency)
            kb = InlineKeyboardMarkup()
            for tx in rows[:5]:
                kb.row(
                    InlineKeyboardButton(f"✏️ {tx['symbol']} {tx['trade_date']}", callback_data=f"{PREFIX}_tx_edit:{tx['id']}"),
                    InlineKeyboardButton(f"🗑 {tx['symbol']} {tx['trade_date']}", callback_data=f"{PREFIX}_tx_delete:{tx['id']}"),
                )
            kb.row(
                InlineKeyboardButton("⬅️ Kembali", callback_data=f"{PREFIX}_transactions"),
                InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
            )
            _send_or_edit(bot, call.message, lines, kb)
            return True

        if data.startswith(f"{PREFIX}_tx_edit:"):
            tx_id = data.split(":", 1)[1]
            kb = InlineKeyboardMarkup()
            kb.row(
                InlineKeyboardButton("Lot", callback_data=f"{PREFIX}_tx_edit_field:{tx_id}:lot"),
                InlineKeyboardButton("Harga", callback_data=f"{PREFIX}_tx_edit_field:{tx_id}:price"),
            )
            kb.row(
                InlineKeyboardButton("Tanggal", callback_data=f"{PREFIX}_tx_edit_field:{tx_id}:trade_date"),
                InlineKeyboardButton("Catatan", callback_data=f"{PREFIX}_tx_edit_field:{tx_id}:notes"),
            )
            kb.row(
                InlineKeyboardButton("⬅️ Kembali", callback_data=f"{PREFIX}_tx_history"),
                InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
            )
            _send_or_edit(bot, call.message, "Pilih field yang ingin diedit.", kb)
            return True

        if data.startswith(f"{PREFIX}_tx_edit_field:"):
            _, _, tx_id, field = data.split(":", 3)
            pending_actions[user_id] = {"kind": "inv_tx_edit_field", "tx_id": tx_id, "field": field}
            bot.send_message(call.message.chat.id, f"Masukkan nilai baru untuk {field}.", reply_markup=_nav_kb())
            return True

        if data.startswith(f"{PREFIX}_tx_delete:"):
            tx_id = data.split(":", 1)[1]
            supabase.table("investasi_transactions").delete().eq("id", tx_id).execute()
            bot.send_message(call.message.chat.id, "🗑 Transaksi dihapus.", reply_markup=_txn_kb())
            return True

        if data == f"{PREFIX}_tx_import":
            pending_actions[user_id] = {"kind": "inv_import_csv"}
            bot.send_message(call.message.chat.id, "Kirim file CSV transaksi.", reply_markup=_txn_kb())
            return True

        if data == f"{PREFIX}_alerts":
            _send_or_edit(bot, call.message, _alert_text(supabase, account_id, currency), _alert_kb())
            return True

        if data == f"{PREFIX}_alert_add":
            pending_actions[user_id] = {"kind": "inv_alert_symbol"}
            bot.send_message(call.message.chat.id, "Masukkan kode saham untuk alert.", reply_markup=_nav_kb())
            return True

        if data == f"{PREFIX}_alert_list":
            _send_or_edit(bot, call.message, _alert_text(supabase, account_id, currency), _alert_kb())
            return True

        if data == f"{PREFIX}_news":
            _send_or_edit(bot, call.message, "📰 <b>Berita</b>\n\nPilih simbol atau cari saham.", _news_kb())
            return True

        if data == f"{PREFIX}_news_search":
            pending_actions[user_id] = {"kind": "inv_search"}
            bot.send_message(call.message.chat.id, "Kirim kode saham atau nama emiten untuk berita.", reply_markup=_news_kb())
            return True

        if data == f"{PREFIX}_settings":
            _send_or_edit(bot, call.message, "⚙ <b>Pengaturan</b>\n\nAtur interval, mata uang, notifikasi, dan reset.", _settings_kb(settings))
            return True

        if data == f"{PREFIX}_set_refresh":
            pending_actions[user_id] = {"kind": "inv_set_refresh"}
            bot.send_message(call.message.chat.id, "Masukkan interval refresh dalam detik (minimal 30).", reply_markup=_settings_kb(settings))
            return True

        if data == f"{PREFIX}_set_currency":
            pending_actions[user_id] = {"kind": "inv_set_currency"}
            bot.send_message(call.message.chat.id, "Masukkan mata uang, misalnya IDR atau USD.", reply_markup=_settings_kb(settings))
            return True

        if data == f"{PREFIX}_set_notif":
            now = bool(settings.get("notifications_enabled", True))
            _set_settings(supabase, account_id, notifications_enabled=not now)
            bot.send_message(call.message.chat.id, f"🔔 Notifikasi sekarang {'ON' if not now else 'OFF'}.", reply_markup=_settings_kb(_settings(supabase, account_id)))
            return True

        if data == f"{PREFIX}_set_reset":
            supabase.table("investasi_transactions").delete().eq("account_id", account_id).execute()
            supabase.table("investasi_watchlist").delete().eq("account_id", account_id).execute()
            supabase.table("investasi_alerts").delete().eq("account_id", account_id).execute()
            bot.send_message(call.message.chat.id, "♻️ Portofolio, watchlist, dan alert di-reset.", reply_markup=_settings_kb(_settings(supabase, account_id)))
            return True

        return False

    except Exception as exc:
        bot.send_message(call.message.chat.id, f"⚠️ Investasi error: {escape(str(exc))}", parse_mode="HTML", reply_markup=_nav_kb())
        return True


def _check_alerts_once(bot, supabase, owner_user_id):
    accounts = (
        supabase.table("investasi_accounts")
        .select("*")
        .eq("telegram_user_id", str(owner_user_id))
        .execute()
    ).data or []

    for acc in accounts:
        account_id = acc["id"]
        chat_id = int(acc.get("telegram_chat_id") or 0)
        if not chat_id:
            continue
        settings = _settings(supabase, account_id)
        if not settings.get("notifications_enabled", True):
            continue

        alerts = _alert_rows(supabase, account_id, only_active=True)
        uniq = sorted({_normalize_symbol(a["symbol"]) for a in alerts})
        qmap = {}

        for sym in uniq:
            try:
                qmap[sym] = _quote(sym)
            except Exception:
                pass

        now = datetime.utcnow()
        for a in alerts:
            sym = _normalize_symbol(a["symbol"])
            q = qmap.get(sym)
            if not q:
                continue

            current = Decimal(str(q["current"]))
            target = _safe_decimal(a["target_price"])
            op = a["operator"]

            hit = (op == ">" and current > target) or (op == "<" and current < target)
            if not hit:
                continue

            last = a.get("last_triggered_at")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00")).replace(tzinfo=None)
                    if (now - last_dt).total_seconds() < ALERT_COOLDOWN_SECONDS:
                        continue
                except Exception:
                    pass

            bot.send_message(
                chat_id,
                (
                    "🔔 <b>Alert Harga Tercapai</b>\n\n"
                    f"<b>{escape(sym)}</b> {escape(op)} {_money(target, settings.get('currency', DEFAULT_CURRENCY), 2)}\n"
                    f"Harga sekarang: <b>{_money(current, settings.get('currency', DEFAULT_CURRENCY), 2)}</b>"
                ),
                parse_mode="HTML",
                reply_markup=_alert_kb(),
            )
            supabase.table("investasi_alerts").update({
                "last_triggered_at": now.isoformat(),
                "last_trigger_price": str(current),
                "updated_at": now.isoformat(),
            }).eq("id", a["id"]).execute()

            supabase.table("investasi_alert_events").insert({
                "alert_id": a["id"],
                "triggered_price": str(current),
                "payload": {"symbol": sym, "operator": op, "target_price": str(target)},
            }).execute()


def start_investasi_alert_watcher(bot, supabase, owner_user_id, interval_sec=180):
    def loop():
        while True:
            try:
                _check_alerts_once(bot, supabase, owner_user_id)
            except Exception as exc:
                print(f"[WARN] investasi alert watcher: {exc}")
            time.sleep(interval_sec)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
