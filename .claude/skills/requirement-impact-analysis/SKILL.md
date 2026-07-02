---
name: requirement-impact-analysis
description: 用 ContextOS MCP 分析一份需求(eml/docx/text)对存量代码库的影响, 产出按需求闭环的概要设计(需求分解 / 每需求现状事实清单 / 本需求设计思路 / 影响落点 + 按需 §3 ADR 完整记录)+ 事实/建议/设计分级。当用户要做需求影响分析 / 拆需求看改哪里时用。
---

# 需求影响分析(requirement-impact-analysis)

把"用 ContextOS MCP 做需求影响分析"固化成可复用流程:输入一份需求(eml/docx/text),输出业务专家式的**概要设计**(总-分-总:需求分解 -> 逐需求块闭环 -> 按需 §3 设计决策完整记录(ADR-lite)-> 横切汇总)。逐需求块必须就近讲清:当前系统现状(`[事实]`事实清单)+ 需求理解(经 RAG 业务校准)+ 本需求设计思路 + 影响落点,`[事实]`/`[建议]`/`[设计]` 分级 + 诚实标 gap。

**这是定位器 + 概要设计决策层**:ContextOS 先告诉你"现在系统怎么运转 / 改哪里 / 存量长什么样"(`[事实]`);**在现状之上**,对命中"主数据归并"等设计气味的跨需求点,开 **Evidence-grounded ADR-lite** 给架构决策(`[设计]`,概念级)。但只到**概念级**(概念实体 / 字段语义 / 范围意图 / 迁移意图 / 兼容边界)—— 精确 DDL / 接口签名体 / 迁移 SQL / 页面布局是下游"AI 代码开发"或 ③ 详细设计的活(见 `references/设计决策层-ADR速查.md` §6)。`[设计]` 提案 grounded 在现状 + 给兼容边界 + 给翻案条件,**绝不借 `[事实]` 的可信度**。

## 适用

用户要分析一份需求对存量代码的影响,或要拆需求看"改哪里"。前提:host 连着 ContextOS MCP,且目标客户已 `contextos init` / build(查询期硬依赖 = `engine=ok` AND `code_projection` 已 build,见 Phase 0)。

## 红线(必守)

