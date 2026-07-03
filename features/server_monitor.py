from __future__ import annotations

import datetime as _dt
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from html import escape
from typing import Any, Dict, List, Optional, Tuple

import psutil
import requests
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


TABLE_NAME = "server_monitor_settings"
SERVICE_NAME = os.getenv("BOT_SERVICE_NAME", "myasistenbot").strip() or "myasistenbot"
OWNER_CHAT_ID_ENV = os.getenv("OWNER_CHAT_ID", "").strip()
DEFAULT_TIMEZONE = os.getenv("MONITOR_TIMEZONE", "Asia/Jakarta").strip() or "Asia/Jakarta"
DEFAULT_INTERVAL_MINUTES = int(os.getenv("MONITOR_INTERVAL_MINUTES", "5") or 5)
THREAD_SLEEP_MIN_SECONDS = 60

_WATCHER_STARTED = False
_STATE_LOCK = threading.Lock()

# Runtime-only state to avoid alert spam
_RUNTIME_ALERT_STATE: Dict[str, Any] = {
    "cpu_high": False,
    "ram_high": False,
    "disk_high": False,
    "bot_down": False,
    "internet_down": False,
}


def _resolve_owner_chat_id(fallback: Optional[int] = None) -> Optional[int]:
    if OWNER_CHAT_ID_ENV:
        try:
            return int(OWNER_CHAT_ID_ENV)
        except Exception:
            pass
    if fallback is not None:
        return int(fallback)
    return None


def _run_cmd(args: List[str], timeout: int = 8) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return out if out else err
    except Exception as exc:
        return f"ERROR: {exc}"


def _run_shell(command: str, timeout: int = 8) -> str:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            executable="/bin/bash",
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return out if out else err
    except Exception as exc:
        return f"ERROR: {exc}"


def _format_bytes(num: float) -> str:
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num) < step:
            return f"{num:,.2f} {unit}".replace(",", ".")
        num /= step
    return f"{num:,.2f} EB".replace(",", ".")


def _format_percent(value: float) -> str:
    return f"{value:.1f}%"


def _format_uptime(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days} hari")
    if hours:
        parts.append(f"{hours} jam")
    if minutes or not parts:
        parts.append(f"{minutes} menit")
    return " ".join(parts)


def _progress_bar(percent: float, length: int = 20) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = int(round((percent / 100.0) * length))
    return "█" * filled + "░" * (length - filled)


def _public_ip() -> str:
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=5)
        if resp.ok:
            data = resp.json()
            return data.get("ip") or "-"
    except Exception:
        pass
    try:
        resp = requests.get("https://ifconfig.me/ip", timeout=5)
        if resp.ok:
            ip = (resp.text or "").strip()
            return ip or "-"
    except Exception:
        pass
    return "-"


def _local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "-"


def _ping_host(host: str = "1.1.1.1") -> Optional[float]:
    out = _run_cmd(["ping", "-c", "1", "-W", "1", host], timeout=3)
    if not out or "time=" not in out.lower():
        return None
    m = re.search(r"time[=<]?\s*([\d.]+)\s*ms", out, re.I)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def _read_os_release() -> Dict[str, str]:
    data: Dict[str, str] = {}
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip().strip('"')
                data[key] = value
    except Exception:
        pass
    return data


def _ubuntu_version() -> str:
    osr = _read_os_release()
    pretty = osr.get("PRETTY_NAME") or osr.get("NAME") or platform.platform()
    return pretty


def _timezone_now(tz_name: str) -> str:
    try:
        if ZoneInfo is None:
            return _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        tz = ZoneInfo(tz_name)
        return _dt.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _server_timezone() -> str:
    try:
        out = _run_cmd(["timedatectl", "show", "-p", "Timezone", "--value"], timeout=5)
        if out and "ERROR:" not in out:
            return out.strip()
    except Exception:
        pass
    return DEFAULT_TIMEZONE


def _load_settings_row(supabase, owner_chat_id: int) -> Dict[str, Any]:
    defaults = {
        "owner_chat_id": owner_chat_id,
        "cpu_alert": True,
        "ram_alert": True,
        "disk_alert": True,
        "bot_alert": True,
        "reboot_alert": True,
        "ssh_alert": True,
        "fail2ban_alert": True,
        "internet_alert": True,
        "interval_minutes": DEFAULT_INTERVAL_MINUTES,
        "timezone": DEFAULT_TIMEZONE,
        "last_boot_id": None,
        "last_ssh_login_line": None,
        "last_banned_count": 0,
    }
    try:
        res = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("owner_chat_id", owner_chat_id)
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0] or {}
        defaults.update(row)
    except Exception:
        pass
    return defaults


