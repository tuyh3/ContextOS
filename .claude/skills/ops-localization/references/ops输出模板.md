# ops 根因定位输出模板

输出差异化根因假设表(三态 + 业务因果排序 + abstain),不下判决(locator-not-simulator);报告落 gitignored `<data_dir>/tmp/ops-localization/`。

> 用法:Phase 4 把每个假设的 drill 结果按本模板渲染成差异化假设表;Phase 0/4 写报告头与排序输出;Phase 4 假设表 + 排序之后,按 §5 把差异化假设 + pairwise-discriminator 渲染成人可跑的**推荐排查流程 playbook**(诊断排查顺序、只诊断不给药方)。三态/排序/abstain 的判定纪律权威出处为 spec(`docs/superpowers/specs/2026-06-29-运维行为根因定位skill-design.md`)§3 五守则 + §4。behavior_class / mechanism_tag / 时点矩阵口径见 `行为类别与dontmiss速查.md` §1-§4;工具签名与负判纪律见 `../requirement-impact-analysis/references/drill-loop速查.md`,health/读数口径见 `../requirement-impact-analysis/references/contextos-已知gap.md`(跨 skill 引用不复制)。

---

## 0. 报告头

每份报告先写头区,给消费方交代证据地基与读法。固定字段:

- **现象输入**:期望 vs 实际 + 被违反的不变量(从 Phase 1 结构化模板搬来,中性化)。一句行为类别路由结论(命中哪个 behavior_class,见速查 §1)。
- **health_check 工具态**:贴 `health_check` 读数,按 `../requirement-impact-analysis/references/contextos-已知gap.md` 的「health_check 读数」节口径解释(`code_projection.status` / `jdt_ls` / `oracle` / `ripgrep` 等取值含义);标明哪些桥可用、哪些软降级(例 `oracle offline` -> 在线 DDL/注释受限,`search_sql` + 已建 lineage 仍可查;`ripgrep missing` -> `search_source` 不可用,字符串派发 census 标 gap)。
- **census 地基**:`profile_info` 读出 `rag_corpora` + `repo_root` + `source_roots`,列出来供消费方核 census 覆盖范围(消费方穷举 / caller census 的 root 口径以此为准;root 不全则后文 census 标 gap"roots 可能不全",不称完整覆盖)。
- **案例库可用性**:`rag_search(corpora=["confirmed-cases"])` 命中数;命中则注命中的 mechanism_tag,作 Phase 2 differential 起点;**库未建 / 0 命中则注"无案例库,纯 host + don't-miss"**(退速查 §2 强制枚举,漏召回退化成"没历史加速"而非"漏假设")。
- **怎么读**(给消费方):
  - 三态释义:`validated` = 路径 + 不变量被 MCP 证据坐实;`invalidated` = 被证据反证(机制不成立);`inconclusive` = 决定性数据出 index 无法在代码侧定夺(**注:inconclusive 不等于低可能性**,spec §3 守则二)。
  - 排序键:第一键 = 业务因果(behavior_class textbook signature + 案例库/专家外部信号),第二键 = 三态(见 §2);**不是按"代码能不能证"排**。
  - 本表是差异化假设(候选),不是判决(locator-not-simulator);头名未必是定论,见 §3 abstain。

---

## 0A. Phase 1A 症状阶段表征 + checkpoint(报告头之后, 假设表之前)

报告头之后 MUST 渲染 Phase 1A 表征 + checkpoint 终态:

- **症状阶段表征三元组**: 失败动作/对象 + 故障阶段(generic 探针格 + 业务词)+ 爆炸半径(结构化三项 scope/brother_comparison/recent_change)。
- **checkpoint 终态**: `status`(`confirmed` / `assumed_with_user_correction` / `assumed_unconfirmed` / `cancelled`)+ `stage_assumption` + 是否经用户确认。`awaiting_user_confirmation` **只能存在于未完成报告**, 最终报告 MUST 落到后四终态之一。

**`assumed_unconfirmed` 降级输出契约(MUST, 真非交互或越过 turn boundary 时):**
- 报告**置顶** banner:`阶段理解未确认(assumed_unconfirmed)—— 请先确认` + 鉴别问原文(不埋结尾)。
- `stage_assumption` 保留、**不静默改阶段、不跳表征**。
- 下方 §1 差异化根因假设表 **进降级模式**:**不给单一头名**;只给"待确认的头部假设组 + 每对需要的鉴别信号"(见 §2 pairwise discriminator)。

