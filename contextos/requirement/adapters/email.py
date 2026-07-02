"""email 源适配器(source_kind="email"): 取 text/plain 最新一轮 + 剥引用历史/签名。

.eml 文件路径由调用方显式配 adapter_kind="email" 传入(不做后缀自动探测, 红线 #9); 本模块不判后缀。
"""
from __future__ import annotations

import email
import re
from email import policy

from contextos.requirement.adapters.base import AdapterResult, parse_failure, register

# 引用历史分隔(双语): 切在第一处之前。
# From: 行要求含 @(真实发件地址), 不切裸 "From: UserService" 这种接口文档标签。
_QUOTE_RE = re.compile(
    r"(?m)^(-----\s*原始邮件\s*-----|-----\s*Original Message\s*-----|"
    r"From:.*@|发件人[:：]\s?|On .+ wrote:|在.+写道[:：])"
)
_SIGN_RE = re.compile(r"(?m)^(Regards|Best regards|Thanks|此致|谢谢)[,，]?\s*$")
# 签名行后若还有"像需求的行"(编号/项目符号/段标题/长行), 说明这处寒暄在正文中而非末尾, 不剥。
_CONTENT_LINE_RE = re.compile(r"^\s*(\d+[.、)]|[-*]|[一二三四五六七八九十]+[.、)])")

# known limitation(本轮未修, 残留启发式 gap):
# (a) Outlook 折行的 "On ...\nwrote:" 归属行被换行打断, 不被 _QUOTE_RE 命中;
# (b) 裸 "发件人:" / 不含 @ 的 "From:" 后接 "To:" 的接口标注, 仍可能误切或误留;
# (c) _SIGN_RE 大小写敏感, 小写 "best regards" 漏网(当作正文保留, 宁留噪音不丢内容);
# (d) 签名词后若紧跟 1-3 行极短(<8 字, 无编号无冒号)裸需求, 会被当签名块静默剥掉(宁噪音不丢内容的边界代价)。


def _line_looks_like_content(line: str) -> bool:
    """签名行之后的某行是否"像需求正文"(编号/项目符号/段标题/较长行)。

    用于区分"真·末尾签名"(其后只剩短名字/职务行)与"正文中的寒暄"(其后还有需求)。
    宁可判成 content(保留不剥)也不漏丢, 阈值偏保守。
    """
    s = line.strip()
    if not s:
        return False
    if _CONTENT_LINE_RE.match(s):
        return True
    if s.endswith((":", "：")):  # "二期需求:" 这类段标题
        return True
    return len(s) >= 8  # 较长行更像正文而非签名档的名字/职务


def parse_email(raw_input: str) -> AdapterResult:
    try:
        with open(raw_input, "rb") as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
    except FileNotFoundError:
        return parse_failure(f"文件不存在: {raw_input}")
    except Exception as e:  # 损坏 / 非 eml
        return parse_failure(f"{type(e).__name__}: {e}")

    plain = None
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            plain = part.get_content()
            break
    if plain is None:
        return parse_failure("邮件无 text/plain 正文")

    # 截最新一轮(第一处引用分隔前)
    m = _QUOTE_RE.search(plain)
    latest = plain[: m.start()] if m else plain
    # 剥签名: 只剥"真·末尾签名"(取最后一处匹配; 尾部 <=4 非空行且签名行之后无"像需求的行"),
    # 防开头/正文中寒暄(如 "Thanks" 在需求前)被当签名切掉而静默丢需求(宁留签名噪音不丢内容)。
    sign_matches = list(_SIGN_RE.finditer(latest))
    if sign_matches:
        sm = sign_matches[-1]
        tail_lines = [ln for ln in latest[sm.start():].splitlines() if ln.strip()]
        after_sign = tail_lines[1:]  # 去掉签名行本身, 看其后是否还有正文
        looks_like_content = any(_line_looks_like_content(ln) for ln in after_sign)
        if len(tail_lines) <= 4 and not looks_like_content:
            latest = latest[: sm.start()]
    latest = latest.strip()
    if not latest:
        return parse_failure("最新一轮为空")
    return AdapterResult(raw_text=latest)


register("email", parse_email)
