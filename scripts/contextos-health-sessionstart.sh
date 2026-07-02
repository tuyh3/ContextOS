#!/usr/bin/env bash
# ContextOS SessionStart hook —— ops-localization 组件 C。
#
# 职责: 在 Claude Code 会话启动时跑一次 `contextos health`, 把探活 JSON 注入
#       会话 context(hookSpecificOutput.additionalContext), 给所有 skill 的
#       Phase 0 探活提供"hook 注入"那一支(强 host)。横切基建, 惠及所有 skill。
#
# 红线(MUST):
#   - fail-open: 任何失败(CLI 缺失 / 超时 / 非零退出)都 exit 0 + 注入降级 JSON,
#     绝不卡会话启动。探活不可用 != 会话不可用。
#   - timeout: 探活封顶 ${CONTEXTOS_HEALTH_TIMEOUT:-20} 秒, 防 JDT/Oracle 冷启拖死启动。
#     脚本级超时需要 coreutils 的 timeout / gtimeout(Linux 自带 timeout; macOS 需
#     `brew install coreutils` 装 gtimeout)。两者都缺时本脚本退化为裸跑(无脚本级超时),
#     依赖 Claude Code hook 框架层 timeout:25(见 settings.json.example)兜底。
#   - 注入格式: 官方 SessionStart 契约 stdout JSON
#     {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<字符串>"}}
#     additionalContext 是字符串(把 health JSON 字符串原样塞进去, host 当文本读)。
#   - hook 不进指令层: 本脚本只"注入", 不假设 skill 会用; skill Phase 0 自有退化三分支。
#
# 安装: 见 examples/hooks/README.md。脚本放 tracked 的 scripts/(避开 .gitignore 的 .claude/*)。

set -u  # 注意: 不开 set -e —— fail-open 要靠我们显式控制退出码, errexit 会破坏 fail-open。

TIMEOUT_SECS="${CONTEXTOS_HEALTH_TIMEOUT:-20}"
# 允许用环境变量覆盖 contextos 可执行入口(uv run / 绝对路径 / venv); 默认裸 contextos。
CONTEXTOS_BIN="${CONTEXTOS_BIN:-contextos}"

# 探测脚本级超时命令: 优先 GNU coreutils 的 timeout(Linux), 次 gtimeout(macOS brew),
# 两者都无则空字符串 -> 裸跑, 退 Claude Code hook 框架层 timeout 兜底(见脚本头注释)。
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout"
else
  TIMEOUT_CMD=""
fi

# 把任意字符串安全嵌进 JSON 字符串字面量(转义 \ 和 ", 删控制字符换行)。
# 纯 shell 实现, 不依赖 jq/python(hook 跑在用户环境, 不能假设有解释器)。
json_escape() {
  # 读 stdin, 输出转义后(不含外层引号)的内容。
  local s
  s="$(cat)"
  s="${s//\\/\\\\}"   # 反斜杠先转
  s="${s//\"/\\\"}"   # 双引号
  s="${s//$'\n'/\\n}" # 换行
  s="${s//$'\r'/}"    # 回车删
  s="${s//$'\t'/\\t}" # 制表
  printf '%s' "$s"
}

emit() {
  # $1 = 已转义的 additionalContext 字符串内容
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$1"
}

emit_degraded() {
  # $1 = 降级原因短语
  local note="[contextos-health] 探活不可用($1)。skill Phase 0 请自跑 contextos health 或 MCP health_check。"
  emit "$(printf '%s' "$note" | json_escape)"
  exit 0
}

# 命令存在性检查(fail-open: 没装 contextos 不报错, 注入降级提示)。
if ! command -v "${CONTEXTOS_BIN%% *}" >/dev/null 2>&1; then
  emit_degraded "未找到 ${CONTEXTOS_BIN} 可执行"
fi

# 跑探活, 带超时(若有 timeout/gtimeout); 否则裸跑(依赖 hook 框架层 timeout 兜底)。
# stderr 丢弃(探活的诊断噪音不该污染 hook stdout 的 JSON 契约)。
# timeout/gtimeout 退出码 124 = 超时。
if [ -n "${TIMEOUT_CMD}" ]; then
  HEALTH_JSON="$("${TIMEOUT_CMD}" "${TIMEOUT_SECS}" ${CONTEXTOS_BIN} health 2>/dev/null)"
  RC=$?
else
  HEALTH_JSON="$(${CONTEXTOS_BIN} health 2>/dev/null)"
  RC=$?
fi

if [ "$RC" -eq 124 ]; then
  emit_degraded "探活超时 ${TIMEOUT_SECS}s"
fi
if [ "$RC" -ne 0 ]; then
  emit_degraded "contextos health 非零退出 rc=${RC}"
fi
if [ -z "${HEALTH_JSON}" ]; then
  emit_degraded "探活输出为空"
fi

# 成功: 把 health JSON 原样转义后注入。
emit "$(printf '%s' "${HEALTH_JSON}" | json_escape)"
exit 0
