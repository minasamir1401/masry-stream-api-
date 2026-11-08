"""Public Arabic/English names; provider-only identifiers stay internal.

Arabic labels in title_data/anime_titles.json are mapped by exact AniList ID from
Wikidata P8729. Common audience names below take precedence over those labels.
Unmapped names use a phonetic Arabic spelling, never a fabricated translation.
"""
import functools
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from app.services.catalog_links import public_path

_DATA = json.loads((Path(__file__).resolve().parents[1] / "title_data" / "anime_titles.json").read_text(encoding="utf-8"))
COMMON_NAMES = {
    "Attack on Titan": "هجوم العمالقة", "Death Note": "مذكرة الموت", "Naruto": "ناروتو",
    "Naruto: Shippuden": "ناروتو شيبودن", "One Piece": "ون بيس", "Bleach": "بليتش",
    "Demon Slayer: Kimetsu no Yaiba": "قاتل الشياطين", "JUJUTSU KAISEN": "جوجوتسو كايسن",
    "Fullmetal Alchemist: Brotherhood": "الخيميائي المعدني الكامل: الأخوة", "Hunter x Hunter": "هانتر إكس هانتر",
    "Dragon Ball": "دراغون بول", "Dragon Ball Z": "دراغون بول زد", "Dragon Ball Super": "دراغون بول سوبر",
    "Black Clover": "بلاك كلوفر", "My Hero Academia": "أكاديمية بطلي", "One-Punch Man": "ون بنش مان",
    "Tokyo Ghoul": "طوكيو غول", "Tokyo Revengers": "منتقمو طوكيو",
    "Tokyo Revengers: War of the Three Titans Arc": "منتقمو طوكيو: حرب العمالقة الثلاثة",
    "The Apothecary Diaries": "يوميات الصيدلانية", "Sword Art Online": "سورد آرت أونلاين",
    "SPY x FAMILY": "سباي إكس فاميلي", "Chainsaw Man": "رجل المنشار", "Solo Leveling": "سولو ليفلينج",
    "Vinland Saga": "ملحمة فينلاند", "Frieren: Beyond Journey's End": "فريرن: ما وراء نهاية الرحلة",
    "DAN DA DAN": "دان دادان", "Dandadan": "دان دادان", "Monster": "مونستر", "Berserk": "بيرسيرك",
    "HAIKYU!!": "هايكيو", "Haikyu!!": "هايكيو", "Blue Lock": "بلو لوك", "Steins;Gate": "شتاينز غيت",
    "Your Name.": "اسمك", "A Silent Voice": "صوت صامت", "Spirited Away": "المخطوفة",
    "Howl's Moving Castle": "قلعة هاول المتحركة", "Princess Mononoke": "الأميرة مونونوكي",
    "Weathering With You": "الطقس معك", "Suzume": "سوزومي", "Violet Evergarden": "فايلت إيفرغاردن",
    "Cowboy Bebop": "كاوبوي بيبوب", "Code Geass: Lelouch of the Rebellion": "كود غياس: لولوش المتمرد",
    "Neon Genesis Evangelion": "نيون جينيسيس إيفانجيليون", "Mob Psycho 100": "موب سايكو 100",
    "ERASED": "الممحاة", "Gintama": "غينتاما", "Fairy Tail": "فيري تيل", "Dr. STONE": "دكتور ستون",
    "Fire Force": "قوة النار", "Soul Eater": "آكل الأرواح", "Black Butler": "الخادم الأسود",
    "Assassination Classroom": "فصل الاغتيال", "The Promised Neverland": "نيفرلاند الموعودة",
    "Detective Conan": "المحقق كونان", "Case Closed": "المحقق كونان", "Pokémon": "بوكيمون",
    "Re:ZERO -Starting Life in Another World-": "ري زيرو: بدء الحياة في عالم آخر",
    "Mushoku Tensei: Jobless Reincarnation": "موشوكو تينسي: تناسخ العاطل عن العمل",
    "That Time I Got Reincarnated as a Slime": "حول تجسدي إلى سلايم", "Overlord": "أوفرلورد",
    "No Game, No Life": "لا لعبة لا حياة", "KONOSUBA -God's blessing on this wonderful world!": "كونوسوبا",
    "The Rising of the Shield Hero": "نهوض بطل الدرع", "The Eminence in Shadow": "سيد الظلال",
    "Cyberpunk: Edgerunners": "سايبربانك: إيدجرنرز", "86 EIGHTY-SIX": "ستة وثمانون",
    "To Your Eternity": "إلى أبديتك", "March comes in like a lion": "يأتي مارس كالأسد",
    "Your lie in April": "كذبتك في أبريل", "Kaguya-sama: Love is War": "كاغويا ساما: الحب حرب",
    "Toradora!": "تورادورا", "Horimiya": "هوريميا", "Fruits Basket": "سلة الفواكه",
    "Clannad": "كلاناد", "CLANNAD": "كلاناد", "The Disastrous Life of Saiki K.": "حياة سايكي الكارثية",
}
EXTRA_ALIASES = {"بلاك كلوفر": ["بلاك لكوفر", "بلاككلوفر", "بلاك كلوڤر"], "هجوم العمالقة": ["اتاك اون تايتن", "هجوم العمالقه"], "مذكرة الموت": ["ديث نوت"], "أكاديمية بطلي": ["ماي هيرو اكاديميا", "بوكو نو هيرو"], "الخيميائي المعدني الكامل: الأخوة": ["فول ميتال الكميست"], "قاتل الشياطين": ["ديمون سلاير", "كيميتسو نو يايبا"]}


