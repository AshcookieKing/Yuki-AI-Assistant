"""Подготовка текста: эмоции, разбиение на фразы, нормализация для озвучки."""
import re

from num2words import num2words

EMOTIONS = ("happy", "laugh", "excited", "smug", "evil", "angry", "pout", "sad", "cry", "surprised", "scared",
            "shy", "love", "think", "confused", "sleepy", "disgust", "relaxed", "wink", "neutral", "proud", "bored",
            "determined")
EMO_RE = re.compile(r"\[(" + "|".join(EMOTIONS) + r")\]", re.I)
GESTURES = ("wave", "cheer", "hips", "shrug", "point", "facepalm", "heart", "dance", "clap", "bow", "nod", "shake",
            "jump", "salute", "laughcover")
GEST_RE = re.compile(r"\[(" + "|".join(GESTURES) + r")\]", re.I)
# любые [английские_теги] вроде [sigh] — служебные, вслух не читаем
ANY_TAG_RE = re.compile(r"\[[a-zA-Z_ -]{2,20}\]")
THINK_RE = re.compile(r"<think>.*?</think>", re.S)

SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")

# Частые галлюцинации Whisper на тишине/шуме
STT_JUNK = (
    "субтитры", "продолжение следует", "dimatorzok", "редактор субтитров",
    "спасибо за просмотр", "подписывайтесь на канал", "amara.org",
)

_LAT = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и",
    "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р",
    "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
}
_LAT_WORDS = {
    "windows": "виндоус", "youtube": "ютуб", "google": "гугл", "chrome": "хром", "steam": "стим",
    "discord": "дискорд", "telegram": "телеграм", "python": "пайтон", "ok": "окей",
    "pc": "пэ кэ", "ai": "эй ай", "wi-fi": "вай фай", "wifi": "вай фай", "usb": "ю эс би",
}


_ABBR = [
    (r"(?<=\d)\s*°C", " градусов"), (r"(?<=\d)\s*°", " градусов"), (r"\bкм/ч\b", "километров в час"),
    (r"\bГБ\b", "гигабайт"), (r"\bМБ\b", "мегабайт"), (r"\bТБ\b", "терабайт"), (r"\bКБ\b", "килобайт"),
    (r"\bГц\b", "герц"), (r"\bруб\.", "рублей"), (r"\bт\.\s?е\.", "то есть"), (r"\bт\.\s?д\.", "так далее"),
]


def split_emotion(text: str):
    """Возвращает (эмоция|None, текст без тегов)."""
    m = EMO_RE.search(text)
    emo = m.group(1).lower() if m else None
    return emo, EMO_RE.sub("", text).strip()


def split_tags(text: str):
    """Возвращает (эмоция|None, жест|None, текст без тегов)."""
    m = EMO_RE.search(text)
    g = GEST_RE.search(text)
    return (m.group(1).lower() if m else None, g.group(1).lower() if g else None,
            re.sub(r"\s{2,}", " ", ANY_TAG_RE.sub("", text)).strip())


def strip_tags(text: str) -> str:
    return ANY_TAG_RE.sub("", text).strip()


def pop_sentences(buf: str):
    """Отрезает законченные предложения из буфера стрима."""
    parts = SENTENCE_END.split(buf)
    if len(parts) <= 1:
        return [], buf
    done = [p for p in parts[:-1] if p.strip()]
    return done, parts[-1]


def _translit(word: str) -> str:
    lw = word.lower()
    if lw in _LAT_WORDS:
        return _LAT_WORDS[lw]
    return "".join(_LAT.get(ch, ch) for ch in lw)


def _num(m: re.Match) -> str:
    s = m.group(0).replace(",", ".")
    try:
        if "." in s:
            return num2words(float(s), lang="ru")
        return num2words(int(s), lang="ru")
    except Exception:
        return s


def for_speech(text: str) -> str:
    """Чистит текст под TTS: без markdown, кода, ссылок, латиницы и цифр."""
    t = THINK_RE.sub("", text)
    t = ANY_TAG_RE.sub("", t)
    t = re.sub(r"```.*?```", " (код на экране) ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"https?://\S+", "ссылка", t)
    t = re.sub(r"[A-Za-z]:\\[^\s]*", "этот путь", t)
    t = re.sub(r"[*_#>|~]+", " ", t)
    t = re.sub(r"(\d+)%", lambda m: m.group(1) + " процентов", t)
    for a, b in _ABBR:
        t = re.sub(a, b, t)
    t = re.sub(r"\d+(?:[.,]\d+)?", _num, t)
    t = re.sub(r"[A-Za-z][A-Za-z\-]*", lambda m: _translit(m.group(0)), t)
    t = re.sub(r"[^\w\s.,!?…:;\-—()«»\"'ёЁ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_junk_transcript(text: str) -> bool:
    low = text.lower().strip()
    if len(low) < 2:
        return True
    return any(j in low for j in STT_JUNK)
