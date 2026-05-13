"""
明日方舟通行证查询插件 - Amiyabot
按干员名查询盒号 / 按盒号查干员

使用方式：
  发送「通行证」进入菜单 → 选 1/2 → 输入关键字
  直接指令：「通行证查干员 阿米娅」「通行证查盒号 1」
"""

import logging
import os

from amiyabot import GroupConfig, Equal
from amiyabot import Message, Chain
from core import AmiyaBotPluginInstance

curr_dir = os.path.dirname(__file__)

# 本地 search_engine 在同目录
from .search_engine import (
    search_character,
    search_box,
    get_suggestions,
    get_box_type_text,
    get_box_image_url,
    load_character_data,
    load_search_words,
    refresh_character_data,
    refresh_search_words,
    _cache,
    BUILTIN_NICKNAMES,
    scheduler,
    DATA_DIR,
)

log = logging.getLogger("amiyabot-arknights-authentication")

# ============== 插件实例 ==============
bot = AmiyaBotPluginInstance(
    name="明日方舟通行证查询",
    version="1.1",
    plugin_id="amiyabot-arknights-authentication",
    description="查询明日方舟通行证干员信息和盒号信息",
    global_config_schema=f'{curr_dir}/config_schema.json',
    global_config_default=f'{curr_dir}/config_default.yaml',
)

fn_group = GroupConfig("通行证", check_prefix=False)
bot.set_group_config(fn_group)


# ============== 插件启动回调 ==============
def load(self):
    """插件加载时执行（AmiyaBotPluginInstance 用实例方法，不是装饰器）"""
    log.info('明日方舟通行证插件加载中...')
    
    # 加载初始数据
    try:
        load_character_data()
        load_search_words()
        log.info('初始数据加载完成')
    except Exception as e:
        log.error(f'初始数据加载失败: {e}')
    
    log.info('明日方舟通行证插件加载完成')


# ============== 状态机 ==============
class SessionState:
    __slots__ = ("step", "mode", "keyword")

    def __init__(self):
        self.step: str = "idle"
        self.mode: str = ""
        self.keyword: str = ""


_user_states: dict[str, SessionState] = {}


def get_state(user_id: str) -> SessionState:
    if user_id not in _user_states:
        _user_states[user_id] = SessionState()
    return _user_states[user_id]


def clear_state(user_id: str):
    if user_id in _user_states:
        del _user_states[user_id]


# ============== 辅助函数 ===============
MATCH_TYPE_TEXT = {
    "chinese": "中文名",
    "english": "英文名",
    "japanese": "日文名",
    "nickname": "外号",
}


# ============== 验证器：入口（支持带前缀或单字/单数字）==============
async def _verify_entry(data: Message):
    """
    在菜单状态下接受：
    - "1" → 直接进入按干员名查询
    - "2" → 直接进入按盒号查询
    - 任意包含"通行证"的消息 → 显示菜单
    允许不带"老猫"前缀，插件名/前缀会被自动剥离。
    """
    text = data.text.strip()

    # 直接快捷入口（进入菜单后用户发 "1" 或 "2"，无需前缀）
    if text == "1":
        return True, 1
    if text == "2":
        return True, 1

    # 排除已有的搜索指令前缀
    for prefix in ("通行证查干员", "通行证查角色", "通行证查盒号", "通行证查询盒号",
                    "兔兔通行证查干员", "兔兔通行证查角色", "兔兔通行证查盒号", "兔兔通行证查询盒号"):
        if text == prefix or text.startswith(prefix + " "):
            return False

    # 只有纯入口关键词才触发（不包含空格的纯词，或以关键词开头的消息）
    # 支持："通行证"、"老猫通行证"、"@机器人 通行证" 等各种带前缀的形式
    for kw in ("通行证", "方舟通行证", "方舟谷子", "通行证查询"):
        if text == kw or text.startswith(kw):
            return True, 1
    return False


