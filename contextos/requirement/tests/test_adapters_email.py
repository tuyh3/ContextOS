"""email adapter 测试(中性合成 .eml)。

设计思路: 只取最新一轮 text/plain, 剥引用历史(From:/发件人:/-----Original/-----原始邮件)
与签名; source_kind 注册名 = "email"(非 "eml")。
评分标准: 最新一轮内容在, 引用历史不在; register("email") 可被 get_adapter 取到。
脚本逻辑: 构造多轮 .eml 字节, 走 parse_email, 断言 raw_text。
"""
from __future__ import annotations

import email.message

from contextos.requirement.adapters import get_adapter
from contextos.requirement.adapters.email import _line_looks_like_content, parse_email


def _make_eml() -> str:
    msg = email.message.EmailMessage()
    msg["Subject"] = "需求"
    msg["From"] = "a@x.com"
    msg["To"] = "b@x.com"
    msg.set_content(
        "最新一轮:\n1. 新增配置开关\n2. 前台界面\n\n"
        "Regards,\nJoe\n"
        "-----原始邮件-----\n"
        "发件人: c@x.com\n旧一轮: 这是历史引用, 不该进 raw_text\n"
    )
    return msg.as_string()


def test_email_takes_latest_round_only(tmp_path):
    p = tmp_path / "r.eml"
    p.write_text(_make_eml(), encoding="utf-8")
    res = parse_email(str(p))
    assert "新增配置开关" in res.raw_text
    assert "前台界面" in res.raw_text
    assert "历史引用" not in res.raw_text


def test_email_registered_under_email_source_kind():
    assert get_adapter("email") is parse_email


def _eml_bytes(body: str) -> bytes:
    """按既有 _make_eml 同款构造 .eml 字节(EmailMessage + set_content)。"""
    msg = email.message.EmailMessage()
    msg["Subject"] = "需求"
    msg["From"] = "a@x.com"
    msg["To"] = "b@x.com"
    msg.set_content(body)
    return msg.as_bytes()


def _parse_body(tmp_path, body: str):
    p = tmp_path / "r.eml"
    p.write_bytes(_eml_bytes(body))
    return parse_email(str(p))


def test_email_pleasantry_before_requirements_not_dropped(tmp_path):
    """寒暄(Thanks)出现在需求之前时, 需求绝不能被签名剥除逻辑静默丢掉。

    设计思路: 旧逻辑 _SIGN_RE.search 命中正文开头的 "Thanks" 就把后面全切掉,
    "Hi,\\nThanks\\n1. 需要增加配置开关" 会只剩 "Hi,"。新逻辑只剥真·末尾签名。
    评分标准: raw_text 必须 CONTAINS "需要增加配置开关"(内容存活); 不要求 Thanks 被剥。
    脚本逻辑: 构造该正文 .eml, parse_email, 断言需求串在 raw_text 内。
    """
    res = _parse_body(tmp_path, "Hi,\nThanks\n1. 需要增加配置开关\n")
    assert "需要增加配置开关" in res.raw_text


def test_email_midbody_pleasantry_not_a_cut_point(tmp_path):
    """正文中段的寒暄(后面还有第二批需求)不能成为切断点。

    设计思路: "1. 配置开关\\nThanks\\n二期需求:\\n2. 前台页面" 里 Thanks 在中段,
    其后还有需求行; 签名行之后存在"像需求的行", 故判定为正文寒暄, 不剥。
    评分标准: raw_text 必须 CONTAINS "前台页面"(中段寒暄后的需求存活)。
    脚本逻辑: 构造该正文 .eml, parse_email, 断言后段需求串在 raw_text 内。
    """
    res = _parse_body(tmp_path, "1. 配置开关\nThanks\n二期需求:\n2. 前台页面\n")
    assert "前台页面" in res.raw_text