暂停前(诊断主流程唯一暂停点)写入的 checkpoint 块格式(11 字段):

```
OPS_LOCALIZATION_CHECKPOINT
checkpoint_id: <稳定唯一 id, 例 incident-slug+时间戳; 路径变了也不变>
report_path: <data_dir>/tmp/ops-localization/<incident>.md
phase: Phase 1 symptom-stage
status: awaiting_user_confirmation
stage_assumption: <generic 格 + 业务词, 例 呈现/显示阶段>
business_wording: <一句症状业务表述, 例 某入口首屏未呈现>
blast_radius:
  scope: <范围, 只这一个还是全部, 未知写 not_provided>
  brother_comparison: <正常兄弟对照, 未知写 not_provided>
  recent_change: <最近变更, 未知写 not_provided>
excluded_stages: <显式排除, 例 后续导航 / 执行 / 落库>
question: <中性鉴别问, 例 这个理解对吗? 只影响这一个还是全部?>
resume_next: behavior_class_route_then_phase_2
incident_signature: <确定性签名, 算法见下, 不含爆炸半径>
```

**`incident_signature` 确定性算法(MUST, 不让模型抽关键词, 否则 resume/hook 复现不出)**:`signature = sha256(norm(failed_action) + "|" + norm(failed_object) + "|" + norm(stage_grid) + "|" + norm(business_wording))[:12]`;`norm` = NFKC + 小写 + 去首尾空白 + 折叠内部空白 + 去标点。**不含**爆炸半径 / 用户后续鉴别信号(签名在 Phase 1A 定、不随 resume 答复变)。

worked example(中性):`stage_assumption: 呈现/显示阶段` · `business_wording: 某入口首屏未呈现` · `blast_radius.scope: 仅该入口` · `blast_radius.brother_comparison: 正常, 同后台` · `status: confirmed`。

---

## 1. 差异化根因假设表(主体,每假设一个纵向块)

每个假设一个**纵向块**(固定字段逐行铺开,不横向挤一张大表 —— 字段多、横表 cell 会爆,渲染丢内容)。每块固定字段:

- **假设 N**:一句机制描述(中性,不含客户名 / value_raw)。
- **behavior_class + mechanism_tag**:命中速查 §1 的 behavior_class 枚举类 + 速查 §4 命名规范的中性 mechanism_tag(受控枚举 `MECHANISM_TAGS`,隶属单一 behavior_class)。
- **机制族来源**:标这条假设从哪来 —— 案例库命中(注 case mechanism_tag) / 通用 RAG 线索(注 corpus) / host 临场对照速查 §2 don't-miss 兜底。三源都标来源,便于核"这假设有没有外部信号背书"。
- **时点拆解**:本假设在生命周期哪个时点发生(订购 / 资格校验 / 余额信用校验 / 扣费 / 账单 / 对账,见速查 §3 时点矩阵)、被违反的不变量本应在哪个时点守、实际有没有在那守住。现象常发生在与校验不同的时点(时点解耦 / 递延 / 批扣),铺时点就是不漏掉非同步时点。
- **drill 证据 + MCP 出处**:逐条标 `[事实]` / `[推断机制]`:
  - `[事实]` = MCP 真命中,带工具名 + 关键参数 + 命中摘要,可重放(例 `[事实] search_code("OfferOrder")-> com.example.app.order.OfferOrderService · read_symbol(...)-> 第 40-58 行无 balance 校验`)。
  - `[推断机制]` = 从事实推的机制,defeasible(可被后续证据推翻),明确标"推断机制"不冒充事实。
  - **实体必须 MCP 真命中(select-not-generate,spec §3 守则四)**:根因里的方法 / 表 / config / FQN 必须是 MCP 命中坐实的,**不脑补**;机制可以推断(标"推断机制"),但承载机制的实体名不许编。