# ============== 等待输入处理（任意文本，不含前缀要求）==============
async def _strip_prefix(text: str) -> str:
    """去掉常见前缀，返回实际命令文本"""
    text = text.strip()
    # 去掉 "老猫"、"兔兔" 等常见机器人昵称前缀
    for prefix in ("老猫", "兔兔"):
        if text.startswith(prefix):
            rest = text[len(prefix):].strip()
            if rest:
                return rest
    return text


# ============== 其他验证器 ==============
async def _verify_search_mode(data: Message):
    state = get_state(str(data.user_id))
    if state.step == "search_mode":
        return True, 1
    return False


async def _verify_direct_char(data: Message):
    text = data.text.strip()
    for kw in ("通行证查干员", "通行证查角色", "兔兔通行证查干员", "兔兔通行证查角色"):
        if text.startswith(kw) and len(text) > len(kw):
            return True, 1
    return False


async def _verify_direct_box(data: Message):
    text = data.text.strip()
    for kw in ("通行证查盒号", "通行证查询盒号", "兔兔通行证查盒号", "兔兔通行证查询盒号"):
        if text.startswith(kw) and len(text) > len(kw):
            return True, 1
    return False


async def _verify_waiting_input(data: Message):
    """仅在等待用户输入关键字时响应（排除快捷指令前缀）"""
    text = data.text.strip()
    for kw in ("通行证查干员", "通行证查角色", "通行证查盒号", "通行证查询盒号",
                "兔兔通行证查干员", "兔兔通行证查角色", "兔兔通行证查盒号", "兔兔通行证查询盒号"):
        if text.startswith(kw):
            return False
    state = get_state(str(data.user_id))
    if state.step in ("search_by_char", "search_by_box", "waiting_suggest"):
        return True, 1
    return False


@bot.on_message(verify=_verify_waiting_input, allow_direct=True)
async def waiting_input_handler(data: Message):
    # 去掉常见前缀（如 "老猫"），让不带前缀的输入也能被识别
    raw_text = data.text.strip()
    text = await _strip_prefix(raw_text)

    log.info(f"[waiting_input] user_id={data.user_id} raw={raw_text!r} text={text!r} step={get_state(str(data.user_id)).step}")

    state = get_state(str(data.user_id))

    if text.lower() == "q":
        clear_state(str(data.user_id))
        return Chain(data).text("已退出查询。发送「通行证」可重新开始。")

    if text.lower() == "b":
        state.step = "search_mode"
        return Chain(data).text(
            "🎴 明日方舟通行证查询\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请选择查询方式：\n"
            "① 按干员名查询盒号\n"
            "② 按盒号查询干员\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 输入 q 返回上级菜单"
        )

    # 数字切换（在菜单状态下可免唤醒词前缀，如 "老猫 1"）
    if text == "1":
        state.step = "search_by_char"
        return Chain(data).text(
            "🔍 按干员名查盒号\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请输入干员名称（支持中文名/英文名/日文名/外号）：\n"
            "例如：阿米娅、Amiya、Logics、李狗剩\n"
            "\n"
            "b - 返回上级菜单\n"
            "q - 退出查询"
        )
    if text == "2":
        state.step = "search_by_box"
        return Chain(data).text(
            "📦 按盒号查干员\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请输入盒号：\n"
            "例如：1、1.0、W-01、限定-03\n"
            "\n"
            "b - 返回上级菜单\n"
            "q - 退出查询"
        )

    if state.step == "search_by_char":
        return await _handle_character_search(data, text)
    elif state.step == "search_by_box":
        return await _handle_box_search(data, text)


