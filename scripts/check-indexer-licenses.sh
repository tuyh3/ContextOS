#!/usr/bin/env bash
#
# check-indexer-licenses.sh — java-indexer 依赖 license 报告生成 + allowlist fail-closed 校验
#
# 机制:
#   1. 在 vendor/java-indexer 跑 license-maven-plugin:add-third-party(direct+transitive,
#      排除 test/provided scope)生成 target/generated-sources/license/THIRD-PARTY.txt
#   2. 用 scripts/runtime-bundle/check_licenses.py 对 scripts/runtime-bundle/license-allowlist.txt
#      逐行核对: 任一依赖 license Unknown/缺失/不在单/行解析不出/0 记录 -> exit 1(fail-closed)
#   3. --download-texts 时额外跑 download-licenses goal 落 license 全文, 再用
#      check_licenses.py --texts-manifest 对 licenses.xml 逐 dependency 断言"至少一个
#      allowlist 内 license 带真实存在的全文文件"(2026-07-05 新增, 治 download-licenses
#      errorRemedy=warn 吞单个下载失败 + 旧版只查目录非空, 半成品静默出包的缺陷)
#
# 用法:
#   ./scripts/check-indexer-licenses.sh [--download-texts] [--skip-generate]
#     --download-texts  额外跑 download-licenses goal, license 全文落
#                       vendor/java-indexer/target/generated-resources/licenses/(打包接线用);
#                       随后跑 licenses.xml 全文台账校验, 任一依赖缺全文 -> exit 1
#     --skip-generate   跳过 mvn, 直接校验已有 THIRD-PARTY.txt(离线调试用)
#
# 环境变量:
#   MVN_SETTINGS  可选。指向替代 settings.xml, 同时作为 -s(user)与 -gs(global)传给 mvn。
#                 本机全局 settings 配了不可达内网 mirror 时, 用仓内中性 settings 直连 central:
#                   MVN_SETTINGS=scripts/runtime-bundle/maven-settings-central.xml ./scripts/check-indexer-licenses.sh
#                 CI(GitHub Actions)默认 settings 即可, 不需要设。
#
# 兼容性: bash 3.2。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INDEXER_DIR="$REPO_ROOT/vendor/java-indexer"
ALLOWLIST="$REPO_ROOT/scripts/runtime-bundle/license-allowlist.txt"
CHECKER="$REPO_ROOT/scripts/runtime-bundle/check_licenses.py"
REPORT="$INDEXER_DIR/target/generated-sources/license/THIRD-PARTY.txt"
LICENSES_XML="$INDEXER_DIR/target/generated-resources/licenses.xml"
LICENSE_PLUGIN="org.codehaus.mojo:license-maven-plugin:2.4.0"

log() { echo "[check-indexer-licenses] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

DOWNLOAD_TEXTS=0
SKIP_GENERATE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --download-texts) DOWNLOAD_TEXTS=1 ;;
    --skip-generate)  SKIP_GENERATE=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "未知参数: $1(可选: --download-texts --skip-generate)" ;;
  esac
  shift
done

command -v python3 >/dev/null 2>&1 || die "缺少依赖命令: python3"
[ -f "$ALLOWLIST" ] || die "allowlist 不存在: $ALLOWLIST"
[ -f "$CHECKER" ] || die "校验器不存在: $CHECKER"

# MVN_SETTINGS -> mvn 附加参数。拼字符串 + 后续不加引号按词展开是刻意的:
# 不用数组是因为 bash < 4.4 下空数组 "${arr[@]}" 会撞 set -u 报 unbound variable(本脚本要兼容 bash 3.2)。
MVN_EXTRA=""
if [ -n "${MVN_SETTINGS:-}" ]; then
  [ -f "$MVN_SETTINGS" ] || die "MVN_SETTINGS 指向的文件不存在: $MVN_SETTINGS"
  # 转绝对路径(mvn 在 vendor/java-indexer 下跑, 相对路径会失效)
  MVN_SETTINGS_ABS="$(cd "$(dirname "$MVN_SETTINGS")" && pwd)/$(basename "$MVN_SETTINGS")"
  MVN_EXTRA="-s $MVN_SETTINGS_ABS -gs $MVN_SETTINGS_ABS"
  log "使用替代 Maven settings: $MVN_SETTINGS_ABS"
fi

if [ "$SKIP_GENERATE" -eq 0 ]; then
  command -v mvn >/dev/null 2>&1 || die "未找到 mvn。license 报告生成需要 Maven(+可用 JDK); 或先手工产出 THIRD-PARTY.txt 后用 --skip-generate"
  log "生成 THIRD-PARTY.txt: $LICENSE_PLUGIN:add-third-party ..."
  # shellcheck disable=SC2086  # MVN_EXTRA 刻意按词展开
  (cd "$INDEXER_DIR" && mvn -q $MVN_EXTRA "$LICENSE_PLUGIN:add-third-party" \
      -Dlicense.excludedScopes=test,provided) \
    || die "add-third-party 失败。确认网络可达 Maven Central(内网 mirror 环境见头部 MVN_SETTINGS 说明)与 JDK 可用"
  if [ "$DOWNLOAD_TEXTS" -eq 1 ]; then
    log "下载 license 全文: $LICENSE_PLUGIN:download-licenses ..."
    # 刻意不带 -q: download-licenses 的 errorRemedy=warn 会在单个 license 全文下载失败时
    # 只告警不报错(退出码仍是 0), -q 会把这条告警一起吞掉, 三层放行链的第二层就是它
    # (第一层 errorRemedy=warn, 第三层是旧版只查目录非空)。告警必须可见, 人工过目才发现得了。
    # shellcheck disable=SC2086
    (cd "$INDEXER_DIR" && mvn $MVN_EXTRA "$LICENSE_PLUGIN:download-licenses" \
        -Dlicense.excludedScopes=test,provided) \
      || die "download-licenses 失败(打包需要 license 全文, fail-closed 不放行)"
    [ -f "$LICENSES_XML" ] || die "licenses.xml 不存在: $LICENSES_XML(download-licenses 没产出?)"
    log "校验 license 全文台账: $LICENSES_XML"
    python3 "$CHECKER" --texts-manifest "$LICENSES_XML" "$ALLOWLIST" \
      || die "license 全文台账校验 FAIL —— 部分依赖缺全文(常见于本机到 eclipse.org 等上游站点路由问题; 本机遇红多为路由问题, 正解是走 CI 出包, 不是本机硬重试)"
  fi
fi

[ -f "$REPORT" ] || die "报告不存在: $REPORT(add-third-party 没产出? 或 --skip-generate 但从没生成过)"

python3 "$CHECKER" "$REPORT" "$ALLOWLIST"
