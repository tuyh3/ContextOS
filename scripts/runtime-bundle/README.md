# runtime-bundle manifest

`manifest.json` 是 runtime bundle 三个上游依赖(JDT LS / Temurin 21 JRE / lombok)的唯一 SSOT:
版本号、固定下载 URL(版本化, 非 latest 别名)、sha256 全在这里, 打包脚本只读它, 不设脚本头版本变量。

升级版本时: 解析新版固定 URL -> 真下载 -> `shasum -a 256` 实测 -> 改 manifest 对应字段, 别的什么都不用改。
sha256 绝不手编或抄记忆(fail-closed: 哈希不匹配打包即失败)。
JRE 必须是 21(JDT LS 硬要求); windows 是 zip, mac/linux 是 tar.gz, archive 字段照实填。

消费方: 打包脚本(Task 2)与 CI release workflow 按 manifest 下载并校验后组装三平台四包。

本目录其余文件(Task 3, license gate):
- `license-allowlist.txt`: java-indexer 依赖可再分发 license 词形放行单(修改纪律见文件头)。
- `check_licenses.py`: THIRD-PARTY.txt 对 allowlist 的 fail-closed 校验器
  (单测 contextos/tests/test_license_allowlist_check.py; bash 入口 scripts/check-indexer-licenses.sh)。
- `maven-settings-central.xml`: 中性 Maven settings, 本机全局 settings 配了不可达内网 mirror 时
  经 MVN_SETTINGS 环境变量选用, 直连 Maven Central。