# ============== 业务处理函数 ==============
async def _handle_character_search(data: Message, keyword: str):
    state = get_state(str(data.user_id))

    if keyword == "1":
        return Chain(data).text("请输入要查询的干员名称：")
    if keyword == "2":
        state.step = "search_by_box"
        return Chain(data).text("请输入要查询的盒号（如 1、1.0、W-01 等）：")

    if not keyword or len(keyword) < 1:
        return Chain(data).text("请输入有效的干员名称。")

    results = await search_character(keyword, enable_nickname=True, enable_english=True)

    if not results:
        box_data = _cache.get_data()
        sw_data = _cache.get_search_words()
        if box_data is None:
            return Chain(data).text(
                f"未获取到通行证数据，请先发送「刷新通行证数据」或「更新搜索词」后再试。"
            )
        if sw_data is None:
            return Chain(data).text(
                f"未找到与「{keyword}」相关的干员。\n"
                f"如需支持英文名/外号搜索，请先发送「更新搜索词」。"
            )
        return Chain(data).text(
            f"未找到与「{keyword}」相关的干员。\n请尝试其他名称，或输入 b 返回上级。"
        )

    lines = [f"🔍 「{keyword}」的查询结果（共 {len(results)} 条）：\n"]
    image_url = None
    for r in results[:10]:
        name = r["characterName"]
        boxes = r.get("boxes", [])
        match_t = r.get("matchType", "chinese")
        match_text = MATCH_TYPE_TEXT.get(match_t, "")
        if match_text:
            match_text = f"（{match_text}匹配）"
        only_e1 = " [仅精一]" if r.get("nolyELITE1") else ""
        hot = " 🔥" if r.get("hotcharacter") else ""
        boxes_str = "、".join(b["boxId"] for b in boxes) if boxes else "未找到盒号"
        lines.append(f"• {name}{hot}{only_e1}\n  所在盒号：{boxes_str}\n  {match_text}\n")

        # 如果只有一个盒且有图片，记录下来
        if len(boxes) == 1 and boxes[0].get("imageUrl"):
            image_url = boxes[0]["imageUrl"]

    if len(results) > 10:
        lines.append(f"\n…还有 {len(results) - 10} 条结果未显示。")

    # 多盒时提示用户可以查大图
    if not image_url:
        all_box_ids = [b["boxId"] for r in results[:10] for b in r.get("boxes", [])]
        if all_box_ids:
            examples = "、".join(f"「查官图{bid}」" for bid in all_box_ids[:3])
            lines.append(f"\n阿米娅为博士准备了以上 {len(all_box_ids)} 盒的通行证大图，例如{examples}等")

    lines.append("\n输入其他干员名继续查询，或 b 返回上级。")

    clear_state(str(data.user_id))
    state.step = "search_by_char"
    chain = Chain(data).text("".join(lines))
    if image_url:
        chain = chain.image(url=image_url)
    return chain


async def _handle_box_search(data: Message, keyword: str):
    state = get_state(str(data.user_id))

    if keyword == "1":
        state.step = "search_by_char"
        return Chain(data).text("请输入要查询的干员名称：")
    if keyword == "2":
        return Chain(data).text("请输入要查询的盒号（如 1、1.0、W-01 等）：")

    if not keyword or len(keyword) < 1:
        return Chain(data).text("请输入有效的盒号。")

    results = await search_box(keyword)

    if not results:
        box_data = _cache.get_data()
        if box_data is None:
            return Chain(data).text(
                f"未获取到通行证数据，请先发送「刷新通行证数据」或「更新搜索词」后再试。"
            )
        return Chain(data).text(
            f"未找到盒号「{keyword}」的相关信息。\n请确认盒号格式正确，或 b 返回上级。"
        )

    lines = [f"📦 盒号「{keyword}」的查询结果：\n"]
    for box in results[:5]:
        bid = box["boxId"]
        btype = get_box_type_text(box.get("boxType", "normal"))
        chars = box.get("characters", [])

        if not chars:
            continue

        lines.append("━━━━━━━━━━━━━━━━━━━━\n")
        lines.append(f"盒号：{bid}  类型：{btype}\n")
        lines.append("包含干员：\n")

        char_lines = []
        for c in chars:
            name = c["name"]
            e1_only = " [仅精一]" if c.get("nolyELITE1") else ""
            hot = " 🔥" if c.get("hotcharacter") else ""

            mp = c.get("market_price")
            price_text = ""
            if mp:
                if mp.get("ELITE1"):
                    price_text += f" 精一:{mp['ELITE1']}元"
                if mp.get("ELITE2") and not c.get("nolyELITE1"):
                    price_text += f" 精二:{mp['ELITE2']}元"
            if price_text:
                price_text = f" | 价:{price_text.strip()}"

            char_lines.append(f"  ★ {name}{hot}{e1_only}{price_text}")

        lines.append("\n".join(char_lines) + "\n")

    if len(results) > 5:
        lines.append(f"\n…还有 {len(results) - 5} 个相同盒号未显示。")
    lines.append("\n输入其他盒号继续查询，或 b 返回上级。")

    clear_state(str(data.user_id))
    state.step = "search_by_box"
    chain = Chain(data).text("".join(lines))
    # 查盒号时附带大图
    if results and results[0].get("imageUrl"):
        chain = chain.image(url=results[0]["imageUrl"])
    return chain


