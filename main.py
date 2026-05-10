"""
明日方舟通行证查询插件 - Amiyabot
按干员名查询盒号 / 按盒号查干员

使用方式：
  发送「通行证」进入菜单 → 选 1/2 → 输入关键字
  直接指令：「通行证查干员 阿米娅」「通行证查盒号 1」
"""

import logging
import os

from amiyabot import PluginInstance, GroupConfig
from amiyabot import Message, Chain

# 本地 search_engine 在同目录
from .search_engine import (
    search_character,
    search_box,
    get_suggestions,
    get_box_type_text,
    load_character_data,
    load_search_words,
    BUILTIN_NICKNAMES,
)

log = logging.getLogger("amiyabot-arknights-authentication")

# ============== 插件实例 ==============
bot = PluginInstance(
    name="明日方舟通行证查询",
    version="1.0",
    plugin_id="amiyabot-arknights-authentication",
    description="查询明日方舟通行证干员信息和盒号信息",
)

fn_group = GroupConfig("通行证", check_prefix=False)
bot.set_group_config(fn_group)


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


# ============== 辅助函数（放在装饰器之前）==============
MATCH_TYPE_TEXT = {
    "chinese": "中文名",
    "english": "英文名",
    "japanese": "日文名",
    "nickname": "外号",
}


# 验证器：仅在等待用户输入关键字时响应（排除快捷指令和数字切换）
async def _verify_waiting_input(data: Message):
    text = data.text.strip()
    # 排除快捷指令
    for kw in ("通行证查干员", "通行证查角色", "通行证查盒号", "通行证查询盒号",
                "兔兔通行证查干员", "兔兔通行证查角色", "兔兔通行证查盒号", "兔兔通行证查询盒号"):
        if text.startswith(kw):
            return False
    # 排除数字切换（1/2 用于切换模式）
    if text in ("1", "2"):
        return False
    state = get_state(str(data.user_id))
    if state.step in ("search_by_char", "search_by_box", "waiting_suggest"):
        return True, 1
    return False


# 验证器：仅在 search_mode 阶段响应 1/2
async def _verify_search_mode(data: Message):
    state = get_state(str(data.user_id))
    if state.step == "search_mode":
        return True, 1
    return False


# 验证器：直接指令（通行证查干员/兔兔通行证查干员）
async def _verify_direct_char(data: Message):
    text = data.text.strip()
    for kw in ("通行证查干员", "通行证查角色", "兔兔通行证查干员", "兔兔通行证查角色"):
        if text.startswith(kw) and len(text) > len(kw):
            return True, 1
    return False


# 验证器：直接指令（通行证查盒号/兔兔通行证查盒号）
async def _verify_direct_box(data: Message):
    text = data.text.strip()
    for kw in ("通行证查盒号", "通行证查询盒号", "兔兔通行证查盒号", "兔兔通行证查询盒号"):
        if text.startswith(kw) and len(text) > len(kw):
            return True, 1
    return False


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
        return Chain(data).text(
            f"未找到与「{keyword}」相关的干员。\n请尝试其他名称，或输入 b 返回上级。"
        )

    lines = [f"🔍 「{keyword}」的查询结果（共 {len(results)} 条）：\n"]
    for r in results[:10]:
        name = r["characterName"]
        boxes = r.get("boxIds", [])
        match_t = r.get("matchType", "chinese")
        match_text = MATCH_TYPE_TEXT.get(match_t, "")
        if match_text:
            match_text = f"（{match_text}匹配）"
        only_e1 = " [仅精一]" if r.get("nolyELITE1") else ""
        hot = " 🔥" if r.get("hotcharacter") else ""
        boxes_str = "、".join(str(b) for b in boxes) if boxes else "未找到盒号"
        lines.append(f"• {name}{hot}{only_e1}\n  所在盒号：{boxes_str}\n  {match_text}\n")

    if len(results) > 10:
        lines.append(f"\n…还有 {len(results) - 10} 条结果未显示。")
    lines.append("\n输入其他干员名继续查询，或 b 返回上级。")

    clear_state(str(data.user_id))
    state.step = "search_by_char"
    return Chain(data).text("".join(lines))


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
    return Chain(data).text("".join(lines))