- **可达性**:路径可达 + 有无硬拦截,标 `[事实]`(MCP 命中坐实)。**一句固定免责**:可达性只喂三态、不反证现象 —— 路径可达 + 有硬拦截只说明"若走这条同步校验会被拦",不能反证"现象不可能发生"(真实路径可能异步 / 递延 / 批处理 / 账单 / 配置侧,spec §3 守则一)。
- **三态判定**:`validated` / `invalidated` / `inconclusive`。决定性数据出 index(charge model / 运行态数据等不在代码与索引内)标 `inconclusive`;**注:inconclusive 不等于低可能性**(spec §3 守则二)—— 它只表"代码侧无法定夺",不表"这假设可能性低"。
- **业务因果排序键**:第一键 = 业务因果(behavior_class textbook signature + 案例库/专家外部信号),第二键 = 三态(详见 §2)。每块标出本假设的两键取值,供 §2 排序。

---

## 2. 排序规则(MUST,spec §3 守则三 + pairwise-discriminator)

**业务因果先验优先于代码可证性。** 排序两级键:

- **第一键 = 业务因果**:behavior_class textbook signature 命中强度 + 案例库 / 专家外部信号背书。命中 signature(例后付费"无余额仍下单成功"约等于递延/周期计费,见速查 §2.1)或案例库命中的假设,业务因果先验高。
- **第二键 = 三态**:同业务因果先验下,validated > inconclusive。

关键裁决(不要回退):

- **命中 signature / 案例库的 inconclusive 可超业务先验低的 validated。** 决定性数据出 index 标 inconclusive 的假设(例递延收费 charge model 出 index),只要业务因果先验更强,排在"代码可证但业务因果次之"的 validated 之上。
- **唯一下沉的是 invalidated。** 被证据反证(机制不成立)的假设下沉。
- **外部信号优先于对抗自纠。** 有案例库命中 / 专家 ground truth 时,以它为锚,**不让 host 的 refute 推理压过外部信号**(spec §3 末 + §1 核心教训:源起递延案两次押错头名,就是让"代码可证"压过了业务先验、让对抗推理压过了外部信号)。
- **缺决定性鉴别信号时 pairwise abstain,不拿弱代理顶替。** 每个**头部假设对**必须声明"区分这两者需要哪个决定性信号";该信号未获取 -> **只对这一对 abstain**(不押其一为头名),有充分证据的其它假设照常排序(不打瘫全部)。**固化反例**:`共享基建故障` vs `单实体专属配置坏` 的决定性信号 = 兄弟入口/兄弟短码/同后台其它实体对照;**规模信号(呼入量大/爆炸半径大)只说明影响面/紧急度,不区分二者(单实体配置坏一样让全部该实体用户报障),不得作排序第一键抬升"共享"类;缺兄弟对照就在这一对间 abstain**。

排序输出格式:`假设 a > 假设 b > 假设 c`,每个 `>` 后一句为什么(业务因果先验比较 + 三态);若某对因信号缺失 abstain,该对写 `假设 a (?) 假设 b — 待确认: <所需信号>` 而非武断排序,让消费方核排序逻辑。

---

## 3. abstain(早停弃权,spec §4 刹车)

无足够证据区分头部假设时**弃权,不强排头名**。运维 = 事故分析非实时聊天,接受深档慢,宁弃权不押错头名(spec §1 教训 + §4)。

abstain 块写清:

- **为什么无法定夺**:头部 2-N 个假设业务因果先验相近 / 三态都 inconclusive / 关键证据缺失,无法区分谁是头名。
- **还差什么证据**:哪个 drill 未完成(列具体 MCP 调用 / 待 read_symbol 的方法)/ 哪个决定性数据出 index(列具体数据项,例 charge model 配置、运行态账户数据),拿到才能定夺。

abstain 不是失败,是诚实的早停刹车:把"差什么"列清,比押错头名误导排障方向更有价值。

---

## 4. 报告落点(MUST,Appendix B)