def _upsert_settings_row(supabase, settings: Dict[str, Any]) -> None:
    try:
        payload = dict(settings)
        payload["updated_at"] = _dt.datetime.utcnow().isoformat()
        supabase.table(TABLE_NAME).upsert(payload, on_conflict="owner_chat_id").execute()
    except Exception:
        # Jangan mematikan bot hanya karena tabel setting belum siap
        pass


def _ensure_owner_chat_id(chat_id: int) -> int:
    owner = _resolve_owner_chat_id(chat_id)
    return int(owner if owner is not None else chat_id)


def _safe_send(bot, chat_id: int, text: str, reply_markup=None):
    try:
        bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        try:
            bot.send_message(chat_id, escape(text), reply_markup=reply_markup)
        except Exception:
            pass


def _safe_edit(bot, message, text: str, reply_markup=None):
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


def build_server_monitor_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📊 Status Server", callback_data="server_monitor_status"),
        InlineKeyboardButton("🤖 Status Bot", callback_data="server_monitor_bot"),
    )
    kb.row(
        InlineKeyboardButton("📈 Resource Live", callback_data="server_monitor_resource"),
        InlineKeyboardButton("🛡️ Keamanan", callback_data="server_monitor_security"),
    )
    kb.row(
        InlineKeyboardButton("🌐 Jaringan", callback_data="server_monitor_network"),
        InlineKeyboardButton("📜 Log Error", callback_data="server_monitor_log"),
    )
    kb.row(
        InlineKeyboardButton("🔔 Notifikasi", callback_data="server_monitor_notify_menu"),
        InlineKeyboardButton("⚙️ Pengaturan", callback_data="server_monitor_settings_menu"),
    )
    kb.row(
        InlineKeyboardButton("🔄 Restart Bot", callback_data="server_monitor_restart"),
    )
    kb.row(
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def build_server_monitor_back_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="server_monitor_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def build_server_monitor_confirm_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Ya", callback_data="server_monitor_restart_yes"),
        InlineKeyboardButton("❌ Batal", callback_data="server_monitor_restart_no"),
    )
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="server_monitor_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def build_server_monitor_notify_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    def state(val: Any) -> str:
        return "ON" if bool(val) else "OFF"

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(f"CPU: {state(settings.get('cpu_alert'))}", callback_data="server_monitor_notify_toggle:cpu"),
        InlineKeyboardButton(f"RAM: {state(settings.get('ram_alert'))}", callback_data="server_monitor_notify_toggle:ram"),
    )
    kb.row(
        InlineKeyboardButton(f"Disk: {state(settings.get('disk_alert'))}", callback_data="server_monitor_notify_toggle:disk"),
        InlineKeyboardButton(f"Bot: {state(settings.get('bot_alert'))}", callback_data="server_monitor_notify_toggle:bot"),
    )
    kb.row(
        InlineKeyboardButton(f"Reboot: {state(settings.get('reboot_alert'))}", callback_data="server_monitor_notify_toggle:reboot"),
        InlineKeyboardButton(f"SSH: {state(settings.get('ssh_alert'))}", callback_data="server_monitor_notify_toggle:ssh"),
    )
    kb.row(
        InlineKeyboardButton(f"Fail2Ban: {state(settings.get('fail2ban_alert'))}", callback_data="server_monitor_notify_toggle:fail2ban"),
        InlineKeyboardButton(f"Internet: {state(settings.get('internet_alert'))}", callback_data="server_monitor_notify_toggle:internet"),
    )
    kb.row(
        InlineKeyboardButton("✅ Semua ON", callback_data="server_monitor_notify_all_on"),
        InlineKeyboardButton("❌ Semua OFF", callback_data="server_monitor_notify_all_off"),
    )
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="server_monitor_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def build_server_monitor_settings_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⏱ Interval Monitoring", callback_data="server_monitor_interval_menu"),
        InlineKeyboardButton("🌍 Zona Waktu", callback_data="server_monitor_timezone_menu"),
    )
    kb.row(
        InlineKeyboardButton("🔄 Refresh", callback_data="server_monitor_settings_refresh"),
    )
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="server_monitor_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def build_server_monitor_interval_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    for minutes in [1, 5, 10, 15, 30]:
        kb.row(InlineKeyboardButton(f"{minutes} menit", callback_data=f"server_monitor_interval_set:{minutes}"))
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="server_monitor_settings_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def build_server_monitor_timezone_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    for tz in ["Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura", "UTC"]:
        kb.row(InlineKeyboardButton(tz, callback_data=f"server_monitor_timezone_set:{tz}"))
    kb.row(
        InlineKeyboardButton("🔙 Kembali", callback_data="server_monitor_settings_menu"),
        InlineKeyboardButton("🏠 Dashboard", callback_data="back_dashboard"),
    )
    return kb


