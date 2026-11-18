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
    "Sakamoto Days": "أيام ساكاموتو", "SAKAMOTO DAYS": "أيام ساكاموتو",
    "Gachiakuta": "جاتشياكوتا", "Kagurabachi": "كاجوراباتشي",
    "Kaiju No. 8": "كايجو رقم 8", "Wind Breaker": "ويند بريكر",
    "Oshi no Ko": "أوشي نو كو", "Shangri-La Frontier": "شانغريلا فرونتير",
    "MASHLE: MAGIC AND MUSCLES": "ماشل: السحر والعضلات",
    "The Seven Deadly Sins": "الخطايا السبع المميتة", "Tower of God": "برج الإله",
    "Dragon Ball DAIMA": "دراغون بول دايما", "Dragon Ball Super: Beerus": "دراغون بول سوبر: بيروس",
    "Aoashi": "آو آشي", "JoJo's Bizarre Adventure": "مغامرات جوجو العجيبة",
    "Steel Ball Run: JoJo no Kimyou na Bouken": "مغامرات جوجو: ستيل بول ران",
    "Parasyte: The Maxim": "الطفيلي", "Boruto: Naruto Next Generations": "بوروتو: الأجيال القادمة من ناروتو",
    "Witch Hat Atelier": "أتليه قبعة الساحرة", "Hell's Paradise": "جنة الجحيم",
}
EXTRA_ALIASES = {
    "بلاك كلوفر": ["بلاك لكوفر", "بلاككلوفر", "بلاك كلوڤر", "البرسيم الاسود"],
    "هجوم العمالقة": ["اتاك اون تايتن", "هجوم العمالقه", "شينجيكي نو كيوجين"],
    "مذكرة الموت": ["ديث نوت"],
    "أكاديمية بطلي": ["ماي هيرو اكاديميا", "بوكو نو هيرو", "اكاديمية الابطال"],
    "الخيميائي المعدني الكامل: الأخوة": ["فول ميتال الكميست", "الخيميائي المعدني"],
    "قاتل الشياطين": ["ديمون سلاير", "دايمون سلاير", "كيميتسو نو يايبا", "كيميتسو"],
    "جوجوتسو كايسن": ["جوجوتسو", "جوجتسو كايسن"],
    "دان دادان": ["داندادان", "دان دا دان"],
    "فريرن: ما وراء نهاية الرحلة": ["فريرن", "فرييرين", "سوسو نو فريرن", "سوسو نو فرييرين"],
    "سولو ليفلينج": ["سولو ليفيلينج", "سولو ليفلينغ", "سولو ليفيلينغ", "الارتقاء المنفرد"],
    "ون بيس": ["اون بيس"],
    "بليتش": ["بلياتش"],
    "رجل المنشار": ["تشينسو مان", "شينسو مان"],
    "ناروتو": ["ناروتو شيبودن", "ناروتو شيبودين"],
    "هانتر إكس هانتر": ["القناص", "هانتر", "هنتر"],
    "سباي إكس فاميلي": ["عائلة الجاسوس", "سباي فاميلي"],
    "أيام ساكاموتو": ["ساكاموتو", "ساكاموتو دايز", "ساكاموتو ديز"],
    "كايجو رقم 8": ["كايجو 8", "كاي جو 8"],
    "حول تجسدي إلى سلايم": ["تنسورا", "سلايم", "تجسدي الى سلايم"],
    "الخطايا السبع المميتة": ["الخطايا السبع", "ناناتسو نو تايزاي"],
    "مغامرات جوجو العجيبة": ["جوجو", "ستيل بول ران"],
}


def clean_title(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", "".join(char if (not char.isalpha() or "LATIN" in unicodedata.name(char, "") or "ARABIC" in unicodedata.name(char, "")) and unicodedata.category(char) not in ("Cc", "Cf", "Cs", "So") else " " for char in text)).strip(" |–—-")


_PERSIAN_CHARS_RE = re.compile(r"[\u067e\u0686\u0698\u06af\u06a9\u06cc\u06c1\u06d2]")
_PERSIAN_MARKERS = (" به ", " فصل ", " مرد تک ", " های ", " در ", " است ", " دفترچه ", " برای ", " با ", " از ")


def has_arabic(value):
    return bool(re.search(r"[\u0621-\u064a]", value or ""))


def _is_arabic_display(value):
    text = str(value or "")
    if not has_arabic(text) or _PERSIAN_CHARS_RE.search(text):
        return False
    padded = f" {text} "
    return not any(marker in padded for marker in _PERSIAN_MARKERS)


def english_title(*values):
    for value in values:
        cleaned = clean_title(value)
        if re.search(r"[A-Za-z]", cleaned) and not has_arabic(cleaned):
            return cleaned
    return ""


def normalize_search(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char) and char != "ـ")
    value = value.translate(str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي",
        "پ": "ب", "چ": "ج", "ژ": "ز", "گ": "ك", "ک": "ك", "ی": "ي", "ڤ": "ف",
    }))
    return re.sub(r"[^\w]+", " ", value).strip()


