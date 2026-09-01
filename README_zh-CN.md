# NGINX Markdown for Agents

[![Latest Release](https://img.shields.io/github/v/release/cnkang/nginx-markdown-for-agents?sort=semver)](https://github.com/cnkang/nginx-markdown-for-agents/releases) [![NGINX](https://img.shields.io/badge/NGINX-%3E%3D1.24.0-009639?logo=nginx&logoColor=white)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/docs/guides/INSTALLATION.md) [![CI](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml) [![Security Scanning](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml) [![License](https://img.shields.io/github/license/cnkang/nginx-markdown-for-agents)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/LICENSE) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=cnkang_nginx-markdown-for-agents&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=cnkang_nginx-markdown-for-agents)

[English](README.md) | 简体中文

> HTML 进，Markdown 出。
> 客户端请求时返回，或者由你决定何时提供。

> 当前版本线：v0.9.2 是开发候选版本，尚未发布。
> 这是 v1.0 前最后一次破坏性版本。v0.9.2 发布前，请使用已发布的标签安装。

NGINX Markdown for Agents 为现有 HTML 页面增加适合机器消费的 Markdown
表示。发送 `Accept: text/markdown` 的客户端会收到 Markdown。浏览器和其他
客户端仍然收到原始 HTML。

模块在 NGINX 边缘层完成转换。不需要改造应用、维护第二套内容 API 或运行
额外的抓取服务。你也可以通过 User-Agent 匹配只为指定 bot 开启转换。

## 功能概览

| 请求 | 结果 |
|------|------|
| `Accept: text/markdown` | 返回带有 `Content-Type: text/markdown` 的 Markdown |
| `Accept: text/html` | 返回原始 HTML |
| 匹配的 User-Agent 与 `markdown_accept force` | 返回 Markdown，且不修改发往上游的 `Accept` 请求头 |

模块会在 Agent 消费页面前移除面向浏览器的噪声。这可以减少 token 使用量，
也让页面结构更容易理解。同一个 URL 仍然可以为浏览器提供 HTML。

## 快速上手

### 1. 安装模块

请参阅[安装指南](docs/guides/INSTALLATION.md#2-shortest-success-path)，
其中包含签名 release 安装器和各平台安装包的使用方法。该指南也覆盖
Docker、源码构建、Homebrew 和安装故障排查。

macOS 用户可以使用项目 Homebrew tap 安装基于 release tag 的软件包：

```bash
brew tap cnkang/nginx-markdown
brew install cnkang/nginx-markdown/nginx-markdown-module
```

### 2. 在一个 location 上启用 Markdown

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    upstream backend {
        server 127.0.0.1:8080;
    }

    server {
        listen 80;

        location / {
            markdown_filter on;
            markdown_streaming auto;
            markdown_auto_decompress on;
            proxy_pass http://backend;
        }
    }
}
```

### 3. 验证两种表示

```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/
```

第一个请求应返回 `Content-Type: text/markdown`。第二个请求应保留上游的
HTML 响应。如果结果不符合预期，请查看[安装故障排查指南](docs/guides/INSTALLATION.md#10-troubleshooting)。

## 0.9.2 配置要点

0.9.2 将公共配置冻结为 25 条有效指令。请显式配置行为，使 `nginx -T`
能展示运维人员选择的设置。

```nginx
http {
    markdown_cache_validation ims_only;
    markdown_streaming auto;
    markdown_limits conversion_memory=64m conversion_timeout=30s
        parser_timeout=10s max_inflight=64;

    server {
        listen 80;

        location /docs/ {
            markdown_filter on;
            markdown_accept strict;
            proxy_pass http://backend;
        }
    }
}
```

- `markdown_streaming off` 选择全缓冲转换。`auto` 使用有界的响应形态判断。
  `force` 会在缓存和准入检查通过后请求流式转换。
- `markdown_limits` 限制转换内存、处理时间、解压、流式缓冲区和并发工作量。
- `markdown_accept strict` 适合分阶段上线。只有在明确需要时才使用
  `wildcard` 或 `force`。
- `markdown_error_policy` 决定转换失败时透传原响应，或返回指定状态码。

完整指令表见[配置参考](docs/guides/CONFIGURATION.md)。修改现有 0.9.1
配置前，请先阅读[0.9.2 迁移指南](docs/guides/MIGRATION-0.9.2.md)。

## 针对指定 bot

许多 AI 爬虫发送浏览器风格的 `Accept` 请求头。可以用 NGINX 的 `map`
匹配已知 User-Agent，再为该 location 设置 `markdown_accept force`。
模块不会改写发往上游的 `Accept` 请求头。

```nginx
http {
    map $http_user_agent $markdown_for_bot {
        default       off;
        "~*ClaudeBot" on;
        "~*GPTBot"    on;
        "~*Googlebot" on;
    }

    server {
        listen 80;

        location /docs/ {
            markdown_filter $markdown_for_bot;
            markdown_accept force;
            proxy_pass http://backend;
        }
    }
}
```

```bash
curl -sS -D - -o /dev/null \
    -A "ClaudeBot/1.0" -H "Accept: text/html" http://localhost/docs/
```

[Bot 定向示例](examples/nginx-configs/06-bot-targeted-conversion.conf)包含
更完整的 User-Agent map。状态码、Content-Type、大小和其他准入检查仍然生效。

## 0.9.2 的变化

0.9.2 是破坏性发布候选版本。升级前请阅读[发布说明](docs/releases/0.9.2-release-notes.md)。

- 公共配置从 63 条指令减少到 25 条。profile、OTel、按路径指标、
  shadow mode 和其他已移除的旧指令不再接受。迁移后运行 `nginx -t`。
- 动态配置现在只接受 JSON schema v1 和五个运行时键。重载失败时，
  active 与 last-known-good 快照保持不变。恢复文件时请原子替换文件。
- Diagnostics 使用只读 JSON schema v2，并且只接受 `GET` 和 `HEAD`。
  内置访问边界仅允许 loopback。Prometheus 指标使用冻结的 v1 合同。
- 内部 C/Rust FFI ABI 升级到 v2。请同时重新构建模块和转换器。
  FFI 只供内部使用，不保证跨版本兼容。

[升级指南](docs/guides/UPGRADE-TO-0.9.2.md)介绍二进制替换、配置迁移、服务
重启和升级后检查。需要降级时请使用[回滚指南](docs/guides/VERSION_ROLLBACK-0.9.2.md)。

## 核心能力

| 能力 | 说明 |
|------|------|
| 内容协商 | 按请求或按指定 bot 返回 Markdown |
| HTML 透传 | 浏览器和不符合条件的响应保持不变 |
| 压缩处理 | 处理上游 gzip、deflate 和 Brotli 响应 |
| 有界转换 | 支持全缓冲转换和有界流式转换 |
| 缓存感知响应 | 为不同表示支持 ETag 和条件请求 |
| 输出控制 | 清洗链接、删除噪声并添加可选元数据 |
| 失败策略与可观测性 | 配置错误策略、Diagnostics 和 Prometheus 指标 |

## 平台支持

下面的矩阵由 release policy 源文件生成，列出经过测试的 NGINX 版本、
平台、制品和支持级别。安装细节请参阅[软件包兼容性指南](docs/guides/PACKAGE_COMPATIBILITY.md)。

<!-- BEGIN:release-matrix:support-matrix -->

| NGINX | Channel | OS | libc | Arch | Artifact | Tier | Blocking |
|-------|---------|-----|------|------|----------|------|----------|
| 1.31.4 | mainline | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | arm64 | docker-image | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | amd64 | docker-image | supported | Yes |
| 1.31.4 | mainline | alpine3.24 | musl | arm64 | docker-image | supported | Yes |
| 1.31.4 | mainline | alpine3.24 | musl | amd64 | docker-image | supported | Yes |
| 1.31.4 | mainline | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.31.4 | mainline | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.30.4 | stable | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.30.4 | stable | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.30.4 | stable | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.30.4 | stable | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.28.3 | legacy | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.28.3 | legacy | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.28.3 | legacy | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.28.3 | legacy | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.26.3 | legacy | macos | darwin | arm64 | homebrew-formula | experimental | No |
| 1.26.3 | legacy | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | arm64 | docker-image | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | amd64 | docker-image | supported | Yes |
| 1.26.3 | legacy | any | n/a | any | source | best-effort | No |
| 1.26.3 | legacy | alpine3.20 | musl | arm64 | docker-image | supported | Yes |
| 1.26.3 | legacy | alpine3.20 | musl | amd64 | docker-image | supported | Yes |
| 1.26.3 | legacy | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.26.3 | legacy | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.24.0 | legacy | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.24.0 | legacy | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.24.0 | legacy | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.24.0 | legacy | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
<!-- END:release-matrix:support-matrix -->

## 文档导航

| 需求 | 规范文档 |
|------|----------|
| 安装或构建 | [安装指南](docs/guides/INSTALLATION.md)、[构建说明](docs/guides/BUILD_INSTRUCTIONS.md) |
| 配置指令 | [配置参考](docs/guides/CONFIGURATION.md) |
| 部署与运维 | [部署示例](docs/guides/DEPLOYMENT_EXAMPLES.md)、[运维指南](docs/guides/OPERATIONS.md) |
| 升级或回滚 0.9.2 | [迁移](docs/guides/MIGRATION-0.9.2.md)、[升级](docs/guides/UPGRADE-TO-0.9.2.md)、[回滚](docs/guides/VERSION_ROLLBACK-0.9.2.md) |
| 了解功能 | [功能索引](docs/features/README.md)、[解压缩](docs/features/DECOMPRESSION.md)、[流式转换](docs/features/STREAMING_COMPATIBILITY.md) |
| 了解架构 | [架构索引](docs/architecture/README.md)、[系统架构](docs/architecture/SYSTEM_ARCHITECTURE.md) |
| 验证或贡献 | [测试索引](docs/testing/README.md)、[Harness 索引](docs/harness/README.md) |

## 开发与验证

针对变更运行最小的相关检查：

```bash
make test
make test-rust
make test-nginx-unit
make test-e2e-rust
```

涉及文档和仓库 contract 的变更还需要运行：

```bash
make docs-check
make harness-check
```

运行时集成测试和 native E2E 检查需要真实的 NGINX 二进制文件。
如果 NGINX 不在 `PATH` 中，请设置 `NGINX_BIN=/absolute/path/to/nginx`。
完整测试矩阵见[测试文档](docs/testing/README.md)。

从源码构建需要 Rust 1.97.1（MSRV 1.97，由 `rust-toolchain.toml` 固定）。

## 较早版本

0.9.1 是 0.9.2 的直接兼容性基线。升级时请使用
[0.9.2 迁移指南](docs/guides/MIGRATION-0.9.2.md)。
0.9.0 及更早版本请查看 [CHANGELOG](CHANGELOG.md) 和对应的版本迁移指南。
本 README 不重复维护历史版本日志。

## 路线图

- 保持冻结的 Prometheus 与 Diagnostics 合同兼容外部监控系统。
- 扩展官方 APT 与 YUM 分发渠道。
- 扩展 `nginx-markdown-doctor` 和运行时监控指南。

## 许可证

BSD 2-Clause "Simplified" License。详见 [LICENSE](LICENSE)。

## 文档更新

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.9.2 | 2026-09-01 | 围绕 0.9.2 合同重组入口文档，并链接规范指南。 |

更早的 README 修改记录请查看 Git 历史。
