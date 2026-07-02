"""AppContext — 进程级 lazy 共享重资源(Plan 10 §4.2 + Plan 04b T14)。

设计:MCP server 进程内只建一个 AppContext(`from_profile(profile)`),持有跨请求
复用的重资源 —— llm / engine(05 lineage + 06 config 同库,共用 engine_from_profile)/
searcher(04b code_* 投影查表,秒级零 JDT)/ rag_provider(03 hybrid)/ oracle_router
(多库查询路由,Block 1b)。每个资源**独立 lazy**:首次访问该属性时才构造,之后缓存
复用(functools.cached_property)。

并发说明:stdio 单用户 v1 进程级单例够用;HTTP 多并发(v1.x)由 per-request registry
保证 shared 隔离,AppContext 重资源仍共享(design §4.2)。

资源构造全部委托既有工厂,不在此重写:
  - llm:llm/factory.py provider_from_profile(override=llm_override)
  - engine:storage/db.py engine_from_profile(05/06 表 + 04b code_* 投影同库)
  - searcher:ProjectionSearcher(engine)—— 04b 查询期投影-only(spec D3),
    JDT 只在 init/增量 build 期(jdt_adapter)
  - jdt_adapter:JdtlsAdapter(仅 build 期消费:init 抽样对照 / 手测;serve 不触发)
  - rag_provider:RagProvider(materialized_dir, make_reranker(profile.rag), profile.rag)
  - oracle_router():DbRouter(profile, engine),多库查询路由(Block 1b);engine 失败 -> None
  - oracle_querier():单连接探针(保留供测试/探针;多库路由走 oracle_router)
"""
from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
from contextos.code_intel.jdtls_provider.config import (
    JdtlsRuntimeConfig as JdtlsRuntimeConfigDC,
)
from contextos.code_intel.jdtls_provider.config import (
    ProjectConfig as ProjectConfigDC,
)
from contextos.code_intel.jdtls_provider.config import (
    StorageConfig as StorageConfigDC,
)
from contextos.code_intel.projection.searcher import ProjectionSearcher
from contextos.db_provider.sqlcl_mcp import connect_from_profile
from contextos.llm.base import LLMProvider
from contextos.llm.factory import provider_from_profile
from contextos.recall.rag_provider import RagProvider
from contextos.recall.reranker import make_reranker
from contextos.storage.db import engine_from_profile

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from contextos.db_provider.sqlcl_mcp import OracleClient
    from contextos.lineage.db_router import DbRouter
    from contextos.profile.schema import Profile

log = logging.getLogger(__name__)


