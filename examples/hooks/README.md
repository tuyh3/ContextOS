# ContextOS SessionStart 探活 hook(组件 C)

> ops-localization 组件 C 横切基建。把 ContextOS 探活结果在会话启动时注入 context,
> 给所有 skill 的 Phase 0 探活提供"hook 注入"那一支(强 host)。
> 风格:纯文本 + ASCII 箭头(->)。

## 1. 这是什么

`scripts/contextos-health-sessionstart.sh` 是一个 Claude Code SessionStart hook 脚本。
会话启动时它跑一次 `contextos health`(探活体检 + profile 非敏感元信息),把结果 JSON
通过官方 `hookSpecificOutput.additionalContext` 注入到会话 context。skill(如
ops-localization / requirement-impact-analysis)的 Phase 0 就能直接读到探活结果,
不必每次自己重跑。

横切:这个 hook 惠及所有 skill,不是 ops-localization 专属。

## 2. 安装(.claude/settings.json 本身 gitignored,故给范例)

`.claude/settings.json` 被 `.gitignore` 的 `.claude/*` 规则忽略(不入仓),所以仓里给的是
范例 `examples/hooks/settings.json.example`。把它的 `hooks` 段合并进你的
`.claude/settings.json`(若该文件已有别的键如 permissions / enabledMcpjsonServers,
只加 `hooks` 顶层键,别覆盖):

    cp examples/hooks/settings.json.example .claude/settings.json
    # 或手动把 "hooks" 段 merge 进已有 .claude/settings.json

`$CLAUDE_PROJECT_DIR` 是 Claude Code 注入的项目根环境变量,脚本路径用它锚定,无需写绝对路径。
`timeout: 25`(秒)是 hook 框架层的硬超时;脚本内部还有自己的 `contextos health` 超时
(默认 20s,见下),两层都兜。

装好后重启会话(或开新会话)即生效。

## 3. 探活三段(skill Phase 0 怎么用)

探活结果有三种来源,skill Phase 0 按优先级用:

    hook 注入(强 host)     本 hook 在会话启动注入 additionalContext -> 直接读, 不重跑
    session 复用            本轮已探过(hook 注入或前轮自跑)-> 复用, 不重跑
    失效重探                 MCP 重启 / 跑过 incremental_rebuild / 工具报状态变化 /
                            环境变更 -> 重跑覆盖(自跑 contextos health 或 MCP health_check)

## 4. 弱 host 退化三分支(MUST —— hook 不进指令层)

hook 是 Claude Code 专属基建。skill 的指令层(Phase 0)**绝不假设 hook 存在**,按下面
三分支退化(这是 skill SKILL.md 的职责,本文档只点明,组件 C 保证"自跑"那一支的命令
`contextos health` 备齐且单跑产出与 hook 注入同构的 JSON):

    context 有 hook 注入                    -> 用注入的探活 JSON
    无注入但前轮自跑过(session 内有结果)    -> 复用前轮结果
    都没有(首轮 + 无 hook)                  -> 自跑 MCP health_check / `contextos health`

无 hook 的 host(非 Claude Code,或没装这个 hook)走第三支,功能不退化,只是每次自跑。

## 5. fail-open 契约(MUST)

脚本任何失败都 `exit 0` 并注入一段降级 JSON,绝不卡会话启动:

    contextos 未安装   -> additionalContext = "探活不可用(未找到 contextos 可执行)..."
    探活超时           -> additionalContext = "探活不可用(探活超时 20s)..."
    非零退出 / 空输出  -> 对应降级提示

降级提示里都带一句"skill Phase 0 请自跑 contextos health 或 MCP health_check",指引 skill
走退化第三支。

脚本级超时依赖 coreutils 的 `timeout` / `gtimeout`:Linux 自带 `timeout`;macOS 需
`brew install coreutils` 装 `gtimeout`。脚本顶部按 `timeout -> gtimeout -> 空` 探测,两者都
缺时退化为**裸跑** `contextos health`(无脚本级超时),此时依赖 hook 框架层的 `timeout: 25`
(见 §2 settings.json.example)兜底——会话仍不会被拖死,只是封顶从 20s 变成框架层的 25s。

可调环境变量:

    CONTEXTOS_HEALTH_TIMEOUT   脚本内 `contextos health` 超时秒数, 默认 20
    CONTEXTOS_BIN              contextos 可执行入口, 默认 contextos
                              (用 uv 时设 "uv run contextos"; 或设绝对路径)

## 6. 可手动验证步骤(hook 真触发部分无法单测,手动核)

脚本的纯逻辑部分(成功注入 / 各降级分支 / JSON 转义)有 pytest 自动测
(`contextos/cli/tests/test_health_hook.py`)。但"Claude Code 真在 SessionStart 触发本
hook 并把 additionalContext 喂进会话"这一环依赖 Claude Code 运行时,无法单测,按下面手动核:

1. 装好 hook(§2),确认 `scripts/contextos-health-sessionstart.sh` 有可执行位
   (`ls -l` 看到 `x`)。

2. 在仓根开一个新 Claude Code 会话,首条消息问:
   "把你 context 里 contextos 的探活信息(health / profile_info)原样贴出来。"
   预期:host 能贴出 `health`(jdt_ls / oracle / models / engine / code_projection /
   ripgrep)+ `profile_info`(data_dir / repo_root / rag_corpora 等)的内容 ->
   证明 additionalContext 注入生效。

3. 降级核验:临时把 `CONTEXTOS_BIN` 设成不存在的命令再开会话
   (在 settings.json 的 command 前加 `CONTEXTOS_BIN=nope `),首条消息同上。
   预期:host 贴出的探活信息是降级提示文案("探活不可用(未找到 ...)"),会话照常启动
   不卡 -> 证明 fail-open 生效。核完撤掉改动。

4. 超时核验(可选):设 `CONTEXTOS_HEALTH_TIMEOUT=1` 且让 `contextos health` 故意慢
   (例如指向一个真要冷启 JDT/Oracle 的 profile),开会话。预期:1 秒后注入"探活超时"降级,
   会话不卡。
