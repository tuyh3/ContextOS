#!/usr/bin/env bash
#
# build-runtime-bundle.sh — 组装 ContextOS runtime bundle(JDT LS + JRE + lombok + java-indexer)
#
# 用法:
#   ./scripts/build-runtime-bundle.sh [--platform win-x64|mac-arm64|mac-x64|linux-x64] [--out dist/] [--version <bundle_ver>] [--source-ref <ref>]
#   --platform 省略 = 四平台全打; --version 缺省 = dev(CI 传 release tag); --out 缺省 = <repo>/dist/
#   --source-ref  按 git ref 合装源码树出完整包(只出完整包; 省略 = 只出 runtime-only 包)
#
# 机制:
#   - 读 scripts/runtime-bundle/manifest.json(版本/URL/sha256 唯一 SSOT, 本脚本不硬编任何 sha)
#   - 下载缓存于 .cache/runtime-bundle/(文件名 = URL 尾段), sha256 命中免重下
#   - 逐项 sha256 校验 fail-closed(spec A4): 缓存不匹配 -> 删掉重下; 重下仍不匹配 -> exit 1
#   - 组装 B2 布局 contextos-runtime/{jdtls/, jre/, lombok.jar, java-indexer.jar, licenses/, NOTICE.md}
#     - jdtls: 上游 tar 是 tarbomb(bin/ plugins/ features/ config_* 顶层直出), 全量保留;
#       四平台共用同一份解包(所有平台的 config_* 都留着, 不按平台裁剪)
#     - jre: Temurin 归档结构不一(mac 真 JRE 根在 jdk-*/Contents/Home/ 下, linux/win 在
#       jdk-*/ 直下), 统一归一成 jre/bin/java(win 为 jre/bin/java.exe)直达
#     - java-indexer.jar: vendor/java-indexer/target/ 找现成 jar, 没有则 mvn -q package
#   - win 打 zip(mac 用 ditto 保真, 无 ditto 时 zip -r 兜底), 其余 tar.gz(保执行位)
#   - license gate(Task 3, fail-closed): 组装前跑 scripts/check-indexer-licenses.sh
#     --download-texts(报告生成 + allowlist 校验 + license 全文下载), 任一红 -> 不出包;
#     THIRD-PARTY.txt 与 license 全文拷入 bundle 的 licenses/, NOTICE.md 引用之。
#     内网 mirror 环境需 MVN_SETTINGS 直连 central, 见 check-indexer-licenses.sh 头部。
#   - 完整包模式(--source-ref, spec A9): 结构闸+内容泄漏闸(verify_source_ref)通过后,
#     把闸验通过的 commit(SOURCE_SHA, 非按名重新解析)用 git archive 导出源码树, 与
#     runtime bundle 合装成 contextos-<ver>/{...源码..., runtime/contextos-runtime/}, 再整体
#     打包; 省略 --source-ref 则只出 runtime-only 包(旧行为不变, 不进闸)。
#
# 兼容性: bash 3.2(macOS 默认 /bin/bash 可跑; 不用关联数组等 bash 4 特性)。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$REPO_ROOT/scripts/runtime-bundle/manifest.json"
CACHE_DIR="$REPO_ROOT/.cache/runtime-bundle"
ALL_PLATFORMS="mac-arm64 mac-x64 linux-x64 win-x64"
RUNTIME_NAME="contextos-runtime"   # bundle 顶层目录名与包名前缀, 全脚本唯一出处

# 中途 die 也不留解压残留: glob 同时盖 staging-shared-jdtls 与 staging-<platform>;
# 成功路径已逐个手动清, trap 空转无害。单引号 = 变量在 EXIT 时才展开。
trap 'rm -rf "$CACHE_DIR"/staging-*' EXIT

PLATFORM=""
OUT_DIR="$REPO_ROOT/dist"
BUNDLE_VER="dev"
SOURCE_REF=""
SOURCE_SHA=""

usage() {
  cat <<EOF
用法: $0 [--platform win-x64|mac-arm64|mac-x64|linux-x64] [--out <dir>] [--version <bundle_ver>] [--source-ref <ref>]
  --platform    只打指定平台; 省略 = 四平台全打
  --out         归档输出目录(缺省 <repo>/dist/)
  --version     包名里的版本段(缺省 dev; CI 传 release tag, 如 v0.1.0)
  --source-ref  按 git ref 合装源码树出完整包(只出完整包; 省略 = 只出 runtime-only 包)
EOF
}