def _service_show(service_name: str) -> Dict[str, str]:
    out = _run_cmd(
        [
            "systemctl",
            "show",
            service_name,
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "MainPID",
            "-p",
            "ExecMainStartTimestamp",
            "-p",
            "UnitFileState",
        ],
        timeout=8,
    )
    result: Dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _service_status(service_name: str) -> Dict[str, Any]:
    data = _service_show(service_name)
    active_state = data.get("ActiveState", "-")
    sub_state = data.get("SubState", "-")
    pid = 0
    try:
        pid = int(data.get("MainPID") or 0)
    except Exception:
        pid = 0

    proc_cpu = 0.0
    proc_rss = 0
    if pid > 0:
        try:
            p = psutil.Process(pid)
            proc_cpu = p.cpu_percent(interval=0.1)
            proc_rss = p.memory_info().rss
        except Exception:
            proc_cpu = 0.0
            proc_rss = 0

    restart_last = data.get("ExecMainStartTimestamp", "-") or "-"
    enabled_state = data.get("UnitFileState", "-") or "-"

    return {
        "active_state": active_state,
        "sub_state": sub_state,
        "pid": pid,
        "cpu": proc_cpu,
        "rss": proc_rss,
        "restart_last": restart_last,
        "enabled_state": enabled_state,
    }


def _ufw_status() -> str:
    out = _run_cmd(["ufw", "status"], timeout=6)
    if "Status: active" in out:
        return "Aktif"
    if "Status: inactive" in out:
        return "Tidak aktif"
    return out or "-"


def _fail2ban_status() -> Dict[str, Any]:
    out = _run_cmd(["fail2ban-client", "status", "sshd"], timeout=8)
    banned = 0
    m = re.search(r"Currently banned:\s*(\d+)", out, re.I)
    if m:
        try:
            banned = int(m.group(1))
        except Exception:
            banned = 0
    else:
        m2 = re.search(r"Banned IP list:\s*(.*)", out, re.I | re.S)
        if m2:
            raw = (m2.group(1) or "").strip()
            if raw and raw.lower() != "n/a":
                banned = len([x for x in re.split(r"[,\s]+", raw) if x])
    return {"raw": out, "banned": banned}


def _sshd_effective_config() -> Dict[str, str]:
    out = _run_cmd(["sshd", "-T"], timeout=8)
    cfg: Dict[str, str] = {}
    for line in out.splitlines():
        if " " in line:
            key, value = line.split(None, 1)
            cfg[key.strip().lower()] = value.strip()
    return cfg


def _last_ssh_login() -> str:
    out = _run_shell("last -a | head -1", timeout=6)
    return out.strip() if out.strip() else "-"


def _system_logs_tail(service_name: str, n: int = 20) -> str:
    out = _run_cmd(["journalctl", "-u", service_name, "-n", str(n), "--no-pager", "-o", "short-iso"], timeout=10)
    return out.strip() if out.strip() else "Tidak ada log."


def _uptime_seconds() -> float:
    try:
        return float(time.time() - psutil.boot_time())
    except Exception:
        return 0.0


def _load_average() -> str:
    try:
        la = os.getloadavg()
        return f"{la[0]:.2f}, {la[1]:.2f}, {la[2]:.2f}"
    except Exception:
        return "-"


def _resource_snapshot() -> Dict[str, Any]:
    cpu = psutil.cpu_percent(interval=0.2)
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    swap = psutil.swap_memory()
    net = psutil.net_io_counters()
    return {
        "cpu": cpu,
        "ram_percent": vm.percent,
        "ram_used": vm.used,
        "ram_total": vm.total,
        "disk_percent": disk.percent,
        "disk_used": disk.used,
        "disk_total": disk.total,
        "swap_percent": swap.percent,
        "swap_used": swap.used,
        "swap_total": swap.total,
        "net_sent": net.bytes_sent,
        "net_recv": net.bytes_recv,
    }


