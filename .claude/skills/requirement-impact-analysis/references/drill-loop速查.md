# drill-loop 速查:8 个证据工具

每个工具:何时用 / 传什么 / 读什么 / 坑。签名与返回形态以真代码为准。

## 1. search_code(query, kind="")

- **何时用**:Phase 3 起手,给需求点找存量邻居符号(类/方法/字段)。
- **传什么**:`query` = 自由文本符号名(中英皆可,空 / 全空白 -> 返 `[]`);`kind` 可选,非空时只留该 01-Kind(`CLASS` / `METHOD` / `FIELD` / ...)。
- **读什么**:返回 `list[{target, kind, score, file, name_match}]`。**没有独立 `package` 字段** —— 包名要从 `target`(FQN)字符串自己截(`target` 形如 `com.x.y.ClassName.method(sig)`)。多结果按 `score`(= `name_match` 命中强度)排。
- **坑**:`score` 是裸命中强度,不是 08 的 `source_confidence`,别当置信度判决。

## 2. read_symbol(fqn)

- **何时用**:确认某 FQN 真实签名 / 是否 `//todo` 桩 / 现有方法实现模式。**默认用它切源码,不用 Read 裸读**(四护栏)。
- **传什么**:参数名是 `fqn`(**不是** `method_fqn`)。方法 FQN 裸名 / 带签名均可;裸名多重载 -> 报错(`AmbiguousMethodFqn`)列全部带签名候选,带签名重调。
- **读什么**:返回 `{fqn, resolved_fqn, file, line_start, line_end, source, stale, truncated, redacted}`。`source` = 已脱敏源码;`redacted=true` 表脱过敏;`truncated=true` 表超 cap 截断;`stale` 表投影与磁盘可能不一致。
- **坑**:四护栏(FQN-only / resolve 前缀校验 / cap 行数 / 脱敏+redacted);FQN 形态在 middleware 校验(max 512 字符)。
- **命中同域存量模块时读到方法级**:别只拿 `search_code` 的类清单就退回通用底层原语(通用扣减 / 通用查询),先 `read_symbol` 该模块**自己的业务方法** —— 存量限制规则 / 校验 / 配置读取多半在域模块业务方法里,漏读会把"扩存量"误判成"从零新增"、还漏掉现成 hook 点。
- **枚举同名族 / 兄弟方法(root cause D)**:命中同域存量类时,不只读第一个撞上的方法,还要 `read_symbol` 它的**同名族 / 兄弟方法**(如 `checkXxx` / `validateXxx` 一族,或某域类里成组的限制 / 校验方法)。需求要的那条校验常落在兄弟方法上,只读首个命中会漏掉(实测漏过同域类里某 `checkXxxDeduct` 兄弟校验)。

## 3. lookup_calls(method_fqn, direction="callees", depth=1)

- **何时用**:挖"存量未接线"(某 BC/方法没人调 -> callers 空)或往下看调了哪些 DAO/BC。
- **callers 查哪个 FQN 是条件的,不是铁律(root cause B)**:先识别调用入口的抽象层,再决定 callers 查谁。
  - 若框架经接口 / 服务工厂派发(如 `ServiceFactory.getService(接口)`)-> callers 查**接口** FQN(查 impl 会得假"未接线",因为实际调用方挂在接口上)。
  - 若直连 impl / controller route / job / menu handler -> callers 查**实际入口** FQN。
  - 不确定时,interface + impl 都查,对比两边 callers。
  - **别把某框架特例固化成跨项目铁律**:`查 impl` 不是默认口径,得看本项目的派发机制(修正"默认查 impl"的隐含假设)。
