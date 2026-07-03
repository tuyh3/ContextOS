# java-indexer (vendored)

来源: 上游内部工具, 已 vendored 进本仓(随本仓 MIT 发布)。JDT Core 3.20.0 批量解析 + binding 解析,
产 7 个 JSONL: files/classes/methods/fields/calls/inheritance/references。

ContextOS 修改: Main/JdtIndexer 加 `--files <list.txt>` 子集解析(增量用;
environment 仍给全 source roots + classpath, binding 跨文件照常解)。

定性(构建契约 §2 + spec §3.1): 本工具是 JDT LS workspace 的**同源快照投影引擎**,
受"受控派生引擎四条件"约束 —— 不是第二套持久 binding 索引。binding 状态跑完即弃。

构建: cd vendor/java-indexer && mvn -q package -DskipTests
产物: target/java-indexer-1.0.0.jar (15MB, gitignored; 缺失时 contextos init 会给重建指引)
运行: java -Xmx4g -jar target/java-indexer-1.0.0.jar <build_context.json> <out_dir> [--files <list.txt>]
JDK: Java 8+ (复用 profile jdtls_runtime.java_home 的 JRE 21 即可)
