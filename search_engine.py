"""
明日方舟通行证查询 - 搜索引擎模块

数据来源优先级：
  1. 内存缓存（TTL 30分钟）
  2. data/ 本地文件（zip 外，用户可手动放置）
  3. GitHub 网络下载（成功写入 data/）
  4. searchword_builtin.json 内嵌 fallback（仅搜索词）
"""

import json
import time
import re
import os
import asyncio
from typing import Optional

import aiohttp

# ============== GitHub 数据地址 ==============
GITHUB_BASE = "https://raw.githubusercontent.com/awadwd/ArknightsAuthorization_Series-mirror/refs/heads/main"
BOX_DATA_URL = f"{GITHUB_BASE}/Box_Id.json"
SEARCH_WORD_URL = f"{GITHUB_BASE}/searchWord.json"

# ============== 本地数据目录（zip 外，用户可手动管理）==============
# 插件目录下的 data/ 文件夹
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_PLUGIN_DIR, "data")
BOX_DATA_FILE = os.path.join(DATA_DIR, "Box_Id.json")
SEARCH_WORDS_FILE = os.path.join(DATA_DIR, "search_words.json")
SEARCHWORD_RAW_FILE = os.path.join(DATA_DIR, "searchWord_raw.json")

# 确保 data 目录存在
os.makedirs(DATA_DIR, exist_ok=True)

# ============== 缓存 ==============
class DataCache:
    """简单的内存缓存，TTL 30分钟"""

    def __init__(self, ttl: int = 1800):
        self._data: Optional[list] = None
        self._search_words: Optional[list] = None
        self._fetch_time: float = 0
        self._words_fetch_time: float = 0
        self._ttl = ttl
        self._last_network_error: Optional[str] = None  # 最近一次网络错误信息

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

    def set_network_error(self, msg: str):
        self._last_network_error = msg

    def get_network_error(self) -> Optional[str]:
        return self._last_network_error

    def clear_network_error(self):
        self._last_network_error = None


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
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            return json.loads(text)


# ============== 本地文件加载 ==============
def _load_local_json(filepath: str) -> Optional[list]:
    """从本地 JSON 文件加载数据"""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data
    except Exception:
        pass
    return None


