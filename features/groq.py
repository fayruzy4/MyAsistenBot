import os
import tempfile
import traceback

from groq import Groq

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
SYSTEM_PROMPT = os.getenv(
    "GROQ_SYSTEM_PROMPT",
    "Kamu adalah Nexus-2, jawab harus minimalis tapi jelas dan ramah, dalam Bahasa Indonesia."
)


def _clean_text(value: str) -> str:
    return (value or "").strip()


class GroqAI:
    def __init__(self, supabase_client, bot):
        self.supabase = supabase_client
        self.bot = bot
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY belum diisi di environment variables.")
        self.client = Groq(api_key=api_key)

    def _fetch_history(self, user_id: int, limit: int = 16):
        resp = (
            self.supabase.table("ai_chat_memory")
            .select("role, message_text")
            .eq("user_id", str(user_id))
            .eq("ai_type", "groq")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = list(reversed(resp.data or []))

        messages = []
        for row in rows:
            role = row.get("role", "user")
            if role not in ("user", "assistant"):
                role = "user"
            text = _clean_text(row.get("message_text", ""))
            if not text:
                continue
            messages.append({"role": role, "content": text})
        return messages

    def _save_message(self, user_id: int, role: str, message_text: str) -> None:
        payload = {
            "user_id": str(user_id),
            "ai_type": "groq",
            "role": role,
            "message_text": message_text,
        }
        self.supabase.table("ai_chat_memory").insert(payload).execute()

    def _download_telegram_voice_to_tempfile(self, file_id: str) -> str:
        file_info = self.bot.get_file(file_id)
        file_bytes = self.bot.download_file(file_info.file_path)

        # Selalu pakai suffix .ogg supaya Groq mengenali voice Telegram sebagai audio valid.
        fd, temp_path = tempfile.mkstemp(prefix="tg_voice_", suffix=".ogg")
        os.close(fd)

        with open(temp_path, "wb") as f:
            f.write(file_bytes)

        return temp_path

    def transcribe_voice(self, file_id: str) -> str:
        temp_path = None
        try:
            temp_path = self._download_telegram_voice_to_tempfile(file_id)
            with open(temp_path, "rb") as audio_file:
                transcription = self.client.audio.transcriptions.create(
                    file=audio_file,
                    model=WHISPER_MODEL,
                )

            text = getattr(transcription, "text", None) or str(transcription)
            return _clean_text(text)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def ask_text(self, user_id: int, prompt: str, history_limit: int = 16) -> str:
        prompt = _clean_text(prompt)
        if not prompt:
            return "Pesan kosong."

        history = self._fetch_history(user_id, limit=history_limit)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": prompt}]

        chat_completion = self.client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.5,
            max_tokens=1024,
        )

        answer = chat_completion.choices[0].message.content or ""
        answer = _clean_text(answer) or "Maaf, saya belum mendapat jawaban yang jelas."

        self._save_message(user_id, "user", prompt)
        self._save_message(user_id, "assistant", answer)
        return answer

    def ask_voice(self, user_id: int, file_id: str, history_limit: int = 16):
        transcript = self.transcribe_voice(file_id)
        if not transcript:
            return "", "Maaf, suara belum terbaca dengan jelas."

        answer = self.ask_text(user_id, transcript, history_limit=history_limit)
        return transcript, answer