# ============== 入口命令 ==============
@bot.on_message(verify=_verify_entry, allow_direct=True)
async def entry(data: Message):
    log.info(
        f"[entry] triggered! user_id={data.user_id} channel_id={data.channel_id} "
        f"is_direct={data.is_direct} text={data.text!r}"
    )
    state = get_state(str(data.user_id))
    state.step = "search_mode"
    return Chain(data).text(
        "🎴 明日方舟通行证查询\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "请选择查询方式：\n"
        "① 按干员名查询盒号\n"
        "② 按盒号查询干员\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 输入 q 返回上级菜单"
    )


# ============== 全局退出（任意状态均可退出）==============
@bot.on_message(keywords=[Equal('q'), Equal('Q')], allow_direct=True)
async def quit_handler(data: Message):
    clear_state(str(data.user_id))
    return Chain(data).text("已退出查询。发送「通行证」可重新开始。")


# ============== 快捷指令（直接搜索，无需进入菜单）==============
@bot.on_message(keywords=["通行证查干员", "通行证查角色", "兔兔通行证查干员", "兔兔通行证查角色"], verify=_verify_direct_char, allow_direct=True)
async def direct_character_search(data: Message):
    log.info(f"[direct_char] user_id={data.user_id} text={data.text!r}")
    text = data.text.strip()
    for kw in ("通行证查干员", "通行证查角色", "兔兔通行证查干员", "兔兔通行证查角色"):
        if text.startswith(kw):
            keyword = text.replace(kw, "", 1).strip()
            break

    if not keyword:
        return Chain(data).text("用法：通行证查干员 <干员名>\n例如：通行证查干员 阿米娅")

    results = await search_character(keyword, enable_nickname=True, enable_english=True)

    if not results:
        return Chain(data).text(f"未找到与「{keyword}」相关的干员。")

    lines = [f"🔍 「{keyword}」的查询结果：\n"]
    image_url = None
    for r in results[:5]:
        name = r["characterName"]
        boxes = r.get("boxes", [])
        match_t = r.get("matchType", "chinese")
        match_text = MATCH_TYPE_TEXT.get(match_t, "")
        if match_text:
            match_text = f"（{match_text}匹配）"
        only_e1 = " [仅精一]" if r.get("nolyELITE1") else ""
        hot = " 🔥" if r.get("hotcharacter") else ""
        boxes_str = "、".join(b["boxId"] for b in boxes) if boxes else "未找到盒号"
        lines.append(f"• {name}{hot}{only_e1}\n  盒号：{boxes_str}\n  {match_text}\n")

        if len(boxes) == 1 and boxes[0].get("imageUrl"):
            image_url = boxes[0]["imageUrl"]

    if len(results) > 5:
        lines.append(f"\n…还有 {len(results) - 5} 条结果。")

    # 多盒时提示查大图
    if not image_url:
        all_box_ids = [b["boxId"] for r in results[:5] for b in r.get("boxes", [])]
        if all_box_ids:
            examples = "、".join(f"「查官图{bid}」" for bid in all_box_ids[:3])
            lines.append(f"\n阿米娅为博士准备了以上 {len(all_box_ids)} 盒的通行证大图，例如{examples}等")

    chain = Chain(data).text("".join(lines))
    if image_url:
        chain = chain.image(url=image_url)
    return chain