def compact_search(value):
    return normalize_search(value).replace(" ", "")


_COMMON = {normalize_search(en): ar for en, ar in COMMON_NAMES.items()}
_SEARCH = {}
_COMPACT_SEARCH = {}
_SEARCH_IDS = {}
_COMPACT_SEARCH_IDS = {}
_ID_TO_EN = {}
_SUGGESTIONS = {}


def _register_ids(anime_id, aliases, english=None):
    if not anime_id:
        return
    aid = int(anime_id)
    if english:
        _ID_TO_EN.setdefault(aid, english)
    for alias in aliases:
        if has_arabic(alias):
            norm = normalize_search(alias)
            comp = compact_search(alias)
            if norm:
                _SEARCH_IDS.setdefault(norm, set()).add(aid)
            if comp:
                _COMPACT_SEARCH_IDS.setdefault(comp, set()).add(aid)


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
    ar = next((clean_title(value) for value in [item.get("title_ar"), item.get("title_arabic"), item.get("title"), item.get("original_title")] if _is_arabic_display(value)), "")
    if not en and ar:
        en = _SEARCH.get(normalize_search(ar), "") or _COMPACT_SEARCH.get(compact_search(ar), "")
    en = en or f"Anime {item.get('anime_id') or item.get('id') or ''}".strip()
    source = (item.get("title_ar_source") or "metadata") if ar else ""
    key = normalize_search(en)
    if not ar and key in _COMMON:
        ar, source = _COMMON[key], "curated"
    if not ar:
        candidate = clean_title(data.get("ar"))
        if _is_arabic_display(candidate):
            ar, source = candidate, "wikidata"
    if not ar:
        # Preserve the common franchise name when the provider adds a season.
        for base in sorted(COMMON_NAMES, key=len, reverse=True):
            if en.casefold().startswith(base.casefold() + " "):
                ar, source = COMMON_NAMES[base] + " " + phonetic_arabic(en[len(base):].strip()), "curated+phonetic"
                break
    if not ar:
        ar = next((clean_title(value) for value in (item.get("synonyms") or []) if _is_arabic_display(value)), "")
        if ar:
            source = "metadata"
    if not ar:
        ar, source = phonetic_arabic(en), "phonetic"
    aliases = list(dict.fromkeys([ar, en, *EXTRA_ALIASES.get(ar, [])]))
    for alias in aliases:
        norm = normalize_search(alias)
        comp = compact_search(alias)
        if norm:
            _SEARCH[norm] = en
        if comp:
            _COMPACT_SEARCH[comp] = en
    if item.get("title_namespace") != "local":
        _register_ids(item.get("anime_id") or item.get("id"), aliases, en)
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


_AR_TO_LATIN_PAIRS = {
    "تش": "ch", "ش": "sh", "ث": "th", "خ": "kh", "ذ": "dh", "ظ": "z",
    "ض": "d", "ص": "s", "ط": "t", "ق": "k", "ف": "f", "ڤ": "v", "غ": "g",
    "ا": "a", "ب": "b", "ت": "t", "ج": "j", "ح": "h", "د": "d", "ر": "r",
    "ز": "z", "س": "s", "ع": "a", "ك": "k", "ل": "l", "م": "m", "ن": "n",
    "ه": "h", "و": "o", "ي": "i",
}


def arabic_to_latin(query):
    """Best-effort phonetic transliteration for unseen Arabic anime queries."""
    norm = normalize_search(query)
    words = []
    for token in norm.split():
        if token in ("الموسم", "الجزء"):
            words.append("season" if token == "الموسم" else "part")
            continue
        if token == "الفيلم":
            words.append("movie")
            continue
        if token == "نو":
            words.append("no")
            continue
        out = []
        i = 0
        while i < len(token):
            two = token[i:i + 2]
            if two == "تش":
                out.append("ch")
                i += 2
                continue
            if two == "كس":
                out.append("x")
                i += 2
                continue
            ch = token[i]
            if ch == "و" and i == 0:
                out.append("wa")
            elif ch == "ي" and i == 0:
                out.append("y")
            else:
                out.append(_AR_TO_LATIN_PAIRS.get(ch, ch))
            i += 1
        words.append("".join(out))
    return " ".join(w for w in words if w)


def _fuzzy_english_match(query):
    norm = normalize_search(query)
    comp = compact_search(query)
    if not comp:
        return None
    if norm in _SEARCH:
        return _SEARCH[norm]
    if comp in _COMPACT_SEARCH:
        return _COMPACT_SEARCH[comp]
    # Check _SUGGESTIONS and _SEARCH with substring or high-similarity fuzzy ratio.
    if len(comp) >= 3:
        best_ratio = 0.0
        best_en = None
        for alias_comp, en in _COMPACT_SEARCH.items():
            if not has_arabic(alias_comp) and comp == alias_comp:
                return en
            if len(alias_comp) < 3:
                continue
            if comp in alias_comp or alias_comp in comp:
                ratio = min(len(comp), len(alias_comp)) / max(len(comp), len(alias_comp))
                if ratio >= 0.72 and ratio > best_ratio:
                    best_ratio, best_en = ratio, en
            elif len(comp) >= 4:
                ratio = SequenceMatcher(None, comp, alias_comp).ratio()
                if ratio >= 0.80 and ratio > best_ratio:
                    best_ratio, best_en = ratio, en
        if best_en:
            return best_en
    return None


