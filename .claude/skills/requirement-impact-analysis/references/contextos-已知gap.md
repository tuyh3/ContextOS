# ContextOS 已知 gap:host 易踩的读数 / 机制坑

procedure 各步指过来。事实以真代码为准。

## 切分拍平(Phase 1 必 LLM 自结构化)

segmentation 的 X-detector(`detector.py` 的标记正则)只认 1-2 位阿拉伯数字 / 罗马数字 / 单个 ASCII 字母;中文标签标题(`账务:` / `CRM:` 全角冒号结尾)一个都不匹配 -> 被 `if not m: continue` 跳过,吸进父级(root preamble),层级丢失。所以 Phase 1 必须 LLM 重读原文自己结构化,不信工具扁平 segments。

## RAG 非对称身份(理解层重要 / 定位层无用)

RAG 在两个层的身份完全相反,别混用:

- **理解层(重要)**:`rag_search` 查客户业务文档做**业务语义校准**,防 LLM 误读 —— LLM 只有通用行业知识、不懂本客户的具体业务含义,单靠它极易自信误读需求。需求理解 / 现状理解阶段命中的业务语境,落成 `[背景]` 标签(grounding,**非 `[事实]` 落点、非判决**),只进理解,不当落点。
- **定位层(无用)**:RAG **不产任何 FQN / 表 / 列落点**,落点全靠代码 / SQL / 配置工具。`build_impact_map` 里 RAG 召回的文档按 G5 直接丢弃(只回三维候选),它只是被丢弃的佐证桥。所以背景文档要**单独** `rag_search`,且**别拿 RAG 命中当 `[事实]` 落点**;config 维 RAG 同理是佐证非定位。

