<!-- Release 描述模板: gh release create <tag> -F 本文件(先把末段"本版变更"换成实际内容)。
     维护说明: 安装步骤若变, 与 README「快速开始」同步改。 -->
## 这个页面怎么用

按你的操作系统下载 Assets 里**一个**完整包即可 —— 里面是 ContextOS 代码 +
全部 Java 运行时依赖(JDT LS / JRE 21 / lombok / 代码索引器),解压即用:

| 附件 | 适用系统 |
|---|---|
| `contextos-<版本>-win-x64.zip` | Windows(x64) |
| `contextos-<版本>-mac-arm64.tar.gz` | macOS(Apple 芯片,M 系列) |
| `contextos-<版本>-mac-x64.tar.gz` | macOS(Intel 芯片) |
| `contextos-<版本>-linux-x64.tar.gz` | Linux(x64) |

(页面上 GitHub 自动生成的 Source code 是纯代码,给开发者取码用;普通用户
下上面的完整包。)

## 安装三步

细节见仓库 README 的「快速开始」:

1. **解压**:解出 `contextos-<版本>/` 目录,进入该目录
2. **装依赖**:执行 `uv sync`(需先安装 [uv](https://docs.astral.sh/uv/),一条命令)
3. **配置并启动**:拷 `config/profile.example.toml` 为 `config/profile.toml`,
   把 `[[projects]]` 指向你的 Java 项目,然后 `uv run contextos init`。
   Java 运行时**不用配** —— 程序自动使用包内自带的一套(`uv run contextos health`
   可查看当前生效来源)。

## 本版变更

(发布时把本段替换为实际 changelog,一两句即可)
