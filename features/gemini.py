import os
import threading
import time
import traceback
from typing import Any, Dict, List

from google import genai
from google.genai import types

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
SYSTEM_PROMPT = os.getenv(
    "GEMINI_SYSTEM_PROMPT",
    "Kamu adalah Nexus-1 Seri 3gH54, jawab dalam Bahasa Indonesia plus ramah dan jelas."
)

# Batas aman supaya chat tidak terlalu lambat.
DEFAULT_HISTORY_LIMIT = 8
MAX_HISTORY_LIMIT = 8


def _clean_text(value: str) -> str:
    return (value or "").strip()


def _is_quota_or_rate_limit_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)

    return (
        status_code == 429
        or "429" in text
        or "resource exhausted" in text
        or "rate limit" in text
        or "quota" in text
        or "please retry" in text
    )


def _extract_response_text(response: Any) -> str:
    """
    Hindari akses response.text kalau SDK mengeluarkan warning thought_signature.
    Ambil hanya part teks yang benar-benar ada.
    """
    parts_text: List[str] = []

    try:
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    parts_text.append(str(part_text))
    except Exception:
        pass

    if parts_text:
        return "\n".join(parts_text).strip()

    # Fallback terakhir. Bisa memunculkan warning, tetapi tetap aman.
    text = getattr(response, "text", None)
    if text:
        return _clean_text(str(text))

    return ""


class GeminiAI:
    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self.keys = [
            os.getenv("GEMINI_KEY", "").strip(),
            os.getenv("GEMINI_KEY_2", "").strip(),
            os.getenv("GEMINI_KEY_3", "").strip(),
            os.getenv("GEMINI_KEY_4", "").strip(),
        ]
        self.keys = [k for k in self.keys if k]

        if not self.keys:
            raise RuntimeError("Minimal 1 GEMINI_KEY harus diisi di environment variables.")

        self.current_key_index = 0
        self.lock = threading.Lock()

    def _get_active_key(self) -> str:
        with self.lock:
            return self.keys[self.current_key_index]

    def _advance_key(self) -> str:
        with self.lock:
            self.current_key_index = (self.current_key_index + 1) % len(self.keys)
            return self.keys[self.current_key_index]

    def _build_client(self):
        return genai.Client(api_key=self._get_active_key())

    def _fetch_history(self, user_id: int, limit: int = DEFAULT_HISTORY_LIMIT) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))

        resp = (
            self.supabase.table("ai_chat_memory")
            .select("role, message_text")
            .eq("user_id", str(user_id))
            .eq("ai_type", "gemini")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        rows = list(reversed(resp.data or []))
        history: List[Dict[str, Any]] = []

        for row in rows:
            role = row.get("role", "user")
            if role == "assistant":
                role = "model"
            elif role not in ("user", "model"):
                role = "user"

            text = _clean_text(row.get("message_text", ""))
            if not text:
                continue

            history.append(
                {
                    "role": role,
                    "parts": [{"text": text}],
                }
            )

        return history

    def _save_message(self, user_id: int, role: str, message_text: str) -> None:
        payload = {
            "user_id": str(user_id),
            "ai_type": "gemini",
            "role": role,
            "message_text": message_text,
        }
        self.supabase.table("ai_chat_memory").insert(payload).execute()

    def ask(self, user_id: int, prompt: str, history_limit: int = DEFAULT_HISTORY_LIMIT) -> str:
        prompt = _clean_text(prompt)
        if not prompt:
            return "Pesan kosong."

        history_limit = max(1, min(int(history_limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))
        history = self._fetch_history(user_id, limit=history_limit)

        # Coba semua key yang ada, pindah otomatis kalau kena 429/quota.
        attempts = max(1, len(self.keys))
        last_error = None

        for attempt in range(attempts):
            client = self._build_client()
            try:
                contents = list(history)
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                )

                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.4,
                    ),
                )

                answer = _extract_response_text(response)
                if not answer:
                    answer = "Maaf, saya belum mendapat jawaban yang jelas."

                # Simpan hanya kalau request sukses.
                self._save_message(user_id, "user", prompt)
                self._save_message(user_id, "assistant", answer)
                return answer

            except Exception as exc:
                last_error = exc

                if _is_quota_or_rate_limit_error(exc):
                    next_key = self._advance_key()
                    # Retry halus, jangan terlalu lama karena user menunggu.
                    time.sleep(min(0.4 * (attempt + 1), 1.2))
                    continue

                # Error non-quota: log ke terminal supaya kelihatan penyebabnya.
                print(f"[GeminiAI] Error non-quota: {exc}")
                print(traceback.format_exc())
                break

        if last_error and _is_quota_or_rate_limit_error(last_error):
            return "Semua key Gemini sedang penuh. Coba lagi sebentar lagi."
        return "Terjadi gangguan saat memproses jawaban."

    def reset_user_memory(self, user_id: int) -> None:
        self.supabase.table("ai_chat_memory").delete().eq("user_id", str(user_id)).execute()
