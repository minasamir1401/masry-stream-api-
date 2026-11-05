import re
from typing import Dict, List, Optional, Tuple


class MetadataParser:
    RESOLUTION_PATTERN = re.compile(
        r"\b(4k|2160p|1080p|fhd|720p|hd|480p|sd|bluray|web-dl|webrip|dvdrip)\b",
        re.IGNORECASE,
    )
    YEAR_PATTERN = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")
    SYNOPSIS_PATTERN = re.compile(r"\s*تدور\s*[أإا]حداث\s*(الفيلم|المسلسل)")
    SEASON_EPISODE_PATTERNS = [
        re.compile(r"s(?P<season>\d{1,2})\s*e(?P<episode>\d{1,3})", re.IGNORECASE),
        re.compile(r"الموسم\s*(?P<season>\d+|[\u0621-\u064a ]+?)\s*[|¦:–-]*\s*الحلق[ةه]\s*(?P<episode>\d+)"),
        re.compile(r"(?:ال)?حلق[ةه]\s*(?P<episode>\d+)"),
        re.compile(r"episode\s*(?P<episode>\d+)", re.IGNORECASE),
        re.compile(r"ep\s*(?P<episode>\d+)", re.IGNORECASE),
    ]

    GENRE_KEYWORDS = {
        "disney-masry": ["ديزني", "مصري", "بالمصري", "لهجة مصرية", "مدبلج مصري", "disney"],
        "animation": ["رسوم متحركة", "أنيميشن", "انيميشن", "كرتون", "كارتون", "animation", "anime"],
        "comedy": ["كوميديا", "كوميدي", "ضحك", "comedy"],
        "adventure": ["مغامرة", "مغامرات", "adventure"],
        "family": ["عائلي", "عائلة", "أطفال", "اطفال", "family", "kids"],
        "fantasy": ["خيال", "سحر", "أساطير", "fantasy"],
        "musical": ["موسيقي", "أغاني", "استعراضي", "musical"],
        "action": ["أكشن", "اكشن", "حركة", "action"],
        "drama": ["دراما", "drama"],
    }

    ARABIC_NUMERALS = {
        "الأول": 1,
        "الاول": 1,
        "الثاني": 2,
        "الثالث": 3,
        "الرابع": 4,
        "الخامس": 5,
        "السادس": 6,
        "السابع": 7,
        "الثامن": 8,
        "التاسع": 9,
        "العاشر": 10,
    }

    CLEANUP_REGEXES = [
        re.compile(r"https?://\S+"),
        re.compile(r"@[A-Za-z0-9_]+"),
        re.compile(r"#(?:[^\s#]+)"),
        re.compile(r"t\.me/\S+"),
        re.compile(r"\b(1080p|720p|480p|4k|fhd|hd|hevc|x264|x265|aac|bluray|webrip|web-dl|mkv|mp4)\b", re.IGNORECASE),
        re.compile(r"[\[\]\(\)\{\}_]"),
    ]

    @classmethod
    def parse(
        cls,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> Dict:
        combined_text = f"{caption or ''}\n{file_name or ''}".strip()
        first_line = ""
        if caption:
            lines = [line.strip() for line in caption.split("\n") if line.strip()]
            if lines:
                first_line = lines[0]
        if not first_line and file_name:
            first_line = file_name

        year = cls._extract_year(combined_text)
        resolution, quality_label = cls._extract_resolution(combined_text)
        identity = f"{first_line}\n{file_name or ''}"
        season, episode = cls._extract_season_episode(identity)
        genres = cls._extract_genres(combined_text)
        clean_title = cls._clean_title(first_line)

        media_type = "MOVIE"
        if episode is not None:
            media_type = "EPISODE"
        elif re.match(r"^\s*(مسلسل|سلسلة)\s", first_line) or any(
            match[1] == "المسلسل" for match in cls.SYNOPSIS_PATTERN.finditer(caption or "")
        ):
            media_type = "SERIES"

        return {
            "title": clean_title or "محتوى ديزني غير معنون",
            "release_year": year,
            "resolution": resolution,
            "quality_label": quality_label,
            "season_number": season,
            "episode_number": episode,
            "media_type": media_type,
            "genre_slugs": genres,
            "description": caption.strip() if caption else None,
        }

    @classmethod
    def _extract_year(cls, text: str) -> Optional[int]:
        match = cls.YEAR_PATTERN.search(text)
        if match:
            try:
                year = int(match.group(1))
                if 1930 <= year <= 2030:
                    return year
            except ValueError:
                pass
        return None

    @classmethod
    def _extract_resolution(cls, text: str) -> Tuple[Optional[str], Optional[str]]:
        match = cls.RESOLUTION_PATTERN.search(text)
        if match:
            val = match.group(1).upper()
            if val in ["4K", "2160P"]:
                return "4K", "Ultra HD"
            elif val in ["1080P", "FHD", "BLURAY"]:
                return "1080p", "FHD"
            elif val in ["720P", "HD"]:
                return "720p", "HD"
            elif val in ["480P", "SD"]:
                return "480p", "SD"
            return val, "HD"
        return None, None

    @classmethod
    def _extract_season_episode(cls, text: str) -> Tuple[Optional[int], Optional[int]]:
        season = None
        episode = None
        for pattern in cls.SEASON_EPISODE_PATTERNS:
            match = pattern.search(text)
            if match:
                groups = match.groupdict()
                if "season" in groups and groups["season"]:
                    raw_s = groups["season"].strip()
                    if raw_s in cls.ARABIC_NUMERALS:
                        season = cls.ARABIC_NUMERALS[raw_s]
                    elif raw_s.isdigit():
                        season = int(raw_s)
                if "episode" in groups and groups["episode"]:
                    raw_e = groups["episode"]
                    if raw_e.isdigit():
                        episode = int(raw_e)
                if episode is not None:
                    break
        return season, episode

    @classmethod
    def _extract_genres(cls, text: str) -> List[str]:
        lower_text = text.lower()
        matched = set()
        matched.add("animation")
        matched.add("family")

        for slug, keywords in cls.GENRE_KEYWORDS.items():
            for kw in keywords:
                if kw in lower_text:
                    matched.add(slug)
                    break

        if "disney" in lower_text or "ديزني" in lower_text:
            matched.add("disney-masry")

        return list(matched)

    @classmethod
    def _clean_title(cls, raw: str) -> str:
        text = cls.SYNOPSIS_PATTERN.split(raw, maxsplit=1)[0]
        text = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\u200d\ufe0f]", "", text)
        for pattern in cls.CLEANUP_REGEXES:
            text = pattern.sub(" ", text)

        for kw in ["فيلم", "كارتون", "كرتون", "مدبلج", "مصري", "بالمصري", "كامل", "جودة عالية"]:
            text = re.sub(rf"\b{kw}\b", " ", text)

        text = re.sub(r"\s+", " ", text).strip(" -:–|")
        return text if text else "فيلم ديزني مدبلج"