# ============== 入口命令 ==============
# verify: 仅当文本纯粹是入口关键词时触发，排除「通行证查干员」等快捷指令
async def _verify_entry(data: Message):
    text = data.text.strip()
    # 快捷指令前缀，不触发入口
    for prefix in ("通行证查干员", "通行证查角色", "通行证查盒号", "通行证查询盒号",
                    "兔兔通行证查干员", "兔兔通行证查角色", "兔兔通行证查盒号", "兔兔通行证查询盒号"):
        if text.startswith(prefix):
            return False
    return True, 1

@bot.on_message(keywords=["通行证", "方舟通行证", "方舟谷子", "通行证查询"], verify=_verify_entry, allow_direct=True)
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


# ============== 模式选择（search_mode 阶段响应 1/2）==============
@bot.on_message(keywords=["1"], verify=_verify_search_mode, allow_direct=True)
async def choose_char_search(data: Message):
    state = get_state(str(data.user_id))
    state.step = "search_by_char"
    return Chain(data).text(
        "🔍 按干员名查盒号\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "请输入干员名称（支持中文名/英文名/日文名/外号）：\n"
        "例如：阿米娅、Amiya、Logos、李狗剩\n"
        "\n"
        "b - 返回上级菜单\n"
        "q - 退出查询"
    )


@bot.on_message(keywords=["2"], verify=_verify_search_mode, allow_direct=True)
async def choose_box_search(data: Message):
    state = get_state(str(data.user_id))
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


# ============== 等待输入处理（任意文本）==============
@bot.on_message(verify=_verify_waiting_input, allow_direct=True)
async def waiting_input_handler(data: Message):
    log.info(f"[waiting_input] user_id={data.user_id} text={data.text!r} step={get_state(str(data.user_id)).step}")
    text = data.text.strip()

    if text.lower() == "q":
        clear_state(str(data.user_id))
        return Chain(data).text("已退出查询。发送「通行证」可重新开始。")

    if text.lower() == "b":
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

    state = get_state(str(data.user_id))

    # 数字切换：在搜索状态下输入 1/2 切换模式
    if text == "1":
        state.step = "search_by_char"
        return Chain(data).text(
            "🔍 按干员名查盒号\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请输入干员名称（支持中文名/英文名/日文名/外号）：\n"
            "例如：阿米娅、Amiya、Logos、李狗剩\n"
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
    for r in results[:5]:
        name = r["characterName"]
        boxes = r.get("boxIds", [])
        match_t = r.get("matchType", "chinese")
        match_text = MATCH_TYPE_TEXT.get(match_t, "")
        if match_text:
            match_text = f"（{match_text}匹配）"
        only_e1 = " [仅精一]" if r.get("nolyELITE1") else ""
        hot = " 🔥" if r.get("hotcharacter") else ""
        boxes_str = "、".join(str(b) for b in boxes) if boxes else "未找到盒号"
        lines.append(f"• {name}{hot}{only_e1}\n  盒号：{boxes_str}\n  {match_text}\n")

    if len(results) > 5:
        lines.append(f"\n…还有 {len(results) - 5} 条结果。")

    return Chain(data).text("".join(lines))


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

    return Chain(data).text("".join(lines))


# ============== 数据刷新 ==============
@bot.on_message(keywords=["刷新通行证数据", "通行证更新数据"], allow_direct=True)
async def refresh_data(data: Message):
    try:
        await load_character_data(force=True)
        await load_search_words(force=True)
        print('1')
        return Chain(data).text("✅ 通行证数据已刷新。")
    except Exception as e:
        return Chain(data).text(f"❌ 数据刷新失败：{e}")
