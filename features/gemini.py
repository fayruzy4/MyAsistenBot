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
    "Kamu adalah asisten yang ringkas, jelas, akurat, dan menjawab dalam Bahasa Indonesia."
)

def _clean_text(value: str) -> str:
    return (value or "").strip()

def _looks_like_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return (
        status_code == 429
        or "429" in text
        or "resource exhausted" in text
        or "rate limit" in text
        or "quota" in text
    )

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

    def _advance_key(self) -> None:
        with self.lock:
            self.current_key_index = (self.current_key_index + 1) % len(self.keys)

    def _build_client(self):
        return genai.Client(api_key=self._get_active_key())

    def _fetch_history(self, user_id: int, limit: int = 16) -> List[Dict[str, Any]]:
        resp = (
            self.supabase.table("ai_chat_memory")
            .select("role, message_text")
            .eq("user_id", str(user_id))
            .eq("ai_type", "gemini")
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        rows = resp.data or []

        history = []
        for row in rows:
            role = row.get("role", "user")
            if role == "assistant":
                role = "model"
            elif role not in ("user", "model"):
                role = "user"

            text = _clean_text(row.get("message_text", ""))
            if not text:
                continue

            history.append({
                "role": role,
                "parts": [{"text": text}],
            })
        return history

    def _save_message(self, user_id: int, role: str, message_text: str) -> None:
        payload = {
            "user_id": str(user_id),
            "ai_type": "gemini",
            "role": role,
            "message_text": message_text,
        }
        self.supabase.table("ai_chat_memory").insert(payload).execute()

    def ask(self, user_id: int, prompt: str, history_limit: int = 16) -> str:
        prompt = _clean_text(prompt)
        if not prompt:
            return "Pesan kosong."

        history = self._fetch_history(user_id, limit=history_limit)
        attempts = max(1, len(self.keys))
        last_error = None

        for _ in range(attempts):
            client = self._build_client()
            try:
                contents = list(history)
                contents.append({
                    "role": "user",
                    "parts": [{"text": prompt}],
                })

                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.4,
                    ),
                )

                answer = getattr(response, "text", None) or str(response)
                answer = _clean_text(answer) or "Maaf, saya belum mendapat jawaban yang jelas."

                self._save_message(user_id, "user", prompt)
                self._save_message(user_id, "assistant", answer)
                return answer

            except Exception as exc:
                last_error = exc
                if _looks_like_rate_limit(exc):
                    # Kena 429 / quota / resource exhausted -> pindah key lalu retry.
                    self._advance_key()
                    time.sleep(0.2)
                    continue

                print(f"[GeminiAI] Error non-rate-limit: {exc}")
                print(traceback.format_exc())
                break

        if last_error:
            return "Sistem Gemini sedang padat. Coba lagi sebentar lagi."
        return "Terjadi gangguan saat memproses jawaban."

    def reset_user_memory(self, user_id: int) -> None:
        self.supabase.table("ai_chat_memory").delete().eq("user_id", str(user_id)).execute()