def clean_title(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", "".join(char if (not char.isalpha() or "LATIN" in unicodedata.name(char, "") or "ARABIC" in unicodedata.name(char, "")) and unicodedata.category(char) not in ("Cc", "Cf", "Cs", "So") else " " for char in text)).strip(" |–—-")


def has_arabic(value):
    return bool(re.search(r"[\u0621-\u064a]", value or ""))


def english_title(*values):
    for value in values:
        cleaned = clean_title(value)
        if re.search(r"[A-Za-z]", cleaned) and not has_arabic(cleaned):
            return cleaned
    return ""


def normalize_search(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char) and char != "ـ")
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))
    return re.sub(r"[^\w]+", " ", value).strip()


_COMMON = {normalize_search(en): ar for en, ar in COMMON_NAMES.items()}
_SEARCH = {}
_SEARCH_IDS = {}
_SUGGESTIONS = {}


def _register_ids(anime_id, aliases):
    if not anime_id:
        return
    for alias in aliases:
        if has_arabic(alias):
            _SEARCH_IDS.setdefault(normalize_search(alias), set()).add(int(anime_id))


def phonetic_arabic(value):
    words = {"season": "الموسم", "part": "الجزء", "movie": "الفيلم", "the": "ذا", "of": "أوف", "and": "آند", "in": "إن", "a": "إيه", "no": "نو", "final": "الأخير"}
    pairs = {"sh": "ش", "ch": "تش", "th": "ث", "ph": "ف", "gh": "غ", "kh": "خ", "ts": "تس", "oo": "و", "ou": "و", "ee": "ي", "ai": "اي"}
    letters = dict(zip("abcdefghijklmnopqrstuvwxyz", ["ا", "ب", "ك", "د", "ي", "ف", "ج", "ه", "ي", "ج", "ك", "ل", "م", "ن", "و", "ب", "ك", "ر", "س", "ت", "و", "ف", "و", "كس", "ي", "ز"]))
    def word(match):
        token = match[0].casefold()
        if token in words:
            return words[token]
        out, index = [], 0
        while index < len(token):
            pair = token[index:index + 2]
            if pair in pairs:
                out.append(pairs[pair]); index += 2
            else:
                out.append(letters.get(token[index], token[index])); index += 1
        return "".join(out)
    return re.sub(r"[a-z]+", word, unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode(), flags=re.I)


def names_for(item):
    data = _DATA.get(str(item.get("anime_id") or item.get("id")), {}) if item.get("title_namespace") != "local" else {}
    en = english_title(item.get("title_english"), item.get("title_en"), item.get("title"), data.get("en"), item.get("title_romaji"), item.get("original_title"))
    ar = next((clean_title(value) for value in [item.get("title_ar"), item.get("title_arabic"), item.get("title"), item.get("original_title"), *(item.get("synonyms") or [])] if has_arabic(value)), "")
    if not en and ar:
        en = _SEARCH.get(normalize_search(ar), "")
    en = en or f"Anime {item.get('anime_id') or item.get('id') or ''}".strip()
    source = (item.get("title_ar_source") or "metadata") if ar else ""
    key = normalize_search(en)
    if not ar and key in _COMMON:
        ar, source = _COMMON[key], "curated"
    if not ar:
        ar = clean_title(data.get("ar"))
        if ar:
            source = "wikidata"
    if not ar:
        # Preserve the common franchise name when the provider adds a season.
        for base in sorted(COMMON_NAMES, key=len, reverse=True):
            if en.casefold().startswith(base.casefold() + " "):
                ar, source = COMMON_NAMES[base] + " " + phonetic_arabic(en[len(base):].strip()), "curated+phonetic"
                break
    if not ar:
        ar, source = phonetic_arabic(en), "phonetic"
    aliases = list(dict.fromkeys([ar, en, *EXTRA_ALIASES.get(ar, [])]))
    for alias in aliases:
        _SEARCH[normalize_search(alias)] = en
    if item.get("title_namespace") != "local":
        _register_ids(item.get("anime_id") or item.get("id"), aliases)
    return en, ar, aliases, source