def _status_server_text(owner_chat_id: int, settings: Dict[str, Any]) -> str:
    cpu = psutil.cpu_percent(interval=0.2)
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    swap = psutil.swap_memory()
    uptime = _uptime_seconds()
    loadavg = _load_average()
    public_ip = _public_ip()
    local_ip = _local_ip()
    tz_name = str(settings.get("timezone") or DEFAULT_TIMEZONE)
    now_text = _timezone_now(tz_name)

    return (
        "🖥️ <b>Status Server</b>\n\n"
        f"<b>Hostname:</b> {escape(platform.node() or '-')}\n"
        f"<b>OS:</b> {escape(platform.system() or '-')}\n"
        f"<b>Versi Ubuntu:</b> {escape(_ubuntu_version())}\n"
        f"<b>Python Version:</b> {escape(platform.python_version())}\n"
        f"<b>CPU:</b> {cpu:.1f}%\n"
        f"<b>RAM:</b> {vm.percent:.1f}% ({_format_bytes(vm.used)} / {_format_bytes(vm.total)})\n"
        f"<b>Disk:</b> {disk.percent:.1f}% ({_format_bytes(disk.used)} / {_format_bytes(disk.total)})\n"
        f"<b>Swap:</b> {swap.percent:.1f}% ({_format_bytes(swap.used)} / {_format_bytes(swap.total)})\n"
        f"<b>Uptime:</b> {_format_uptime(uptime)}\n"
        f"<b>Load Average:</b> {loadavg}\n"
        f"<b>Public IP:</b> {escape(public_ip)}\n"
        f"<b>Local IP:</b> {escape(local_ip)}\n"
        f"<b>Waktu Server:</b> {escape(now_text)}\n"
        f"<b>Timezone:</b> {escape(tz_name)}"
    )


def _status_bot_text() -> str:
    stat = _service_status(SERVICE_NAME)
    state = "🟢 Running" if stat["active_state"] == "active" else "🔴 Stopped"
    return (
        "🤖 <b>Status Bot</b>\n\n"
        f"<b>Service:</b> {escape(SERVICE_NAME)}\n"
        f"<b>Status:</b> {state}\n"
        f"<b>SubState:</b> {escape(str(stat['sub_state']))}\n"
        f"<b>PID:</b> {stat['pid']}\n"
        f"<b>CPU:</b> {stat['cpu']:.2f}%\n"
        f"<b>RAM:</b> {_format_bytes(stat['rss'])}\n"
        f"<b>Restart Terakhir:</b> {escape(str(stat['restart_last']))}\n"
        f"<b>Unit State:</b> {escape(str(stat['enabled_state']))}"
    )


def _resource_live_text() -> str:
    snap = _resource_snapshot()
    net_up = _format_bytes(snap["net_sent"])
    net_down = _format_bytes(snap["net_recv"])

    return (
        "📈 <b>Resource Live</b>\n\n"
        f"<b>CPU</b>\n{_progress_bar(snap['cpu'])} {snap['cpu']:.1f}%\n\n"
        f"<b>RAM</b>\n{_progress_bar(snap['ram_percent'])} {snap['ram_percent']:.1f}%\n\n"
        f"<b>Disk</b>\n{_progress_bar(snap['disk_percent'])} {snap['disk_percent']:.1f}%\n\n"
        f"<b>Swap</b>\n{_progress_bar(snap['swap_percent'])} {snap['swap_percent']:.1f}%\n\n"
        f"<b>Upload</b>: {net_up}\n"
        f"<b>Download</b>: {net_down}"
    )


def _security_text() -> str:
    ufw = _ufw_status()
    f2b = _fail2ban_status()
    ssh_cfg = _sshd_effective_config()
    port = ssh_cfg.get("port", "22")
    permit_root = ssh_cfg.get("permitrootlogin", "-")
    ssh_last = _last_ssh_login()

    return (
        "🛡️ <b>Keamanan</b>\n\n"
        f"<b>Status UFW:</b> {escape(str(ufw))}\n"
        f"<b>Status Fail2Ban:</b> Aktif\n"
        f"<b>Jumlah IP Diblokir:</b> {f2b['banned']}\n"
        f"<b>Login SSH Terakhir:</b>\n<pre>{escape(ssh_last)}</pre>\n"
        f"<b>Port SSH:</b> {escape(str(port))}\n"
        f"<b>Root Login:</b> {escape(str(permit_root))}"
    )