def provider_search(query):
    key = normalize_search(query)
    if key in _SEARCH:
        return _SEARCH[key]
    comp = compact_search(query)
    if comp in _COMPACT_SEARCH:
        return _COMPACT_SEARCH[comp]
    if has_arabic(query):
        matched = _fuzzy_english_match(query)
        if matched:
            return matched
        matches = {en for alias, en in _SEARCH.items() if key and key in alias}
        if len(matches) == 1:
            return next(iter(matches))
        latin = arabic_to_latin(query)
        return latin or query
    return query


def provider_filters(query):
    """Arabic partial matches use exact catalog IDs, then AniList paginates/filters."""
    key = normalize_search(query)
    comp = compact_search(query)
    if has_arabic(query) and key:
        ids = _SEARCH_IDS.get(key) or _COMPACT_SEARCH_IDS.get(comp)
        if not ids:
            ids = set().union(*(values for alias, values in _SEARCH_IDS.items() if key in alias))
        if not ids and len(comp) >= 4:
            ids = set().union(*(values for alias, values in _COMPACT_SEARCH_IDS.items() if comp in alias))
        if ids:
            return {"ids": sorted(ids)}
    return {"search": provider_search(query)}


def english_query_for_ids(ids):
    """Find a franchise English search term for curated ID lookups."""
    titles = []
    for aid in ids:
        en = _ID_TO_EN.get(int(aid))
        if not en:
            entry = _DATA.get(str(aid)) or _SUGGESTIONS.get(int(aid)) or {}
            en = english_title(entry.get("en"), entry.get("title_english"), entry.get("title"))
        if en:
            base = re.split(r"[:–—\-\|\/]", en)[0].strip()
            titles.append(base or en)
    if not titles:
        return None
    # Use shortest common prefix/root when IDs share a franchise (e.g. Naruto -> Naruto).
    shortest = min(titles, key=len)
    if all(shortest.casefold() in t.casefold() for t in titles):
        return shortest
    return titles[0] if len(set(titles)) == 1 else None


def suggest_local(query, limit=6, hidden_ids=()):
    key = normalize_search(query)
    comp = compact_search(query)
    if not key:
        return []
    ranked = []
    for anime_id, item in _SUGGESTIONS.items():
        if anime_id in hidden_ids:
            continue
        aliases = item.get("title_aliases", [item.get("title", "")])
        score = 4
        for raw_alias in aliases:
            alias = normalize_search(raw_alias)
            alias_comp = compact_search(raw_alias)
            if alias == key or alias_comp == comp:
                score = min(score, 0)
            elif alias.startswith(key) or alias_comp.startswith(comp):
                score = min(score, 1)
            elif key in alias or (len(comp) >= 3 and comp in alias_comp):
                score = min(score, 2)
            elif len(comp) >= 4 and (
                SequenceMatcher(None, key, alias[:len(key) + 1]).ratio() >= .78
                or SequenceMatcher(None, comp, alias_comp[:len(comp) + 1]).ratio() >= .78
            ):
                score = min(score, 3)
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
    _en_data = clean_title(_names.get("en")) if _names.get("en") else None
    _register_ids(_id, [_names["ar"]], _en_data)
    if _en_data:
        _SEARCH[normalize_search(_names["ar"])] = _en_data
        _COMPACT_SEARCH[compact_search(_names["ar"])] = _en_data
        _curated_ar = _COMMON.get(normalize_search(_en_data))
        if _curated_ar:
            _register_ids(_id, [_curated_ar, *EXTRA_ALIASES.get(_curated_ar, [])], _en_data)
for _en, _ar in COMMON_NAMES.items():
    _SEARCH[normalize_search(_ar)] = _en
    _COMPACT_SEARCH[compact_search(_ar)] = _en
    for _alias in EXTRA_ALIASES.get(_ar, []):
        _SEARCH[normalize_search(_alias)] = _en
        _COMPACT_SEARCH[compact_search(_alias)] = _en

# Verified popular AniList identities also work before any visitor warms the catalog.
for _en, _id in {"Attack on Titan": 16498, "Demon Slayer: Kimetsu no Yaiba": 101922,
                 "Black Clover": 97940,
                 "JUJUTSU KAISEN": 113415, "Death Note": 1535, "My Hero Academia": 21459,
                 "Hunter x Hunter": 11061, "One-Punch Man": 21087, "One Piece": 21}.items():
    _ar = COMMON_NAMES[_en]
    _register_ids(_id, [_ar, *EXTRA_ALIASES.get(_ar, [])], _en)