- **传什么**:**参数名是 `method_fqn`(不是 `fqn` —— 那是 `read_symbol` 的,别传错)**。`direction` = `callers` | `callees`;`depth` <= 2(超过被 profile 夹)。裸名多重载同 `read_symbol` 报错列候选。
- **读什么**:返回 `{edges, truncated, direction, depth, root}`。`callers` 方向 `edges` 空 = 没人调(存量未接线信号);`callees` 看调用下游。
- **坑**:`depth` 受 `profile.code_index.lookup_calls_max_depth` 夹;结果可能 `truncated`(看字段)。**别省 callers 这步**:存量方法已实现但 `callers` 空 = 未接线(需求是接线非重写),这信号只有 callers drill 查得到,`search_code`/`build_impact_map` 看不出。

## 4. search_sql(pattern, limit=20)

- **何时用**:反推某表/列被哪些 SQL 模板碰过(纯本地恢复,**离线可用**)。
- **传什么**:`pattern` = 要搜的文本(表名 / 列名 / SQL 片段)。
- **读什么**:返回 `list[{template_id, source_file, container, recovery_mode, confidence, snippet}]`;`snippet` 截 200 字。
- **坑**:grep 式本地恢复反推,不是活 DDL;`recovery_mode` 表恢复路径,`confidence` 是恢复置信度非业务判决。

## 5. lookup_table(table, owner="")

- **何时用**:查某表在线列 / 注释 + 本地血缘计数。
- **传什么**:`table` = 表名;`owner` 可选。
- **读什么**:返回 `{table, owner, columns:[{column_name, data_type}], comment, edges_in, edges_out, note?}`。血缘计数(`edges_in`/`edges_out`)恒返(本地)。
- **坑**:**不是完整 DDL dump,别期待建表 SQL**。两种 `columns=[]` 区分:
  - `columns=[]` 且 `note=oracle_offline` -> Oracle 不可达(router=None 或白名单库全没连);血缘仍出。
  - `columns=[]` 且**无** note -> 在线路径试过但没取到列:**可能没这表,也可能列查询异常被跳过**(`tools.py` 对每个 querier 的 query 异常是 catch + continue、不置 note)。
  即:note 在 = 库没连(offline / router=None);note 不在 = 在线试过没取到列,**不能单凭此判"确无表"** —— 走 `search_sql` 看 `FROM` + owner/router + 人工确认。
- **别把 DAO 包名(小写)当表名**(真表名大写下划线);表存在与否 + 列用 `search_sql` 看 `FROM` 验真。**`oracle=offline` 下 `lookup_table` 对任何名都列空,不能当"表存在 / 不存在"的判据**——要判存在走 `search_sql`,活库部署与否需 Oracle 在线 / 人确认。

## 6. lookup_config(config_key)

- **何时用**:Phase 3 每个 `[配置-增]`/`[配置-改]` 必走 —— 看某业务配置项现有值 / 归属实体 / 来源 + 是否物化。
- **传什么**:只传 `config_key`(MCP 层自动注入敏感 patterns + salt,host 不传也不该传)。
- **读什么**:返回 `{config_key, items:[{item_id, config_key, key_path, value_raw, value_type, is_sensitive, description}], entity 或 None, sources}`。先精确匹配再 `key_path` 子串。无命中 / 未物化 -> `items=[]` / `entity=None` / `sources=[]`。
- **配套纪律(别只写"加 XX 配置")**:配置有载体,要先钻出它落哪种。先 `search_code`/`read_symbol` 读**消费该配置的代码**(读配置的类 / 常量类),拿到**字面 config key + 载体种类**,再 `lookup_config(key)` 查现值。常见配置载体:
  - 静态数据字典表(按 `CODE_TYPE` 枚举码值;某静态数据 util 读)。
  - 系统参数表(按参数 key 取单值)。
  - 参数配置表(按 key 取多个 `PARAn` 字段,如某字段存一个允许 / 受限列表)。
  - 权限常量类 + 权限注册表(privilege 码常量 + DB 注册表)。
  - 菜单 / 功能注册表(GUI 入口 / tab 注册)。