- **MCP 证据工具是定位事实源(红线 #1)**:`search_code` / `read_symbol` / `lookup_calls` 形成主索引结论。默认用 `read_symbol`(四护栏:FQN-only / resolve 校验 / cap / 脱敏)切源码;**裸读源码(Read)只在 `read_symbol` 不足以核验时用**(如读非 symbol 的 `.sql` 文件取完整列),且只看已定位文件片段 / 人工核验 FQN,不得替代 MCP 工具形成主索引结论。
- **现状段可贴存量片段, 其余仍不复制(D6)**:现状节可贴 `read_symbol` 真切存量片段作为最硬证据(脱敏,gitignored 报告;短摘要非 raw dump,每段 <= 20 行、每块 <= 2 段,超出只给摘要 + `文件:行` 指针,记 `read_symbol` 的 `redacted`/`truncated` 状态)。**其余仍不复制源码 / 值承载内容进最终输出**;`rag_search` 的 passage 同此边界(短摘要,不贴长 passage)。
- **不绕脱敏(红线 #9 家族)**:表数据 / 配置原始值 / 凭据不进输出。`lookup_config` 只取元信息(`config_key`/`key_path`/`entity`/`is_sensitive`/`value_type`/是否命中);**`value_raw` 即便已掩码也不进最终报告** —— 命中只写"已命中但值不输出",无命中写"未物化 / 未命中"(与文末自检"没把 value_raw 复制进输出"一致)。现状节贴的存量片段一样脱敏。
- **客户内容只落 gitignored**:产出含客户 FQN / 表名的报告落 `<data_dir>/tmp/requirement-impact-analysis/`(默认 `<repo>/database/` 已 gitignored;**根 `tmp/` 未被忽略,不准落那**),绝不进 tracked 文件(本 skill 自身是 tracked,worked example 必须中性合成)。
- **`[事实]`/`[建议]` 不混**:存量命中 = 事实;新增包名 / 列名 / 类名 = 建议(照邻居拟);建议不写成判决。
- **不产可实施级构件(D5/D1)**:不写新表列清单 / 新接口签名体 / 新代码实现 / 迁移 SQL / 页面布局。② 的 ADR 出**概念级决策 + 概念模型 + 范围意图**(该建什么实体、含什么语义字段、照哪个邻居),不出可建的物。
- **设计决策闭环(D10/D11)**:命中"统一/收口"类决策且约束 2+ 需求章或 2+ 落点时,附录 B 建「落点归属表」(按落点名 key,无 L-NN),每落点 -> 归属决策 + conform 三值枚举 + 证据脚注;业务正文设计思路节写本需求摘要 + "遵循 决策 N"(不只跳走);架构 fork 是否开决策块按 D5、是否进附录 B 按 D10(机器见 `references/设计决策层-ADR速查.md` §8/§9)。
- **ADR 段三标签不混(D4)**:§3 设计决策段内 `[事实]`(① 定位证据)/ `[推断]`(从事实推的系统含义,defeasible)/ `[设计]`(架构选择)严格三分;`[设计]` 绝不标成 `[事实]`。段外逐需求块保持 `[事实]/[建议]/gap/[背景]/[背景-gap]`。
- **每条带 MCP 工具出处(D8)**:每个改点 / 新增点 / 落点 drill 期标 `[事实]`/`[建议]`/`gap` 工作标记 + 记产出它的 MCP 工具调用(工具名 + 关键参数 + 命中摘要)可重放核验;**Phase 4 渲染时 MCP 出处沉附录 A 脚注、标记按渲染桥落地,正文不内联**;无工具命中记"无工具命中,纯推断"。报告附"本次 MCP 调用清单"(附录 D)。
- **拿不到就诚实标 gap**:业务配置数据 / 活 DDL / Oracle 缺表 / 无业务语料,明说,不脑补。

## 过程(共 6 步:前置探活 + Phase 1-4,其中 Phase 3.5 设计综合按需开)

### Phase 0 前置探活

跑 `health_check`,按下列口径判硬停 / 软降级 / 忽略。字段精确取值见 `references/contextos-已知gap.md`(health_check 读数节)。

- `code_projection.status=not_built` -> **硬停**:报"先 `contextos init` / build"(返回里自带 `hint: run \`contextos init\``)。
- `code_projection.status=degraded` -> **可继续,带质量警示**(数据可用,质量降级)。
- `code_projection.status` 以 `error:` 开头 -> **硬停**:投影元数据读取失败。
- `engine` 以 `error:` 开头 -> **硬停,归因不同**:storage/DB/profile 连不上,报"查连接",**不说"先 build"**。
- `jdt_ls=cold` -> **忽略**:只表 ProjectionSearcher 未懒构造,首次 `search_code` 零 JDT 自动构造,不阻塞查询(取值是 `cold`/`ready`,没有 `warm`)。
- `oracle=offline` -> **软降级**:`search_sql`(纯本地恢复)+ 已建 lineage 图仍可查;受限的是 `lookup_table` 在线列/注释、活 DDL、依赖类 Oracle 元数据、部分 owner/table 消歧。产出标受限,**不说"SQL 维全废"**。
- `models=lazy` -> **不硬停**:只表 LLM provider 未构造,未证 key/base_url/model 可用(取值是 `lazy`/`ready`,没有 `ok`)。Phase 1/2 真跑 LLM 若配置失败 -> **硬停**报"查 LLM 环境变量 / 模型配置"(跟 engine / projection 硬停归因区分)。
- `ripgrep=missing` -> **软降级**:`search_source`(原始源码文本检索)不可用。涉框架字符串派发 / 内联字面量 / 配置文本的 caller / 消费方 census 标 gap"无文本检索,caller 覆盖不完整",**不说"caller 已全"**(取值是 `ok`/`missing`)。

**取 `rag_corpora`(供 D9 业务校准):** 跑 `profile_info()` 记下 `rag_corpora`(注册的语料子集名单)。Phase 1 / 需求理解节的 `rag_search` 的 `corpora` **业务校准固定用 `corpora=[]`(搜全量业务文档);`rag_corpora` 的具名子集只在客户确有业务文档子集时才按名缩范围 —— 它可能只含 `confirmed-cases`(ops 案例库非业务文档),别拿来做业务校准。可选具名子集或 `[]`(全量),host 传 ad-hoc 名/路径会被 middleware 硬拒**。

**取 census 模式(供 Phase 3 caller/消费方 census):** `profile_info()` 现还返 `dispatch_patterns` / `carrier_read_patterns`(框架字符串派发 / 配置载体读取模式,非敏感 = 框架类名/前缀)。Phase 3 caller / 消费方 census 用这两列表喂 `search_source`;**列表为空时退回 `references/drill-loop速查.md` 的中性示例**,并标注"模式未配,census 可能不全"。

**硬 checkpoint:没跑 `health_check` 不准往下。** 在产出头部贴 `health_check` 原始返回 + 一句结论(全绿 / 哪维降级)+ `profile_info` 的 `rag_corpora`。

### Phase 1 语义结构化 + 业务校准(切分引擎做不到那层)

LLM 重读原文,按**真实层级**拆。认 `账务:` / `CRM:` 这类中文标签标题(切分 X-detector 只认数字/罗马/单字母,会把它们拍平进父级,见 `references/contextos-已知gap.md`)。逐编号列点 + 给可溯源点号(如 `账务-2.A`)。**以原文层级为准,不信工具扁平 segments。**

**需求理解先经 RAG 业务校准(D9,防 LLM 业务误读):** 拆出每个需求点后,先 `rag_search(queries={"zh": 需求点+关键词, "en": ...}, corpora=[], top_k=3~5)` 查客户业务文档(**业务校准固定传 `corpora=[]` 搜全量;别套 `rag_corpora` —— 本 profile 下它可能只有 `confirmed-cases`(ops 案例库非业务文档),scope 过去会把业务校准误搜进案例库**),校准"这术语/行为在本系统到底什么意思"——LLM 只有通用行业知识、不懂本客户具体业务含义,单靠它极易**自信误读**(专家判语"需求理解错了"正是这类)。命中的业务语境进需求理解节标 `[背景]`(grounding 工作标记;Phase 4 渲染时写成需求理解散文 + rag_search 出处沉附录 A 脚注,**非 `[事实]` 落点、非判决**;短摘要不贴长 passage);无命中标 `[背景-gap]`,文案写"RAG 未命中(无法区分:无语料 / 未物化 / 确实没有)",**绝不断言"业务文档不存在"**,也绝不用 LLM priors 编业务含义。RAG **理解层重要、定位层无用**(落点靠代码/SQL/配置工具,不靠 RAG)。

**硬 checkpoint(覆盖):** 列"原文编号 vs 清单编号"对照表,确认每点都进清单(防漏 5.f 这类),层级父子对(如"账务"是 1-6 的父);每个需求点都跑了 `rag_search` 业务校准(命中标 `[背景]` / 无命中标 `[背景-gap]`)。
**软(判断):** 标题 vs 人名 vs 字段标签的识别靠脑子,无死规则。

### Phase 2 宏观影响图

跑 `build_impact_map`,**先读 `summary`**(`by_tier` / `by_domain_source` / **`dimension_quality`** / `recommended_use`)再看 `impact_map.evidence_items`。`dimension_quality` 里 config=`fallback_only` 谨慎采信(grep 兜底非定位)。

**compact vs full**:默认(`full=False`)返强核压缩 evidence;**`summary` 计数基于全量**(`evidence_total` 可能远大于默认可见的 `returned`,`truncated=true` 时带 `how_to_get_full` 提示)。要审弱线索 / 确认"没漏" -> 传 `full=true`,或以 Phase 3 逐点 drill 为准。别被"summary 说 N 条、默认只看强核"绊住。envelope 顶层 key 是 `response_schema_version`(不是 `schema_version`)。
**别习惯性 `full=true` + 存文件写脚本解析**:默认 compact 的强核 `evidence_items` 就在响应里,直接读即可;把全量大输出存文件再用临时脚本嚼,既慢又脆 —— 各 item 的 `sql_lineage` / `config_binding` / `sub_project` / `entrypoint_kind` / `miss_reason` 等字段**按维度可为 `null`**(代码维 item 的 `sql_lineage`=null,SQL 维的 `config_binding`=null …),下标前不判 None 就 `'NoneType' object is not subscriptable`。要脚本就 None-safe;要审全量才 `full=true`。

**硬 checkpoint:读了 `summary`(含 `dimension_quality` 子字段)才往下。**

### Phase 3 逐点 drill(主体)

对 Phase 1 每个点先定位存量锚点,再对每个 `[增]`/`[改]`/`[接口-增]` 跑四步(工具签名 / 返回 / 坑见 `references/drill-loop速查.md`):

1. `search_code(query, kind)` -> 邻居 FQN(在 `target` 字段,没有独立 `package` 字段,包名自己从 FQN 截)+ file + score。
2. `read_symbol(fqn)` -> 真实签名 / 是否 `//todo` 桩 / 现有方法模式。
3. `lookup_calls(method_fqn=..., direction="callers")` -> 查调用方,挖"存量未接线"(callers 空 = 没人调)。**参数名是 `method_fqn`,不是 `fqn`。** **先识别调用入口的抽象层再查 callers(root cause B)**:若框架经接口 / 服务工厂派发(如 `ServiceFactory.getService(接口)`),callers 必须查**接口** FQN(查 impl 会得假"未接线");若直连 impl / controller route / job / menu handler,查实际入口 FQN;不确定时 interface + impl 都查。**别把某框架特例固化成跨项目铁律。** 落在"看起来像存量"锚点上的 `[改]`/`[接口-增]` 必须真跑这步并报 callers 计数——"已实现但未接线"(需求 = 接线非重写)只有按入口抽象 drill 的 callers 查得到,`search_code`/`build_impact_map` 都看不出;不准只停在 `search_code`。
   **caller census = `lookup_calls`(符号调用)+ `search_source`(框架字符串派发文本命中):** `lookup_calls` 对字符串派发返 `[]`(结构性失明)—— 跨模块 caller 经 `FrameworkDispatcher.callByName("<svc>.<method>")` 这类字符串派发调用的,符号图里没有边,**只有 `search_source` 找得到**。所以涉字符串派发的 caller census 必须补 `search_source`(模式取 Phase 0 的 `dispatch_patterns`,空则退中性示例 `FrameworkDispatcher.callByName`)。`.java` 命中带 `enclosing_method_fqn` -> `read_symbol` 升级成符号事实;配置 / 非 Java 命中标 `text-hit` 弱证据(低于符号事实,不当判决)。
4. `search_sql(pattern)` / `lookup_table(table, owner)` -> 表列:在线活 DDL(`oracle=connected` 时),否则恢复 SQL。**表落点用 `search_sql` 看 `FROM <真表名>` 验真,写大写下划线真表名,别拿 DAO 包名 / 小写串当表名;`oracle=offline` 时 `lookup_table` 对任何名都列空,不准用它"命中"当表存在的证据。**

**现状必回溯数据源头(root cause A):** 现状节的"数据落在哪"要**回溯到源头,不停在第一个撞上的模块**(第一个命中的常是中转/通用层,真正的分类机制 / 数据字典 / 配置载体往往在更上游的常量类 / 字典表)。沿 `search_code` -> `read_symbol` -> `search_sql` / `lookup_config` 追到定义处。

**现状段下"无 X"前必走负判自查(根因 E,见 `references/drill-loop速查.md`):** E1 术语桥接(换系统内代号重搜)/ E2 碎片综合(扫本块 + 同族/同载体 + 横切候选池)/ E3 消费方穷举(用 `search_source` 在 profile source_roots 下做载体消费方 census,逐个 `read_symbol` 坐实记出处);三者未做齐改标 gap("未定位到,非确认无"),**不下"无"**。

**载体消费方 census(改某共享配置载体读写时):** 用 `search_source` 搜载体 read 调用穷举读点(模式取 Phase 0 的 `carrier_read_patterns`,空则退中性示例 `StaticDict.getList("<dict>",...,"<CODE_TYPE>")` / `ParamReader.getDetail("<PARA>")`)。`search_source` 命中是 text-hit 候选,**逐个 `read_symbol` 坐实才入 N/N**(rg census 只发现候选,不算 MCP 出处);命中带 `enclosing_method_fqn` 直接拿来升级。census **未跑 `search_source`** 则禁给"数据源替换 / 影响面"结论。

要看业务配置现有行 -> `lookup_config(config_key)`(命中返已 build 的 `config_items`;无命中返 `items=[]` / `entity=None` / `sources=[]`;业务表行多未物化 -> 标 gap)。

**配置维落点(每个 `[配置-增]`/`[配置-改]` 必做):** 别只写"加 XX 配置"。先 `search_code`/`read_symbol` 读**消费该配置的代码**(读配置的类 / 常量类),拿到字面 config key + 它落哪种载体(静态数据字典 / 系统参数 / 参数配置 / 权限常量类 + 权限注册表 / 菜单注册表 —— 种类见 `references/contextos-已知gap.md`);再 `lookup_config(config_key)` 查现值 + 是否物化(无命中 / 未物化 = `items=[]`,"现有值"拿不到就诚实标 gap,别脑补一个值)。落点写清"哪种载体 + 哪个 key/常量/注册表",`[事实]`(载体/key 已定位)与 `[建议]`(新码值)分开。

**读存量域模块要读到方法级 + 枚举兄弟方法(root cause D):** `search_code` 命中某**同域存量模块/业务类**时,别只拿类清单就退回通用底层原语(通用扣减 / 通用查询)写"在通用入口加校验"。先 `read_symbol` 该模块**自己的业务方法** —— 存量的限制规则 / 校验 / 配置读取多半写在域模块业务方法里,不在通用原语里;漏读会把"扩存量规则"误判成"从零新增",还会漏掉现成 hook 点。**且要枚举同名族 / 兄弟方法**(如 `checkXxx` / `validateXxx` 一族,某需求的校验常在兄弟方法里,只读到一个会漏)。跟 `lookup_calls(callers)` 是一对:callers 查"存量有没有被接线",方法级 `read_symbol` + 兄弟枚举查"存量域模块自己做了什么"。

**GUI 类需求先 census web 层入口(D13):** 凡 GUI / 前端类需求(新增页 / 改页 / 加 Tab / 改展示),**先 `search_source` / `search_code` 定位 web 层入口**(page/html/jsp、serviceCode、前端路由 / controller route),定位到了才写"前端改哪(范围)";**定位不到写 `[gap] web 层入口未定位`**(代码定位 gap,**不是** `[背景-gap]` —— 那是 RAG 业务语料未命中专用)。**不许凭通用知识脑补"加个页面 / Tab"**。细节(布局 / 控件)留 ③,但范围(改哪个页面入口)不能漏也不能编。

**硬 checkpoint(覆盖):** 每个 `[增]`/`[改]`/`[接口-增]` 都 drill 了邻居(**不准停在 tag**);每条落点带 `[事实]`/`[建议]` + MCP 工具出处;引用的存量 FQN 是 `read_symbol`/`search_code` 真命中(可点开),非编造;落在存量锚点的 `[改]`/`[接口-增]` 都**按调用入口抽象**真跑了 `lookup_calls(callers)`(框架派发查接口)并报了计数;表落点都经 `search_sql` 验真(`FROM` 大写真表名,非 DAO 包名),`oracle=offline` 下没拿 `lookup_table` 列空当"表存在 / 不存在"的判据;数据源回溯到源头(非停在第一个模块)。
**软(判断):** 新增放哪个包、照哪个签名。

### Phase 3.5 设计综合(跨需求 ADR;按 D5 触发,非每次都开)

Phase 3 收集每个点的现状证据 + 落点(并记录 truncated/未跑/红灯完整性信号)后,**回看全局**:有没有跨需求点的设计气味需要收成一个架构决策?判定 + 模板 + 四 gate + 降级 + 概念级边界全部查 `references/设计决策层-ADR速查.md`,这里只给流程钩子。

1. **触发判定(D5,速查 §1)**:先过 design-smell 目录(主数据归并 = 静态字典表 + CODE_TYPE + 多消费方 + 前端前缀分类;已验证);没中过架构 fork 自检;再过影响阈值(读 ① 的真 blast-radius:`lookup_calls` callers / `search_source` consumers 计数,局限单方法/单页/单 SQL 就**不开**)。都不中 = 纯定位器,跳过本 Phase。
2. **开 ADR(D3,速查 §3 空模板)**:命中则按空模板逐段填 —— 决策 / 现状证据`[事实]`(引 Phase 3 的真命中)/ 推断链`[推断]` / 候选(>=2)/ 推荐+理由`[设计]` / 被否 / 概念模型与范围意图 / 兼容边界 / 证据依赖与降级表 / 翻案条件 / 置信度。
3. **四 gate 自查(D8,速查 §4)**:候选 >=2 + 1 被否?兼容边界单列了?翻案条件写了?证据依赖表带了?缺任一 = 不算 ADR,补齐。
4. **per-claim 降级(D6,速查 §5)**:**按证据源分** —— `lookup_table` 未跑/列空 -> 表级**方向 claim** 降候选 + 标 schema 未验证;consumer census(`search_source`/`lookup_calls`)截断 -> **范围 claim** 标 `[限制] 范围未闭合`,不连累方向、不降整个 ADR。大仓 `search_source` 常截断,范围 claim 这条真会触发,据实标 `[限制]`。
5. **概念级天花板(D1,速查 §6)**:ADR 只到概念级。一旦要写精确列/类型/签名体/迁移 SQL -> 停,那是 ③。
6. **兼容不变量升格(D12,速查 §10)**:**对每个已开的 §3 ADR**,把 Phase 3 现状 drill 定位到的、**约束该 ADR 方案选择 / 迁移兼容**的现状编码 / 前缀 / 格式约定(如 `<前缀>_+id` 类影响主键 / 路由的命名、报文格式、菜单 serviceCode 规约)逐条**升为兼容不变量**写进该 ADR 的兼容边界段;**普通命名风格不升**(变量名 / 类名风格不进不变量),防 ADR 膨胀。无 ADR 的需求不做此升格。

**硬 checkpoint(覆盖):** 命中 design-smell / fork 自检 / 过阈值的跨需求点都开了 ADR(没把该综合的散成逐点补丁);每个 ADR 四 gate 齐(候选>=2 + 被否 + 兼容边界 + 翻案 + 证据依赖表);三标签 `[事实]/[推断]/[设计]` 没混(设计判断没标成 `[事实]`);降级按证据源分(方向 vs 范围);没越概念级天花板(无 DDL/签名体/迁移 SQL)。
**软(判断):** 气味识别、候选方案怎么摆、推荐哪个,留推理空间(目录是起点非铁律,只有"主数据归并"被验证)。

### Phase 4 产出 + 诚实标 gap

按 `references/输出模板.md`(两层概要设计:业务正文[报告头 / 1 需求分解 / 2 逐需求章(当前系统现状分析[现状总表] / 新需求内容 / 概要设计-设计思路 / 影响落点[三桶:改动现有·新增·不改但需兼容])/ 3 设计决策[决策块 ≤6 段,按需] / 4 横切汇总]+ ==== 溯源附录 ====[A 逐条出处脚注 / B 落点归属表按落点名 / C 证据降级 / D MCP 调用清单])产出。**所有 bracket 装置(出处 / 三标签 / L-NN / [gap] / [冲突] / [背景-gap])全沉附录;业务正文零记号,开放项与矛盾用大白话(spec §5.4:未定位 / 原文未给 / 人·PD / ③ 四类各自点名 + 谁来定;矛盾用"重要:...不一致")。** **不在 SKILL.md 内联模板,按需 `Read` 那份 + `references/设计决策层-ADR速查.md`。**

**drill 工作标记 -> 两层渲染桥(可读性 v2 关键):** Phase 0-3 期间 host 用 `[事实]`/`[建议]`/`[背景]`/`[gap]` 标工作证据(那是 drill 纪律,红线 / 各 Phase 沿用);**Phase 4 把这些工作标记渲染成两层 device-free,不照搬进正文** —— `[事实]`/`[建议]` 落点 -> 业务正文大白话 + 工具出处沉附录 A 脚注;`[背景]` RAG 校准 -> 需求理解散文 + 出处沉脚注;`[gap]` -> 大白话点名 4 类 + 谁来定(未定位 / 原文未给 / 人·PD / ③);`[冲突]` -> "重要:...不一致(详见附录 C)";`[推断]`/`[设计]` -> 决策块 plain 散文;`[背景-gap]` RAG 未命中 -> 需求理解写"业务语料未命中(不等于不存在)" + 尾部全局开放项列一条;`[限制]` 范围 claim 截断 -> 沉附录 C 证据降级。任何 bracket 装置都不进业务正文(细则见下"两层铁律")。

承重原则:
- **现状节(当前系统现状分析)是评审者的信任闸,用现状总表(现状结论 / 涉及对象 / 对设计的含义)**:结论先行、证据后置;每条 1 个系统状态,用"数据源/分类机制/状态/读写入口/调用链/现状缺口"等标签开头。不要把工具调用过程写成正文长段。全 `[事实]`,回溯到数据源头;错了或缺位,后面写得再漂亮他也不信。
- **逐需求章的"概要设计-设计思路"节必须就近写本需求设计思路**:读者读完一个需求章就应知道这个需求准备怎么设计。若本需求命中 §3 ADR,设计思路节写本需求可读摘要 + 引用 §3 决策 N,**不能只写"遵循 决策 N"**;**commit vs 设计空间按证据强弱分(非需求数量)**:证据足下 committed 推荐 + 开 §3 完整 ADR(单/跨需求皆可),证据薄(关键依赖缺/census 截断)写"设计空间"列候选不硬推荐。
- **设计思路节不逃逸架构决策(D7)**:设计思路节可以摘要设计思路,但凡 `[建议]` 涉及 数据模型 / 迁移 / 接口边界 / 页面边界 / 兼容格式,必须受 §3 对应 ADR 约束;没有 ADR 支撑时只能列候选/待决策,不把架构判断伪装成普通建议。
- **D5 / D6 / D8(详见红线 + 自检清单,此处不重述数值)**:不产可实施级构件(D5/D1,② 出概念级决策非可建的物);现状段可贴 `read_symbol` 脱敏短片段、行数上限见红线(D6);每条带 MCP 工具出处 + 报告附"本次 MCP 调用清单"(D8)。
- **[现状段负判自查](硬 gate):** 每条"系统无 X / 没有 X 机制"结论,出报告前须满足:(a) 术语桥接 —— 列全可得来源(RAG 术语 / 同域类名 / 配置 key / UI 文案 / 命名前缀),>= 2 类时至少覆盖 2 类、各记出处(随手换词不算);(b) 扫本块 + 同族/同载体 + 横切候选池、确认无碎片可拼成 X;(c) X 涉共享载体读写时,用 `search_source` 在 profile source_roots 下 census 找全候选、逐个 `read_symbol` 坐实标 N/N(N = 坐实有效消费方,非裸文本命中数)。三者未齐改标 gap,不下"无"。机制细节见 `references/drill-loop速查.md` 根因 E。
- **census 降级规则(`search_source` 未跑 / 截断 / roots 不全):**
  - caller census **未跑 `search_source`**(或 `health_check.ripgrep=missing`)-> 禁称"完整 caller 覆盖"。
  - 载体消费方 census 未跑 `search_source` -> 禁给"数据源替换 / 影响面"结论。
  - **截断即不完整(截断 gate):** caller / 消费方 census 的 `search_source` 结果 `truncated=true` 或 `per_file_truncated=true`(limit / max_matches_per_file / max_files_scanned / max_bytes_per_file 任一触发)-> 该 census 标"不完整,需放宽预算重跑",与"未跑"**同级降级**,禁称完整覆盖。
  - `searched_roots` 不含疑似相关的跨模块根 -> 标 gap"roots 可能不全,跨模块 census 不完整"。
- **大报告分段写入:** 用宿主原生的安全编辑工具分段产出 —— 先建文件(仅报告头),再逐段追加(Claude Code:`Write` 建头 + `Edit` 追加;Codex 用 `apply_patch`);**不要单次写几十 KB**(弱 host 单次大 `content` 工具调用会截断报错)。shell `heredoc` 仅在 host 无安全编辑工具且目标为 gitignored 报告时作兜底。报告仍只落 gitignored `<data_dir>/tmp/requirement-impact-analysis/`(默认 `<repo>/database/` 已 gitignored;**根 `tmp/` 未被忽略,不准落那**)。
- **两层铁律(可读性 v2)**:业务正文(分割线前)零任何 bracket 装置 —— 零内联出处 / 零逐行 [事实]/[建议]/[推断]/[设计]/[背景]/[限制] / 零 (归属 L-NN) / 零 [gap]/[冲突]/[背景-gap]。开放项按 4 类大白话点名 + 谁来定(未定位"需进一步核实" / 原文未给"需需求方补充" / 人·PD"由产品PD定" / ③"留详细设计定");矛盾用"重要:X 在两处不一致,需以 Y 为准"plain 醒目提示([冲突] 记号沉附录)。存量名挂脚注 [^aN] 到附录 A;落点归属(D10)按落点名列附录 B(无 L-NN);证据降级(D6)列附录 C。可重放核验性一字不丢,全在附录 —— 降级是搬家不是删。[gap] 仍是 Phase 0-3.5 内部 authoring 纪律,只最终报告正文渲染成大白话。

尾部列全局 gap:RAG 业务校准无语料命中(`[背景-gap]`)、config `fallback_only`、业务配置数据未物化、Oracle 白名单缺表、`change_type` 是粗标别当判决;哪些是人/PD 专属(枚举编码、索引命名、字段长度、验收、风险、工期),skill 不产。

**现状事实源一致性自检(D14):** 同一 claim 规范化为 `subject + predicate`(如 subject=`服务类型`,predicate=`存储载体`),比较范围覆盖 现状节 / §3 ADR 证据段 / §4 横切。**只有同一 `subject + predicate` 被多个数据源给出不兼容值时**(一处说静态字典、另一处说某表),标 `[冲突]` + 降级,不静默二选一;同一 claim 被多源一致佐证不算冲突(反而加强)。

**硬 checkpoint:** 每个原文点都有需求块;现状节三必答齐;`[事实]`/`[建议]` 分级 + MCP 出处齐;现存(已定位)与新增(提案)硬隔离;gap 都列;`[建议]` 没写成判决。

## checkpoint 总原则

- **硬(机械/覆盖,防"声称做了没做")**:布尔可验的"做没做",见文末自检清单,逐条核,且要求可验证动作(如"列原文编号 vs 清单编号"),不是口头打勾。
- **软(判断,留推理空间)**:层级识别 / 操作映射 / 落点包名签名,给方法 + 参照,不给死规则。

## 自检清单(硬 gate,产出前逐条核;人工抽审为准)

- [ ] **Phase 0**:跑了 `health_check`,贴出原始返回 + 一句结论;跑了 `profile_info` 记了 `rag_corpora`;按口径判了硬停 / 软降级 / 忽略(没硬停才继续)。
- [ ] **Phase 1**:列了"原文编号 vs 清单编号"对照表,每个原文点都进了清单(无遗漏),层级父子对;**需求理解经 RAG 业务校准**(每点跑了 `rag_search`,命中标 `[背景]` / 无命中标 `[背景-gap]`,没断言"业务文档不存在")。
- [ ] **Phase 2**:先读了 `summary`(`by_tier`/`by_domain_source`/`dimension_quality`/`recommended_use`)才看 evidence;`dimension_quality` 里 config=`fallback_only` 的已标"谨慎"。
- [ ] **Phase 3**:每个 `[增]`/`[改]`/`[接口-增]` 都至少 drill 了 `search_code` 邻居(没停在 tag);引用的存量 FQN 是真命中(可点开)非编造;**落在存量锚点的 `[改]`/`[接口-增]` 按调用入口抽象跑了 `lookup_calls(callers)`(框架派发查接口、直连查实际入口、不确定 interface+impl 都查)并报了计数**;表落点经 `search_sql` 验真(大写真表名非小写包名),`offline` 没用 `lookup_table` 列空判表存在;**数据源回溯到了源头(非停在第一个撞上的模块)**。
- [ ] **Phase 3 配置维 + 域模块**:每个 `[配置-增]`/`[配置-改]` 跑了 `lookup_config` + 定位了配置载体(种类 + key/常量/注册表),没只写"加 XX 配置",未物化标了 gap;`search_code` 命中的同域存量模块 `read_symbol` 读到了业务方法级 + **枚举了同名族/兄弟方法**(没停在类清单退回通用原语)。
- [ ] **Phase 3 GUI/Web census(D13)**:GUI / 前端类需求先 `search_source`/`search_code` census 了 web 层入口(page/html/serviceCode/路由);定位到才写前端范围,定位不到标 `[gap] web 层入口未定位`(没用 `[背景-gap]`、没脑补页面)。
- [ ] **Phase 4 现状节(信任闸 + 可读性)**:每个需求章的现状节用**现状总表(现状结论 / 涉及对象 [^aN] / 对设计的含义)**而非长段落;结论先行、证据后置;每条 1 个系统状态;至少覆盖数据源 / 读写入口 / 缺口,按需覆盖分类机制 / 状态生命周期 / 调用链;全 `[事实]` 带取证,没定位写"未定位"不臆造;出处不内联,挂脚注到附录 A;贴的存量片段守 D6(脱敏,每段 <= 20 行、每块 <= 2 段,记 `redacted`/`truncated`)。
- [ ] **Phase 4 现状事实源一致性(D14)**:同一 `subject + predicate` claim 跨 现状节 / §3 / §4 是否被多源给出不兼容值;矛盾标了 `[冲突]` + 降级,没静默二选一。
- [ ] **Phase 4 隔离 + 不越界**:现存(已定位)与新增(提案)**硬隔离** —— 改由影响落点**三桶**(改动现有 / 新增 / 不改但需兼容)承载,非逐行标签;提案不借 `[事实]` 可信度;**未产新代码体**(无新表列清单 / 新接口签名体 / 新代码实现 / 迁移 SQL,D5);**新构件名跨段一致**(同一新表/类在各段命名对齐,现状段引的表真存在否则标 gap)。
- [ ] **Phase 4 设计决策闭环(D10/D11)**:命中统一/收口决策(约束 2+ 需求章或 2+ 落点)时建了附录 B 落点归属表(按落点名 key,无 L-NN;列固定 + conform 三值枚举);每个架构级落点在设计思路节摘要 + 遵循 决策 N;影响落点节无未背书的新表/config/状态/接口/页面落点;架构 fork 没在设计思路节内联判决(开不开 ADR 按 D5、是否进附录 B 按 D10)。
- [ ] **Phase 4 两层边界(可读性 v2)**:业务正文(分割线前)无任何 bracket 装置 —— 无 出处:/ read_symbol( / (归属 L- / 逐行 [事实][建议][推断][设计][背景][限制] / [gap][冲突][背景-gap];开放项按 4 类大白话点名(未定位 / 原文未给 / 人·PD / ③)+ 谁来定,矛盾用"重要:...不一致"plain;脚注 [^aN] 都在附录 A 闭合(无悬空);溯源附录 A/B/C/D 齐(A 出处 / B 落点归属表按落点名 / C 只列被降级 claim / D MCP 清单)。
- [ ] **Phase 4 出处 + 覆盖**:每个原文点都有需求章;每条 `[事实]/[建议]/gap` 的 MCP 工具出处挂附录 A 脚注(工具 + 关键参数 + 命中摘要),报告含"**本次 MCP 调用清单**"(附录 D);`[事实]`/`[建议]` 分级齐;尾部 gap 都列(RAG 无语料 / config `fallback_only` / 业务配置未物化 / Oracle 缺表 / `change_type` 粗标);`[建议]` 没写成判决。
- [ ] **Phase 3.5 ADR(若触发)**:命中 design-smell(主数据归并等)/ fork 自检 / 过影响阈值的跨需求点**开了 ADR**(没散成逐点补丁);每个 ADR **四 gate 齐**(候选 >=2 + 被否理由 + 兼容边界单列 + 翻案条件 + 证据依赖与降级表);三标签 `[事实]/[推断]/[设计]` **没混**(设计判断没标 `[事实]`,系统含义标 `[推断]`);降级**按证据源分**(`lookup_table` 未跑 -> 方向降候选;census 截断 -> 范围标 `[限制]`);**没越概念级**(无 DDL/精度/签名体/迁移 SQL/页面布局)。小需求(单方法/单页/单 SQL)**没硬开 ADR**(保持纯定位)。
- [ ] **Phase 3.5/Phase 4 兼容不变量升格(D12)**:每个已开 ADR 把约束其方案选择 / 迁移兼容的现状编码 / 前缀 / 格式约定都升进了兼容边界(漏一条即不合格);普通命名风格没误升;无 ADR 的需求没强加此项。
- [ ] **Phase 4 设计思路节就近 + 逃逸封口(D7)**:每个需求章的设计思路节都有"本需求设计思路";若引用 §3 ADR,已写本需求摘要 + `决策 N`,没有只写"遵循 决策 N";架构 fork 按证据强弱分(证据足走 §3 完整 ADR + committed 摘要 / 证据薄写"设计空间"列候选),没在设计思路节把薄证据架构判断伪装成普通 `[建议]`。
- [ ] **脱敏 + 落点**:没把 `value_raw` / 配置原始值 / 表数据原始值 / 长 passage / raw dump 复制进输出(现状段片段除外,且已脱敏 + 守 D6 行数);客户 FQN/表名只落 gitignored `<data_dir>/tmp/requirement-impact-analysis/`(默认 `<repo>/database/` 已 gitignored;根 `tmp/` 未被忽略,不准落那),绝不进 tracked 文件。
- [ ] **现状段负判自查(根因 E)**:每条"系统无 X"结论都做了 E1 术语桥接(>= 2 类来源覆盖、记出处)+ E2 碎片综合(本块 + 同族/同载体 + 横切候选池)+ E3 消费方穷举(`search_source` 在 profile source_roots 下 census + 逐个 `read_symbol` 坐实标 N/N);未做齐的改标 gap,没有无条件下"无"。
- [ ] **census 降级(`search_source`)**:涉字符串派发的 caller census 跑了 `search_source`(没只靠 `lookup_calls`),载体消费方 census 跑了 `search_source`;`ripgrep=missing` / 未跑 / `truncated`=true / `per_file_truncated`=true / `searched_roots` 漏跨模块根 任一成立时,**没称"完整覆盖"** 而是标了 gap 降级(截断与未跑同级)。
- [ ] **大报告分段写入**:报告分段产出(先建头再逐段追加),没单次写几十 KB;客户产出只落 gitignored `<data_dir>/tmp/requirement-impact-analysis/`(默认 `<repo>/database/` 已 gitignored;根 `tmp/` 未被忽略,不准落那)。
