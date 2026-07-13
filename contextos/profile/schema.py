"""9-namespace Profile schema. Cross-namespace validation lives in validator.py."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


class LLMConfig(_StrictBase):
    provider: str                 # 标签/家族,如 "claude" / "qwen"(语义不变)
    api_key_env: str              # 持有 API key 的环境变量名
    # 以下为构建真实 OpenAI 兼容 client 所需(Plan 02a 新增,全可选不破坏既有 profile)
    base_url: str | None = None   # OpenAI 兼容端点,如 https://host/v1
    model: str | None = None      # 端点上的模型 id
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    timeout_seconds: Annotated[int, Field(gt=0)] = 60
    max_retries: Annotated[int, Field(ge=0)] = 2


class EmbeddingConfig(_StrictBase):
    model: str
    device: Literal["cpu", "cuda", "mps"] = "cpu"


class RerankerConfig(_StrictBase):
    enabled: bool = True
    model: str
    top_k_input: Annotated[int, Field(gt=0)] = 50
    top_k_output: Annotated[int, Field(gt=0)] = 10


class RagConfig(_StrictBase):
    """03 桥 2 hybrid 检索配置(Plan 03b)。

    dense 路默认关 -> MVP sparse-only; gate 评测决定是否开(spec §3.3/§6)。
    """

    dense_enabled: bool = False                          # dense 路开关; 默认关
    reranker_backend: Literal["fake", "bge"] = "fake"
    window_radius: Annotated[int, Field(ge=1)] = 8       # query-时窗口: 命中行上下各取几行
    max_passages_per_doc: Annotated[int, Field(ge=1)] = 3


class QueryExpansionConfig(_StrictBase):
    enabled: bool = True
    translation_provider: str
    fallback_provider: str


class StorageConfig(_StrictBase):
    data_dir: str
    jdtls_workspace_dir: str | None = None


class IngestionConfig(_StrictBase):
    default_cleanup: Literal["none", "incremental", "full", "scoped_full"] = "full"
    chunk_strategy: Literal["h2_h3", "paragraph", "fixed_chars"] = "h2_h3"
    min_chunk_chars: Annotated[int, Field(ge=0)] = 30


class SourceConfig(_StrictBase):
    type: Literal["git", "dir"]
    location: str
    glob: list[str] = Field(
        default_factory=lambda: ["**/*.md", "**/*.docx", "**/*.png"]
    )
    leakage_exclude_regex: list[str] = Field(default_factory=list)


class OcrConfig(_StrictBase):
    backend: Literal["fake", "paddle", "tesseract"] = "fake"
    languages: list[str] = Field(default_factory=lambda: ["ch", "en"])


class CorpusConfig(_StrictBase):
    sources: list[SourceConfig] = Field(default_factory=list)
    materialized_dir: str = ""        # 空 = <data_dir>/materialized
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    formats: list[str] = Field(default_factory=lambda: ["md", "docx", "png"])


class ScopeConfig(_StrictBase):
    # Plan 02b 三道 guard 数字旋钮(spec 7)。逐客户可调。
    prefilter_enabled: bool = True
    min_chars: Annotated[int, Field(ge=0)] = 12
    min_alpha_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.3
    samples: Annotated[int, Field(ge=1)] = 1          # scope judge 判几遍(1=二元单次; >1=自一致采样)
    reject_below: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    degraded_below: Annotated[float, Field(ge=0.0, le=1.0)] = 0.8
    domain_description: str = ""                       # B 层接缝; 空=只跑 A 层
    signal_terms_path: str = ""                        # 客户词表路径; 空=用默认表
    stop_keywords_path: str = ""                       # 客户停用词表路径; 空=只用通用 default


class InputConfig(_StrictBase):
    # source_kind -> 是否启用该输入适配器(design 02 §1.1 "Profile 化")。
    # v1 实装 text + docx + email;im/transcript 留 v2(枚举占位)。
    # 默认工厂保守只开 text+docx, email 经 profile 显式启用(见 config/profile.example.toml)。
    adapters: dict[str, bool] = Field(
        default_factory=lambda: {"text": True, "docx": True}
    )
    scope: ScopeConfig = Field(default_factory=ScopeConfig)


class JdtlsRuntimeConfig(_StrictBase):
    jdtls_path: str
    lombok_path: str
    java_home: str


class OracleConfig(_StrictBase):
    tns_admin: str
    allowed_instances: list[str] = Field(..., min_length=1)
    # 查询限制 + 元数据缓存(Profile 设计 §3.6)
    max_rows_hard_limit: Annotated[int, Field(gt=0)] = 1000
    query_timeout_seconds: Annotated[int, Field(gt=0)] = 30
    # 连接(握手)超时秒数: 短值让 oracle offline 时连接快速放弃, 避免 health_check 的 fan_out
    # 逐个等满 oracledb 默认连接超时(几十秒级 × 白名单实例数)而卡几分钟。与 query_timeout 无关。
    connect_timeout_seconds: Annotated[int, Field(gt=0)] = 5
    reconnect_on_idle: bool = True
    metadata_cache_ttl_hours: Annotated[int, Field(ge=0)] = 24
    # 测试实例 -> 生产显示名映射(可选, 默认空, 非客户必配; 裁决 5)。
    # 仅影响 canonical key 的 db 段展示, 不影响匹配/分析(身份锚 = owner.table)。
    instance_alias: dict[str, str] = Field(default_factory=dict)
    # dblink 兜底映射(Block 1b): dblink 名 -> 目标库 TNS/别名。仅当 ALL_DB_LINKS.HOST
    # 自动解析不出目标库时手配; 系统优先解析 TNS 描述符(见 05 §8.4)。
    dblink_map: dict[str, str] = Field(default_factory=dict)


# ---- 多方言 [database] 统一段(spec 2026-07-10 附录 A) ----

_ENV_SAFE_ALIAS = r"[A-Za-z_][A-Za-z0-9_]*"


class MysqlInstanceConfig(_StrictBase):
    """一个 MySQL 实例: alias 承担凭据键(MYSQL_<ALIAS>_USER/_PASSWORD)+ 白名单键
    两个角色(MySQL 无 TNS 亦不造等价物, host/port 直连)。旋钮语义沿 Oracle。"""
    alias: str
    host: str
    port: Annotated[int, Field(gt=0, le=65535)] = 3306
    databases: list[str] = Field(..., min_length=1)
    max_rows_hard_limit: Annotated[int, Field(gt=0)] = 1000
    query_timeout_seconds: Annotated[int, Field(gt=0)] = 30
    connect_timeout_seconds: Annotated[int, Field(gt=0)] = 5
    metadata_cache_ttl_hours: Annotated[int, Field(ge=0)] = 24

    @field_validator("alias")
    @classmethod
    def _alias_env_safe(cls, v: str) -> str:
        # A.5: alias 要拼进环境变量名 MYSQL_<ALIAS>_USER, 必须是合法标识符
        import re
        if not re.fullmatch(_ENV_SAFE_ALIAS, v):
            raise ValueError(
                f"mysql instance alias {v!r} must match {_ENV_SAFE_ALIAS} "
                "(用于拼环境变量名 MYSQL_<ALIAS>_USER/_PASSWORD)"
            )
        return v


class MysqlConfig(_StrictBase):
    instances: list[MysqlInstanceConfig] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _aliases_unique(self) -> "MysqlConfig":
        # 冷验证 M1(2026-07-10): alias 是凭据键(MYSQL_<ALIAS>_USER/_PASSWORD),
        # 重复=凭据静默碰撞; env 变量名 upper 拼接, 按 case-insensitive 查重
        seen: dict[str, str] = {}
        for inst in self.instances:
            key = inst.alias.upper()
            if key in seen:
                raise ValueError(
                    f"duplicate mysql instance alias {inst.alias!r} "
                    f"(与 {seen[key]!r} 大小写不敏感冲突; alias 是凭据键必须唯一)"
                )
            seen[key] = inst.alias
        return self


class OpenGaussConfig(_StrictBase):
    """预留形状(本轮不实装, 仅 schema 占位; DatabaseConfig 拒载 type=opengauss)。
    compat_mode 是库级属性, 决定 sqlglot 方言映射(dialects.get_traits)。"""
    compat_mode: Literal["A", "B", "PG"] = "A"


class DatabaseConfig(_StrictBase):
    """目标业务库统一段: type 判别式 + type 专属子段一一对应(附录 A.1)。
    一个项目只对应一种数据库(2026-07-07 用户裁决), 不做多库列表。"""
    type: Literal["oracle", "mysql", "postgres", "opengauss"]
    oracle: OracleConfig | None = None
    mysql: MysqlConfig | None = None
    opengauss: OpenGaussConfig | None = None

    @model_validator(mode="after")
    def _type_matches_subsection(self) -> "DatabaseConfig":
        if self.type in ("postgres", "opengauss"):
            # 预留未实装: 显式拒载不静默降级(A.1); traits 行与设计依据见
            # db_provider/dialects.py + spec 附录 B/I。
            raise ValueError(
                f"database.type={self.type!r} is reserved for future dialects "
                "and not implemented yet (预留未实装)"
            )
        present = {n for n in ("oracle", "mysql", "opengauss")
                   if getattr(self, n) is not None}
        if self.type not in present:
            raise ValueError(
                f"database.type={self.type!r} requires [database.{self.type}] subsection"
            )
        extra = sorted(present - {self.type})
        if extra:
            raise ValueError(
                f"subsection(s) {extra!r} does not match database.type={self.type!r}"
            )
        return self


class CodeIndexConfig(_StrictBase):
    """04b code_* 投影(spec D1-D9)。全部有默认值: 老 profile 不写 [code_index] 也能 load。"""

    indexer_jar: str = "vendor/java-indexer/target/java-indexer-1.0.0.jar"  # 相对仓根或绝对
    indexer_xmx: str = "4g"
    java_version: str = "1.8"                       # build_context java_version
    extra_classpath_dirs: list[str] = Field(default_factory=list)
    watcher_enabled: bool = True
    watcher_debounce_seconds: Annotated[float, Field(gt=0.0, le=60.0)] = 2.0
    incremental_max_files: Annotated[int, Field(ge=1)] = 500       # 契约 §4.3 全量回退阈值
    sample_check_classes: Annotated[int, Field(ge=0)] = 50         # spec §3.1 条件 3
    sample_check_methods: Annotated[int, Field(ge=0)] = 100
    sample_check_max_mismatch: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05
    read_symbol_max_lines: Annotated[int, Field(ge=1)] = 400       # spec §7 护栏 3
    lookup_calls_max_depth: Annotated[int, Field(ge=1, le=2)] = 2  # spec §9 caps
    lookup_calls_fanout: Annotated[int, Field(ge=1)] = 200
    lookup_calls_max_rows: Annotated[int, Field(ge=1)] = 1000


class DaoSqlPattern(_StrictBase):
    """DAO .sql 文件识别规则(05 Layer 2)。避开 LP 硬编码 /impl/+/src/main/。"""
    path_contains: list[str] = Field(default_factory=list)
    conjunction: Literal["all", "any"] = "all"


class CodeConfig(_StrictBase):
    """profile.code(04 代码搜索 + 05 Layer 2 源码扫描)。Profile 设计 §3.2。"""
    source_roots: list[str] = Field(default_factory=list)   # 空 -> 扫 project.path
    exclude_dirs: list[str] = Field(
        default_factory=lambda: ["target", "build", "node_modules", ".git"]
    )
    dao_sql_patterns: list[DaoSqlPattern] = Field(default_factory=list)
    # search_source census 模式(① 基座; profile 驱动, 非敏感=框架类名/前缀, 非值/凭据)。
    dispatch_patterns: list[str] = Field(default_factory=list)        # 框架字符串派发(caller census)
    carrier_read_patterns: list[str] = Field(default_factory=list)    # 配置载体读取(消费方 census)


class ShardStrategy(_StrictBase):
    """分片表归并策略。避开 LP 硬编码巴基斯坦区号正则。"""
    type: Literal["regex"] = "regex"       # v1 只支持 regex(plugin 留 v2)
    pattern: str                            # 后缀正则, 去掉视为同表


# Oracle 标准系统/内置 schema(跨客户通用, 每个 Oracle 库都有)。作 exclude_schemas 默认值:
# discover_owners 会把它们排掉, 不去抓它们的元数据(纯浪费)。这是中立默认, 非耦合 —— 不含任何
# 客户业务 owner(那种写死才是耦合, 见 LP 的 'AD'/'CD'/... 反例)。fnmatch: 精确名 + glob。
_ORACLE_SYSTEM_SCHEMAS: tuple[str, ...] = (
    "SYS", "SYSTEM", "SYSAUX", "OUTLN", "DBSNMP", "APPQOSSYS", "AUDSYS",
    "GSMADMIN_INTERNAL", "GSMCATUSER", "GSMUSER", "GSMROOTUSER", "ANONYMOUS",
    "XDB", "XS$NULL", "WMSYS", "CTXSYS", "ORDSYS", "ORDDATA", "ORDPLUGINS",
    "SI_INFORMTN_SCHEMA", "MDSYS", "MDDATA", "OLAPSYS", "LBACSYS", "DVSYS", "DVF",
    "OJVMSYS", "DBSFWUSER", "REMOTE_SCHEDULER_AGENT", "SYS$UMF", "GGSYS",
    "DGPDB_INT", "DIP", "TSMSYS", "EXFSYS", "ORACLE_OCM", "SYSBACKUP", "SYSDG",
    "SYSKM", "SYSRAC", "SPATIAL_CSW_ADMIN_USR", "SPATIAL_WFS_ADMIN_USR",
    "APEX_*", "FLOWS_*", "C##*",     # glob: APEX / 老 flows / 公共(common)用户
)


class TablesConfig(_StrictBase):
    """profile.tables(05 Layer 7 NameResolver 数据表归一化)。Profile 设计 §3.3。

    注意: 与 profile.config_tables(06 配置表识别)是两件事, v1 不实现 config_tables。
    """
    exclude_schemas: list[str] = Field(default_factory=lambda: list(_ORACLE_SYSTEM_SCHEMAS))
    # discover_owners 用 fnmatch 匹配: 精确名 + glob(如 "APEX_*" / "C##*" / "*_STAGE" 排 stage 环境)。
    # 默认 = Oracle 标准系统/内置 schema 全集(跨客户中立, 非耦合; 避免逐个白查它们的元数据)。
    shard_strategy: ShardStrategy | None = None     # 默认 None -> 不归并
    monthly_pattern: str = r"_\d{6}$"               # 默认 _YYYYMM 后缀(Python 正则, 喂 NameResolver 归一)
    typo_map: dict[str, str] = Field(default_factory=dict)
    # 方案 B: Oracle 元数据抓取时按表名排除历史/分区/备份/临时表(削减某大型客户代码库海量字典)。
    # **Oracle REGEXP_LIKE 语法**(用 [0-9] 非 \d), 与 monthly_pattern(Python 语法, 喂归一)分开,
    # 服务端 NOT REGEXP_LIKE 注入 table 类元数据查询; 空列表 = 不排除。默认中立高置信(跨域普适
    # 历史/临时标记); 客户特定(分片 / 年表 / 尾号)放客户 profile, 守 default 跨域中立。
    exclude_table_patterns: list[str] = Field(default_factory=lambda: [
        r"_[0-9]{6}$",        # 月表 _YYYYMM(某大型客户代码库实测占 62%, 大头)
        r"_[0-9]{8}$",        # 日表 _YYYYMMDD
        r"_BAK[0-9]*$",       # 备份 _BAK / _BAK1 ...
        r"_BACKUP$",          # 备份 _BACKUP
        r"_(TMP|TEMP)$",      # 临时
    ])
    # option A: 数据库维度对象元数据抓取范围。默认 False = 只抓表级血缘需要的 dependencies +
    # dblinks(轻查), 跳过 columns/indexes/constraints(per-table 重查, 某大型客户代码库满库抓列 ~40min,
    # 唯一消费方是 config 维度)+ sequences/views/procedures(全仓暂无消费方)。将来 config 维度
    # 按 LP 模板归并(分片表只抓样本表的列)抓列时再置 True opt-in。
    fetch_full_object_metadata: bool = False

    @field_validator("exclude_table_patterns")
    @classmethod
    def _no_empty_pattern(cls, v: list[str]) -> list[str]:
        # 空串/纯空白模式 -> NOT REGEXP_LIKE(TABLE_NAME,'') 会误排全表(元数据全空), 配置层硬拒。
        if any(not (p and p.strip()) for p in v):
            raise ValueError("exclude_table_patterns 不能含空串/纯空白(会误排全部表)")
        return v


class GradleJavaConfig(_StrictBase):
    gradle_home: str | None = None
    gradle_version_override: str | None = None
    gradle_arguments: str | None = None
    gradle_java_home: str | None = None
    gradle_wrapper_enabled: bool = False


class ProjectConfig(_StrictBase):
    name: str
    path: str
    language: Literal["java", "python", "ts", "go"]
    build_system: Literal["gradle", "maven", "uv", "pip", "go", "npm"] = "gradle"
    java: GradleJavaConfig | None = None


class ConfigTableDetection(_StrictBase):
    """06 路径 A: DB 配置表识别启发(design 06 §3.4)。默认通用中立, 业务词由客户/seed 填。"""
    name_patterns: list[str] = Field(default_factory=list)        # 表名启发(通用中立, 客户/seed 填)
    rule_columns: list[str] = Field(default_factory=lambda: [
        "EFFECTIVE_DATE", "EXPIRE_DATE", "BEGIN_DATE", "END_DATE",
        "STATUS", "STATE", "PRIORITY", "USE_FLAG", "ENABLED_FLAG",
    ])  # 通用规则列(跨域, 非客户专属业务)
    comment_keywords_zh: list[str] = Field(default_factory=lambda: ["配置", "参数", "规则", "白名单", "黑名单", "开关"])
    comment_keywords_en: list[str] = Field(default_factory=lambda: ["config", "parameter", "rule", "whitelist", "switch", "threshold"])


class ConfigTablesConfig(_StrictBase):
    """profile.config_tables(06 配置表维度)。与 profile.tables(05 数据表归一化)是两件事。"""
    detection: ConfigTableDetection = Field(default_factory=ConfigTableDetection)
    big_table_row_threshold: int = 50000


class ConfigFileSources(_StrictBase):
    """06 配置**文件**源识别(design 06 §3.5)。"""
    include_extensions: list[str] = Field(default_factory=lambda: [
        ".properties", ".yaml", ".yml", ".json", ".xml",
    ])
    include_paths: list[str] = Field(default_factory=list)        # 空 = 全仓扫
    exclude_paths: list[str] = Field(default_factory=lambda: ["**/test/**", "**/node_modules/**"])
    json_blacklist: list[str] = Field(default_factory=lambda: ["package.json", "tsconfig.json", "package-lock.json"])


class ConfigConfig(_StrictBase):
    """profile.config(06 配置文件维度 + 自研框架注解 C+B 策略 + sanitizer 词表来源)。"""
    file_sources: ConfigFileSources = Field(default_factory=ConfigFileSources)
    framework_annotations: list[str] = Field(default_factory=list)  # 自研框架注解名(C+B 策略, profile 驱动生成 query)
    sensitive_key_patterns: list[str] = Field(default_factory=lambda: [
        "password", "passwd", "secret", "token", "credential",
    ])
    custom_semgrep_rules: list[str] = Field(default_factory=list)   # 占位(v1 不用 Semgrep; 留兼容字段)
    corpus_subset_prefixes: dict[str, list[str]] = Field(default_factory=dict)  # corpus 子集名 -> path_prefixes(scoped grep 不串库, 见 06 corpus_scope)

    @model_validator(mode="after")
    def _ensure_confirmed_cases_corpus(self) -> "ConfigConfig":
        # spec Appendix C MUST: confirmed-cases 是服务端内建固定 corpus(非 host 动态注册)。
        # 默认键必在, 使 rag_search(corpora=["confirmed-cases"]) 过 middleware 白名单。
        # 客户显式给了该键则尊重客户配置(不 clobber); 缺失才补默认。
        if "confirmed-cases" not in self.corpus_subset_prefixes:
            self.corpus_subset_prefixes["confirmed-cases"] = ["confirmed-cases"]
        return self


class LlmRerankConfig(_StrictBase):
    """07 LLM 重排(provider.rerank)运行旋钮; 区别于 [reranker](03 BGE 重排, RerankerConfig)。

    映射到 contextos.rerank.schema.RerankConfig(在 build_impact_map_impl 的 rerank_config_from_profile
    接线), 默认值与 RerankConfig 对齐, 故既有 profile(无 [llm_rerank] 段)向后兼容。批量/并发实测背景:
    真 DeepSeek 逐候选(batch=1)串行 80 次 ~9.9min 超时; 批量 8 + 并发 6 -> ~29s(见 provider 注释)。
    """

    batch_size: Annotated[int, Field(ge=1)] = 8        # 每次 LLM 判几个候选(1=逐候选最准最慢, 易超时)
    max_concurrency: Annotated[int, Field(ge=1)] = 6   # chunk 并发上限(线程池; 1=串行)
    method_cap: Annotated[int, Field(ge=0)] = 30       # 每维 defensive cap(只 LLM 判 top-N)
    sql_cap: Annotated[int, Field(ge=0)] = 30
    config_cap: Annotated[int, Field(ge=0)] = 20


class CorroborationConfig(_StrictBase):
    """08 corroboration 透明加权求和的可调参数(design §3.1/§3.2)。

    基权 w_* 和 ~= 1.0;eligible-set 重归一化在 corroboration 引擎内按 candidate.kind
    动态做(不在此预算)。09 评测 §6 校准后调这些值。
    """

    # 基桥权重(design §3.1 v1 初值;eligible 子集内重归一化)。ge=0 拦负/坏权重(防静默扭曲
    # 置信度: 负权重会让 renormalize 出负有效权重, 虽 score_overall 终被 clamp 但相对排序失真)。
    w_code_search: float = Field(default=0.25, ge=0.0)
    w_db_lineage: float = Field(default=0.20, ge=0.0)
    w_config_dimension: float = Field(default=0.15, ge=0.0)
    w_rag: float = Field(default=0.15, ge=0.0)
    w_dict: float = Field(default=0.15, ge=0.0)            # [v1 deferred -> dict 桥未实装] 权重占位
    w_llm_rerank: float = Field(default=0.10, ge=0.0)
    # corroboration bonus + 阈值(阈值 [0,1]; consensus 桥数 >=1)
    alpha_consensus: float = Field(default=0.10, ge=0.0)   # 多桥共识 bonus 系数
    high_threshold: float = Field(default=0.75, ge=0.0, le=1.0)    # HIGH 分桶阈值(§3.2 SSOT)
    medium_threshold: float = Field(default=0.4, ge=0.0, le=1.0)   # MEDIUM 下界
    consensus_score: float = Field(default=0.6, ge=0.0, le=1.0)    # 单桥计入共识的 score_bridge 门
    consensus_min_bridges: int = Field(default=2, ge=1)   # HIGH/bonus 需要的共识桥数


class Profile(_StrictBase):
    llm: LLMConfig
    embedding: EmbeddingConfig
    reranker: RerankerConfig
    query_expansion: QueryExpansionConfig
    storage: StorageConfig
    ingestion: IngestionConfig
    input: InputConfig = Field(default_factory=InputConfig)
    corpus: CorpusConfig = Field(default_factory=CorpusConfig)
    rag: RagConfig = Field(default_factory=RagConfig)
    code: CodeConfig = Field(default_factory=CodeConfig)        # Plan 05 新增
    tables: TablesConfig = Field(default_factory=TablesConfig)  # Plan 05 新增
    config_tables: ConfigTablesConfig = Field(default_factory=ConfigTablesConfig)  # Plan 06 新增
    config: ConfigConfig = Field(default_factory=ConfigConfig)  # Plan 06 新增
    corroboration: CorroborationConfig = Field(default_factory=CorroborationConfig)  # Plan 08 新增
    llm_rerank: LlmRerankConfig = Field(default_factory=LlmRerankConfig)  # Plan 07 旋钮接 profile (2026-06-09)
    code_index: CodeIndexConfig = Field(default_factory=CodeIndexConfig)  # Plan 04b 新增
    jdtls_runtime: JdtlsRuntimeConfig
    # [oracle] 旧段(兼容垫片输入): 单独出现时归一进 database 并清为 None(A.2/A.3)。
    # 生产码禁止直接引用 profile.oracle —— 统一取值点是 profile.database。
    oracle: OracleConfig | None = None
    database: DatabaseConfig | None = None
    projects: list[ProjectConfig] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _normalize_database(self) -> "Profile":
        """兼容垫片(附录 A.2): [oracle] -> [database] type=oracle 等价映射。

        归一后顶层 oracle 清 None——机械强制 A.3 的引用清零: 任何残留
        profile.oracle.* 引用在真跑时立刻 AttributeError, 不靠 convention。
        两段并存 = 配置错误硬拒; 都缺 = 无目标库配置硬拒。
        """
        if self.oracle is not None and self.database is not None:
            raise ValueError(
                "both [oracle] and [database] sections present; "
                "use [database] only ([oracle] 是旧式写法, 单独出现时自动归一)"
            )
        if self.oracle is not None:
            self.database = DatabaseConfig(type="oracle", oracle=self.oracle)
            self.oracle = None
        if self.database is None:
            raise ValueError(
                "missing [database] section (或旧式 [oracle] 段); "
                "目标业务库配置是必填项"
            )
        return self