def localize_payload(payload):
    if isinstance(payload, list):
        for item in payload:
            localize_payload(item)
    elif isinstance(payload, dict):
        if isinstance(payload.get("title"), str) and ("media_type" in payload or "anime_id" in payload or "poster_url" in payload):
            en, ar, aliases, source = names_for(payload)
            payload.update(title=en, title_english=en, title_ar=ar, title_aliases=aliases, title_ar_source=source, original_title=ar)
            if payload.get("id") or payload.get("anime_id"):
                payload["canonical_path"] = public_path(payload)
            if payload.get("media_type") in ("SERIES", "MOVIE") and payload.get("title_namespace") != "local":
                fields = ("id", "title", "title_english", "title_ar", "title_aliases", "canonical_path", "poster_url", "poster_proxy_url", "release_year", "media_type", "popularity", "average_score")
                _SUGGESTIONS[payload["id"]] = {key: payload[key] for key in fields if key in payload}
        for value in payload.values():
            if isinstance(value, (dict, list)):
                localize_payload(value)
    return payload


def public_titles(function):
    @functools.wraps(function)
    async def wrapper(*args, **kwargs):
        from app.services.arabic_descriptions import arabic_descriptions
        return await arabic_descriptions.enrich(localize_payload(await function(*args, **kwargs)))
    return wrapper


def provider_search(query):
    key = normalize_search(query)
    if key in _SEARCH:
        return _SEARCH[key]
    matches = {en for alias, en in _SEARCH.items() if key and key in alias}
    return next(iter(matches)) if has_arabic(query) and len(matches) == 1 else query


def provider_filters(query):
    """Arabic partial matches use exact catalog IDs, then AniList paginates/filters."""
    key = normalize_search(query)
    if has_arabic(query) and key:
        ids = _SEARCH_IDS.get(key)
        if not ids:
            ids = set().union(*(values for alias, values in _SEARCH_IDS.items() if key in alias))
        if ids:
            return {"ids": sorted(ids)}
    return {"search": provider_search(query)}


def suggest_local(query, limit=6, hidden_ids=()):
    key = normalize_search(query)
    if not key:
        return []
    ranked = []
    for anime_id, item in _SUGGESTIONS.items():
        if anime_id in hidden_ids:
            continue
        aliases = [normalize_search(alias) for alias in item.get("title_aliases", [item.get("title", "")])]
        score = 4
        for alias in aliases:
            if alias == key: score = min(score, 0)
            elif alias.startswith(key): score = min(score, 1)
            elif key in alias: score = min(score, 2)
            elif len(key) >= 4 and SequenceMatcher(None, key, alias[:len(key) + 1]).ratio() >= .78: score = min(score, 3)
        if score < 4:
            ranked.append((score, -(item.get("popularity") or item.get("average_score") or 0), anime_id, item))
    ranked.sort(key=lambda row: row[:3])
    return [{key: value for key, value in item.items() if key not in ("episodes", "stream_url", "synonyms", "title_romaji")} for *_, item in ranked[:limit]]


async def warm_catalog_search():
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import CatalogEntry
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(CatalogEntry.payload))).scalars().all()
    for payload in rows:
        try:
            localize_payload(json.loads(payload))
        except (ValueError, TypeError):
            continue


for _id, _names in _DATA.items():
    _register_ids(_id, [_names["ar"]])
    if _names.get("en"):
        _SEARCH[normalize_search(_names["ar"])] = clean_title(_names["en"])
        _curated_ar = _COMMON.get(normalize_search(_names["en"]))
        if _curated_ar:
            _register_ids(_id, [_curated_ar, *EXTRA_ALIASES.get(_curated_ar, [])])
for _en, _ar in COMMON_NAMES.items():
    _SEARCH[normalize_search(_ar)] = _en
    for _alias in EXTRA_ALIASES.get(_ar, []):
        _SEARCH[normalize_search(_alias)] = _en

# Verified popular AniList identities also work before any visitor warms the catalog.
for _en, _id in {"Attack on Titan": 16498, "Demon Slayer: Kimetsu no Yaiba": 101922,
                 "Black Clover": 97940,
                 "JUJUTSU KAISEN": 113415, "Death Note": 1535, "My Hero Academia": 21459,
                 "Hunter x Hunter": 11061, "One-Punch Man": 21087, "One Piece": 21}.items():
    _ar = COMMON_NAMES[_en]
    _register_ids(_id, [_ar, *EXTRA_ALIASES.get(_ar, [])])