def _network_text() -> str:
    pub = _public_ip()
    local = _local_ip()
    latency = _ping_host("1.1.1.1")
    dns_ip = "-"
    internet = "🔴 Offline"

    try:
        dns_ip = socket.gethostbyname("google.com")
        internet = "🟢 Online"
    except Exception:
        dns_ip = "-"

    if latency is None:
        latency_text = "-"
    else:
        latency_text = f"{latency:.2f} ms"

    return (
        "🌐 <b>Jaringan</b>\n\n"
        f"<b>IP Public:</b> {escape(pub)}\n"
        f"<b>IP Local:</b> {escape(local)}\n"
        f"<b>Latency:</b> {latency_text}\n"
        f"<b>DNS:</b> {escape(dns_ip)}\n"
        f"<b>Internet:</b> {internet}"
    )


def _error_log_text() -> str:
    tail = _system_logs_tail(SERVICE_NAME, n=20)
    if len(tail) > 3200:
        tail = tail[-3200:]
    return (
        "📜 <b>Log Error</b>\n\n"
        f"<pre>{escape(tail)}</pre>"
    )


def _notify_text(settings: Dict[str, Any]) -> str:
    def onoff(v: Any) -> str:
        return "✅ ON" if bool(v) else "❌ OFF"

    return (
        "🔔 <b>Notifikasi</b>\n\n"
        f"CPU tinggi      : {onoff(settings.get('cpu_alert'))}\n"
        f"RAM tinggi      : {onoff(settings.get('ram_alert'))}\n"
        f"Disk penuh      : {onoff(settings.get('disk_alert'))}\n"
        f"Bot mati        : {onoff(settings.get('bot_alert'))}\n"
        f"Restart VPS     : {onoff(settings.get('reboot_alert'))}\n"
        f"Login SSH baru  : {onoff(settings.get('ssh_alert'))}\n"
        f"Fail2Ban blokir  : {onoff(settings.get('fail2ban_alert'))}\n"
        f"Internet putus   : {onoff(settings.get('internet_alert'))}"
    )


def _settings_text(settings: Dict[str, Any]) -> str:
    return (
        "⚙️ <b>Pengaturan Monitor Server</b>\n\n"
        f"<b>Interval Monitoring:</b> {int(settings.get('interval_minutes') or DEFAULT_INTERVAL_MINUTES)} menit\n"
        f"<b>Zona Waktu:</b> {escape(str(settings.get('timezone') or DEFAULT_TIMEZONE))}\n"
        f"<b>Service Bot:</b> {escape(SERVICE_NAME)}\n"
        f"<b>Owner Chat ID:</b> {settings.get('owner_chat_id')}\n"
    )


def show_server_monitor_menu(bot, message) -> None:
    text = (
        "🖥️ <b>Monitor Server</b>\n\n"
        "Selamat datang di pusat monitoring VPS.\n\n"
        "Silakan pilih menu."
    )
    _safe_edit(bot, message, text, build_server_monitor_keyboard())


def show_server_monitor_status(bot, message, supabase) -> None:
    owner_chat_id = _ensure_owner_chat_id(message.chat.id)
    settings = _load_settings_row(supabase, owner_chat_id)
    _safe_edit(bot, message, _status_server_text(owner_chat_id, settings), build_server_monitor_back_keyboard())


def show_server_monitor_bot(bot, message, supabase) -> None:
    _safe_edit(bot, message, _status_bot_text(), build_server_monitor_back_keyboard())


def show_server_monitor_resource(bot, message, supabase) -> None:
    _safe_edit(bot, message, _resource_live_text(), build_server_monitor_back_keyboard())


def show_server_monitor_security(bot, message, supabase) -> None:
    _safe_edit(bot, message, _security_text(), build_server_monitor_back_keyboard())


def show_server_monitor_network(bot, message, supabase) -> None:
    _safe_edit(bot, message, _network_text(), build_server_monitor_back_keyboard())


def show_server_monitor_logs(bot, message, supabase) -> None:
    _safe_edit(bot, message, _error_log_text(), build_server_monitor_back_keyboard())