- **落 gitignored `<data_dir>/tmp/ops-localization/`**:默认 `<repo>/database/` 已 gitignored;**根 `tmp/` 未被 .gitignore 忽略,不准落那**。data_dir 在 worktree 内时 `git check-ignore` 必须命中;data_dir 仓外绝对路径则不在任何 git 仓库管理范围。
- **客户 FQN / 表名只在 gitignored 报告**,对外摘要中性化。
- **不进 value_raw / 凭据 / MSISDN / 姓名 / email**(红线 #9 家族):诊断报告只写实体名(FQN / 表 / config key)+ 机制描述,不写行数据快照 / 配置原始值 / 客户标识。
- **大报告分段写入**:先建报告头,再逐段(逐假设块 / 排序输出 / abstain)追加;弱 host 单次大 content 会截断(同现 `requirement-impact-analysis` skill 经验),分段写入防截断。

---

## 5. 推荐排查流程(disambiguation playbook,MUST,给人跑非自动执行)

把 Phase 4 的差异化假设表 + §2 pairwise-discriminator 渲染成**有序、人可执行的排查清单**,让 ops 沿着它收敛到真根因。**这是诊断排查顺序、不是判决(locator-not-simulator)**:它是 §3 abstain 里"还差什么证据"的有序可执行版。**skill 不自动执行**(无生产库 / 日志权限),是给 ops 在自己的 DB / 日志工具里跑的清单。

**只诊断、不给药方(MUST):** playbook 只列"查什么、看哪张表哪列 / 哪个日志签名、结果怎么判读转向哪个假设",**绝不**给修复方向 / 药方(改哪行代码 / 改哪个配置值 / 怎么补校验闸)—— 修复留给 ops / dev。它回答"下一步查什么才能劈开假设",不回答"怎么修"。

**排序 = 鉴别力 × 成本:** 最便宜且最能劈开头部假设的检查排最前(本质是把 §2 pairwise-discriminator 排成序 —— 每对假设的决定性鉴别信号,按"拿到它的成本低 + 一次能分开的假设多"升序铺)。

**每步固定格式(纯文本):**

```
[排查N] 查什么 -> [位置] 表/FQN/日志签名(带 MCP 出处) -> [判读] 结果A->假设X坐实 / 结果B->转假设Y
```

**第 0 步 = 边界/可达性门(MUST 打头):** 先判"故障源是否在本系统可见边界内",再排内部检查步。

- (a) **用户可见错误串在索引代码里搜得到吗**(`search_source` / `search_code`)?搜不到 = 强暗示故障源在 scope 外(上游网关 / 下游外部系统 / 客户端),别在路灯下找钥匙。
- (b) **有无请求到达本系统的证据**(入口 census / 入站访问日志)?
- 判定:
  - **scope 外** -> 标注"故障源可能在 ContextOS 索引范围外(`source_roots` 仅本系统),不强钻内部;指向该查的上游 / 外部系统",**停或降级**(不硬押一个内部假设为头名)。
  - **scope 内** -> 继续内部排查步(排查 1、2、...)。

**select-not-generate(检查目标也要,MUST):** playbook 每步点名的表 / 列 / FQN / 常量 / 日志签名,必须是 Phase 3 drill 中 MCP 真命中带出处的 —— 表带 `lookup_table` / 血缘出处,代码带 FQN + file:line,错误码 / 常量带定义出处。**不脑补检查目标。** 索引外的操作侧指针(生产日志 / 网关配置 / 运行态账户数据)明确标 `[操作侧, 需人工核, 非代码事实]`,不冒充代码命中。

**红线 #9(MUST):** 说"查哪张表哪列 / 看什么字段"可以;**绝不** emit 行值 / `value_raw` / 配置原始值 / MSISDN / 姓名 —— 那是 ops 在自己 DB / 日志工具里做,skill 只给"去哪查、看哪列"的指针。

**收尾 -> Phase 5:** 某步把某假设坐实后,接 Phase 5 人确认回写(`record_confirmed_case`);**绝不 host 自判就写**,确认信号必来自外部人(human-gated)。

### Worked example(中性合成 playbook)

沿用本文件末 Worked example 的中性合成场景(`com.example.app.*` FQN + `APP_*` 表/配置;后付费余额 0 却订购成功),把假设 1(递延 / 周期计费)vs 假设 2(订购路径无同步余额闸)排成人可跑的清单:

```
[排查0 边界门]
  (a) 用户可见错误 / 现象串在索引里搜得到吗?
      -> search_source("订购成功") / search_code(相关入口)  [MCP: search_source]
      -> 命中 com.example.app.order.OfferOrderService = 入口在 scope 内, 继续内部排查
      -> 搜不到 = 疑故障源在 scope 外(下单入口在上游编排/外部计费系统), 标注并停/降级
  (b) 请求到达本系统证据? -> 入站访问日志有该订购请求  [操作侧, 需人工核, 非代码事实]

[排查1 charge_mode 取值]  (决定性、劈开假设1 vs 假设2, 成本低排最前)
  查什么 -> 该 offer 的计费模式是同步扣还是递延/周期扣
  位置   -> APP_OFFER_CONFIG.charge_mode 列  [MCP: search_source("chargeMode") / lookup_table:APP_OFFER_CONFIG]
           (查该列取值属操作侧: 看运行态配置行 [操作侧, 需人工核, 非代码事实] —— 只看哪列, 不 emit 行值)
  判读   -> 取值=递延/周期 -> 假设1(deferred_charge)坐实, 同步路径本就不实扣
         -> 取值=同步实扣 -> 转假设2, 继续排查2

[排查2 订购路径余额闸缺失]
  查什么 -> 订购同步路径是否调用余额/信用校验
  位置   -> com.example.app.order.OfferOrderService.submit (第40-58行)  [MCP: read_symbol, file:line]
           lookup_calls("com.example.app.balance.BalanceChecker.check", callers)  [MCP: lookup_calls]
  判读   -> submit 内无 balance 校验 且 callers=0 -> 假设2(order_no_balance_gate)坐实
         -> 有校验调用 -> 转配置侧假设3

[排查3 校验开关]
  查什么 -> 余额校验开关是否被关闭 / 缺失回退
  位置   -> app.order.balance_check_enabled  [MCP: lookup_config / trace_config_impact]
           (查开关运行态取值属操作侧 [操作侧, 需人工核, 非代码事实])
  判读   -> 开关=关 -> 假设3(config_switch_off)坐实
         -> 开关=开(默认) -> 假设3 invalidated

收尾: 某步坐实 -> 经 ops/专家确认后 record_confirmed_case 回写(不 host 自判)。
```

注:上例排查目标(FQN / 表 / 列 / 开关 key)全部是 Worked example 里 MCP 真命中带出处的;"看该列取值 / 看开关取值"是操作侧动作(ops 在自己 DB / 配置工具里做),已标 `[操作侧, 需人工核, 非代码事实]`,playbook 只给指针、不 emit 任何行值。

---

## Worked example(中性,递延收费类比合成版)

下例全用中性合成值(`com.example.app.*` FQN + `APP_*` 表/配置),仅作格式示范,非真客户。

**合成现象**:后付费账户余额为 0、信用额度小于订单金额,订购某 offer 却成功;期望不变量 = `余额 + 信用 >= 应扣`。

### 报告头(节选)

- **现象输入**:期望 = 余额+信用 < 应扣时订购应被拦;实际 = 余额 0、信用 < 订单额仍订成功;被违反不变量 = `余额+信用 >= 应扣`。behavior_class 路由 = `扣费`(见速查 §1)。
- **health_check 工具态**:`code_projection.status=ok` / `jdt_ls=cold`(非问题,首次 search_code 自动构造)/ `oracle=offline`(软降级,`search_sql` + 已建 lineage 仍可查)/ `ripgrep=ok`。
- **census 地基**:`profile_info -> rag_corpora=[general, confirmed-cases]` · `repo_root=/path/to/app` · `source_roots=[/path/to/app/src]`(census 以这些 root 为准)。
- **案例库可用性**:`rag_search(corpora=["confirmed-cases"])-> 1 命中`(mechanism_tag `deferred_charge`),作 Phase 2 differential 起点。
- **怎么读**:三态 + 业务因果排序释义(同 §0)。

### 假设 1(递延 / 周期计费)

- **假设 1**:订购与扣费时点解耦,扣费在后置周期 / 账单侧发生,同步订购路径不实扣,故同步路径无余额闸也能订成功。
- **behavior_class + mechanism_tag**:`扣费` / `deferred_charge`。
- **机制族来源**:案例库命中(`deferred_charge` case)+ 速查 §2.1 don't-miss textbook signature("后付费无余额仍下单成功约等于递延/周期计费")。
- **时点拆解**:现象发生在「订购」时点;不变量本应在「扣费」时点守,但扣费在后置账单侧 -> 订购时点的同步校验覆盖不到递延扣费(见速查 §3 时点矩阵)。
- **drill 证据 + MCP 出处**:
  - `[事实]` `search_code("OfferOrderService")-> com.example.app.order.OfferOrderService`。
  - `[事实]` `read_symbol("com.example.app.order.OfferOrderService.submit")-> 第 40-58 行:只校验 offer 状态,无 balance/credit 校验`。
  - `[事实]` `search_source("chargeMode")-> APP_OFFER_CONFIG.charge_mode 配置项(text-hit)`,charge model 取值决定同步扣或递延扣。
  - `[推断机制]` charge_mode = 递延时,订购同步路径不实扣 -> 订购时点不变量 `余额+信用>=应扣` 不在该时点被守(推断机制,defeasible)。
- **可达性**:`[事实]` 订购路径可达,同步路径无 balance 硬拦截。免责:可达性只喂三态、不反证现象(同步无闸不证明递延机制存在与否)。
- **三态判定**:`inconclusive` —— charge model 决定性数据(`APP_OFFER_CONFIG.charge_mode` 运行态取值)出 index,代码侧无法定夺是否递延。注:inconclusive 不等于低可能性。
- **业务因果排序键**:第一键 = 高(命中 signature + 案例库);第二键 = inconclusive。

### 假设 2(订购路径无同步余额闸)

- **假设 2**:订购路径本身缺余额 / 信用同步校验闸,无论 charge model 如何,同步入口都不拦。
- **behavior_class + mechanism_tag**:`扣费` / `order_no_balance_gate`。
- **机制族来源**:host 临场 don't-miss(扣费类:订购同步路径缺余额/信用闸),代码结构直接可证。
- **时点拆解**:现象与校验同在「订购」同步时点;若该时点应守余额不变量却无闸,则同步入口放行。
- **drill 证据 + MCP 出处**:
  - `[事实]` `read_symbol("com.example.app.order.OfferOrderService.submit")-> 第 40-58 行无 balance/credit 校验调用`。
  - `[事实]` `lookup_calls("com.example.app.balance.BalanceChecker.check", callers)-> 0`(订购路径未调余额校验接口)。
- **可达性**:`[事实]` 路径可达、无硬拦截 -> validated(代码侧坐实订购同步路径确无余额闸)。免责:可达只喂三态。
- **三态判定**:`validated`。
- **业务因果排序键**:第一键 = 中(代码可证但 textbook 上"无闸"是表层现象,递延才是更深业务因果);第二键 = validated。
- **排序说明**:虽 validated,业务因果先验次于假设 1,**不押头名**(押"代码可证的无闸"为 primary 正是源起案的错)。

### 假设 3(配置开关关闭放行)

- **假设 3**:某校验开关配置被关闭(或缺失回退到关闭),订购侧等于无该校验。
- **behavior_class + mechanism_tag**:`配置` / `config_switch_off`。
- **机制族来源**:host 临场 don't-miss(配置类:校验开关关闭)。
- **drill 证据 + MCP 出处**:
  - `[事实]` `lookup_config("app.order.balance_check_enabled")-> 默认 true(缺失时仍开校验,非放行)`。
  - `[事实]` `trace_config_impact("app.order.balance_check_enabled")-> 命中校验分支但默认值不放行`。
- **三态判定**:`invalidated` —— 配置开关默认开校验,反证"开关关闭放行"机制不成立。
- **业务因果排序键**:invalidated 下沉。

### 排序输出

`假设 1 > 假设 2 > 假设 3`

- `假设 1 > 假设 2`:假设 1 业务因果先验更强(命中 signature + 案例库),虽 inconclusive 仍超假设 2 的 validated(spec §3 守则三:命中 signature/案例库的 inconclusive 可超业务先验低的 validated;别让"代码可证"压业务先验)。
- `假设 2 > 假设 3`:假设 3 被反证(invalidated 唯一下沉),排末。

### abstain 触发条件(演示)

若案例库**未**命中 `deferred_charge`、且 host 无法从 signature 区分假设 1 与假设 2 的业务因果先验高低(两者都 inconclusive / 证据相近),则 abstain:不强排头名,列"还差 `APP_OFFER_CONFIG.charge_mode` 运行态取值(决定性数据出 index)+ 假设 1 的账单侧 drill 未完成",拿到才能定夺谁是头名。