class AppContext:
    """进程级共享重资源,lazy init,跨请求复用。每个属性首次访问时构造。

    资源:profile / llm / searcher / engine(lineage+config 共用 contextos.db) /
    rag_provider / oracle_router(多库查询路由,Block 1b) / oracle_querier(单连接探针)。
    """

    def __init__(self, profile: Profile, *, llm_override: LLMProvider | None = None) -> None:
        self.profile = profile
        self._llm_override = llm_override

    @classmethod
    def from_profile(
        cls, profile: Profile, *, llm_override: LLMProvider | None = None
    ) -> AppContext:
        return cls(profile, llm_override=llm_override)

    @cached_property
    def llm(self) -> LLMProvider:
        return provider_from_profile(self.profile, override=self._llm_override)

    @cached_property
    def engine(self) -> Engine:
        # 05 lineage 表 + 06 config 表同库,一处 engine 两维共用(red line #6 SQLAlchemy)。
        return engine_from_profile(self.profile)

    @cached_property
    def searcher(self) -> ProjectionSearcher:
        """04b 查询期投影-only(spec D3), JDT 只在 init/增量 build 期。

        查 code_* 投影表平替 workspaceSymbol(秒级零 JDT 预热)。投影未 build 时
        request_workspace_symbol 抛 ProjectionMissingError("run `contextos init`")
        -> search_code tool 转 ToolError / pipeline §5.1 fail-safe 当 miss(诚实 miss,
        不静默空结果)。
        """
        return ProjectionSearcher(self.engine)

    @cached_property
    def jdt_adapter(self) -> JdtlsAdapter:
        """JDT LS adapter —— **仅 build 期消费**(init 抽样对照 / 手测用)。

        serve 查询路径不触发(查询走 searcher=ProjectionSearcher);首次访问构造并尝试
        start(JDT 冷启 ~196s 只在这一刻付一次,之后缓存复用)。start 失败(JDT 环境 /
        lombok 路径 / 超时)**在此 catch 不传播**:返回 unstarted adapter,消费方用时
        "Adapter not started" 自行降级;unstarted adapter 被缓存(不每次重试 196s),
        环境修好后重启进程重试。
        """
        p = self.profile
        if not p.projects:
            raise ValueError("profile.projects 为空,无法构造 JDT searcher")
        proj = p.projects[0]
        project = ProjectConfigDC(
            name=proj.name,
            path=proj.path,
            language=proj.language,
            build_system=proj.build_system,
            java_settings=proj.java.model_dump() if proj.java is not None else {},
        )
        data_dir = str(Path(p.storage.data_dir).expanduser())
        ws_dir = p.storage.jdtls_workspace_dir or str(
            Path(p.storage.data_dir).expanduser() / "jdtls-workspaces"
        )
        storage = StorageConfigDC(
            data_dir=data_dir,
            jdtls_workspace_dir=str(Path(ws_dir).expanduser()),
        )
        runtime: JdtlsRuntimeConfigDC = JdtlsRuntimeConfigDC.from_profile(p)
        adapter = JdtlsAdapter(project=project, storage=storage, runtime=runtime)
        try:
            adapter.start()   # request_workspace_symbol 要求先 start(JDT 冷启 ~196s)
        except Exception as exc:
            # start 失败(JDT 环境 / lombok 路径 / 超时)不传播 -> 返回 unstarted adapter,
            # build 期消费方(init 抽样对照)用时 "Adapter not started" 自行降级不崩。
            log.warning("JDT adapter start 失败, build 期对照降级: %s", exc)
        return adapter

    @property
    def projection_lockfile(self) -> Path:
        """04b 投影重建跨进程锁文件(MCP tool / CLI / watcher 共用一个锁, spec §8)。"""
        return Path(self.profile.storage.data_dir).expanduser() / "projection.lock"

    @cached_property
    def rag_provider(self) -> RagProvider:
        # spec Appendix C MUST: 写入(record_confirmed_case)与检索(rag_search)走同一
        # resolver。复用 ops.paths.resolved_materialized_dir(同口径 + 同 .expanduser()),
        # 否则 profile 自定义 materialized_dir 含 ~ 时写入展开/检索字面分叉 -> confirmed-cases
        # prefix 失效, strict scope 回退搜全量根污染业务文档。
        from contextos.ops import paths as _ops_paths
        p = self.profile
        materialized_dir = _ops_paths.resolved_materialized_dir(p)
        return RagProvider(materialized_dir, make_reranker(p.rag), p.rag)

    def oracle_router(self) -> "DbRouter | None":
        """返回进程级缓存的 DbRouter(多库查询路由),无 Oracle 配置/全连失败时仍返回
        router 对象(其 querier_for_owner/fan_out 内部各自降级 None/[]); engine 缺失才 None。
        红线 #4 由 DbRouter 内部 connect_from_profile 守(白名单 + prod 关键词硬拒)。
        """
        cached = getattr(self, "_oracle_router", "unset")
        if cached != "unset":
            return cached  # type: ignore[return-value]
        from contextos.lineage.db_router import DbRouter
        router: DbRouter | None
        try:
            router = DbRouter(self.profile, self.engine)
        except Exception as exc:  # noqa: BLE001
            log.warning("oracle_router 降级为 None: %s", exc)
            router = None
        self._oracle_router: DbRouter | None = router
        return router

    def oracle_querier(self) -> OracleClient | None:
        """单连接探针:返回一个**已连接**的只读 OracleClient,或在未配/凭据缺失/连接失败时返 None。

        Block 1b 注:多库查询路由走 oracle_router();本方法保留供测试 / 单连接探针场景。
        lazy + 缓存(连一次复用)。connect_from_profile 在缺 ORACLE_<TNS>_USER/_PASSWORD
        凭据时即 OracleSafetyError(不真连网);__enter__ 才开真连接 —— 两处任一异常都
        吞掉返 None 并 log.warning(离线降级,red line #4 白名单仍由 connect_from_profile
        的 assert_tns_is_test_only 守)。返回的 client 已 __enter__,生命周期挂 AppContext;
        AppContext 进程退出时随之 close(stdio 单进程,无显式 teardown 钩子需求)。
        """
        cached = getattr(self, "_oracle_querier", "unset")
        if cached != "unset":
            return cached  # type: ignore[return-value]
        querier: OracleClient | None
        try:
            client = connect_from_profile(self.profile)
            querier = client.__enter__()
        except Exception as exc:  # 未配 / 凭据缺失 / 连接失败 -> 降级
            log.warning("oracle_querier 降级为 None(离线/未配/连接失败): %s", exc)
            querier = None
        self._oracle_querier: OracleClient | None = querier
        return querier
