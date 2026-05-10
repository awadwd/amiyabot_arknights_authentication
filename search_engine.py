"""
明日方舟通行证查询 - 搜索引擎模块
数据来源: GitHub
"""

import json
import time
import re
import os
from typing import Optional

import aiohttp

# ============== GitHub 数据地址（硬编码，可自行部署） ==============
GITHUB_BASE = "https://raw.githubusercontent.com/awadwd/ArknightsAuthorization_Series-mirror/refs/heads/main"
BOX_DATA_URL = f"{GITHUB_BASE}/Box_Id.json"
SEARCH_WORD_URL = f"{GITHUB_BASE}/searchWord.json"

# ============== 缓存 ==============
class DataCache:
    """简单的内存缓存，TTL 30分钟"""

    def __init__(self, ttl: int = 1800):
        self._data: Optional[list] = None
        self._search_words: Optional[list] = None
        self._fetch_time: float = 0
        self._words_fetch_time: float = 0
        self._ttl = ttl

    @property
    def is_expired(self) -> bool:
        return time.time() - self._fetch_time > self._ttl

    @property
    def is_words_expired(self) -> bool:
        return time.time() - self._words_fetch_time > self._ttl

    def set_data(self, data: list):
        self._data = data
        self._fetch_time = time.time()

    def get_data(self) -> Optional[list]:
        if self._data is None or self.is_expired:
            return None
        return self._data

    def set_search_words(self, words: list):
        self._search_words = words
        self._words_fetch_time = time.time()

    def get_search_words(self) -> Optional[list]:
        if self._search_words is None or self.is_words_expired:
            return None
        return self._search_words


_cache = DataCache(ttl=1800)

# ============== 内置外号映射表 ==============
BUILTIN_NICKNAMES = {
    "阿米娅": ["兔兔", "阿米驴", "amiya"],
    "温蒂": ["weedy", "推推"],
    "维什戴尔": ["ew", "EW"],
    "逻各斯": ["李狗剩", "Logos"],
    "星熊": ["鬼姐"],
    "塑心": ["阿尔图罗"],
    "凯尔希": ["老猞猁", "牢猫"],
}

# ============== 盒类型映射 ==============
BOX_TYPE_MAP = {
    "normal": "常规款",
    "whitelist": "白名单凭证",
    "special": "特别通行认证",
    "cooperation": "联动款",
    "ambience": "音律联觉通行证",
    "限定": "常规款（限定）",
}


def get_box_type_text(box_type: str) -> str:
    return BOX_TYPE_MAP.get(box_type, box_type or "常规款")