精确形态(真签名核过):先 `profile_info` 读 `rag_corpora` -> `rag_search(queries={"zh":..., "en":...}, corpora=已注册子集 或 [], top_k=3~5)`。middleware 拒 ad-hoc corpus / path(红线 #9),`corpora` 必须 ∈ profile 注册子集(`[]` = 全量搜)。

**输出边界**:`rag_search` 的 `passage` 是 **raw 字段**(不过 `read_symbol` 那道脱敏 gate),命中只写**短摘要 / 受限摘录**(不贴长 passage),禁凭据 / 配置原始值 / 值承载内容(红线 #9),引用则记已截断 / 已脱敏。

**空结果别断言"不存在"**:`rag_search` 返空时,文案写"**RAG 未命中(无法区分:无语料 / 未物化 / 确实没有)**",标 `[背景-gap]`;**绝不**写成"业务文档不存在 / 系统里没有这个概念" —— 空结果三种原因(profile 没注册对应语料 / 语料没物化 / 确实没有)工具分不清,断言"不存在"会把"我没查到"伪装成"客观没有"。

## change_type(粗标别当判决)

需求级全局 `actions` 广播 —— 存量类也可能被标 `add_class`。`change_type` 是粗标,别当落点判决,以逐点 drill 为准。

## lookup_calls(callers) 别省(最值钱、最易被跳的一步)

逐点 drill 里 `lookup_calls(method_fqn=..., direction="callers")` 是最容易被省、又最值钱的一步。存量某 BC / 方法**已完整实现但 callers 空 = 未接线**,意味着需求是"接线 + 编排"而非从零写——这条**只有 callers drill 查得到**,`search_code` / `build_impact_map` 都看不出。落在"看起来像存量"锚点上的 `[改]` / `[接口-增]`,必须真跑并报 callers 计数,别只停在 `search_code` 邻居层(弱 host 尤其爱跳这步,会把"接线"误估成"全新开发")。

**callers 查哪个 FQN 是条件判断,不是固定铁律(root cause B,修正 `e0a96c3` 把"查 impl"当默认的隐含假设)**:先识别该调用入口的抽象层,再决定查接口还是查 impl。

- **框架经接口 / 服务工厂派发**(如 `ServiceFactory.getService(接口)` 取实例再调):callers 必须查**接口 FQN**;真实调用方是冲着接口签名写的,查 impl FQN 会得**假"未接线"**(impl 直接零 callers,接线全挂在接口上)。
- **直连 impl / controller route / job / menu handler**:查**实际入口 FQN**(就是被框架/容器直接拿来跑的那个),查接口反而空。
- **不确定入口抽象**:interface 和 impl **都查**,两边 callers 合并看,别赌一边。

别把"某框架走接口派发"这个特例固化成跨项目铁律——不同仓的入口约定不同,每次先看清这个仓 / 这个模块的派发方式再下手。

## dimension_quality(谨慎采信 fallback_only)

`dimension_quality` 取值 = `strong` / `low_confidence` / `fallback_only` / `not_applicable`(只有 config 维可能 `fallback_only`)。`fallback_only` = grep 全文兜底命中,非真绑定定位,谨慎采信。

## 配置维落点(必定位载体 + 未物化标 gap)

**别只写"加 XX 配置"**:配置都有载体,Phase 3 每个 `[配置-增]`/`[配置-改]` 要先钻出它落哪种 —— 先 `search_code`/`read_symbol` 读**消费该配置的代码**(读配置的类 / 常量类)拿到字面 config key + 载体种类,再 `lookup_config(key)` 查现值。常见配置载体:

- 静态数据字典表(按 `CODE_TYPE` 枚举码值)。
- 系统参数表(按参数 key 取单值)。
- 参数配置表(按 key 取多个 `PARAn` 字段,如某字段存一个允许 / 受限列表)。
- 权限常量类 + 权限注册表(privilege 码常量 + DB 注册表)。
- 菜单 / 功能注册表(GUI 入口 / tab 注册)。

**现值未物化标 gap**:`lookup_config` 查已 build 的 `config_items`(底层是 config 维 DB 快照物化层 db_snapshot,非 host 可调工具);但目标客户业务配置行多未物化(`config_items` row/count/summary = 0)-> "现有值是什么"多半拿不到 -> `items=[]`,明说 gap,不脑补一个值。落点写清"哪种载体 + 哪个 key/常量/注册表",`[事实]`(载体/key 已 `read_symbol` 定位)与 `[建议]`(新码值)分开。

## 数据血缘回溯源头(需求常是数据模型改造,不是功能新增 / root cause A)

一条需求**多半是数据模型 / 分类机制的改造,不是从零加功能**(给某分类加一档、给某码值加一种受限类型、把单值字段拆多档)。这种需求的现状段(X.1)**必须回溯到数据的真正源头** —— 数据落在哪张目录 / 字典表、由哪个上游常量类 / 枚举 / 分类机制定义的 —— **不能停在第一个撞上的模块**。

第一个 `search_code` 命中的往往只是**消费方**(读这个分类去做业务的某个 BC),不是**定义方**(声明这个分类码值的上游常量类 / 字典表)。需求真正要动的常是定义方(加码值 / 改分类),消费方只是跟着受影响。手段:从命中的消费代码顺着读到它**读的是哪个常量 / 哪张表**,再 `search_code` / `read_symbol` 那个上游常量类、`search_sql` / `lookup_table` 那张字典表,一路回溯到"这个分类 / 码值最初在哪定义"。回溯不到源头就标"**未定位到数据源头**"(红旗),别拿消费方冒充源头。把消费方当源头 = 锚错层,后面整段现状都站在错地基上(某次盲测报告"数据源认错"就是这条)。

## 存量域模块读到方法级(别停在类清单退通用原语)

`search_code` 命中某**同域存量模块 / 业务类**时,别只拿类名清单就退回某个通用底层原语(通用扣减 / 通用查询)写"在通用入口加校验"。先 `read_symbol` 该域模块**自己的业务方法** —— 存量的限制规则 / 校验 / 配置读取多半写在域模块业务方法里,不在通用原语里。读到方法级才看得见"存量其实已做了一半"(已有同类校验 / 已读同类 config key);漏读会把"扩存量规则"误判成"从零新增",高估工作量、且漏掉现成 hook 点。这跟 `lookup_calls(callers)` 是一对:callers 查"存量有没有被接线",方法级 `read_symbol` 查"存量域模块自己做了什么"。

**读到方法级还要枚举同名族 / 兄弟方法(root cause D)**:撞上一个业务方法别就停。同域里**同名前缀 / 同动作族**的方法(如已找到一个 `checkXxx` 校验,十有八九旁边还有 `checkYyy` / `checkZzz` 一串平级兄弟,各管一类规则)往往就是这条需求要扩 / 要照抄的真落点。手段:对命中方法所在的类 / 包再 `search_code` 同前缀(如 `search_code("check")`)或 `read_symbol` 该类拿出方法清单,把同名族列全。只报一个、漏掉兄弟方法 = 漏掉一半落点,且会把"加一个平级兄弟"误判成"改通用入口"。这是现状段(X.1)成立的前提之一:现状不只是"有没有这个方法",而是"这一族方法各做了什么、缺哪个"。

## Oracle(白名单缺表)

白名单测试库(profile 注册的测试实例,见 hard constraint #4)缺部分业务表 -> `lookup_table` 列空(且**无** `oracle_offline` note)、活 DDL 拿不到。**但"列空 + 无 note"不单独等于"确无表"**:在线路径下列查询异常会被 catch + continue、同样返回空列且不置 note(见 `contextos/lineage/tools.py` per-querier try/except),所以空列可能是"没这表",也可能是"列查询失败"。判表存在/不存在要 `search_sql` 看 `FROM` + owner/router + 人工确认,别单凭 lookup_table。

## 表名验真(别把 DAO 包名当表名 / 别用 offline 的 lookup_table 判表存在)

- **DAO 包名 / 小写串不是表名**:仓里 DAO 目录是小写(如 `xxxdao`),真表名是大写下划线(如 `XXX_TBL`)。表落点一律用 `search_sql` 看恢复 SQL 的 `FROM <真表名>` + 列验真,写大写真名,别把小写包名 / 目录名照搬成"表名"。
- **offline 下 `lookup_table` 不能判表存在**:`oracle=offline` 时它对**任何**名字都返 `columns=[]` + `note=oracle_offline`,**分不清表存在与否**;不准用"`lookup_table` 命中"当表存在的证据(那只表示工具有返回,不表示表真在)。要确认"表是否存在 / 列是什么"走 `search_sql`(离线可用,看 `FROM` + 列);活库是否真部署仍需 Oracle 在线或人确认(老仓常有引用了表的死 DAO)。fresh 库(血缘表未建,如只跑过 `init --only code`)时 note 为 `lineage_not_built; oracle_offline` 双态串(在线则单 `lineage_not_built`),此时 edges 计数恒 0 同样不当"无血缘"的证据——先 `contextos init` 建库。

## health_check 读数(精确口径,真代码核实 meta.py)

字段取值:

- `engine` = `ok` | `error: <msg>`。
- `code_projection.status` = `not_built` | `ok` | `degraded` | `error: <msg>`(**没有 `building`/`stale`**)。
- `jdt_ls` = `cold` | `ready`(**不是 `warm`**)。
- `oracle` = `connected` | `offline`。
- `models` = `lazy` | `ready`(**不是 `ok`**)。
- `ripgrep` = `ok` | `missing`(`search_source` 依赖 rg;`missing` 时 `search_source` 抛 `ToolError`)。

判定:

- `jdt_ls=cold` 非问题:只表 ProjectionSearcher 未懒构造,首次 `search_code` 零 JDT 秒级自动构造,不阻塞查询。
- `engine error` != 没 build:是 storage/DB/profile 连接失败(`SELECT 1` 挂),报"查连接"不报"先 build"。
- `code_projection.status=not_built` 才是"先 `contextos init` / build"(hint 文案 = `run \`contextos init\``)。`status=degraded` 可继续(数据可用,带质量警示);`status` 以 `error:` 开头 = 投影元数据读失败(硬停)。
- `models lazy` 未证 LLM 可用:只表 `llm` 未构造(故意不访问避免离线 `LLMConfigError`);真失败要等 Phase 1/2 真跑 LLM 才暴露 -> 那时硬停报"查 LLM 环境变量 / 模型配置"。
- `oracle offline` 是软降级不是全废:`search_sql` + 已建 lineage 图仍可查;受限的是在线列 / 注释 / 活 DDL / 部分消歧。
- `ripgrep missing` 是软降级:`search_source`(原始源码文本检索)不可用 -> 调用直接抛 `ToolError`(提示装 rg);涉框架字符串派发 / 内联字面量 / 配置文本的 caller / 消费方 census 标 gap"无文本检索,覆盖不完整",别声称 caller 已全。

## build_impact_map 输出(compact vs full)

默认(`full=False`)返强核压缩 evidence(丢折叠弱线索,留 HIGH 或 consensus >= N 强核,每维 top_n,空核兜底);**`summary` 计数基于全量**(`evidence_total` 可能远大于 `returned`,`truncated=true` + `how_to_get_full` 提示)。要审弱线索 / 确认没漏 -> `full=true`(`top_n` 夹 `1..200`),或以 Phase 3 逐点 drill 为准。

**别习惯性 `full=true` + 存文件写脚本解析**:默认 compact 回的强核 `evidence_items` 就在响应里,直接读;把全量大输出存文件再用临时 python 嚼,慢且脆。各 evidence item 的 `sql_lineage` / `config_binding` / `sub_project` / `business_domain` / `entrypoint_kind` / `miss_reason` 字段**按维度可为 `null`**(代码维 item 的 `sql_lineage`=null;SQL 维的 `config_binding`=null …),脚本下标前不判 None 就 `'NoneType' object is not subscriptable`。要脚本就 None-safe(`(item.get('sql_lineage') or {}).get('dst')`),要审全量才 `full=true`。

## envelope 形态

MCP 出口是 response envelope `{response_schema_version, summary, impact_map}`(顶层 key 是 `response_schema_version`,**不是** `schema_version`);summary 在 envelope 顶层 `summary` 字段,别去 `impact_map` 里翻。`dimension_status` 不在 `summary` 里(`summary` 走 `dimension_quality` 轴)。

## search_code / search_sql 0 命中口径(0 命中 != 不存在)

`search_code` 查符号 FQN 索引、`search_sql` grep 已恢复 SQL 模板。方法体内字面量
(`startsWith` 前缀判断、内联 string、拼接 key)既不进符号索引、也不进恢复的 SQL,命不中属正常。
**0 命中绝不等于机制不存在** —— 用 `read_symbol` 切进方法体看真实逻辑再下结论(见 drill-loop速查 根因 E1)。
方法体内字面量(框架字符串派发 / 内联 string / 拼接 key)走 `search_source`(原始源码文本检索)补这个盲区。

## search_source(text-hit 弱证据 + 覆盖口径)

`search_source` 用服务端 rg 搜 profile `source_roots` 原始源码,补符号索引 / 恢复 SQL 的盲区(框架字符串派发 / 内联字面量 / 配置文本)。读数 / 用法坑:

- **text-hit < 符号事实**:命中带 `evidence_tier:"text-hit"`,是弱证据(低于 JDT / 投影符号事实),**不强制每条 `read_symbol` 坐实** —— 非 Java / 配置命中本身即 text-hit 证据;但 `.java` 投影内命中带 `enclosing_method_fqn`,要升级成符号事实就 `read_symbol` 它。census 入 N/N 仍须逐个 MCP 坐实(text-hit 候选 != MCP 主索引出处)。
- **`searched_roots` 漏跨模块根 -> census 不完整**:返回的 `searched_roots` 若不含疑似相关的跨模块根(客户码常在 profile 外部树多根),census 标 gap"roots 可能不全",**不称完整覆盖**。
- **`ripgrep=missing` -> 抛 `ToolError`**:`health_check.ripgrep=missing` 时 `search_source` 直接抛 `ToolError`(提示装 rg),不静默降级;涉字符串派发的 census 标 gap。
- **`files_scanned` = 预选考量数**(rg `--files` 列出后稳定排序 + cap 的候选数),**不是命中文件数**,别误读成"扫到 N 个命中"。
- **截断字段**:`truncated`(总命中超 limit / 预选超 max_files / 单文件超 max_bytes)/ `per_file_truncated`(单文件命中超 max_matches)任一 true -> 覆盖不完整,census 必降级、标"放宽预算重跑"。caps 服务端固定(host 不可设,防扩成任意 grep)。
- **不收 root / path / owner**:根恒由 profile;`file_extensions` 拒 glob 元字符(`["*"]` / `[".*"]` 这类被拒,防扩成 `*.*` 全扫)。

## search_source census 不替代 MCP 出处

E3 用 `search_source`(服务端 rg)对共享载体做消费方 census 属**发现辅助**,不替代 MCP 出处(红线 #1 / D8)。
`search_source` 命中是 text-hit 候选,必须逐个 `read_symbol` / `search_sql` / `lookup_config` 坐实并记出处才算入 N/N;
主索引结论仍出自 MCP 工具。census 根恒由 profile `source_roots`(服务端 owns rg,host 不传 path/owner;
客户码常在 profile 外部树多根)—— skill 跑在 ContextOS 仓,**不在 cwd 自己拉 rg**。
