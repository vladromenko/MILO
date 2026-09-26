"""Route scene questions without adding another model call to ordinary dialogue."""
import re
from .object_search import ALIASES


def simple_search_request(text, intent):
    if not intent or 'target' not in intent:
        return False
    if intent['target'] is None:
        return True
    aliases = '|'.join(re.escape(alias) + r'\w*' for alias in ALIASES[intent['target']])
    return bool(re.fullmatch(
        r'(?:(?:milo|майло|please|пожалуйста)[, ]+)*(?:find|look for|search for|найди|поищи) '
        r'(?:(?:my|the|a|мой|мою) )?(?:' + aliases + r')[.!?]*', text.lower().strip()))


def visual_request(text):
    return bool(re.search(
        r'look around|look for|search for|\bfind (?:my |the |a )|what (?:do|can) you see|what are you (?:seeing|looking at)|what(?: is|.s) in front of you|what.{0,35}wear|how many (?:people|persons)|count.{0,35}(?:people|persons)|describe.{0,35}(?:person|scene)|'
        r'посмотри|осмотрись|найди|поищи|как выглядит|сколько (?:людей|человек)|что.{0,20}видишь|'
        r'regarde|combien de personnes|décris|cherche|schau|wie viele (?:menschen|personen)|beschreib|suche|'
        r'見て|見回|何人|探して|周り|انظر|ابحث|كم شخص|كيف يبدو|دیکھو|تلاش|کتنے لوگ|کیسا لگ', text.lower()))


def sweep_requested(text):
    text = text.lower().strip()
    if re.search(r'\b(?:не|don.t|do not|without|без|nicht|pas)\b', text):
        return False
    # A count "in front" uses one current view; an explicit "around" count scans.
    if re.match(r'(?:(?:milo|майло|please|пожалуйста)[, ]+)*(?:сколько (?:людей|человек)|how many (?:people|persons))\b', text):
        return bool(re.search(r'\b(?:вокруг|по сторонам|around)\b', text))
    return bool(re.match(
        r'(?:(?:milo|майло|please|пожалуйста)[, ]+)*(?:(?:can you|could you|можешь) )?'
        r'(?:look around\b|search around\b|find\b|look for\b|search for\b|найди\b|поищи\b|'
        r'посмотри (?:вокруг|по сторонам)\b|осмотрись\b|'
        r'regarde autour|schau.{0,12}um|見回|周り|انظر حول|ادھر ادھر|آس پاس)', text))