- **坑**:**`value_raw` 即便已掩码也不复制进最终报告**(build 期 sanitize + 读期 redact 双跑,但报告只读命中与否 / `value_type` / `is_sensitive` / 载体元信息,写"已命中但值不输出"或"未物化",与 SKILL.md 脱敏红线一致)。业务表行多未物化 -> 命中 `items` 也常空 -> "现有值是什么"拿不到,**诚实标 gap,别脑补一个值**;落点写清"哪种载体 + 哪个 key/常量/注册表",`[事实]`(载体/key 已定位)与 `[建议]`(新码值)分开。

## 7. rag_search(queries, corpora, top_k=10)

- **何时用**:取需求背景 / 业务术语文档(`build_impact_map` 按 G5 丢了召回文档,背景要单独调)。
- **传什么**:`queries` = dict,形如 `{"zh": "...", "en": "..."}`(空 `queries` 返 `[]`);`corpora` = list,**必须 ∈ profile 注册子集 或 `[]`**(先 `profile_info` 读 `rag_corpora`;`[]` = 无命名子集 = 全量搜)。middleware 硬拒 ad-hoc corpus / path(红线 #9;field 缺失时 fail-safe 全拒)。
- **读什么**:返回 `list[{doc, passage, score, corpus}]`。
- **坑**:config 维的 RAG 是佐证非定位;别把 `rag_search` 命中当存量绑定事实。
- **passage 输出边界**:`passage` 是 **raw 字段**(不过 `read_symbol` 的脱敏 gate),命中只写**短摘要 / 受限摘录**,**不贴长 passage**;禁凭据 / 配置原始值 / 值承载内容(红线 #9),引用 passage 须记已截断 / 已脱敏。
- **空结果文案**:无命中写"RAG 未命中(**无法区分**:无语料 / 未物化 / 确实没有)",**绝不断言"不存在"**。

## 8. search_source(query, mode="literal"|"regex", case_sensitive=False, context_lines<=5, file_extensions=[...]|None)

- **何时用**:原始源码文本检索(rg,服务端跑)。补 `search_code` / `search_sql` 盲区 —— 框架字符串派发 / 内联字面量 / 配置文件文本(方法体内字面量既不进符号索引也不进恢复 SQL,只有原始 grep 找得到)。caller census 补字符串派发命中、载体消费方 census 穷举读点时用。
- **传什么**:`query` = 文本(literal 字面 / regex 走 rg 线性引擎,无 ReDoS);`mode` 默认 `literal`;`context_lines` <= 5;`file_extensions` 可选(拒 glob 元字符)。**不收 root / path / owner**(根恒由 profile source_roots,caps 服务端固定,host 不可设)。
- **读什么**:返回 `{searched_roots, results:[{root, path, repo_relative|null, line, snippet, ext, evidence_tier:"text-hit", enclosing_class_fqn|null, enclosing_method_fqn|null}], total_matches, files_scanned, truncated, per_file_truncated}`。`snippet` 已脱敏;`.java` 投影内命中回填 `enclosing_*_fqn`(可 `read_symbol` 升级成符号事实)。
- **坑**:
  - **text-hit < 符号事实**:命中是弱证据(低于 JDT/投影符号事实),不当判决;`.java` 投影内命中 -> `read_symbol` 那个 `enclosing_method_fqn` 升级,非 Java / 配置命中本身即 text-hit。
  - **截断即覆盖不完整**:`truncated=true`(总命中超 limit / 预选超 max_files / 单文件超 max_bytes)或 `per_file_truncated=true`(单文件命中超 max_matches)-> census 必降级,标"不完整,放宽预算重跑",**不称完整覆盖**。
  - **`searched_roots` 漏跨模块根 -> census 不完整**:若疑似相关的跨模块根不在 `searched_roots`,标 gap"roots 可能不全",不称完整。
  - **`files_scanned` = 预选考量数**(非命中文件数),别误读成命中数。
  - `health_check.ripgrep=missing` -> 该工具抛 `ToolError`(提示装 rg),涉字符串派发的 census 标 gap。
- **模式清单(中性示例;真模式由 profile `dispatch_patterns` / `carrier_read_patterns`,空则退这里):**
  - 派发(caller census):`FrameworkDispatcher.callByName("<service>.<method>")`
  - 载体(消费方 census):`StaticDict.getList("<dict_table>",...,"<CODE_TYPE>")` / `ParamReader.getDetail("<PARA>")`

## 顺序

`search_code` -> `read_symbol`(命中同域存量模块要读到业务方法级 + 枚举兄弟方法) -> `lookup_calls` -> `search_sql`/`lookup_table`;`[配置-增]`/`[配置-改]` 必走 `lookup_config` + 先 `read_symbol` 定位配置载体;背景文档走 `rag_search`;caller census(字符串派发)+ 载体消费方 census 补 `search_source`(命中再 `read_symbol` 坐实)。

- **定位要回溯到数据源头(root cause A)**:别停在第一个撞上的模块。`search_code` 命中后继续追到**数据真正落地的源头** —— 目录表 / 分类机制 / 配置载体,常是上游某常量类 / 字典表。第一个命中往往只是消费方,源头在它读的那个常量类 / 表里;不回溯就会把"现有分类机制"漏成"系统没有"。

## 根因 E:负向定位失真(现状段下"无 X"前必做)

需求词搜不到就断言"系统没有 X"是最常见的现状段假阴性。下"无"前必走:

- **E1 术语桥接**:需求核心名词 `search_code` 0 命中 != 不存在 —— 业务词与代码命名常不一致。
  换系统内代号/前缀重搜,桥接词取自可得来源(RAG 术语 / 同域类名 / 配置 key / UI 文案 / 命名前缀);
  有 >= 2 类来源时至少覆盖 2 类独立来源(只 1 类则注明),各记出处。覆盖到位才可写"确认无",否则标 gap。
  例:`search_code("Promotion") -> 0`;换代号搜命中 `com.example.app.rule.ChkLegacyBundle`
  用 `name.startsWith("LEGACY_")` 识别促销 -> 机制存在,只是代号叫 LEGACY_。
  反例(防过火):非核心名词的随手一搜 0 命中,不必触发桥接。

- **E2 碎片综合**:下"无 X"前扫 **本块 + 同需求族/同载体已 drill 命中 + 横切候选池**,看有无碎片可拼成 X
  (碎片常散在相邻块,只扫本块会漏)。同一实体多处出现(分类前缀 + 规则类 + 校验入口)要连成一条
  机制叙述,不许一处当 UI 细节、一处当"不存在"。Phase 4 横切前再跑一次全报告级负判自查。

- **E3 消费方穷举**:改某共享载体(数据字典 / 表 / 配置)读写时,census 找全消费方:
  - 用 `search_source`(载体 read 模式取 profile `carrier_read_patterns`,空则退中性示例
    `StaticDict.getList("<dict>",...,"<CODE_TYPE>")` / `ParamReader.getDetail("<PARA>")`)在
    profile `source_roots` 下 census(服务端 owns rg,host 零 shell;根恒由 profile,不收 path/owner)。
  - 每个列入 N/N 的消费方必须 `read_symbol`(或 `search_sql` / `lookup_config`)坐实并记出处
    (`search_source` 命中是 text-hit 候选,不算 MCP 主索引出处)。`.java` 命中带 `enclosing_method_fqn`
    直接拿去 `read_symbol` 升级。
  - **截断 / 未跑 / roots 不全即不完整**:`search_source` 结果 `truncated=true` / `per_file_truncated=true`,
    或根本没跑(`ripgrep=missing`),或 `searched_roots` 漏疑似相关跨模块根 -> census 标 gap、不称穷举完整。
  - N/N 只计 runtime source + active SQL/模板;测试 / 文档 / 注释 / generated / vendor / 死样本不计。
  反例(防过火):不涉共享载体读写的改动,不必穷举消费方。