def show_server_monitor_notify_menu(bot, message, supabase) -> None:
    owner_chat_id = _ensure_owner_chat_id(message.chat.id)
    settings = _load_settings_row(supabase, owner_chat_id)
    _safe_edit(bot, message, _notify_text(settings), build_server_monitor_notify_keyboard(settings))


def show_server_monitor_settings_menu(bot, message, supabase) -> None:
    owner_chat_id = _ensure_owner_chat_id(message.chat.id)
    settings = _load_settings_row(supabase, owner_chat_id)
    _safe_edit(bot, message, _settings_text(settings), build_server_monitor_settings_keyboard(settings))


def show_server_monitor_restart_confirm(bot, message, supabase) -> None:
    text = (
        "⚠️ <b>Restart MyAsistenBot?</b>\n\n"
        f"Service: <code>{escape(SERVICE_NAME)}</code>\n\n"
        "Bot akan berhenti sebentar lalu hidup kembali."
    )
    _safe_edit(bot, message, text, build_server_monitor_confirm_keyboard())


def _toggle_setting(settings: Dict[str, Any], key: str) -> Dict[str, Any]:
    new_settings = dict(settings)
    new_settings[key] = not bool(new_settings.get(key))
    return new_settings


def _set_all_notification_flags(settings: Dict[str, Any], value: bool) -> Dict[str, Any]:
    new_settings = dict(settings)
    for key in [
        "cpu_alert",
        "ram_alert",
        "disk_alert",
        "bot_alert",
        "reboot_alert",
        "ssh_alert",
        "fail2ban_alert",
        "internet_alert",
    ]:
        new_settings[key] = value
    return new_settings


def _restart_service_async(bot, chat_id: int) -> None:
    def worker():
        try:
            time.sleep(1.0)
            subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME], check=False)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def _compose_alert_message(title: str, body: str) -> str:
    return f"{title}\n\n{body}"


