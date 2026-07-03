<div align="center">

# ContextOS

**存量大型项目的 AI 证据底座** — 把代码 / 数据库 / 配置 / 业务文档变成 AI 可查证的结构化证据,其上用 skill 衍生开发 / 测试 / 维护能力。

21 个 MCP 工具,Claude Code / Cursor / Codex / OpenCode 零胶水接入。

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/包管理-uv-DE5FE9)
![MCP](https://img.shields.io/badge/接入-MCP%20原生-000000)
![平台](https://img.shields.io/badge/平台-Linux%20·%20macOS%20·%20Windows-informational)
![状态](https://img.shields.io/badge/状态-v1%20实现完成·评测中-2EA043)

</div>

---

一份需求文档落到桌上。它到底会动到哪些方法、哪些数据库表、哪些配置项?

老办法是:grep 几个关键词、问问写过这块的老员工、再凭经验补几个。漏掉的那部分,往往要等到测试阶段才暴露——那时候返工最贵。

ContextOS 把这件事做成一个**可复现、带证据、可审计**的中间层:需求进去,Impact Map 出来。它不替你做决定,而是把"这个需求碰到了哪里、凭什么这么判、有多大把握"摊开给你看。

而这只是长在底座上的第一个能力。ContextOS 真正做的事,是把一个存量项目的代码、数据库、配置、业务文档**全部变成 AI 可查证的结构化证据**——需求影响分析是第一个长出来的 skill,运维根因定位是第二个;更多开发 / 测试 / 维护能力用纯指令层 skill 即可继续衍生,不用写一行新代码。

> 目标不是给你一张炫技的依赖大图,而是让 AI 在你的项目里说的每句话都有出处。

---

## ✨ 能力特性

- **Impact Map 三维定位** — 受影响的**方法**、**SQL 表**、**配置项**,每条附 evidence 链(命中在哪、怎么命中的)+ 置信度分桶(HIGH / MEDIUM / LOW)。

- **四座证据桥** — 代码搜索(走 JDT LS 同源投影,编译级符号精度)/ 数据库血缘(SQL -> 表的字段级关系)/ 配置维度(配置项 <-> 代码绑定)/ 多源 RAG(业务文档语料)。四桥互不调用、各管一摊,结果在融合层汇总。

- **Corroboration 透明置信度** — 多桥命中同一目标会叠加加权打分,落进 HIGH / MEDIUM / LOW 三桶。是**显式加权公式**,不是黑箱模型——每个分数都能追到是哪几座桥、各贡献多少。

- **MCP 原生** — 21 个对外 tool,Claude Code / Cursor / Codex 直接接,无需写适配代码。一个总入口 `build_impact_map` + 16 个证据查询 tool + 3 个元工具 + 1 个运维回写 tool。

- **查询期零预热** — 代码索引落成本地 SQLite 持久投影(code_* 9 张表),查询不再现起 Java 语言服务器,server 秒级启动、查询毫秒级。

- **内建安全闸门** — 数据库一律只读、测试库白名单(含 PROD / LIVE 等关键字的连接运行时硬拒)、敏感值(密码 / key / 配置原始值)绝不进输出·prompt·artifact。

- **经验持久化(human-gated)** — 专家确认过的根因经 `record_confirmed_case` 回写案例库,进语料、可被检索——同类问题第二次出现,起点不再是零。只有人确认才入库,绝不自动学习(详见能力层)。

- **证据底座 + Skill 应用层** — 底座对能力无关;之上两个 Claude Code Skill(需求影响分析 / 运维根因定位)已实装——零新增代码、纯指令层复用 MCP 工具。同样的方式可继续衍生测试圈定、数据订正评估等能力(详见下文)。

---

## 🏗️ 架构

六层拓扑,数据自底向上流动,最终在集成层对外暴露:

```
  集成层    MCP server (21 tools)  /  CLI  /  Python lib
   |        对外入口,host 输入硬隔离 + 输入校验中间件
   v
  输出层    Impact Map   ->   方法 / SQL表 / 配置项  +  evidence  +  置信度
   ^
   |
  融合层    LLM 软投票重排   ->   数据流编排 (corroboration 置信度分桶)
   ^
   |
  证据层    +------------+------------+------------+------------+
            | 代码搜索桥 | DB 血缘桥  | 配置维度桥 | 多源 RAG桥 |   (四桥并行,互不调用)
            | JDT LS投影 | SQL->表    | 配置<->代码| sparse+rer |
            +------------+------------+------------+------------+
   ^
   |
  输入层    需求拆解   docx/文本  ->  结构化候选词 + 现状核对防脑补
   ^
   |
  横切底座  Profile 配置  |  存储 (SQLAlchemy/信创PG)  |  LLM 网关  |  代码索引 (JDT LS)
```

每一层只依赖下层、对上层暴露稳定契约。证据桥之间零耦合——加一座新桥不会动到其它桥。

---

## 💻 平台支持

CI 在 **Linux / macOS / Windows** 三平台跑全量单测(`.github/workflows/ci.yml` 的 os matrix)。诚实分两层看:

| 平台 | 单测 / CLI / MCP(not-integration) | 真实 Java 项目索引(JDT LS 端到端) |
|---|---|---|
| **macOS** | 支持(CI + 主力开发) | 支持(主力验证) |
| **Linux**(Ubuntu) | 支持(CI) | 预期可用——与 macOS 同一 POSIX 代码路径,未做专门真机验证 |
| **Windows** | 支持(CI + 真机跑通 1390 passed / 0 failed) | 验证中——起真实 JDT LS 索引真 Java 项目这条端到端链路(含 jar 路径分隔符等)尚未在 Windows 跑通 |

跨平台已落地:子进程命令边界、路径归一(统一 `as_posix`)、文件锁(POSIX `fcntl` / Windows 分派)、Windows 专用 profile 范本(`config/profile.example.windows.toml`,TOML 里路径用正斜杠 `C:/...`)。**一句话**:单测 / CLI / MCP 三平台都绿;Windows 上唯一还在收尾的是"真实 JDT LS 索引真项目"这条 E2E,Linux 与 macOS 同源预期一致。

---

## 🚀 快速开始

环境用 [uv](https://github.com/astral-sh/uv)(day-1 锁死,不走 pip/venv)。

```bash
# 1. 装依赖
uv sync

# 2. 配主配置 profile(代码仓路径 / LLM / DB 实例 / 语料目录)
cp config/profile.example.toml config/profile.toml            # Windows 用 profile.example.windows.toml
#   然后编辑 config/profile.toml,把路径填成你自己的项目
#   其中 [jdtls_runtime] 三个路径不用手填 —— 先跑 `uv run contextos health`,
#   它会自动探测并打印现成路径给你抄(详见下面"JDT LS 运行时从哪来")

# 3. 配凭据 .env:LLM API key(+ 连 DB 才要的 Oracle 账号)
cp .env.example .env
#   编辑 .env,填 profile [llm].api_key_env 指定的那个变量,如 DEEPSEEK_API_KEY=sk-...
#   真密钥只放 .env(已 gitignore),profile.toml 里只写变量名。不接真 LLM 可跳过(走 FakeLLM)。

# 4. 一键初始化:四维证据全 build 出来(代码投影 / DB 血缘 / 配置维度 / RAG 语料)
uv run contextos init
#   只 build 单维:  uv run contextos init --only code   # code|database|config|corpus
#   不连数据库:     uv run contextos init --skip-oracle

# 体检各子系统是否就绪
uv run contextos health
```

### 配置文件清单

跑起来最多涉及四个文件(后两个按需):

| 文件 | 作用 | 从哪来 |
|---|---|---|
| `config/profile.toml` | 主配置:仓路径 / LLM / JDT LS / DB / 语料 | 拷 `config/profile.example.toml`(Windows 拷 `config/profile.example.windows.toml`) |
| `.env` | 凭据:LLM API key、Oracle 账号密码 | 拷 `.env.example`;**真密钥只放这里,已 gitignore** |
| `.mcp.json` | 接 AI 编辑器时的 MCP server 注册(见下方用法 A) | 参 `examples/telecom-bss/.mcp.json.example` |
| `tnsnames.ora` | 连 Oracle 才需要;放进 `[oracle].tns_admin` 指的目录 | 你的 DBA 提供 |

分工的关键:`profile.toml` 填**变量名**(`api_key_env = "DEEPSEEK_API_KEY"`),`.env` 填**变量值**(`DEEPSEEK_API_KEY=sk-...`)——两边靠变量名对上,密钥永不进 toml。Oracle 账号密码同理走 `.env`(`ORACLE_<实例名>_USER/PASSWORD`)。下面先说主配置 `profile.toml` 各段怎么改。

### 配置文件里要改什么

`profile.toml` 分若干命名空间,完整字段见 `config/profile.example.toml` 的内联注释。第一次跑,你主要动这几处:

- **`[[projects]]`** — `path` 指向你的 Java 项目根;`java = { gradle_home, gradle_java_home, ... }` 填**编译该项目**用的 Gradle 和 JDK(老项目常是 JDK 8——跟下面跑 JDT LS 的 `java_home` 不是一回事,别填混)。
- **`[llm]`** — `provider` / `model` / `base_url` 填你的 LLM 网关;`api_key_env` 填**环境变量名**(如 `ANTHROPIC_API_KEY`),真正的 key 放 `.env` 或环境里、**不写进 toml**(见 `.env.example`)。只想先跑通不接真 LLM,留空即走内置 FakeLLM。
- **`[jdtls_runtime]`** — JDT LS / lombok / JRE 三路径(见下一小节,`contextos health` 会自动探测)。
- **`[oracle]`** — `tns_admin` 指向 tnsnames 目录,`allowed_instances` 列白名单测试库;**没有数据库就跳过**,用 `contextos init --skip-oracle` 不碰这段(含 `PROD`/`LIVE` 等关键字的连接一律运行时硬拒)。
- **`[[corpus.sources]]`**(可选)— 指向业务文档目录/仓给 RAG 用;不配则语料为空。

其余(`[storage].data_dir`、`[reranker]`、`[llm_rerank]`、`[input.scope]` 三道 guard、`[code]` / `[tables]` / `[code_index]` 旋钮)都有合理默认,先用默认、按需再调。**你的真实 `profile.toml` 含绝对路径 + 库名,已 gitignore,别提交。**

### JDT LS 运行时从哪来([jdtls_runtime] 三个路径)

profile 里有一段长这样,是代码维度的运行时依赖 —— JDT LS(Eclipse 的 Java 语言服务器)本体、
lombok 注解处理 jar、以及跑 JDT LS 用的 JRE(21+):

```toml
[jdtls_runtime]
jdtls_path  = "..."   # JDT LS 安装目录(里面有 plugins/ 和 config_* 子目录)
lombok_path = "..."   # lombok jar 文件
java_home   = "..."   # 跑 JDT LS 的 JRE/JDK 根目录(21+)
```

注意:这个 `java_home` 是"跑语言服务器"的新版 Java,跟 `[[projects]]` 里编译你业务项目的
`gradle_java_home`(老项目常见 JDK 8)是两回事,别填成同一个。

**路线零(最省事):下载 runtime bundle。** Release 页下载对应平台的
`contextos-runtime-<版本>-<平台>` 压缩包,解压到仓根 `runtime/` 目录(解压后会看到
`runtime/contextos-runtime/`,这是正常的),然后跑
`uv run contextos health` —— 探到后会给出可直接照抄的四行配置(含 `code_index.indexer_jar`,
连代码索引器 jar 都不用自己构建)。内网环境把包拷进去解压即可,全程不联网。

**路线一:复用 VSCode 的 Java 扩展。** 如果装过 VSCode 并且装了
[Extension Pack for Java](https://marketplace.visualstudio.com/items?itemName=vscjava.vscode-java-pack)
(或单独的 "Language Support for Java by Red Hat"),这三样你机器上已经全有了,跑一下
体检命令,它会自动扫描 `~/.vscode/extensions/` 并把现成路径递给你:

```bash
uv run contextos health
```

`health` 输出是一大坨 JSON(各子系统体检表),你要的路径在 `health.jdtls_runtime.suggestion`
这一段。当 profile 里的三条路径还没填 / 填错时,这段长这样:

```json
"jdtls_runtime": {
  "status": "missing",
  "missing": ["jdtls_path", "lombok_path", "java_home"],
  "suggestion": {
    "jdtls_path":  "/Users/you/.vscode/extensions/redhat.java-1.54.0-darwin-arm64/server",
    "lombok_path": "/Users/you/.vscode/extensions/redhat.java-1.54.0-darwin-arm64/lombok/lombok-1.18.39.jar",
    "java_home":   "/Users/you/.vscode/extensions/redhat.java-1.54.0-darwin-arm64/jre/21.0.10-macosx-aarch64",
    "source": "redhat.java-1.54.0-darwin-arm64"
  }
}
```

把 `suggestion` 里的三条路径照抄进 profile 的 `[jdtls_runtime]` 即可。嫌在整坨 JSON 里翻麻烦,
直接用 jq 把这三条拎出来(`2>/dev/null` 是丢掉无关的告警行,stdout 本身是干净 JSON):

```bash
uv run contextos health 2>/dev/null | jq '.health.jdtls_runtime.suggestion'
```

填好后再跑一次 `health`,这段会变成 `"status": "ok"`,就说明三条路径都对了。想手动核对的话,
三样都在扩展目录下:`~/.vscode/extensions/redhat.java-<版本>-<平台>/` 里的
`server/`(= jdtls_path)、`lombok/lombok-*.jar`(= lombok_path)、
`jre/<JDK版本>-<平台>/`(= java_home,注意 jre 下还有一层版本目录)。平台后缀:mac 是
`darwin-arm64` / `darwin-x64`,Windows 是 `win32-x64`,Linux 是 `linux-x64`。

**路线二:不用 VSCode,手动下载三样。**

- JDT LS:https://download.eclipse.org/jdtls/(milestones 或 snapshots 里挑一个 tar.gz)
  解压后的目录就是 `jdtls_path`;
- lombok:https://projectlombok.org/download 下载 jar,路径填 `lombok_path`;
- JRE/JDK 21+:任意发行版(如 https://adoptium.net),根目录填 `java_home`。

内网环境把这三样拷进去即可,ContextOS 不会在运行期自动下载 JDT LS / lombok / JRE
(整个系统里唯一会联网下模型的是可选的 bge 重排/embedding 权重,默认 fake 后端不触发)。
Windows 上路径写进 TOML 一律用正斜杠(`C:/...`),原因见 `config/profile.example.windows.toml`
头部的转义纪律。

跑完就能用了。两种用法:

**A. 接进 AI 编辑器(主战场)** — 起 MCP server:

```bash
uv run contextos serve-mcp --stdio
```

在 Claude Code / Cursor 的 `.mcp.json` 里登记(把路径换成你的仓根绝对路径):

```json
{
  "mcpServers": {
    "contextos": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/ContextOS", "contextos", "serve-mcp", "--stdio"],
      "env": { "CONTEXTOS_PROFILE": "config/profile.toml" }
    }
  }
}
```

接上后,直接在对话里让 AI 调 `build_impact_map` 或任一证据 tool。

**B. 命令行直查** — 一次性跑出 Impact Map JSON(给 shell / CI):

```bash
uv run contextos query "新增预付费转后付费的资格校验"
```

**C. 单独试某个 tool:`contextos call`** — 不进编辑器,直接从命令行调任意一个 MCP tool:

```bash
# 无参数的 tool 直接调
uv run contextos call profile_info

uv run contextos call lookup_table --describe   # 不知道参数怎么传?先看参数说明

# 带参数:--args 传 JSON
uv run contextos call lookup_table --args '{"table": "CB_CUSTOMER"}'

# Windows(cmd / PowerShell 引号易碎)推荐把参数写进文件
uv run contextos call lookup_table --args-file args.json
```

工具名打错了会列出全部可用 tool;`--describe` 打印该 tool 的描述 + 参数 schema 不真执行;结果是该 tool 的原始 JSON,方便配 `jq` 或直接看。

### CLI 命令一览

| 命令 | 做什么 |
|---|---|
| `contextos init` | 一键构建四维证据(`--only code\|database\|config\|corpus` 单维 / `--skip-oracle` 跳库) |
| `contextos health` | 体检各子系统就绪度,并自动探测 JDT LS 运行时路径给出 profile 建议 |
| `contextos serve-mcp --stdio` | 起 MCP server,接 AI 编辑器(用法 A) |
| `contextos query "<需求>"` | 一次性跑 `build_impact_map`,Impact Map JSON 打到 stdout(用法 B) |
| `contextos call <tool>` | 单独调任一 MCP tool,`--args` 传 JSON / `--args-file` 读文件(用法 C) |
| `contextos rebuild` | 增量重建 code_* 代码投影(撞阈值自动转全量;`--scope` 当前仅 `code`) |
| `contextos suggest-stop-keywords` | 扫源码统计过宽词,生成停用词草稿(人工核对后经 profile 启用) |
| `contextos run-evaluation` | 评测 runner 占位(尚未接线) |

命令都认 `CONTEXTOS_PROFILE` 环境变量,也可 `--profile <path>` 显式指定(占位的 `run-evaluation` 无参数)。

---

## 🧰 MCP 工具

**21 个 tool** —— 一个总入口 + 16 个证据查询 + 3 个元工具 + 1 个运维回写。

| 类别 | 工具 | 做什么 |
|---|---|---|
| **主入口** (1) | `build_impact_map` | 需求文本进 -> 完整 Impact Map 出(三维 + evidence + 置信度) |
| **证据·代码** (4) | `search_code` / `read_symbol` / `lookup_calls` / `search_source` | 查 Java 符号 / 按类·方法全名(如 `com.pkg.Foo#bar`)取源码 / 查方法调用关系 / 原始源码文本检索(补符号派发盲区) |
| **证据·数据流** (1) | `trace_method_dataflow` | 追方法级数据流 |
| **证据·数据库** (5) | `lookup_table` / `lookup_lineage` / `lookup_dependency` / `lookup_sequence` / `search_sql` | 查表 / 表的上下游血缘 / 对象依赖 / 序列 / 检索 SQL |
| **证据·配置规则** (5) | `lookup_config` / `lookup_rule` / `trace_config_impact` / `diff_config` / `explain_rule_logic` | 查配置项 / 查规则 / 追配置影响面 / 配置 diff / 解释规则逻辑 |
| **证据·语料** (1) | `rag_search` | 检索业务文档语料 |
| **元工具** (3) | `health_check` / `profile_info` / `incremental_rebuild` | 体检各子系统 / 看当前配置(不吐密码)/ 增量重建代码投影 |
| **运维回写** (1) | `record_confirmed_case` | 专家确认根因后回写确诊案例库(human-gated,身份服务端注入防伪造) |

所有 tool 都过输入校验中间件:host 传的 ad-hoc 语料、非白名单数据库连接、注入形态的参数一律硬拒。

---

## 🎯 能力层:证据底座 + 可衍生的 Skill

上面 21 个 MCP 工具合起来,是一层**机器可读的项目理解层**。但从"查到证据"到"能交付的结论"(该怎么设计 / 到底哪儿坏了),还差一层判断与编排——这层不用写代码,是 **Claude Code Skill**:纯指令层,复用 MCP 工具,把证据组织成可交付的产物。已实装两个:

| Skill | 状态 | 产出 |
|---|---|---|
| **运维根因定位**<br>`.claude/skills/ops-localization/` | 已实装 | 差异化根因假设表:每条假设标三态(证实 / 证伪 / 待定),按业务因果排序,不确定处显式 abstain。**不下判决**——把假设、依据、还缺什么证据摊给你,判断权留给人。 |
| **需求影响分析**<br>`.claude/skills/requirement-impact-analysis/` | 概要设计可用,详细设计在做 | 概要设计:需求分解 -> 每条需求的现状事实清单(先摸清代码现状,防脑补)-> 设计思路 -> 影响落点,重要决策按需附一份带证据的设计决策记录。 |

两个 Skill 都随代码走(`.claude/skills/*/SKILL.md`),接上 MCP server 就能用。

**非 Claude Code 用户(Cursor / Codex / OpenCode 等)怎么用 skill?** 21 个 MCP 工具在任何 MCP client 里都是通的;区别只在 skill 的**加载方式**。`.claude/skills/` 是 Claude Code 的自动发现格式,别的 host 没有这套自动加载——但 `SKILL.md` 本身是**可移植的指令**(一套编排 MCP 工具的工作流,只调 MCP 工具、不依赖 Claude Code 专属特性)。所以在别的 host 里:把对应 `SKILL.md` 的正文喂给你的 agent(系统提示 / `.cursorrules` / 自定义指令 / 直接贴进对话都行),它照着同样的流程调同样的 MCP 工具即可。换句话说,自动加载是 Claude Code 的便利,skill 的**能力**不绑 Claude Code。

### 经验持久化:越用越懂你的项目

这层不只是消费证据,还有一个**回写闭环**(目前服务运维根因定位):

```
定位出根因假设 -> 专家人工确认 -> record_confirmed_case 回写
      ^                                    |
      |                                    v
下次同类故障, 检索直接召回历史确诊 <- 案例物化成 markdown 进语料库
```

同类故障第二次出现,定位的起点不再是零——历史确诊案例连同当时的证据指针一起被召回。而"沉淀"这件事最怕的是垃圾进垃圾出,所以写入端全是闸门:

- **human-gated**:只有专家确认过的根因才入库,AI 自己不能往库里写。极端到什么程度——同一现象已有另一条确诊根因时,新旧两条是"并存"还是"互斥"**只有人能定**,调用方说不清就拒绝入库,绝不默认。
- **去重不覆盖**:同因重复确认只加计数,不覆盖正文;不同因显式标记冲突关系。
- **PII 闸**:检索字段里的个人信息(号码 / 工单等)拦在入口。
- **审计留痕**:谁写的、依据什么,记在语料目录之外的审计边车里,身份由服务端注入、host 伪造不了。

同一套"确认 -> 回写 -> 复用"的模式,后续可推广到设计决策、踩坑记录等其它沉淀(方向,未实装)。

同样的模式可以继续衍生——以下是**方向示例,非现成功能**:代码评审影响审计(PR 有没有漏改联动的表 / 配置)、接口变更兼容性检查(全量调用方 + 数据流下游)、测试影响圈定(只跑受影响的测试)、测试数据准备(按血缘上游定造数顺序)、数据订正影响评估(UPDATE 前看下游 + 代码读点)、配置变更预检、新人上手导览……任何"先得理解这个项目"的工程活动,都能把这 21 个工具当证据源编排成 skill。

衍生有四条纪律(从已实装的两个 Skill 提炼,防"AI 瞎编"):**证据优先**(每个结论带出处)、**不下判决**(判断权留给人)、**回写 human-gated**(沉淀须人工确认)、**敏感值不进输出**。

底座本身是**能力无关**的——不绑定任何单一下游。FPA 估算 / USSD 脑图 / 后台流程图等渲染器同在路线图上(尚未实装)。

---

## 🔬 实现原理

核心是**确定性静态分析 + LLM 语义判断**的混合——能用规则精确算的绝不交给模型,需要理解意图的才上 LLM。

- **代码:JDT LS 同源投影** — 用 Eclipse 的 Java 语言服务器(vendored solidlsp + 一个最小 patch)做编译级符号解析,build 期把整仓刷成 9 张 code_* SQLite 表(类 / 方法 / 字段 / 调用边 / 引用 / 继承 / 表引用 / 字符串字面量等)。这套表是 JDT 工作区的**派生投影**,不是第二套索引——查询期完全不碰 JDT,所以秒级响应。

- **数据库血缘:tree-sitter + sqlglot** — Java AST 抽 SQL,sqlglot 解析出表与字段的读写关系,九层 pipeline 恢复 SQL -> 表的血缘,带 owner 身份锚消歧。

- **配置维度:绑定解析** — 五个自研 parser + 八步绑定解析,把配置项和它在代码里的使用点对应起来,自研框架注解走 C+B 双策略不硬编码。

- **多源 RAG:sparse 检索** — v1 唯一实装是 ripgrep sparse(字面全文 + 窗口化)+ 可插拔 reranker 重排;dense 向量召回是后置项,当前默认关闭。

- **融合:LLM 软投票 + corroboration** — LLM 逐候选投票(支持 / 反对 / 弃权)过滤误报,再按多桥叠加做透明加权打分,落进置信度三桶。整条流水线对外是一个 `build_impact_map`。

### 输出长什么样:Impact Map

一份 Impact Map 是结构化 JSON:三维候选各自成条,每条都带出处和置信度。节选一条(合成示例):

```json
{
  "requirement_summary": "新增预付费转后付费的资格校验",
  "dimension_status": { "method": "resolved", "sql_table": "resolved", "config": "partial" },
  "evidence_items": [
    {
      "target": "com.example.order.TransferEligibilityService#check",
      "kind": "METHOD",
      "change_type": "modify_method",
      "confidence": 0.82,
      "confidence_tier": "HIGH",
      "evidence_refs": [
        { "source": "code_search", "rerank_score": 0.9, "content_summary": "类名与需求关键词强匹配, 调用链命中计费入口" },
        { "source": "db_lineage_bridge", "rerank_score": 0.7, "content_summary": "该方法写 TRANSFER_APPLY 表, 与需求涉及的表一致" }
      ],
      "reasoning": "代码桥与 SQL 桥双命中, 语义投票通过"
    }
  ],
  "open_questions": ["资格规则是否涉及信用额度配置表, 语料无描述, 待人工确认"]
}
```

三个设计点:每条候选的 `evidence_refs` **至少一条**——没有出处的结论不允许出现;`confidence` 是分数、`confidence_tier` 是分桶,两个都给,方便机器消费也方便人扫;查不到的维度不装死——`dimension_status` 如实标 `partial` 等状态,拿不准的进 `open_questions`,"不知道"也是显式输出。

### 置信度怎么算:corroboration(可倒查的加权,不是黑箱)

corroboration 的本意就是"多方印证"。四座桥互不调用、证据源彼此独立,所以**多桥命中同一目标**是很强的信号——这套打分把它变成显式算式,四步:

1. **各桥先给自己的质量分**(0-1)——不是"命中了就算 1":SQL 桥按血缘恢复模式给分(直连 SQL 恢复 > 拼接推断),配置桥按绑定解析策略给分(注解直绑 > 文本兜底),代码桥按符号匹配强度,LLM 投票按票分。桥内部就分三六九等。
2. **只在"这次真的给出证据的桥"里重归一权重**——某桥没证据(比如没连数据库),它的权重不摊死分母,别的桥按比例补上。权重本身是 profile 里的显式配置,可调、可审,不是训练出来的参数。
3. **加权求和**得总分。
4. **分桶带共识门**:`HIGH` 不仅要分数达标,还要求 **至少 2 座桥独立作证**——单桥再高分也封顶 `MEDIUM`。这条是防"一座桥自嗨"的硬闸。

示意演算:某方法代码桥质量分 0.9、SQL 桥 0.7,配置桥和 RAG 无证据 -> 只在代码桥与 SQL 桥之间重归一权重(设归一后 0.6 / 0.4)-> 总分 0.9x0.6 + 0.7x0.4 = 0.82 -> 分数达标且 2 桥共识 -> `HIGH`。

输出里每条候选保留 `bridge_scores`(每座桥各贡献多少)、`consensus_count`(几桥共识)、`hit_workers`(谁作的证)——任何一个分数都能从结果**倒查回每座桥的原始证据**。实现就一个文件:`contextos/orchestrator/corroboration.py`,公式即代码。

---

## 📦 项目结构

```
contextos/
|-- profile/          客户实例配置抽象(多命名空间 schema + 校验)
|-- storage/          存储抽象(SQLAlchemy,兼容信创 PG)
|-- llm/              LLM 网关
|-- prompts/          LLM prompt 版本化
|-- code_intel/
|   |-- jdtls_provider/   JDT LS adapter(vendored solidlsp + 最小 patch)
|   |-- projection/       code_* 9 表持久投影(查询期零 JDT)
|   |-- code_search/      代码符号检索(provider / 查询扩展 / seeds)
|   `-- source_search.py  原始源码文本检索(补 JDT 符号派发盲区)
|-- requirement/      需求拆解(输入层)
|-- recall/           多源 RAG 检索(ripgrep sparse;dense 后置默认关)
|-- corpus/           语料物化 + OCR 地基
|-- lineage/          数据库血缘(9 层 pipeline)
|-- config_dim/       配置维度(parser + 绑定解析 + 敏感值脱敏)
|-- db_provider/      Oracle 只读闸门 + 三道安全检查
|-- rerank/           LLM 软投票重排(融合层第一段)
|-- orchestrator/     数据流编排 + corroboration 置信度
|-- impact_map/       Impact Map 输出契约(JSON schema)
|-- ops/              运维回写 + 确诊案例库(record_confirmed_case)
|-- mcp_server/       对外 MCP server(21 tool + 输入校验中间件)
|-- init/             客户初始化编排(四维证据一键 build)
|-- cli/              命令行入口(init / serve-mcp / query / call / rebuild / health / run-evaluation / suggest-stop-keywords)
|-- util/             通用工具
`-- tests/            单测 + E2E smoke
```

---

## 🛡️ 设计红线

几条贯穿全项目、不可违反的硬约束:

1. **单一代码主索引** — JDT LS 工作区是事实源;所有代码投影只能作派生,严禁第二套持久 binding 索引。
2. **客户初始化启发式 + 客户驱动** — 接口字典 / 配置表字典优先从 JDT LS / DDL COMMENT / 客户已有文档自动抽取,框架注解由 profile 枚举定义,客户只补业务专属部分,不要求从零手工梳理。
3. **数据库只读 + 白名单** — 只允许 profile 中登记的测试实例(客户各自配置);任何含 PROD / PRD / LIVE / MASTER / RELEASE 等关键字的连接运行时拒绝;所有 SQL 走只读闸门(仅 SELECT / WITH)。三道闸:白名单 + 关键字拒 + 只读校验。
4. **敏感值绝不外泄** — 密码 / key / 配置原始值 / 表数据快照绝不进输出、prompt 或 artifact,统一过脱敏 chokepoint。
5. **存储走抽象层** — 所有表是逻辑契约,物理落地 SQLAlchemy,兼容信创 PG(GaussDB / KingbaseES / PolarDB / TDSQL-PG),非裸 SQLite。
6. **uv day-1** — 第一个 commit 即锁 uv,不走 pip 后迁;pyproject.toml + uv.lock 入库。
7. **MCP host 不可信** — server 硬拒 host 传的 ad-hoc 语料 / 非白名单连接 / 注入形态输入。

---

## 🗺️ 状态与路线

**v1 实现完成。** 六层全链路从需求拆解到 Impact Map 端到端跑通,MCP server(21 tool)+ CLI 对外可用,两个 Skill 已实装。当前在做全模块人工走查,之后进评测阶段(标准 gold 样本回归)。

| 部件 | 状态 |
|---|---|
| 横切底座(Profile / 存储 / LLM / 代码索引) | ✅ 完成 |
| 四座证据桥(代码 / DB 血缘 / 配置 / RAG) | ✅ 完成 |
| 融合层(LLM 重排 + 编排 corroboration) | ✅ 完成 |
| code_* 持久投影(查询期零预热) | ✅ 完成 |
| 集成层(MCP server 21 tool + CLI) | ✅ 完成 |
| Skill:运维根因定位 | ✅ 已实装 |
| Skill:需求影响分析(概要设计) | ✅ 概要设计;详细设计在做 |
| 全模块人工手测 | 🔄 进行中 |
| 评测与样本管理(标准 gold 回归) | ⏳ 下一步 |

**v2 方向**(评测达标后):skill 衍生(测试影响圈定 / 数据订正影响评估 / 代码评审审计等)、边界能力补全(跨方法 StringBuilder / MyBatis choose 等)、下游渲染器(FPA 估算 / USSD 脑图 / 后台流程图)、HTTP/REST 网关、Web UI、xlsx 通用语料化、dense 向量召回。

---

## 许可

[MIT](LICENSE)。仍处早期验证阶段,接口与格式可能调整。

---

<div align="center">

需求来一份,边界先看清。

</div>