@bot.on_message(keywords=["通行证查盒号", "通行证查询盒号", "兔兔通行证查盒号", "兔兔通行证查询盒号"], verify=_verify_direct_box, allow_direct=True)
async def direct_box_search(data: Message):
    log.info(f"[direct_box] user_id={data.user_id} text={data.text!r}")
    text = data.text.strip()
    for kw in ("通行证查盒号", "通行证查询盒号", "兔兔通行证查盒号", "兔兔通行证查询盒号"):
        if text.startswith(kw):
            keyword = text.replace(kw, "", 1).strip()
            break

    if not keyword:
        return Chain(data).text("用法：通行证查盒号 <盒号>\n例如：通行证查盒号 1、通行证查盒号 W-01")

    results = await search_box(keyword)

    if not results:
        return Chain(data).text(f"未找到盒号「{keyword}」。")

    lines = [f"📦 盒号「{keyword}」查询结果：\n"]
    for box in results[:3]:
        bid = box["boxId"]
        btype = get_box_type_text(box.get("boxType", "normal"))
        chars = box.get("characters", [])
        if chars:
            char_names = "、".join(c["name"] for c in chars[:10])
            if len(chars) > 10:
                char_names += f"…等{len(chars)}名干员"
            lines.append(
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"盒号：{bid}  类型：{btype}\n"
                f"干员：{char_names}\n"
            )

    if len(results) > 3:
        lines.append(f"\n…还有 {len(results) - 3} 个相同盒号。")

    chain = Chain(data).text("".join(lines))
    if results and results[0].get("imageUrl"):
        chain = chain.image(url=results[0]["imageUrl"])
    return chain


# ============== 查官图（大图查询）===============
async def _verify_official_image(data: Message):
    text = data.text.strip()
    return "查官图" in text


@bot.on_message(verify=_verify_official_image, allow_direct=True)
async def search_official_image(data: Message):
    """处理「查官图XX」或「兔兔 查官图XX」的请求"""
    log.info(f"[official_image] user_id={data.user_id} text={data.text!r}")
    text = data.text.strip()

    # 提取盒号
    box_id = None
    for kw in ("查官图", "兔兔查官图", "兔兔 查官图"):
        if kw in text:
            box_id = text.split(kw, 1)[-1].strip()
            break

    if not box_id:
        return Chain(data).text("用法：查官图 <盒号>\n例如：查官图 1、查官图 W-01")

    # 先尝试从 search_box 结果获取图片 URL
    results = await search_box(box_id)
    image_url = None
    if results:
        image_url = results[0].get("imageUrl")

    # 如果 search_box 没有，尝试直接查找
    if not image_url:
        image_url = await get_box_image_url(box_id)

    if not image_url:
        return Chain(data).text(f"未找到盒号「{box_id}」的通行证大图。")

    return Chain(data).text(f"📦 盒号 {box_id} 的通行证大图：").image(url=image_url)


# ============== 数据刷新（使用新 refresh 函数，区分网络成功/失败提示）===============
@bot.on_message(keywords=["刷新通行证数据", "通行证更新数据"], allow_direct=True)
async def refresh_data(data: Message):
    ok1, msg1 = await refresh_character_data()
    ok2, msg2 = await refresh_search_words()
    return Chain(data).text(f"{msg1}\n{msg2}")


# ============== 搜索词单独更新（使用新 refresh 函数）===============
@bot.on_message(keywords=["更新搜索词", "通行证更新搜索词", "刷新搜索词"], allow_direct=True)
async def update_search_words(data: Message):
    ok, msg = await refresh_search_words()
    return Chain(data).text(msg)