def _check_and_notify(bot, supabase, owner_chat_id: int) -> None:
    settings = _load_settings_row(supabase, owner_chat_id)

    # Preserve latest state fields in row even if not using them for toggles
    current_boot_id = _run_shell("cat /proc/sys/kernel/random/boot_id", timeout=3).strip()
    current_ssh_login = _last_ssh_login()
    current_banned_count = _fail2ban_status()["banned"]

    # Alert settings
    cpu_enabled = bool(settings.get("cpu_alert"))
    ram_enabled = bool(settings.get("ram_alert"))
    disk_enabled = bool(settings.get("disk_alert"))
    bot_enabled = bool(settings.get("bot_alert"))
    reboot_enabled = bool(settings.get("reboot_alert"))
    ssh_enabled = bool(settings.get("ssh_alert"))
    fail2ban_enabled = bool(settings.get("fail2ban_alert"))
    internet_enabled = bool(settings.get("internet_alert"))

    snap = _resource_snapshot()
    cpu_high = snap["cpu"] >= 90.0
    ram_high = snap["ram_percent"] >= 90.0
    disk_high = snap["disk_percent"] >= 90.0
    internet_ok = _ping_host("1.1.1.1") is not None

    bot_stat = _service_status(SERVICE_NAME)
    bot_is_running = bot_stat["active_state"] == "active"

    # Reboot detection by comparing persisted boot id
    last_boot_id = settings.get("last_boot_id")
    if reboot_enabled and last_boot_id and current_boot_id and str(last_boot_id) != str(current_boot_id):
        _safe_send(
            bot,
            owner_chat_id,
            _compose_alert_message(
                "🔄 <b>Server Reboot Terdeteksi</b>",
                "Boot ID berubah. VPS kemungkinan baru saja restart.",
            ),
        )

    # SSH login change
    last_ssh_login_line = settings.get("last_ssh_login_line")
    if ssh_enabled and current_ssh_login and current_ssh_login != "-" and current_ssh_login != last_ssh_login_line:
        _safe_send(
            bot,
            owner_chat_id,
            _compose_alert_message(
                "🚨 <b>Login SSH Baru</b>",
                f"<pre>{escape(current_ssh_login)}</pre>",
            ),
        )

    # Fail2Ban banned count change
    last_banned_count = int(settings.get("last_banned_count") or 0)
    if fail2ban_enabled and current_banned_count > last_banned_count:
        _safe_send(
            bot,
            owner_chat_id,
            _compose_alert_message(
                "🛡️ <b>Fail2Ban Memblokir IP</b>",
                f"Jumlah IP diblokir bertambah menjadi {current_banned_count}.",
            ),
        )

    # Resource alerts with local anti-spam state
    if cpu_enabled:
        if cpu_high and not _RUNTIME_ALERT_STATE["cpu_high"]:
            _safe_send(
                bot,
                owner_chat_id,
                _compose_alert_message(
                    "⚠️ <b>CPU Tinggi</b>",
                    f"CPU saat ini {snap['cpu']:.1f}%.",
                ),
            )
        _RUNTIME_ALERT_STATE["cpu_high"] = cpu_high

    if ram_enabled:
        if ram_high and not _RUNTIME_ALERT_STATE["ram_high"]:
            _safe_send(
                bot,
                owner_chat_id,
                _compose_alert_message(
                    "⚠️ <b>RAM Tinggi</b>",
                    f"RAM saat ini {snap['ram_percent']:.1f}% ({_format_bytes(snap['ram_used'])} / {_format_bytes(snap['ram_total'])}).",
                ),
            )
        _RUNTIME_ALERT_STATE["ram_high"] = ram_high

    if disk_enabled:
        if disk_high and not _RUNTIME_ALERT_STATE["disk_high"]:
            _safe_send(
                bot,
                owner_chat_id,
                _compose_alert_message(
                    "⚠️ <b>Disk Hampir Penuh</b>",
                    f"Disk saat ini {snap['disk_percent']:.1f}% ({_format_bytes(snap['disk_used'])} / {_format_bytes(snap['disk_total'])}).",
                ),
            )
        _RUNTIME_ALERT_STATE["disk_high"] = disk_high

    if bot_enabled:
        if not bot_is_running and not _RUNTIME_ALERT_STATE["bot_down"]:
            _safe_send(
                bot,
                owner_chat_id,
                _compose_alert_message(
                    "🔴 <b>Bot Mati</b>",
                    f"Service <code>{escape(SERVICE_NAME)}</code> tidak aktif.",
                ),
            )
        _RUNTIME_ALERT_STATE["bot_down"] = not bot_is_running

    if internet_enabled:
        if not internet_ok and not _RUNTIME_ALERT_STATE["internet_down"]:
            _safe_send(
                bot,
                owner_chat_id,
                _compose_alert_message(
                    "🌐 <b>Internet Putus</b>",
                    "Koneksi internet VPS tidak terdeteksi.",
                ),
            )
        _RUNTIME_ALERT_STATE["internet_down"] = not internet_ok

    # Save latest state
    settings["last_boot_id"] = current_boot_id or settings.get("last_boot_id")
    settings["last_ssh_login_line"] = current_ssh_login or settings.get("last_ssh_login_line")
    settings["last_banned_count"] = current_banned_count
    _upsert_settings_row(supabase, settings)


def _watcher_loop(bot, supabase) -> None:
    while True:
        try:
            owner_chat_id = _resolve_owner_chat_id()
            if owner_chat_id is None:
                time.sleep(THREAD_SLEEP_MIN_SECONDS)
                continue

            settings = _load_settings_row(supabase, owner_chat_id)
            interval_minutes = int(settings.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES)
            interval_seconds = max(THREAD_SLEEP_MIN_SECONDS, interval_minutes * 60)

            _check_and_notify(bot, supabase, owner_chat_id)
            time.sleep(interval_seconds)
        except Exception as exc:
            print(f"[server_monitor._watcher_loop] {exc}")
            print(traceback.format_exc())
            time.sleep(THREAD_SLEEP_MIN_SECONDS)


def start_server_monitor_watcher(bot, supabase) -> None:
    global _WATCHER_STARTED
    with _STATE_LOCK:
        if _WATCHER_STARTED:
            return
        owner_chat_id = _resolve_owner_chat_id()
        if owner_chat_id is None:
            return
        _WATCHER_STARTED = True
        t = threading.Thread(target=_watcher_loop, args=(bot, supabase), daemon=True)
        t.start()