# ============== 下载 JSON 数据 ==============
async def _download_json(url: str) -> list:
    """从 URL 下载 JSON 文件，自动探测编码"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, ssl=False) as resp:
            if resp.status != 200:
                raise RuntimeError(f"数据下载失败 HTTP {resp.status}")
            raw = await resp.read()
            # 尝试 UTF-8，失败则用 GBK
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            return json.loads(text)


# ============== 加载/获取干员数据 ==============
async def load_character_data(force: bool = False) -> list:
    """加载干员盒数据，优先从缓存返回，force=True 强制重新加载"""
    if not force:
        cached = _cache.get_data()
        if cached is not None:
            return cached

    data = await _download_json(BOX_DATA_URL)
    if isinstance(data, list) and len(data) > 0:
        _cache.set_data(data)
        return data
    return []


# ============== 加载/获取搜索词 ==============
async def load_search_words(force: bool = False) -> list:
    """加载搜索词数据"""
    if not force:
        cached = _cache.get_search_words()
        if cached is not None:
            return cached

    raw = await _download_json(SEARCH_WORD_URL)
    if not isinstance(raw, list):
        return []

    words = []
    for item in raw:
        name = (item.get("name") or item.get("character1", {}).get("name") or "").strip()
        if not name:
            continue

        english = (item.get("englishname") or "").strip()
        japanese = (item.get("japanesename") or "").strip()

        raw_sw = item.get("searchword") or []
        if isinstance(raw_sw, str):
            try:
                raw_sw = json.loads(raw_sw)
            except Exception:
                raw_sw = [
                    w.strip()
                    for w in re.split(r"[,，、]", raw_sw)
                    if w.strip()
                ]

        entry = {"name": name}
        if english:
            entry["englishname"] = english
        if japanese:
            entry["japanesename"] = japanese
        if raw_sw and isinstance(raw_sw, list):
            entry["searchword"] = raw_sw
        else:
            entry["searchword"] = []

        words.append(entry)

    _cache.set_search_words(words)
    return words


# ============== 匹配逻辑 ==============
async def search_character(
    keyword: str,
    enable_nickname: bool = True,
    enable_english: bool = True,
) -> list:
    """
    按干员名查询盒号
    返回 [{characterName, boxIds, matchType}]
    """
    term = keyword.strip().lower()
    if not term:
        return []

    data = await load_character_data()
    search_words = await load_search_words() if (enable_nickname or enable_english) else []

    # 构建搜索词字典
    name_to_extra = {}
    for entry in search_words:
        n = entry["name"]
        name_to_extra[n] = {
            "englishname": entry.get("englishname", ""),
            "japanesename": entry.get("japanesename", ""),
            "searchword": entry.get("searchword", []),
        }

    # 合并内置外号
    for char_name, nicks in BUILTIN_NICKNAMES.items():
        if char_name not in name_to_extra:
            name_to_extra[char_name] = {"englishname": "", "japanesename": "", "searchword": nicks}
        else:
            existing = set(name_to_extra[char_name].get("searchword", []))
            name_to_extra[char_name]["searchword"] = list(existing | set(nicks))

    result_map = {}

    for box in data:
        box_id = box.get("Box_id") or box.get("box_id") or ""
        box_type = box.get("Box_type") or box.get("box_type") or "normal"

        for i in range(1, 11):
            char_key = f"character{i}"
            raw = box.get(char_key)
            if not raw:
                continue

            if isinstance(raw, str):
                char_name = raw.strip()
                char_info = {}
            elif isinstance(raw, dict):
                char_name = (raw.get("name") or "").strip()
                char_info = {
                    "avatar": raw.get("imageUrl") or raw.get("avatar") or "",
                    "nolyELITE1": raw.get("nolyELITE1", False),
                    "hotcharacter": raw.get("hotcharacter", False),
                    "market_price": raw.get("market_price"),
                }
            else:
                continue

            if not char_name:
                continue

            matched = False
            match_type = ""
            extra = name_to_extra.get(char_name, {})

            # 1. 中文精确匹配
            if term in char_name.lower():
                matched = True
                match_type = "chinese"
            # 2. 英文名匹配
            elif enable_english:
                en = extra.get("englishname", "").lower()
                if en and term in en:
                    matched = True
                    match_type = "english"
            # 3. 日文名匹配
            if not matched:
                jp = extra.get("japanesename", "").lower()
                if jp and term in jp:
                    matched = True
                    match_type = "japanese"
            # 4. 外号匹配
            if not matched and enable_nickname:
                for nick in extra.get("searchword", []):
                    if term in nick.lower():
                        matched = True
                        match_type = "nickname"
                        break

            if matched:
                if char_name not in result_map:
                    result_map[char_name] = {
                        "characterName": char_name,
                        "boxIds": [],
                        "matchType": match_type,
                        **char_info,
                    }
                if box_id and box_id not in result_map[char_name]["boxIds"]:
                    result_map[char_name]["boxIds"].append(box_id)

    return list(result_map.values())


async def search_box(box_id: str) -> list:
    """
    按盒号查询干员列表
    返回 [{boxId, boxType, characters: [{name, avatar, nolyELITE1, hotcharacter, market_price}]}]
    """
    term = box_id.strip().replace(" ", "").replace("　", "")
    if not term:
        return []

    data = await load_character_data()
    results = []

    for box in data:
        bid = str(box.get("Box_id") or box.get("box_id") or "")
        box_type = box.get("Box_type") or box.get("box_type") or "normal"

        # 精确匹配
        is_match = bid == term

        if not is_match:
            m = re.match(r"^(\d+)(?:\.0+)?$", bid)
            if m:
                is_match = term == m.group(1)

        if not is_match:
            m = re.match(r"^(.+?)(\d+(?:\.\d+)?)$", bid)
            if m:
                prefix, num = m.group(1), m.group(2)
                num_clean = num.replace(".0", "")
                is_match = (prefix + num == term) or (prefix + num_clean == term)

        if not is_match:
            m2 = re.match(r"^(.+?)(\d+(?:\.\d+)?)$", bid)
            if m2 and re.match(r"^\d+(\.\d+)?$", term):
                _, num = m2.group(1), m2.group(2)
                num_clean = num.replace(".0", "")
                is_match = term == num or term == num_clean

        if is_match:
            characters = []
            for i in range(1, 11):
                char_key = f"character{i}"
                raw = box.get(char_key)
                if not raw:
                    continue

                if isinstance(raw, str):
                    char_name = raw.strip()
                    char_info = {}
                elif isinstance(raw, dict):
                    char_name = (raw.get("name") or "").strip()
                    char_info = {
                        "avatar": raw.get("imageUrl") or raw.get("avatar") or "",
                        "nolyELITE1": raw.get("nolyELITE1", False),
                        "hotcharacter": raw.get("hotcharacter", False),
                        "market_price": raw.get("market_price"),
                    }
                else:
                    continue

                if char_name:
                    characters.append({"name": char_name, **char_info})

            if characters:
                results.append({
                    "boxId": bid,
                    "boxType": box_type,
                    "characters": characters,
                })

    return results


# ============== 搜索建议 ==============
async def get_suggestions(keyword: str, mode: str = "character") -> list:
    """获取搜索建议列表"""
    term = (keyword or "").strip().lower()
    suggestions = []

    if mode == "character":
        words = await load_search_words()
        for entry in words:
            name = entry["name"]
            if not term or term in name.lower():
                suggestions.append({"name": name, "type": "searchWords"})
            elif term in (entry.get("englishname", "") or "").lower():
                suggestions.append({"name": name, "type": "english"})
            elif term in (entry.get("japanesename", "") or "").lower():
                suggestions.append({"name": name, "type": "japanese"})
            elif any(term in nick.lower() for nick in entry.get("searchword", [])):
                suggestions.append({"name": name, "type": "nickname"})

        for char_name, nicks in BUILTIN_NICKNAMES.items():
            if not term or term in char_name.lower():
                suggestions.append({"name": char_name, "type": "builtin"})
            elif any(term in nick.lower() for nick in nicks):
                suggestions.append({"name": char_name, "type": "builtin"})

        data = await load_character_data()
        seen = set()
        for box in data:
            for i in range(1, 11):
                char_key = f"character{i}"
                raw = box.get(char_key)
                if not raw:
                    continue
                char_name = raw if isinstance(raw, str) else raw.get("name", "")
                char_name = (char_name or "").strip()
                if char_name and char_name not in seen:
                    seen.add(char_name)
                    if not term or term in char_name.lower():
                        suggestions.append({"name": char_name, "type": "character"})

    else:
        data = await load_character_data()
        seen_box = set()
        for box in data:
            bid = box.get("Box_id") or box.get("box_id") or ""
            if bid and bid not in seen_box:
                seen_box.add(bid)
                if not term or term in bid.lower():
                    suggestions.append({
                        "name": bid,
                        "boxType": box.get("Box_type") or "normal",
                        "type": "box",
                    })

    unique = []
    seen_names = set()
    for s in suggestions:
        if s["name"] not in seen_names:
            seen_names.add(s["name"])
            unique.append(s)
    return unique[:30]
