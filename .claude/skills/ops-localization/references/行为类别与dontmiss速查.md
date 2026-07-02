# 行为类别与 dont-miss 速查

behavior_class 路由 + 每类 textbook signature 兜底 + 时点矩阵;中性无客户名,客户案例靠案例库回写积累(见 SKILL.md Phase 5)。

> 用法:Phase 1B 用 §1 选一个 behavior_class 枚举类路由(Phase 1A 先做阶段表征 + 单点暂停, 之后才路由);Phase 2 案例库 miss 时用 §2 强制枚举该类该想到的机制族(don't-miss);Phase 3 用 §3 把每个假设拆到生命周期时点。命名/同义口径见 §4-§6,权威出处为 spec(`docs/superpowers/specs/2026-06-29-运维行为根因定位skill-design.md`)Appendix A / H。工具签名与负判纪律见 `../requirement-impact-analysis/references/drill-loop速查.md`(引用不复制)。

---

## 1. behavior_class 五类枚举(路由 + 兜底锚)

固定五类(中性枚举词,不增不改;mechanism_tag 受控枚举 `MECHANISM_TAGS` 各隶属其一):

- `扣费`     —— 余额/信用/配额被扣减或本应扣减却未扣减、扣减金额或时点异常的现象(例:无余额却下单成功、扣多扣少、重复扣、漏扣)。
- `资格`     —— 准入/校验/放行类现象:本应被资格校验拦却放行、本应放行却被拒、黑白名单/状态机迁移异常。
- `配置`     —— 行为由某配置项/开关/参数决定,配置取值或生效导致行为偏离预期(例:默认值放行、不同消费方读同一配置行为分叉、配置改了不生效)。
- `数据状态` —— 由数据本身的状态/脏数据/历史遗留绕过校验或语义漂移导致的现象。
- `时序`     —— 并发竞态、批处理与实时双写、时点解耦(订购与扣费、账单与实扣)等"对的逻辑在错的时点/顺序执行"导致的现象。

**召回命门 = host 选对一个枚举类,容错远高于提炼一串锚词。** 主召回靠 behavior_class 粗召回(按类捞该类全部历史案例,见 §6 招二);锚词只做细排。host 在五个枚举里选一个(有 §2 don't-miss 机制族对照)出错概率远低于自由提炼一串关键词(Appendix H.3)。一个现象跨两类时(例:扣费现象其实根在时序解耦),两类都路由、两类的 don't-miss 机制族都枚举,Phase 3 再用证据收敛。

---

## 2. 每类 textbook signature 机制族(don't-miss 兜底)

每类列该类该想到的机制族(中性、行业通用 textbook,非客户特定);案例库 miss 时按本节强制枚举,漏召回退化成"没历史加速"而非"漏假设"(Appendix H.3 招三)。每个机制族标注其中性 mechanism_tag 示例(隶属单一 behavior_class,见 §4)。

> **mechanism_tag 命名声明(MUST):** 本节(§2)mechanism_tag 为**说明性命名示例**,非受控注册 tag;受控 tag 见 `MECHANISM_TAGS`(code);回写 `record_confirmed_case` 时 tag 必须 ∈ 该枚举, 回写新机制族需人工加枚举种子(见 §4)。

> **移植性注:** §2.2-§2.5 是跨域通用软件失效模式(鉴权 / 配置 / 数据状态 / 时序,换行业逐字适用);**§2.1 扣费机制族 + §3 时点轴是"计费类业务"的实例种子,不是框架。** 接入非计费 / 非电信客户时,把 §2.1 与 §3 换成该域的机制族与业务流程轴(框架其余 + §2.2-§2.5 不变),并按 §4 重播 MECHANISM_TAGS 种子;领域真实信号最终靠案例库回写(Phase 5)积累,本节只是 miss 时的中性种子。

### 2.1 扣费类

- 后付费"无余额仍下单成功"约等于递延/周期计费 textbook signature(源起递延收费案,spec §1):订购与扣费时点解耦,扣费在后置周期/账单侧发生,同步订购路径不实扣。mechanism_tag 示例 `deferred_charge`。
- 信用额度与扣费时点解耦:授信放行在订购同步路径、实扣在异步/账单侧,两侧不变量不同。mechanism_tag 示例 `credit_decoupled`。
- 预后付费混合扣减顺序:预付/后付/赠款多账户扣减顺序错配,导致该扣的账户未扣或顺序违例。mechanism_tag 示例 `mixed_deduct_order`。
- 异步批扣回滚:批量扣费失败后回滚不完整,留下"订成功但未扣/部分扣"状态。mechanism_tag 示例 `async_batch_rollback`。

### 2.2 资格类

- 资格校验只在同步入口、异步路径绕过:校验挂在前台同步入口,后台/批处理/补单路径不过同一校验即放行。mechanism_tag 示例 `async_path_bypass`。
- 黑白名单未覆盖:名单维度缺失或未覆盖某场景,本应拦的落在名单覆盖盲区被放行。mechanism_tag 示例 `whitelist_gap`。
- 状态机非法迁移放行:状态机缺迁移守卫,从非法前态直接迁到目标态绕过应有校验。mechanism_tag 示例 `illegal_state_transition`。

### 2.3 配置类

- 配置默认值 fallback 放行:配置项缺失/读取失败时回退到放行默认值,等于无校验。mechanism_tag 示例 `config_default_fallback`。
- 多消费方读同一载体行为分叉:同一配置载体被多处消费,各消费方解读不一致导致行为分叉。mechanism_tag 示例 `multi_consumer_divergence`。
- 配置生效时点滞后:配置已改但缓存/重载/生效时点滞后,旧值仍在生效窗口内放行。mechanism_tag 示例 `config_apply_lag`。

### 2.4 数据状态类

- 脏数据/历史遗留状态绕过校验:历史遗留或异常写入的状态值落在校验断言的盲区,绕过应有拦截。mechanism_tag 示例 `legacy_state_bypass`。
- 状态字段语义漂移:状态字段含义随版本/场景漂移,旧消费方按旧语义判断导致误放行/误拦。mechanism_tag 示例 `status_semantics_drift`。

### 2.5 时序类

- 并发竞态:check 与 act 之间存在窗口,并发请求在窗口内绕过校验(TOCTOU 类)。mechanism_tag 示例 `concurrency_race`。
- 批处理与实时双写:批处理与实时链路对同一数据双写,时点交错产生不一致状态。mechanism_tag 示例 `batch_realtime_dual_write`。
- 时点解耦(订购与扣费、账单与实扣):动作 A 与其约束应守的动作 B 发生在不同时点,A 时点不变量未被 B 时点覆盖。mechanism_tag 示例 `timepoint_decoupled`。

---

## 3. 生命周期时点矩阵(Phase 3 逐假设拆时点用)

业务流程时点轴(**下方是"计费类"实例种子;非计费域替换为该域生命周期轴**,逐假设对照落到具体时点):

```
订购 -> 资格校验 -> 余额/信用校验 -> 扣费(同步/异步/递延)-> 账单 -> 对账
```

Phase 3 逐假设要拆到两问:

- 现象发生在哪个时点(订购?账单?对账?)。
- 被违反的不变量本应在哪个时点被守(同步入口?异步扣费?账单生成?),实际有没有在那个时点守住。

**可达性只喂三态、不反证现象(spec §3 守则一)。** 某条同步路径可达且有硬拦截,只说明"若走这条同步校验会被拦",不能反证"现象不可能发生" —— 真实路径可能走异步/递延/批处理/账单/配置侧。把时点矩阵铺开就是为了不漏掉这些非同步时点:现象常发生在与校验不同的时点,正是各类 textbook signature(时点解耦/递延/批扣)的共性。

---

## 4. mechanism_tag 命名规范(MUST,Appendix H.3)

- mechanism_tag **隶属单一 behavior_class**(命名即隐含其行为类、不跨类同名)。故单键即可区分,**同义池 / dedupe_key / case_id 均用 mechanism_tag 单键,不引 behavior_class 复合键**。此命名规范的准确性是机制天花板。
- 中性示例命名风格 `<class前缀或机制语义>_<机制>`,如扣费类 `deferred_charge` / `credit_decoupled`,资格类 `async_path_bypass`,配置类 `config_default_fallback`(这些是命名风格示例,不代表全部已注册,以 `MECHANISM_TAGS`(code)为准)。
- mechanism_tag 是受控枚举 `MECHANISM_TAGS`(tag -> behavior_class,中性种子 tracked):`record_confirmed_case` 提交的 tag 必须属于枚举且 `MECHANISM_TAGS[tag] == behavior_class`,否则 reject;未知 tag fail-closed、不自动新建(否则不可信 host 可造任意 tag 污染 dedupe/synonym pool)。新机制族扩展 = 人工加 `MECHANISM_TAGS` 种子(受控),非 host 自造;同义池"积累"的是 variants(同义词)、不是新 tag。

---

## 5. 四形态喂三源(Phase 1 拆形态; behavior_class 路由在 Phase 1B)(Appendix H.1)

Phase 1 从自然语言现象填结构化模板(非自由发挥)拆出四形态,喂三源(期望/实际/不变量三元组 + behavior_class 都喂 host 临场,故形态四、源三):

```
拆出形态                        喂哪源                         为什么用这形态
期望/实际/被违反的不变量          host 临场枚举                  现象核心, 喂推理
behavior_class(5 类枚举)         路由(Phase 1B)+ host 临场     对照本速查 §1/§2 选, 拉该类 don't-miss 机制族
离散锚词(规范化到受控词表)        案例库 rag_search(字面 OR)    要和案例库 search_terms 对齐才命中
业务术语(原文 + 系统内代号)       通用 RAG rag_search           校准术语语义 + 找机制线索
```

离散锚词是 host 从现象 LLM 提炼(不是先 RAG 匹配得来),提炼后规范化到受控词表再去 grep。业务术语先过通用 RAG 校准"这术语/行为本系统什么意思"(host 只有通用行业知识、易自信误读),校准后语义进结构化(H.2)。

---

## 6. 召回容错三招(MUST,Appendix H.3 —— 命门不压在"host 提炼锚词准"上)

host 提炼锚词是 LLM 推理,弱 host 必然有时不准/不全,设计不赌它准。三招把命门挪走:

- **招一(写入侧确定性展开同义词 + 用中积累):** `record_confirmed_case` 写 `search_terms` 时,从受控词表的同义词组一次性展开全(规范词 -> 全变体,例 `余额不足 无余额 零余额 余额为0 insufficient_balance`);host 查询提炼出同义词组里任一变体即字面 OR 命中,不要求提炼出规范词本身。写入侧是查表展开(确定性),非 LLM 发挥。同义池 = 种子 + 用中积累:受控词表初始是人工小种子;每次确认回写(Appendix A 职责 6)把本次 `search_terms` 并入该 `mechanism_tag` 的同义词池,用得越多覆盖越全、关键词越准。归并锚点 = mechanism_tag + 人确认(防 false synonym):只有"人确认根因 = 同一 mechanism_tag"的 case,其 `search_terms` 才互进同义池;不靠词频/共现自动归并(那会把"余额不足"和"信用超限"错并成一组)。
- **招二(主召回靠 behavior_class,锚词只是细排):** 按 behavior_class 召回该类全部历史案例(differential)是粗召回兜底;锚词提炼即使全错,粗召回仍捞出同类。召回命门 = host 选对一个枚举类(容错高),非提炼对一串词(易错)。
- **招三(案例库 miss 不致命):** 案例库没召回,host 临场对照本速查 §2 强制枚举该类该想到的机制(don't-miss)。案例库是"加速 + 锦上添花",漏召回退化成"没历史加速",不退化成"漏假设"。

**sparse 局限(Appendix H.4):** 当前 `rag_search` = ripgrep sparse 召回 + BGE reranker 语义精排;rerank 补精度不补召回,sparse 漏召回的(同义不同词)rerank 救不了。靠招一受控词表展开 + 招二 behavior_class 粗召回规避大半;通用 RAG 查业务文档(free-text)的漏召回是真实残留,但它是术语校准/线索非判决,容忍度高。根治靠 v2 dense(不在本 spec)。

---

## 7. 症状阶段探针 + 中性鉴别问(Phase 1A 用)

> Phase 1A 表征故障阶段时用:先用通用**阶段探针**定位故障落在交互的哪一格, 再用本业务的词说清(spec 2026-06-30)。探针**只服务"读懂症状"**, 不从代码符号名反推。

### 7.1 generic 阶段探针(把症状定位到一格)

通用交互阶梯(够不到 -> 看不到 -> 进不下去 -> 被拒 -> 结果错):

- `够不到`: 入口/服务不可达(连接不上、路由不到、超时)。
- `看不到`: 入口到了但**呈现/显示失败**(页面/菜单/列表出不来、空白、报通用错)。
- `进不下去`: 显示出来了但**导航/下一步失败**(选项无效、跳不到下一级)。
- `被拒`: 走到了但被**资格/校验拒绝**(无权限、不在名单、状态非法)。
- `结果错`: 执行成功了但**结果不对**(扣多扣少、数据写错、订成功却异常)。

映射示例(跨域,只示范"症状 -> 探针格"的映射,非限定行业):
- "页面/菜单/列表打不开、报通用错" -> `看不到`(呈现/显示阶段)-> business_wording="某入口首屏未呈现"。
- "操作提交成功但结果不对(金额/数据偏离)" -> `结果错`(执行阶段)-> business_wording="某操作结果偏离预期"。

### 7.2 中性鉴别问(单点暂停时问用户, 中性不诱导)

暂停时问这几个高杠杆问题(收敛差异化, 不替用户判):

- **阶段**: "菜单/页面根本出不来, 还是出来了但某一步失败?"(显示 vs 导航 vs 执行)
- **爆炸半径 + 正常兄弟对照**: "只这一个(如某入口/某页面/某接口)还是全部?有没有同类正常的兄弟(如别的入口/页面/接口)?"
- **变更**: "最近有没有改过配置/发版?什么时候开始的?"

兄弟对照是最值钱的鉴别:坏的和正常兄弟共享后台时, 差异锁定到该实体专属的配置/数据, 排除共享基建。