def _save_local_json(filepath: str, data: list) -> bool:
    """保存数据到本地 JSON 文件"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ============== 内嵌备用搜索词数据（离线 fallback）==============
SEARCHWORD_BUILTIN_FILE = os.path.join(_PLUGIN_DIR, "searchword_builtin.json")


async def _load_builtin_search_words() -> list:
    """从插件包内嵌的 searchword_builtin.json 加载搜索词（网络失败时的离线备用）"""
    if not os.path.exists(SEARCHWORD_BUILTIN_FILE):
        return []
    try:
        with open(SEARCHWORD_BUILTIN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


# ============== 解析原始 searchWord.json（GitHub 格式）==============
def _parse_raw_search_words(raw: list) -> list:
    """
    解析 GitHub 原生的 searchWord.json 数据。
    原始结构: [{character1: {...}}, {character2: {...}}, ...]
    """
    words = []
    for idx, item in enumerate(raw):
        inner = item.get(f"character{idx+1}", {})
        name = inner.get("name", "").strip()
        if not name:
            continue

        english = inner.get("englishname", "").strip()
        japanese = inner.get("japanesename", "").strip()
        # serachword（拼写错误，历史数据）优先，searchword 其次
        raw_sw = inner.get("serachword") or inner.get("searchword", []) or []

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
        entry["searchword"] = raw_sw if isinstance(raw_sw, list) else []

        words.append(entry)
    return words


# ============== 加载/获取干员数据 ==============
async def load_character_data(force: bool = False) -> list:
    """
    加载干员盒数据。

    优先级：内存缓存 > data/ 本地文件 > GitHub 下载 > 返回空列表
    成功下载后写入 data/ 文件夹。
    """
    if not force:
        cached = _cache.get_data()
        if cached is not None:
            return cached

    # 尝试网络下载
    error_msg = None
    try:
        data = await _download_json(BOX_DATA_URL)
    except Exception as e:
        error_msg = str(e)
        data = None

    if isinstance(data, list) and len(data) > 0:
        _cache.set_data(data)
        _cache.clear_network_error()
        # 写入本地文件
        _save_local_json(BOX_DATA_FILE, data)
        return data

    # 网络失败，尝试读本地文件
    _cache.set_network_error(f"下载干员数据失败: {error_msg}")
    local_data = _load_local_json(BOX_DATA_FILE)
    if local_data is not None:
        _cache.set_data(local_data)
        return local_data

    return []


async def refresh_character_data() -> tuple[bool, str]:
    """
    强制刷新干员数据（用于定时任务或用户手动刷新）。
    返回 (是否成功, 消息).
    """
    try:
        data = await _download_json(BOX_DATA_URL)
        if isinstance(data, list) and len(data) > 0:
            _cache.set_data(data)
            _cache.clear_network_error()
            _save_local_json(BOX_DATA_FILE, data)
            return True, f"✅ 干员数据已更新，共 {len(data)} 条记录。"
        else:
            msg = "下载数据为空"
            _cache.set_network_error(msg)
            return False, f"⚠️ 更新失败: {msg}"
    except Exception as e:
        _cache.set_network_error(str(e))
        local_data = _load_local_json(BOX_DATA_FILE)
        if local_data:
            return False, (
                f"⚠️ 网络更新失败（{e}），"
                f"当前使用 data/ 本地文件（{len(local_data)} 条）。"
                f"如需更新，请将新数据放入 {DATA_DIR}/Box_Id.json"
            )
        return False, (
            f"⚠️ 网络更新失败（{e}），"
            f"本地无缓存数据。请将 Box_Id.json 放入 {DATA_DIR}/ 后再试。"
        )


# ============== 加载/获取搜索词 ==============
async def load_search_words(force: bool = False) -> list:
    """
    加载搜索词数据。

    优先级：内存缓存 > data/search_words.json > GitHub 下载并解析 > 内嵌 fallback
    成功下载后写入 data/search_words.json（已解析格式）。
    """
    if not force:
        cached = _cache.get_search_words()
        if cached is not None:
            return cached

    # 尝试读已解析的本地文件（data/search_words.json）
    local_parsed = _load_local_json(SEARCH_WORDS_FILE)
    if local_parsed is not None:
        _cache.set_search_words(local_parsed)
        return local_parsed

    # 尝试网络下载
    error_msg = None
    try:
        raw = await _download_json(SEARCH_WORD_URL)
    except Exception as e:
        error_msg = str(e)
        raw = None

    words = []
    if isinstance(raw, list) and len(raw) > 0:
        words = _parse_raw_search_words(raw)
        # 保存原始文件和已解析文件
        _save_local_json(SEARCHWORD_RAW_FILE, raw)
        _save_local_json(SEARCH_WORDS_FILE, words)

    if not words:
        # 网络失败，尝试内嵌 fallback
        _cache.set_network_error(f"下载搜索词失败: {error_msg}")
        builtin = await _load_builtin_search_words()
        if builtin:
            _cache.set_search_words(builtin)
            return builtin
        return []

    _cache.set_search_words(words)
    _cache.clear_network_error()
    return words


async def refresh_search_words() -> tuple[bool, str]:
    """
    强制刷新搜索词数据（用于定时任务或用户手动刷新）。
    返回 (是否成功, 消息).
    """
    try:
        raw = await _download_json(SEARCH_WORD_URL)
        if isinstance(raw, list) and len(raw) > 0:
            words = _parse_raw_search_words(raw)
            _cache.set_search_words(words)
            _cache.clear_network_error()
            _save_local_json(SEARCHWORD_RAW_FILE, raw)
            _save_local_json(SEARCH_WORDS_FILE, words)
            return True, f"✅ 搜索词已更新，共 {len(words)} 条干员记录。"
        else:
            msg = "下载数据为空"
            _cache.set_network_error(msg)
            return False, f"⚠️ 更新失败: {msg}"
    except Exception as e:
        _cache.set_network_error(str(e))
        local_parsed = _load_local_json(SEARCH_WORDS_FILE)
        local_raw = _load_local_json(SEARCHWORD_RAW_FILE)
        local_count = len(local_parsed) if local_parsed else (len(local_raw) if local_raw else 0)
        if local_parsed:
            return False, (
                f"⚠️ 网络更新失败（{e}），"
                f"当前使用 data/ 本地文件（{local_count} 条）。"
                f"如需更新，请将新的 searchWord.json 放入 {DATA_DIR}/"
            )
        return False, (
            f"⚠️ 网络更新失败（{e}），"
            f"本地无缓存数据。请将 searchWord.json 放入 {DATA_DIR}/ 后再试。"
        )


# ============== 定时刷新任务（由 main.py 调用）==============
class BackgroundScheduler:
    """
    后台定时刷新调度器。
    auto_refresh_interval: 自动刷新间隔（秒），None/0 表示关闭
    """

    def __init__(self, interval_seconds: int = 900):
        self._interval = interval_seconds  # 默认 15 分钟
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._status = "未启动"

    @property
    def interval(self) -> int:
        return self._interval

    @interval.setter
    def interval(self, value: int):
        if value > 0:
            self._interval = value

    @property
    def status(self) -> str:
        return self._status

    def set_interval(self, interval_seconds: int):
        """设置刷新间隔（秒）"""
        if interval_seconds > 0:
            self._interval = interval_seconds

    async def _run_loop(self, bot_instance):
        """定时刷新循环"""
        import importlib
        import amiyabot

        while self._running:
            await asyncio.sleep(self._interval)
            if not self._running:
                break

            self._status = "刷新中..."
            try:
                ok1, msg1 = await refresh_character_data()
                ok2, msg2 = await refresh_search_words()
                if ok1 and ok2:
                    self._status = f"✅ 刷新成功 ({time.strftime('%H:%M:%S')})"
                else:
                    self._status = f"⚠️ 刷新有异常 ({time.strftime('%H:%M:%S')})"
                amiyabot.logging.getLogger("amiyabot-arknights-authentication").info(
                    f"[定时刷新] {msg1} | {msg2}"
                )
            except Exception as e:
                self._status = f"❌ 刷新失败 ({time.strftime('%H:%M:%S')})"
                amiyabot.logging.getLogger("amiyabot-arknights-authentication").error(
                    f"[定时刷新] 异常: {e}"
                )

    def start(self, bot_instance):
        """启动后台刷新任务"""
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(bot_instance))
        self._status = "运行中"

    def stop(self):
        """停止后台刷新任务"""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._status = "已停止"


# 全局调度器实例（默认关闭，用户配置后开启）
scheduler = BackgroundScheduler(interval_seconds=900)


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
                if not char_name:
                    continue
                market_price = None
            elif isinstance(raw, dict):
                char_name = (raw.get("name") or raw.get("character_name") or "").strip()
                market_price = raw.get("market_price", {})
                if not char_name:
                    continue
            else:
                continue

            match_type = None
            if term == char_name.lower():
                match_type = "chinese"
            elif extra := name_to_extra.get(char_name):
                if term == extra["englishname"].lower():
                    match_type = "english"
                elif term == extra["japanesename"].lower():
                    match_type = "japanese"
                elif any(term == sw.lower() for sw in extra["searchword"]):
                    match_type = "nickname"
                elif term == char_name.lower():
                    match_type = "chinese"

            if match_type is None:
                continue

            key = char_name
            if key not in result_map:
                result_map[key] = {
                    "characterName": char_name,
                    "boxes": [],
                    "matchType": match_type,
                    "hotcharacter": box.get("hotcharacter") or box.get("isHot"),
                    "nolyELITE1": bool(
                        market_price and market_price.get("ELITE2") in (None, 0)
                        if market_price
                        else False
                    ),
                }

            if box_id not in [b["boxId"] for b in result_map[key]["boxes"]]:
                result_map[key]["boxes"].append({
                    "boxId": box_id,
                    "imageUrl": box.get("Box_ImageUrl") or box.get("Box_imageUrl") or None,
                })

    return list(result_map.values())


# ============== 按盒号查干员 ==============
async def search_box(keyword: str) -> list:
    """按盒号/关键词查询干员列表"""
    data = await load_character_data()
    term = keyword.strip().lower()
    if not term:
        return []

    results = []
    seen_boxes = set()

    for box in data:
        box_id = str(box.get("Box_id") or box.get("box_id") or "")
        box_type = box.get("Box_type") or box.get("box_type") or "normal"

        if term in box_id.lower():
            if box_id in seen_boxes:
                continue
            seen_boxes.add(box_id)

            chars = []
            for i in range(1, 11):
                char_key = f"character{i}"
                raw = box.get(char_key)
                if not raw:
                    continue
                if isinstance(raw, dict):
                    chars.append({"name": raw.get("name", "") or raw.get("character_name", "")})
                elif isinstance(raw, str) and raw.strip():
                    chars.append({"name": raw.strip()})

            results.append({
                "boxId": box_id,
                "boxType": box_type,
                "characters": chars,
                "imageUrl": box.get("Box_ImageUrl") or box.get("Box_imageUrl") or None,
            })

    return results


# ============== 按盒号查大图 ==============
async def get_box_image_url(box_id: str) -> Optional[str]:
    """根据盒号查找对应的大图 URL"""
    data = await load_character_data()
    for box in data:
        bid = str(box.get("Box_id") or box.get("box_id") or "")
        if bid.lower() == box_id.strip().lower():
            return box.get("Box_ImageUrl") or box.get("Box_imageUrl") or None
    return None


# ============== 模糊提示（预留）==============
async def get_suggestions(keyword: str, limit: int = 5) -> list:
    """返回与关键词相似的干员名称建议"""
    data = await load_character_data()
    term = keyword.strip().lower()
    if not term or len(term) < 2:
        return []

    suggestions = []
    seen = set()

    for box in data:
        for i in range(1, 11):
            char_key = f"character{i}"
            raw = box.get(char_key)
            if not raw:
                continue
            if isinstance(raw, str):
                name = raw.strip()
            elif isinstance(raw, dict):
                name = (raw.get("name") or raw.get("character_name") or "").strip()
            else:
                continue
            if name and term in name.lower() and name not in seen:
                seen.add(name)
                suggestions.append(name)
                if len(suggestions) >= limit:
                    return suggestions

    return suggestions