log() { echo "[build-runtime-bundle] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    # 取值 flag: 尾随裸 flag(后面没值)必须给话再死, 不许静默 exit(bash 3.2 下
    # "shift 空参数" 会被 set -e 无声杀掉, 之前就是这个坑)
    --platform)   [ -n "${2:-}" ] || { usage >&2; die "--platform 后缺取值"; };   PLATFORM="$2"; shift ;;
    --out)        [ -n "${2:-}" ] || { usage >&2; die "--out 后缺取值"; };        OUT_DIR="$2";  shift ;;
    --version)    [ -n "${2:-}" ] || { usage >&2; die "--version 后缺取值"; };    BUNDLE_VER="$2"; shift ;;
    --source-ref) [ -n "${2:-}" ] || { usage >&2; die "--source-ref 后缺取值"; }; SOURCE_REF="$2"; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) usage >&2; die "未知参数: $1" ;;
  esac
  shift
done

# --platform 校验(fail-fast, 非法值直接拒)
if [ -n "$PLATFORM" ]; then
  case " $ALL_PLATFORMS " in
    *" $PLATFORM "*) BUILD_LIST="$PLATFORM" ;;
    *) die "非法 --platform '$PLATFORM'(可选: $ALL_PLATFORMS)" ;;
  esac
else
  BUILD_LIST="$ALL_PLATFORMS"
fi

# 依赖自检(python3 兼做 json 解析与 sha256, 免去 jq 依赖 -> bash 3.2 环境零额外安装)
for cmd in python3 curl tar unzip; do
  command -v "$cmd" >/dev/null 2>&1 || die "缺少依赖命令: $cmd"
done
[ -f "$MANIFEST" ] || die "manifest 不存在: $MANIFEST"

# 读 manifest 里的点路径 key(如 jre.platforms.mac-arm64.url); key 缺失给人话不吐 traceback
manifest_get() {
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
try:
    for k in sys.argv[2].split("."):
        d = d[k]
except (KeyError, TypeError):
    sys.exit("manifest 缺 key %r, 核对 %s" % (sys.argv[2], sys.argv[1]))
print(d)
' "$MANIFEST" "$1"
}

sha256_of() {
  python3 -c '
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for b in iter(lambda: f.read(1 << 20), b""):
        h.update(b)
print(h.hexdigest())
' "$1"
}

# 下载 + 校验(fail-closed): stdout 只输出缓存文件绝对路径, 日志全走 stderr。
# 缓存 sha 不匹配 -> 删掉重下; 重下后仍不匹配 -> exit 1(绝不带病组包, spec A4)。
fetch_verified() {
  local url="$1" want="$2"
  local fname="${url##*/}"
  local dest="$CACHE_DIR/$fname"
  local got
  if [ -f "$dest" ]; then
    got="$(sha256_of "$dest")"
    if [ "$got" = "$want" ]; then
      log "缓存命中: $fname"
      echo "$dest"
      return 0
    fi
    log "缓存 sha256 不匹配, 删除重下: $fname (actual=$got)"
    rm -f "$dest"
  fi
  log "下载: $url"
  curl -fL --retry 3 -o "$dest.part" "$url" || die "下载失败: $url"
  mv "$dest.part" "$dest"
  got="$(sha256_of "$dest")"
  if [ "$got" != "$want" ]; then
    rm -f "$dest"   # 坏文件留着只会下次误命中
    log "sha256 校验失败(fail-closed): $fname"
    log "  expected: $want"
    log "  actual:   $got"
    die "组件完整性校验不通过, 中止打包。核对 manifest.json 或上游发布物。"
  fi
  echo "$dest"
}

# java-indexer.jar: target/ 找现成(排除 sources/javadoc), 没有则 mvn 构建
# 返回码: 0=唯一命中(stdout 给路径) / 1=没有 / 2=多个候选(静默取第一个会拿错版本, die 级)
resolve_indexer_jar() {
  local j hit=""
  for j in "$REPO_ROOT"/vendor/java-indexer/target/java-indexer*.jar; do
    [ -f "$j" ] || continue
    case "$j" in
      *-sources.jar|*-javadoc.jar) continue ;;
    esac
    if [ -n "$hit" ]; then
      log "ERROR: vendor/java-indexer/target/ 有多个候选 jar(含 $(basename "$hit") 与 $(basename "$j")), 清空 target/ 后重跑"
      return 2
    fi
    hit="$j"
  done
  [ -n "$hit" ] || return 1
  echo "$hit"
}