def process_server_monitor_callback(bot, call, supabase, pending_actions: Dict[str, Any], show_dashboard_callback) -> bool:
    data = call.data or ""
    user_id = call.from_user.id
    owner_chat_id = _ensure_owner_chat_id(call.message.chat.id)

    try:
        if data == "server_monitor_menu":
            show_server_monitor_menu(bot, call.message)
            return True

        if data == "server_monitor_status":
            show_server_monitor_status(bot, call.message, supabase)
            return True

        if data == "server_monitor_bot":
            show_server_monitor_bot(bot, call.message, supabase)
            return True

        if data == "server_monitor_resource":
            show_server_monitor_resource(bot, call.message, supabase)
            return True

        if data == "server_monitor_security":
            show_server_monitor_security(bot, call.message, supabase)
            return True

        if data == "server_monitor_network":
            show_server_monitor_network(bot, call.message, supabase)
            return True

        if data == "server_monitor_log":
            show_server_monitor_logs(bot, call.message, supabase)
            return True

        if data == "server_monitor_notify_menu":
            show_server_monitor_notify_menu(bot, call.message, supabase)
            return True

        if data == "server_monitor_settings_menu":
            show_server_monitor_settings_menu(bot, call.message, supabase)
            return True

        if data == "server_monitor_restart":
            show_server_monitor_restart_confirm(bot, call.message, supabase)
            return True

        if data == "server_monitor_restart_no":
            show_server_monitor_menu(bot, call.message)
            return True

        if data == "server_monitor_restart_yes":
            _restart_service_async(bot, call.message.chat.id)
            bot.send_message(
                call.message.chat.id,
                f"🔄 Restart <code>{escape(SERVICE_NAME)}</code> dijalankan.",
                reply_markup=build_server_monitor_back_keyboard(),
            )
            return True

        if data == "server_monitor_notify_all_on":
            settings = _load_settings_row(supabase, owner_chat_id)
            settings = _set_all_notification_flags(settings, True)
            _upsert_settings_row(supabase, settings)
            show_server_monitor_notify_menu(bot, call.message, supabase)
            return True

        if data == "server_monitor_notify_all_off":
            settings = _load_settings_row(supabase, owner_chat_id)
            settings = _set_all_notification_flags(settings, False)
            _upsert_settings_row(supabase, settings)
            show_server_monitor_notify_menu(bot, call.message, supabase)
            return True

        if data.startswith("server_monitor_notify_toggle:"):
            key = data.split(":", 1)[1].strip()
            mapping = {
                "cpu": "cpu_alert",
                "ram": "ram_alert",
                "disk": "disk_alert",
                "bot": "bot_alert",
                "reboot": "reboot_alert",
                "ssh": "ssh_alert",
                "fail2ban": "fail2ban_alert",
                "internet": "internet_alert",
            }
            if key in mapping:
                settings = _load_settings_row(supabase, owner_chat_id)
                settings = _toggle_setting(settings, mapping[key])
                _upsert_settings_row(supabase, settings)
            show_server_monitor_notify_menu(bot, call.message, supabase)
            return True

        if data == "server_monitor_interval_menu":
            settings = _load_settings_row(supabase, owner_chat_id)
            _safe_edit(bot, call.message, _settings_text(settings), build_server_monitor_interval_keyboard(settings))
            return True

        if data.startswith("server_monitor_interval_set:"):
            minutes = int(data.split(":", 1)[1])
            settings = _load_settings_row(supabase, owner_chat_id)
            settings["interval_minutes"] = minutes
            _upsert_settings_row(supabase, settings)
            show_server_monitor_settings_menu(bot, call.message, supabase)
            return True

        if data == "server_monitor_timezone_menu":
            settings = _load_settings_row(supabase, owner_chat_id)
            _safe_edit(bot, call.message, _settings_text(settings), build_server_monitor_timezone_keyboard(settings))
            return True

        if data.startswith("server_monitor_timezone_set:"):
            tz = data.split(":", 1)[1]
            settings = _load_settings_row(supabase, owner_chat_id)
            settings["timezone"] = tz
            _upsert_settings_row(supabase, settings)
            show_server_monitor_settings_menu(bot, call.message, supabase)
            return True

        if data == "server_monitor_settings_refresh":
            show_server_monitor_settings_menu(bot, call.message, supabase)
            return True

        if data.startswith("server_monitor_"):
            _safe_send(bot, call.message.chat.id, "Menu monitor server sedang diproses.", build_server_monitor_back_keyboard())
            return True

        return False

    except Exception as exc:
        print(f"[server_monitor.process_server_monitor_callback] {exc}")
        print(traceback.format_exc())
        _safe_send(bot, call.message.chat.id, "Gagal memproses menu monitor server.", build_server_monitor_back_keyboard())
        return True
