"""Parse explicit voice requests in the selected locale without hardware effects.

``parse_memory_intent(text)`` returns one of:
    {"action": "enroll", "name": NAME}
    {"action": "remember", "fact": FACT}
    {"action": "forget_request", "request_confirmation": True}
    {"action": "forget_confirm"}
or None. Commands are case-insensitive, whole-utterance matches. Enrollment
and forgetting allow one final period or exclamation mark. Facts retain their
punctuation and case. Payload limits are characters, never silent truncation.

The caller must establish the consenting speaker's identity and require a
pending, identity-bound forget request before acting on a confirmation. This
parser neither infers another person's consent nor authorizes deletion.
"""

import re


_FLAGS = re.IGNORECASE | re.ASCII
_ENROLL = (
    re.compile(r"remember +me +as +(.+?)[.!]?", _FLAGS),
    re.compile(r"my +name +is +(.+?), *remember +me[.!]?", _FLAGS),
)
_REMEMBER = re.compile(r"remember +that +(.+)", _FLAGS)
_FORGET_REQUEST = re.compile(r"forget +everything +about +me[.!]?", _FLAGS)
_FORGET_CONFIRM = re.compile(r"yes, *forget +my +memories[.!]?", _FLAGS)

_LOCALES = {
    'ru': (r'запомни меня как (.+?)[.!]?', r'запомни[, ]+что (.+)',
           r'забудь всё обо мне[.!]?', r'да[, ]+забудь мои воспоминания[.!]?'),
    'fr': (r'souviens-toi de moi comme (.+?)[.!]?', r'retiens que (.+)',
           r'oublie tout sur moi[.!]?', r'oui[, ]+oublie mes souvenirs[.!]?'),
    'de': (r'merke dir meinen namen als (.+?)[.!]?', r'merke dir[, ]+dass (.+)',
           r'vergiss alles über mich[.!]?', r'ja[, ]+vergiss meine erinnerungen[.!]?'),
    'ja': (r'私を(.+?)として覚えて[。!！]?', r'(.+?)ことを覚えて[。!！]?',
           r'私のことを全部忘れて[。!！]?', r'はい[、, ]*私の記憶を忘れて[。!！]?'),
    'ar': (r'تذكرني باسم (.+?)[.!۔]?', r'تذكر أن (.+)',
           r'انس كل شيء عني[.!۔]?', r'نعم[،, ]+انس ذكرياتي[.!۔]?'),
    'ur': (r'مجھے (.+?) کے نام سے یاد رکھو[.!۔]?', r'یاد رکھو کہ (.+)',
           r'میرے بارے میں سب کچھ بھول جاؤ[.!۔]?', r'ہاں[،, ]+میری یادیں بھول جاؤ[.!۔]?'),
}


def parse_memory_intent(text: str, language='en') -> dict[str, str | bool] | None:
    """Return a memory intent for a single explicit utterance, otherwise None."""
    if not isinstance(text, str) or not text.isprintable():
        return None
    text = text.strip(" ")
    text = re.sub(r"^(?:hey +)?milo[, ]+", "", text, flags=_FLAGS)
    enroll, remember, forget, confirm = _ENROLL, _REMEMBER, _FORGET_REQUEST, _FORGET_CONFIRM
    if language in _LOCALES:
        patterns = [re.compile(p, re.IGNORECASE) for p in _LOCALES[language]]
        enroll, remember, forget, confirm = (patterns[0],), *patterns[1:]
    if forget.fullmatch(text):
        return {"action": "forget_request", "request_confirmation": True}
    if confirm.fullmatch(text):
        return {"action": "forget_confirm"}
    for pattern in enroll:
        if match := pattern.fullmatch(text):
            name = match[1].strip(" ")
            # Separators must join nonempty runs of Unicode letters.
            parts = re.split(r"[ '\-\u2019]", name)
            if 1 <= len(name) <= 60 and all(part.isalpha() for part in parts):
                return {"action": "enroll", "name": name}
            return None
    if match := remember.fullmatch(text):
        fact = match[1].strip(" ")
        if 1 <= len(fact) <= 400 and any(char.isalnum() for char in fact):
            return {"action": "remember", "fact": fact}
    return None