def test_email_genuine_trailing_signature_stripped(tmp_path):
    """真·末尾签名(Regards + 名字 + 职务)应被剥除, 同时需求行存活。

    设计思路: "...2. 前台页面\\nRegards,\\nJohn\\nPM" 中 Regards 之后只剩短名字/职务行
    (无"像需求的行"且尾部 <=4 非空行), 判为真签名予以剥除。
    评分标准: raw_text CONTAINS "前台页面"(需求存活) 且不含/不以 "PM" 结尾(签名被剥)。
    若阈值改动导致留少量签名噪音, 内容存活优先 -- 但本用例当前实现确实剥净。
    脚本逻辑: 构造带末尾签名的正文 .eml, parse_email, 双断言。
    """
    res = _parse_body(tmp_path, "1. 配置开关\n2. 前台页面\nRegards,\nJohn\nPM\n")
    assert "前台页面" in res.raw_text
    assert "PM" not in res.raw_text


def test_email_from_interface_label_not_cut(tmp_path):
    """正文里的 "From: UserService"(接口文档标签, 不含 @)不能被当引用历史切掉。

    设计思路: 旧 _QUOTE_RE 的 "From:\\s" 会误切接口标注; 新规则要求 From 行含 @,
    裸 "From: UserService" 不再命中, 接口需求说明得以保留。
    评分标准: raw_text 必须 CONTAINS "接口需求说明"(接口正文存活)。
    脚本逻辑: 构造含 From/To 接口标注的正文 .eml, parse_email, 断言正文串在内。
    """
    res = _parse_body(
        tmp_path, "接口文档:\nFrom: UserService\nTo: AccountService\n接口需求说明\n"
    )
    assert "接口需求说明" in res.raw_text


def test_email_real_forwarded_header_still_cuts(tmp_path):
    """真转发头 "From: sender@old.com"(含 @)仍应切掉其后历史内容。

    设计思路: 新规则只放过不含 @ 的 From 标签; 真发件地址行(含 @)依旧是引用历史界标,
    其后旧一轮历史必须被截掉。
    评分标准: raw_text CONTAINS "正文需求"(最新一轮存活) 且 NOT CONTAINS "旧一轮历史内容"。
    脚本逻辑: 构造正文 + 空行 + 真 From 头 + 历史的 .eml, parse_email, 双断言。
    """
    res = _parse_body(
        tmp_path,
        "正文需求一二三\n\nFrom: sender@old.com\nSent: yesterday\n旧一轮历史内容\n",
    )
    assert "正文需求" in res.raw_text
    assert "旧一轮历史内容" not in res.raw_text


def test_line_looks_like_content_threshold():
    """锁定 _line_looks_like_content 判别契约(签名剥离的内容判别核心)。
    设计思路: 编号/项目符号行、冒号结尾(小标题)、>=8 字长行 = 像正文; 短裸行 = 不像。
    评分标准: 边界 7 字 False / 8 字 True; 编号行与冒号行 True; 3 字中文裸片段 False。
    脚本逻辑: 直接对一组代表性输入断言布尔。
    """
    assert _line_looks_like_content("1. 配置开关") is True       # 编号行(len=7, 但 regex 命中)
    assert _line_looks_like_content("子项一二三：") is True       # 全角冒号结尾小标题(len=6)
    assert _line_looks_like_content("七个字刚好七七") is False    # 7 字, 阈值下界外(len=7 < 8)
    assert _line_looks_like_content("八个字刚好满八字") is True   # 8 字, 刚过阈值(len=8 >= 8)
    assert _line_looks_like_content("加开关") is False            # 3 字裸片段(已知边界: 会被当签名)


def test_email_adapter_enabled_in_example_profile():
    """profile.example.toml 的 [input].adapters 应含 email = true(否则 .eml 被 gate 拒)。

    设计思路: profile gate 决定哪些 source_kind 允许通过; email adapter 已实装但 gate
    未放行等于无效 -- 需在 example profile 里声明 email = true 作为默认启用状态。
    评分标准: tomllib 解析后 data["input"]["adapters"]["email"] is True。
    脚本逻辑: 打开仓根 config/profile.example.toml, 断言 email 键存在且为 true。
    """
    import tomllib
    from pathlib import Path
    # sentinel 搜索:向上找第一个含 pyproject.toml 的目录 = 仓根(测试文件移动也安全)
    repo_root = next(p for p in Path(__file__).parents if (p / "pyproject.toml").exists())
    with open(repo_root / "config" / "profile.example.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["input"]["adapters"].get("email") is True