ensure_indexer_jar() {
  local jar rc
  jar="$(resolve_indexer_jar)" && rc=0 || rc=$?
  [ "$rc" -ne 2 ] || exit 1   # 多 jar: resolve 已把原因打到 stderr(与 jdk-* 多目录 die 同级严格)
  if [ "$rc" -eq 0 ]; then
    log "java-indexer.jar: 用 target/ 现成 $(basename "$jar")"
    echo "$jar"
    return 0
  fi
  log "vendor/java-indexer/target/ 无现成 jar, 尝试 mvn -q package ..."
  command -v mvn >/dev/null 2>&1 || die "未找到 mvn。java-indexer.jar 需要 Maven + JDK 构建:
  - 安装 Maven(https://maven.apache.org)与 JDK 后重跑; 或
  - 预先把构建好的 java-indexer*.jar 放进 vendor/java-indexer/target/"
  # mvn 输出整体转 stderr: 本函数经 $() 捕获 stdout 当返回值, 不能被 mvn 告警污染
  (cd "$REPO_ROOT/vendor/java-indexer" && mvn -q package 1>&2) \
    || die "mvn package 失败。确认本机 JDK 可用(java -version)与 Maven 仓库可达; 或
  预先把构建好的 java-indexer*.jar 放进 vendor/java-indexer/target/"
  jar="$(resolve_indexer_jar)" && rc=0 || rc=$?
  [ "$rc" -ne 2 ] || exit 1
  [ "$rc" -eq 0 ] || die "mvn package 完成但 target/ 仍无 java-indexer*.jar, 检查 vendor/java-indexer/pom.xml"
  log "java-indexer.jar: mvn 构建产出 $(basename "$jar")"
  echo "$jar"
}

# NOTICE.md: 组件名/版本/URL/expected sha/actual sha(actual = 打包时对缓存文件重算, 非抄 manifest)
# + Licenses 节指向 bundle 内 licenses/。纯 ASCII 列表, 禁 box-drawing。
write_notice() {
  local notice="$1" platform="$2" jre_url="$3" jre_sha_want="$4" jre_file="$5"
  cat > "$notice" <<EOF
# ContextOS Runtime Bundle NOTICE

Bundle version: $BUNDLE_VER
Platform: $platform
Built at: $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Components

- JDT Language Server $JDTLS_VER
  URL: $JDTLS_URL
  sha256 expected: $JDTLS_SHA
  sha256 actual:   $(sha256_of "$JDTLS_TGZ")

- Eclipse Temurin JRE $JRE_VER ($platform)
  URL: $jre_url
  sha256 expected: $jre_sha_want
  sha256 actual:   $(sha256_of "$jre_file")

- Project Lombok $LOMBOK_VER
  URL: $LOMBOK_URL
  sha256 expected: $LOMBOK_SHA
  sha256 actual:   $(sha256_of "$LOMBOK_JAR")

- java-indexer (vendored source: vendor/java-indexer/, built locally)
  sha256 actual:   $(sha256_of "$INDEXER_JAR")
  License: MIT (随 ContextOS 仓发布, 全文见 licenses/java-indexer-LICENSE.txt)

## Licenses

- licenses/java-indexer-LICENSE.txt: java-indexer 自身授权全文(MIT, 即 ContextOS 仓
  LICENSE; vendored 源码随仓 MIT 发布, spec A5)。
- licenses/THIRD-PARTY.txt: java-indexer 依赖(direct+transitive)license 清单,
  license-maven-plugin add-third-party 生成, 打包前经 allowlist fail-closed 校验
  (scripts/check-indexer-licenses.sh)。
- licenses/ 其余文件: 上述依赖的 license 全文(download-licenses goal 产物)。
- JDT Language Server / Eclipse Temurin JRE / lombok 为原样再分发, 各自归档内自带
  license 与 notice 文件(如 jre/legal/, jdtls plugins 内 EPL 声明)。
EOF
}

# spec A2: 结构闸 + 内容泄漏闸, fail-closed。
# 全部 git 调用必须钉 -C "$REPO_ROOT": 本函数可能在别的仓目录下被调起(cwd != REPO_ROOT),
# 裸 git 会验 cwd 仓的同名 ref, 而组装点(下方 git archive)钉的是 REPO_ROOT —— 闸门与
# archive 分属两仓, 闸门形同虚设(spec review 实测复现: decoy 仓能让本仓闸门放行本仓的树)。
# TOCTOU 防护: SOURCE_REF 是名字(tag/branch), 名字可在闸后被移动(重打 tag/branch 前进)。
# 本函数把 ref 解出的 commit sha 落进脚本级变量 SOURCE_SHA, 闸内后续所有 git 对象访问
# (ls-tree/leak_scan_tree/merge-base)与下方平台循环里的 git archive 全部改用 SOURCE_SHA
# 而非 SOURCE_REF —— 闸验的树与实际打包的树必须是同一个 commit 对象, 不能各自按名重新解析。
verify_source_ref() {
  SOURCE_SHA="$(git -C "$REPO_ROOT" rev-parse --verify "$SOURCE_REF^{commit}")" || die "--source-ref 不是合法 ref: $SOURCE_REF"
  tree_n() { git -C "$REPO_ROOT" ls-tree -r "$SOURCE_SHA" --name-only | { grep -cE "$1" || true; }; }
  [ "$(tree_n '^docs/')" = "0" ]                        || die "源树闸: ref 含 docs/(像私有分支, 只接受公开快照 ref)"
  [ "$(tree_n '^(CLAUDE|AGENTS)\.md$')" = "0" ]         || die "源树闸: ref 含 CLAUDE.md/AGENTS.md"
  [ "$(tree_n '^scripts/publish-github\.sh$')" = "0" ]  || die "源树闸: ref 含发布脚本"
  [ "$(tree_n '^scripts/leak-patterns\.sh$')" = "0" ]   || die "源树闸: ref 含泄漏模式文件"
  [ "$(tree_n '^README\.md$')" = "1" ]                  || die "源树闸: ref 缺 README.md"
  [ "$(tree_n '^LICENSE$')" = "1" ]                     || die "源树闸: ref 缺 LICENSE"
  [ "$(tree_n '^runtime/')" = "0" ]                     || die "源树闸: ref 树内已含 runtime/(会与运行时合装路径嵌套冲突)"
  unset -f tree_n
  # 内容泄漏闸(spec A2): scanner 在场必扫全树; 不在场(公开检出, 如 CI)则
  # 断言 ref 是公开 origin/main 祖先(= 发布期已过同款闸), 两样都不行 -> die。
  if [ -f "$REPO_ROOT/scripts/leak-patterns.sh" ]; then
    . "$REPO_ROOT/scripts/leak-patterns.sh"
    # leak_scan_tree 内部是裸 git(它也供 publish-github.sh 用, 那边脚本开头已 cd 到仓根,
    # 故不改 leak_scan_tree 本身); 这里用子 shell 包一层 cd, 让它与 archive 同一仓上下文,
    # 且不污染本脚本其余部分的 cwd。
    local n; n="$( (cd "$REPO_ROOT" && leak_scan_tree "$SOURCE_SHA") )"
    [ "$n" = "0" ] || die "内容泄漏闸: ref 树内命中 $n 行敏感内容, 中止(spec A2)"
  else
    git -C "$REPO_ROOT" fetch --quiet origin main || die "无 leak-patterns.sh 且 fetch origin/main 失败, 无法断言 ref 已过发布闸(fail-closed)"
    # merge-base 里的 FETCH_HEAD 属于 -C 那个仓的 .git(刚 fetch 写入的也是它), 语义自动跟对。
    git -C "$REPO_ROOT" merge-base --is-ancestor "$SOURCE_SHA" FETCH_HEAD \
      || die "无 leak-patterns.sh 且 ref 不在公开快照链上, 拒绝组包(fail-closed)"
  fi
  log "源树安全闸通过: $SOURCE_REF ($SOURCE_SHA)"
}
[ -z "$SOURCE_REF" ] || verify_source_ref

# ---- 主流程 ----

mkdir -p "$CACHE_DIR" "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

JDTLS_VER="$(manifest_get jdtls.version)"
JDTLS_URL="$(manifest_get jdtls.url)"
JDTLS_SHA="$(manifest_get jdtls.sha256)"
LOMBOK_VER="$(manifest_get lombok.version)"
LOMBOK_URL="$(manifest_get lombok.url)"
LOMBOK_SHA="$(manifest_get lombok.sha256)"
JRE_VER="$(manifest_get jre.version)"

JDTLS_TGZ="$(fetch_verified "$JDTLS_URL" "$JDTLS_SHA")"
LOMBOK_JAR="$(fetch_verified "$LOMBOK_URL" "$LOMBOK_SHA")"
INDEXER_JAR="$(ensure_indexer_jar)"

# license gate(fail-closed, Task 3): 报告生成 + allowlist 校验 + license 全文下载。
# 任一依赖 license 不可再分发/Unknown/格式漂移 -> check 脚本 exit 1 -> 这里 set -e 直接不出包。
log "license gate: scripts/check-indexer-licenses.sh --download-texts"
"$REPO_ROOT/scripts/check-indexer-licenses.sh" --download-texts
LICENSE_REPORT="$REPO_ROOT/vendor/java-indexer/target/generated-sources/license/THIRD-PARTY.txt"
LICENSE_TEXT_DIR="$REPO_ROOT/vendor/java-indexer/target/generated-resources/licenses"
[ -f "$LICENSE_REPORT" ] || die "license gate 通过但报告不存在: $LICENSE_REPORT"
ls "$LICENSE_TEXT_DIR"/* >/dev/null 2>&1 || die "license 全文目录为空(download-licenses 没产出): $LICENSE_TEXT_DIR"
REPO_LICENSE="$REPO_ROOT/LICENSE"
[ -f "$REPO_LICENSE" ] || die "本仓 LICENSE 不存在: $REPO_LICENSE(java-indexer 随仓 MIT 发布, bundle 必须带其授权全文, spec A5)"

# jdtls 只解一次, 四平台共用(内容全平台一致, config_* 全保留)
SHARED_JDTLS="$CACHE_DIR/staging-shared-jdtls"
rm -rf "$SHARED_JDTLS"
mkdir -p "$SHARED_JDTLS"
log "解包 jdtls(共享一次): $(basename "$JDTLS_TGZ")"
tar -xzf "$JDTLS_TGZ" -C "$SHARED_JDTLS"
[ -d "$SHARED_JDTLS/plugins" ] || die "jdtls 解包异常: 未见 plugins/(tarbomb 结构变了? 核对上游归档)"

for platform in $BUILD_LIST; do
  log "==== 组装平台: $platform ===="
  JRE_URL="$(manifest_get "jre.platforms.$platform.url")"
  JRE_SHA="$(manifest_get "jre.platforms.$platform.sha256")"
  JRE_ARCHIVE="$(manifest_get "jre.platforms.$platform.archive")"
  JRE_FILE="$(fetch_verified "$JRE_URL" "$JRE_SHA")"

  STAGING="$CACHE_DIR/staging-$platform"
  RT="$STAGING/$RUNTIME_NAME"
  rm -rf "$STAGING"
  mkdir -p "$RT"

  # jdtls/: 共享解包整体拷入(-p 保执行位)
  cp -Rp "$SHARED_JDTLS" "$RT/jdtls"

  # jre/: 解压 -> 定位真 JRE 根 -> 平移成 jre/bin/java 直达
  JRE_RAW="$STAGING/jre-raw"
  mkdir -p "$JRE_RAW"
  case "$JRE_ARCHIVE" in
    tar.gz) tar -xzf "$JRE_FILE" -C "$JRE_RAW" ;;
    zip)    unzip -q "$JRE_FILE" -d "$JRE_RAW" ;;
    *)      die "manifest 里未知 archive 类型: $JRE_ARCHIVE(平台 $platform)" ;;
  esac
  JDK_TOP=""
  for d in "$JRE_RAW"/jdk-*; do
    [ -d "$d" ] || continue
    [ -z "$JDK_TOP" ] || die "JRE 归档顶层出现多个 jdk-* 目录, 结构异常: $JRE_RAW"
    JDK_TOP="$d"
  done
  [ -n "$JDK_TOP" ] || die "JRE 归档里没找到顶层 jdk-* 目录(Temurin 结构变了?): $JRE_RAW"
  case "$platform" in
    mac-*) JRE_HOME="$JDK_TOP/Contents/Home" ;;   # mac 真 JRE 根在 Contents/Home 下
    *)     JRE_HOME="$JDK_TOP" ;;                 # linux/win 直下
  esac
  [ -d "$JRE_HOME" ] || die "预期 JRE 根不存在: $JRE_HOME"
  mv "$JRE_HOME" "$RT/jre"

  # 归一自证: java 可执行文件必须直达 jre/bin/
  if [ "$platform" = "win-x64" ]; then
    [ -f "$RT/jre/bin/java.exe" ] || die "组装自证失败: $platform 缺 jre/bin/java.exe"
  else
    [ -x "$RT/jre/bin/java" ] || die "组装自证失败: $platform 缺可执行 jre/bin/java"
  fi

  cp -p "$LOMBOK_JAR" "$RT/lombok.jar"
  cp -p "$INDEXER_JAR" "$RT/java-indexer.jar"

  # licenses/: THIRD-PARTY.txt + 依赖 license 全文(license gate 产物)+ 本仓 MIT 全文
  # (bundle 再分发的 java-indexer.jar 自身授权 = 本仓 MIT, 必须随包带全文, spec A5)
  mkdir -p "$RT/licenses"
  cp -p "$LICENSE_REPORT" "$RT/licenses/THIRD-PARTY.txt"
  cp -p "$LICENSE_TEXT_DIR"/* "$RT/licenses/"
  cp -p "$REPO_LICENSE" "$RT/licenses/java-indexer-LICENSE.txt"

  write_notice "$RT/NOTICE.md" "$platform" "$JRE_URL" "$JRE_SHA" "$JRE_FILE"
  rm -rf "$JRE_RAW"

  # ---- 完整包组装(--source-ref): 源码树 + runtime 合装(spec A9) ----
  if [ -n "$SOURCE_REF" ]; then
    PKG_TOP="contextos-$BUNDLE_VER"
    FULL="$STAGING/$PKG_TOP"
    rm -rf "$FULL"; mkdir -p "$FULL"
    # A1: 只取闸验过的 commit sha 提交树, 绝不取工作区、也不按名重新解析(TOCTOU 防护同上)
    git -C "$REPO_ROOT" archive --format=tar "$SOURCE_SHA" | tar -x -C "$FULL"
    [ -f "$FULL/README.md" ] || die "git archive 结果异常: 缺 README.md"
    mkdir -p "$FULL/runtime"
    mv "$RT" "$FULL/runtime/$RUNTIME_NAME"     # A3: runtime/contextos-runtime/ 布局不变
    PKG_BASE="$PKG_TOP-$platform"
    PACK_DIR="$PKG_TOP"                        # 打包对象 = 顶层目录
  else
    PKG_BASE="$RUNTIME_NAME-$BUNDLE_VER-$platform"
    PACK_DIR="$RUNTIME_NAME"
  fi

  # 打包: win 用 zip(ditto 保真; linux/CI 无 ditto 时 zip -r 兜底), 其余 tar.gz 保执行位
  # 一律先写 .part 再 mv(原子性, 与下载路径对称: 中断不留半截包被误当成品)
  if [ "$platform" = "win-x64" ]; then
    OUT_FILE="$OUT_DIR/$PKG_BASE.zip"
    rm -f "$OUT_FILE" "$OUT_FILE.part"
    if command -v ditto >/dev/null 2>&1; then
      # --norsrc: 不把 mac xattr 编码成 AppleDouble(._*)塞进 zip(上游 tar 0 个, 不加实测混入 523 个垃圾文件)
      (cd "$STAGING" && ditto -c -k --norsrc --keepParent "$PACK_DIR" "$OUT_FILE.part")
    else
      command -v zip >/dev/null 2>&1 || die "win 包需要 ditto 或 zip, 两者都没找到"
      (cd "$STAGING" && zip -qr "$OUT_FILE.part" "$PACK_DIR")
    fi
  else
    OUT_FILE="$OUT_DIR/$PKG_BASE.tar.gz"
    rm -f "$OUT_FILE" "$OUT_FILE.part"
    (cd "$STAGING" && tar -czf "$OUT_FILE.part" "$PACK_DIR")
  fi
  mv "$OUT_FILE.part" "$OUT_FILE"
  log "产出: $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1 | tr -d '[:space:]'))"

  rm -rf "$STAGING"   # 解压产物只活在 .cache 的 staging 里, 打完即清, 绝不进 git
done

rm -rf "$SHARED_JDTLS"
log "全部完成。输出目录: $OUT_DIR"
