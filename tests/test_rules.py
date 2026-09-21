"""标题格式与判别逻辑的单测。"""

from __future__ import annotations

from oht_rules import (  # noqa: E402
    CATEGORY_EMOJI,
    MAX_TITLE_CHARS,
    clean_candidate,
    fingerprint,
    flatten_text,
    guess_language,
    is_pure_confirmation,
    is_titleable,
    iter_recent_turns,
    match_category,
    recent_user_texts,
    validate_title,
)


def test_pure_confirmation():
    for text in ["好的", "收到！", "继续", "ok", "Thanks!", "嗯嗯", "可以"]:
        assert is_pure_confirmation(text), text
    for text in ["把登录页改成响应式", "这个 bug 还没修完", "继续修支付回调"]:
        assert not is_pure_confirmation(text), text


def test_is_titleable():
    assert is_titleable("把登录页的布局改成响应式")
    assert not is_titleable("好的")
    assert not is_titleable("...")
    assert not is_titleable("ab")           # 短于 min_chars
    assert not is_titleable("/compress")    # 斜杠命令
    assert is_titleable("修一下", min_chars=2)


def test_flatten_and_turns():
    history = [
        {"role": "system", "content": "prompt"},
        {"role": "user", "content": "帮我改登录页布局"},
        {"role": "assistant", "content": "好的，我先看代码"},
        {"role": "tool", "content": "..."},
        {"role": "user", "content": [{"type": "text", "text": "再顺手加个 loading"}]},
    ]
    turns = iter_recent_turns(history, max_turns=5)
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[-1]["text"] == "再顺手加个 loading"
    assert recent_user_texts(history, max_turns=5) == ["帮我改登录页布局", "再顺手加个 loading"]
    assert flatten_text([{"type": "image", "url": "x"}]) == ""


def test_validate_title():
    assert validate_title("🧩 登录表单｜布局优化") == ("🧩 登录表单｜布局优化", "ok")

    # emoji 与分隔符之间缺空格 → 规范化补上
    assert validate_title("🧩登录表单｜布局优化")[0] == "🧩 登录表单｜布局优化"
    # 代码块围栏 / 引号 / "标题:" 前缀
    assert validate_title('```json\n"标题: 🎨 首页｜响应式适配"\n```')[0] == "🎨 首页｜响应式适配"
    # 多行只取第一行
    assert validate_title("🎬 品牌片｜转场设计\n理由: 随便")[0] == "🎬 品牌片｜转场设计"

    assert validate_title("登录表单｜布局优化")[1].startswith("bad_emoji")
    assert validate_title("🧩 登录表单|布局优化")[1].startswith("separator_count")
    assert validate_title("🧩 登录表单｜布局｜优化")[1].startswith("separator_count")
    assert validate_title("🧩 ｜布局优化")[1] == "empty_object"
    assert validate_title("🧩 登录表单｜")[1] == "empty_part"
    assert validate_title("")[1] == "empty"
    assert validate_title("🧩 " + "长" * (MAX_TITLE_CHARS + 5) + "｜目标")[1].startswith("too_long")


def test_every_category_emoji_validates():
    """回归：⚙️ 是「U+2699 + VS16」两码点，曾经被单码点比对判成 bad_emoji。"""
    for emoji in CATEGORY_EMOJI:
        title, reason = validate_title(f"{emoji} 对象名｜目标")
        assert reason == "ok", (emoji, reason)
        assert title == f"{emoji} 对象名｜目标"


def test_vs16_variants_normalize_to_canonical():
    # 模型可能写不带变体选择符的 ⚙，也要认，并归一化成表里的形式
    assert validate_title("⚙ 环境｜配置")[0] == "⚙️ 环境｜配置"
    assert validate_title("⚙️ 环境｜配置")[0] == "⚙️ 环境｜配置"
    assert match_category("⚙ taskbroad 插件") == ("⚙️", "taskbroad 插件")
    assert match_category("🧩 登录表单") == ("🧩", "登录表单")
    assert match_category("没有 emoji") == (None, "没有 emoji")
    # 别把普通字符误判成 emoji
    assert validate_title("OK 对象｜目标")[1].startswith("bad_emoji")


def test_clean_candidate():
    assert clean_candidate('"🧩 对象｜目标"') == "🧩 对象｜目标"
    assert clean_candidate("标题：🧩 对象｜目标") == "🧩 对象｜目标"


def test_language_and_fingerprint():
    assert guess_language(["帮我把登录页改一下布局"]).startswith("中文")
    assert guess_language(["refactor the auth module please"]).startswith("English")
    assert guess_language([]) == ""
    same = ["把登录页改成响应式", "再看下颜色"]
    assert fingerprint(same) == fingerprint(list(same))
    assert fingerprint(same) != fingerprint(["换个话题"])