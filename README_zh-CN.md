# NGINX Markdown for Agents

[![Latest Release](https://img.shields.io/github/v/release/cnkang/nginx-markdown-for-agents?sort=semver)](https://github.com/cnkang/nginx-markdown-for-agents/releases) [![NGINX](https://img.shields.io/badge/NGINX-%3E%3D1.24.0-009639?logo=nginx&logoColor=white)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/docs/guides/INSTALLATION.md) [![CI](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml) [![Security Scanning](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml) [![License](https://img.shields.io/github/license/cnkang/nginx-markdown-for-agents)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/LICENSE) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=cnkang_nginx-markdown-for-agents&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=cnkang_nginx-markdown-for-agents)

[English](README.md) | 简体中文

让 NGINX 为你已经在提供的 HTML 页面增加一份更适合机器消费的 Markdown 变体。

> HTML 保持原样，Markdown 按需返回——客户端主动请求，或者你指定哪些 bot 自动获得。

客户端发送 `Accept: text/markdown` 时得到 Markdown；浏览器 and 普通调用方仍然拿到原始 HTML。你也可以通过 NGINX 配置针对特定的 AI 爬虫（如 ClaudeBot、GPTBot）按 User-Agent 自动改写 Accept 头，让这些 bot 即使没有主动请求 Markdown 也能收到转换后的内容。你不需要改造业务应用，不需要额外维护一套抓取器，也不需要单独部署一个转换服务。

这是一种很务实的接入方式：在不动现有站点内容生产流程的前提下，把 Agent 友好能力放到团队已经熟悉的 NGINX 层里完成。

> 灵感来自 Cloudflare 的 [Markdown for Agents](https://blog.cloudflare.com/markdown-for-agents/)。本项目把同样的思路带到你自己可控的 NGINX 部署中，在更靠近源站的反向代理层完成转换。

## 这个项目解决什么问题

AI Agent 和 LLM 工具抓网页时，经常面对的是为浏览器而不是为机器设计的 HTML：

- 导航、布局、脚本等噪音会额外消耗 token。
- 真正有价值的正文内容和大量标记混在一起。
- 每个客户端都要自己维护一套 HTML 抽取或清洗逻辑。

与传统搜索爬虫为关键词排序建立索引不同，AI 爬虫的目标是提取知识用于生成答案。它们对 token 成本和语义清晰度更敏感——一个典型 HTML 页面的 token 数量可以是其 Markdown 等价内容的 3 倍甚至更多，而多出来的 token 大部分不携带有用信息。对于大规模运行的 AI 系统，这个成本差异会显著累积。

这个模块把转换工作前移到 Web 层。NGINX 根据内容协商决定是否返回 Markdown，只在客户端明确请求 `text/markdown` 时进行转换。你也可以通过配置让 NGINX 针对特定 User-Agent 的 AI 爬虫自动注入 `text/markdown`，这样即使爬虫本身不会发送该头部，也能拿到干净、省 token 的 Markdown 内容。很多站点——技术文档、博客、开发者 Wiki——本身就是用 Markdown 编写内容再渲染成 HTML 发布的。对这类站点来说，这个转换实际上是在恢复内容的原始编写格式。

这遵循的是 HTTP 协议一直以来就有的内容协商模型：同一个 URL 根据客户端的请求为不同消费方提供不同的表示。

```text
浏览器       -> Accept: text/html      -> HTML（保持不变）
AI Agent     -> Accept: text/markdown  -> Markdown
AI 爬虫（按 User-Agent 匹配）          -> Markdown（通过 NGINX 配置）
```

## 为什么值得尝试

- 复用现有页面和上游服务，不必再造一条平行的内容 API。
- 可以渐进式上线，先对一个路径、一个站点或一个 location 启用。
- 基于标准 HTTP 内容协商，缓存与回源行为仍然容易理解 and 运维。
- 仍然是 NGINX 模块的部署模型，不需要额外引入一个新的常驻服务。
- 在最靠近应用的反向代理层做转换，对 HTML 来源和转换配置有完整的控制权。
- 为 AI 消费方提供更干净、更省 token 的内容表示，减少生成式回答引用你的站点时出现误解或信息丢失的风险。

## 快速上手

第一次试用只要三步：

1. 安装模块。
2. 在一个 location 上启用。
3. 验证 Markdown 与 HTML 两种返回都符合预期。

### 1. 安装模块

```bash
curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
sudo nginx -t && sudo nginx -s reload
```

安装脚本会识别本机 NGINX 版本，下载匹配 of 模块制品，并自动接入 `load_module` 与 `markdown_filter on`，无需手动编辑配置。默认会强制进行 SHA-256 制品完整性校验。

其他安装方式（源码构建、Docker、自定义 NGINX 构建）、故障排查和详细说明见 [安装指南](docs/guides/INSTALLATION.md)。

如果你在 macOS 上通过项目 Homebrew tap 安装（基于 release tag 制品）：

```bash
brew tap cnkang/nginx-markdown
brew install cnkang/nginx-markdown/nginx-markdown-module
```

tap 发布与 GitHub macOS 发布后校验流程见 [docs/guides/HOMEBREW_TAP_RELEASE.md](docs/guides/HOMEBREW_TAP_RELEASE.md)。

### 2. 在一个路由上开启 Markdown

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
            proxy_set_header Accept-Encoding "";
            proxy_pass http://backend;
        }
    }
}
```

如果你的上游可能返回压缩响应，`proxy_set_header Accept-Encoding "";` 是最容易验证的起步方式。等基础链路跑通后，再切换到模块内置的压缩响应处理能力，详见 [Automatic Decompression](docs/features/AUTOMATIC_DECOMPRESSION.md)。

### 3. 验证行为

```bash
# Markdown 变体
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/

# HTML 保持原样
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/
```

预期结果：

- `Accept: text/markdown` 返回 `Content-Type: text/markdown; charset=utf-8`
- `Accept: text/html` 仍返回原始 HTML

如果行为不符合预期，请查看安装指南里的 [Troubleshooting](docs/guides/INSTALLATION.md#10-troubleshooting) 小节。

如果你想直接查看面向生产环境的完整配置示例，参见 [生产示例](examples/production/) 目录（覆盖 balanced、strict_cache、streaming_first 三种 profile）。

## Profiles

生产部署推荐使用 `markdown_profile` 指令，一行配置即可应用一组经过测试的默认值，无需逐一设置每个指令：

```nginx
http {
    markdown_profile balanced;

    server {
        listen 80;
        location /docs/ {
            markdown_filter on;
            proxy_pass http://backend;
        }
    }
}
```

三个可用 profile：

| Profile | 适用场景 |
|---------|----------|
| `balanced` | 通用部署（推荐起步选择） |
| `strict_cache` | CDN / 缓存代理，需要完整 ETag 支持 |
| `streaming_first` | AI Agent 工作负载，面向大文档 |

合并优先级：显式指令 > profile 默认值 > 内置默认值。你可以在同一 context 中用显式指令覆盖 profile 的任何非强制字段。

完整的 profile 参考、默认值表和冲突规则见 [docs/guides/CONFIGURATION.md](docs/guides/CONFIGURATION.md#profiles)。

## 针对特定 Bot 返回 Markdown

大多数 AI 爬虫不会发送 `Accept: text/markdown`，它们使用和浏览器类似的 Accept 头。你可以用 NGINX 的 `map` 指令根据 User-Agent 改写 Accept 头，让匹配的 bot 自动收到 Markdown，而不需要 bot 自身做任何改变。

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    # 为已知 AI bot 改写 Accept 头
    map $http_user_agent $bot_accept_override {
        default         "";
        "~*ClaudeBot"   "text/markdown, text/html;q=0.9";
        "~*GPTBot"      "text/markdown, text/html;q=0.9";
        "~*Googlebot"   "text/markdown, text/html;q=0.9";
    }

    map $bot_accept_override $final_accept {
        ""      $http_accept;
        default $bot_accept_override;
    }

    upstream backend {
        server 127.0.0.1:8080;
    }

    server {
        listen 80;

        location /docs/ {
            markdown_filter on;
            proxy_set_header Accept $final_accept;
            proxy_pass http://backend;
        }
    }
}
```

```bash
# 模拟 ClaudeBot — 返回 Markdown
curl -sD - -o /dev/null -A "ClaudeBot/1.0" http://localhost/docs/
# 预期: Content-Type: text/markdown; charset=utf-8

# 普通浏览器请求 — 仍然返回 HTML
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/docs/
```

原理是模块的内容协商逻辑看到改写后的 Accept 头中包含 `text/markdown`，就会对符合条件的 `text/html` 响应进行转换。所有其他准入检查（状态码、Content-Type、大小限制等）仍然正常生效。浏览器和不匹配的客户端完全不受影响。

完整的配置模板（包含更多 bot 模式）见 [examples/nginx-configs/06-bot-targeted-conversion.conf](examples/nginx-configs/06-bot-targeted-conversion.conf)。详细说明见 [docs/guides/DEPLOYMENT_EXAMPLES.md](docs/guides/DEPLOYMENT_EXAMPLES.md#bot-targeted-conversion-user-agent-based)。

## 核心功能与能力

| 功能特性 | 说明 |
|------------|--------------|
| **内容协商** | 客户端请求 `text/markdown` 时触发转换，也支持按 User-Agent 针对特定 bot 自动转换。 |
| **HTML 透传** | 浏览器和普通客户端流量完全保持原样，无任何行为影响。 |
| **自动解压缩** | 自动处理 gzip、brotli、deflate 上游压缩响应，免除手动编写解压管道的繁琐。 |
| **缓存友好变体** | 自动生成对应 Markdown 变体的 ETags 并完美支持标准的 HTTP 条件请求。 |
| **失败策略可控** | 支持配置失败透传或失败拦截（Fail-open / Fail-closed），完美融入生产 SLA。 |
| **资源限制** | 通过 `markdown_limits` 限制单次转换的最大大小、处理超时、流式缓冲区和并发连接上限。 |
| **安全加固** | 强制校验输出链接、默认拒绝不安全的 forwarded-host，限制解析/解压资源，防范 DDOS。 |
| **可选元数据** | 支持自动估算并插入 Markdown Token 数及干净的 YAML front matter。 |
| **指标监控端点** | 暴露 Prometheus 兼容的转换计数与运行时指标，助力集群可观测性建设。 |
| **双引擎模式** | 典型大小响应走全缓冲（默认），大响应/分块响应支持自动或强制路由到流式引擎。 |
| **有界内存流式** | 流式引擎在有界内存中增量转换并根据 `markdown_stream_flush_min` 阈值按大小即时 flush。 |

## 平台支持

<!-- BEGIN:release-matrix:support-matrix -->

| NGINX | Channel | OS | libc | Arch | Artifact | Tier | Blocking |
|-------|---------|-----|------|------|----------|------|----------|
| 1.31.2 | mainline | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.31.2 | mainline | linux | musl | arm64 | dynamic-module | supported | No |
| 1.31.2 | mainline | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.31.2 | mainline | linux | musl | amd64 | dynamic-module | supported | No |
| 1.31.2 | mainline | debian12 | glibc | arm64 | docker-image | supported | Yes |
| 1.31.2 | mainline | debian12 | glibc | amd64 | docker-image | supported | Yes |
| 1.31.2 | mainline | alpine3.20 | musl | arm64 | docker-image | supported | Yes |
| 1.31.2 | mainline | alpine3.20 | musl | amd64 | docker-image | supported | Yes |
| 1.30.3 | stable | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.30.3 | stable | linux | musl | arm64 | dynamic-module | supported | No |
| 1.30.3 | stable | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.30.3 | stable | linux | musl | amd64 | dynamic-module | supported | No |
| 1.28.3 | stable | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.28.3 | stable | linux | musl | arm64 | dynamic-module | supported | No |
| 1.28.3 | stable | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.28.3 | stable | linux | musl | amd64 | dynamic-module | supported | No |
| 1.26.3 | stable | macos | darwin | arm64 | homebrew-formula | experimental | No |
| 1.26.3 | stable | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.26.3 | stable | linux | musl | arm64 | dynamic-module | supported | No |
| 1.26.3 | stable | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.26.3 | stable | linux | musl | amd64 | dynamic-module | supported | No |
| 1.26.3 | stable | debian12 | glibc | arm64 | docker-image | supported | Yes |
| 1.26.3 | stable | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.26.3 | stable | debian12 | glibc | amd64 | docker-image | supported | Yes |
| 1.26.3 | stable | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.26.3 | stable | any | n/a | any | source | best-effort | No |
| 1.26.3 | stable | alpine3.20 | musl | arm64 | docker-image | supported | Yes |
| 1.26.3 | stable | alpine3.20 | musl | amd64 | docker-image | supported | Yes |
| 1.26.3 | stable | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.26.3 | stable | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.24.0 | oldstable | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.24.0 | oldstable | linux | musl | arm64 | dynamic-module | supported | No |
| 1.24.0 | oldstable | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.24.0 | oldstable | linux | musl | amd64 | dynamic-module | supported | No |

<!-- END:release-matrix:support-matrix -->

## 工作原理

```mermaid
flowchart TD
    client["Agent 或工具<br/>Accept: text/markdown"]
    bot["AI 爬虫（如 ClaudeBot）<br/>NGINX 按 User-Agent 匹配"]

    subgraph edge["NGINX 请求路径"]
        ingress["请求进入 NGINX"]
        rewrite["Accept 头改写<br/>（针对匹配 the User-Agent）"]
        filter["Markdown 过滤模块 (C)<br/>准入判断<br/>响应缓冲<br/>头部策略"]
        passthrough["普通 HTML 响应<br/>给浏览器和普通客户端"]
    end

    subgraph engine["转换引擎"]
        rust["Rust 转换器<br/>HTML 解析<br/>安全清洗<br/>Markdown 生成"]
    end

    markdown["Markdown 响应<br/>Content-Type: text/markdown"]

    client --> ingress
    bot --> ingress
    ingress --> rewrite
    rewrite --> filter
    filter -->|符合 Markdown 转换条件| rust
    rust --> markdown
    ingress -.->|Accept: text/html 或请求不符合条件| passthrough

    classDef client fill:#eef6ff,stroke:#1d4ed8,color:#0f172a,stroke-width:2px;
    classDef bot fill:#eef6ff,stroke:#7c3aed,color:#0f172a,stroke-width:2px;
    classDef nginx fill:#f7fee7,stroke:#65a30d,color:#1f2937,stroke-width:2px;
    classDef module fill:#fff7ed,stroke:#ea580c,color:#1f2937,stroke-width:2px;
    classDef engine fill:#fef2f2,stroke:#dc2626,color:#1f2937,stroke-width:2px;
    classDef output fill:#ecfeff,stroke:#0891b2,color:#0f172a,stroke-width:2px;
    classDef passthrough fill:#f8fafc,stroke:#94a3b8,color:#334155,stroke-dasharray: 5 3;

    class client client;
    class bot bot;
    class ingress nginx;
    class rewrite nginx;
    class filter module;
    class rust engine;
    class markdown output;
    class passthrough passthrough;
```

NGINX 模块负责请求是否可转换、响应缓冲和头部管理。对于按 bot 定向转换的场景，NGINX 的 `map` 指令在模块处理请求之前改写 Accept 头，模块的标准内容协商逻辑照常工作。Rust 转换器负责 HTML 解析、安全清洗、确定性 Markdown 生成等核心逻辑。

### 为什么是 C + Rust

这个拆分是沿着真实的问题边界做的。

- C 负责直接接入 NGINX 模块 API、过滤链、缓冲区和请求生命周期。
- Rust 负责解析不可信 HTML、做内容清洗和生成可预测的 Markdown 输出。
- FFI 边界保持得很小，这样 NGINX 侧的 HTTP 逻辑和转换逻辑可以相对独立演进。

如果你想看完整的设计理由，而不只是这里的简版说明，可以继续读 [docs/architecture/SYSTEM_ARCHITECTURE.md](docs/architecture/SYSTEM_ARCHITECTURE.md)、[docs/architecture/ADR/0001-use-rust-for-conversion.md](docs/architecture/ADR/0001-use-rust-for-conversion.md) 与 [docs/architecture/ADR/0009-rust-first-e2e-test-architecture.md](docs/architecture/ADR/0009-rust-first-e2e-test-architecture.md)。

## 本地开发与测试

```bash
# 快速构建 + 冒烟测试
make test

# 完整 Rust 测试
make test-rust

# 完整 NGINX 模块单元测试
make test-nginx-unit

# 流式专项测试
make test-rust-streaming
make verify-chunked-native-e2e-smoke

# 运行时集成与 canonical E2E 检查
make test-nginx-integration
make test-e2e-rust
make test-e2e
make test-rust-fuzz-smoke
```

`make test-nginx-integration`、`make test-e2e` 和 `make verify-chunked-native-e2e-smoke` require a real `nginx` runtime. If `nginx` is not on `PATH`, set `NGINX_BIN=/absolute/path/to/nginx` so that these commands can find the nginx binary.

更完整的集成测试、E2E 与性能基线说明见 [docs/testing/README.md](docs/testing/README.md) 与 [docs/testing/E2E_TESTS.md](docs/testing/E2E_TESTS.md)。

如果你修改的是仓库 contract、文档校验器或 agent 工作流规则，还需要运行 harness 检查：

```bash
# 仓库级 harness 真相面的廉价阻断检查
make harness-check

# 包含文档与 release-gate 的完整 harness 校验
make harness-check-full
```

把 harness 检查当作仓库 contract 和 release-gate 变更的主入口：

```bash
# workflow、shell、secret、Semgrep 与 Rust 依赖策略的静态安全检查
make security-static

# 面向发布辅助的供应链可见性检查
make supply-chain
```

## 文档导航

### 快速起步与安装
- [安装指南](docs/guides/INSTALLATION.md) — 预构建包、手动配置、Homebrew tap (`brew install cnkang/nginx-markdown/nginx-markdown-module`)。
- [源码构建](docs/guides/BUILD_INSTRUCTIONS.md) — 完整的源码编译说明。
- [配置指令参考](docs/guides/CONFIGURATION.md) — 详细的指令语法和运行时行为。
- [部署示例](docs/guides/DEPLOYMENT_EXAMPLES.md) — 面向生产环境的 NGINX 配置模版。

### 生产部署与运维
- [流式上线指引](docs/guides/streaming-rollout-cookbook.md) — 渐进式引入、安全开启有界流式转换的运维指南。
- [运维排障指南](docs/guides/OPERATIONS.md) — 状态监控、日志调整和运行时故障诊断。
- [迁移升级指引](docs/guides/MIGRATION-0.9.md) — 升级指南 ([0.8.x → 0.9.x 迁移升级](docs/guides/MIGRATION-0.9.md) / [0.7.x → 0.8.x 迁移升级](docs/guides/MIGRATION-0.8.md))。
- [动态热重载](docs/guides/DYNAMIC_CONFIG.md) — 热更新动态变量及 live config 配置。

### 系统架构与自动化 Harness
- [系统架构](docs/architecture/README.md) — 双引擎模型、C + Rust 边界隔离设计。
- [配置映射图](docs/architecture/CONFIG_BEHAVIOR_MAP.md) — 解析指令参数如何映射到底层执行逻辑。
- [Harness 设计原理](docs/harness/README.md) — 为什么我们把自动化门禁校验作为仓库的一等资产。
- [Harness 维护手册](docs/guides/HARNESS_MAINTENANCE.md) — 自定义代码审查规则和校验脚本编写。
- [常见问题 (FAQ)](docs/FAQ.md) & [术语表](docs/glossary.md)。

## v0.9.1 新特性

v0.9.1 正处于 RC 准备阶段，是 **v1.0 前最后一次基线收敛与兼容性重置**。它在性能就绪工作的基础上，完成 v1.0 冻结前最后一轮有意的源码构建与公共契约清理。v0.9.0 发布时原计划作为最后一个破坏性版本；由于 v1.0 尚未发布且采用规模仍有限，兼容性冻结窗口现明确延长至 v0.9.1。

- **Rust 基线重置**：源码构建现在要求 Rust 1.97+；仓库、CI 和发布构建使用精确的 Rust 1.97.0 (MSRV 1.97)。预构建模块的用户不需要安装 Rust。
- **单一流式控制**：`markdown_streaming off|auto|force` 现在是唯一处理路径选择器。重复的 `markdown_streaming_engine` 仅保留拒绝入口，并给出 off/auto/on 的精确迁移提示。
- **明确支持的 flavor**：`markdown_flavor` 仅支持 `commonmark` 和 `gfm`。实验性的 `mdx` 与 `org-mode` 从未有独立生产语义，现会被明确拒绝。
- **混合零拷贝流式输出**：`markdown_streaming_zero_copy on`（默认关闭，需显式开启）允许 `ngx_buf_t` 直接引用 Rust 管理的内存，省去中间的 pool-copy，降低非终端流式分块的 memcpy 开销。NGINX 请求池清理句柄确保在背压和请求销毁场景下 Rust 缓冲区的生命周期安全。
- **流式解压路由（gzip + deflate）**：在 `streaming_first` profile 下，当 `markdown_auto_decompress on` 且 `markdown_cache_validation` 不为 `full` 时，gzip 与 deflate 响应（包括 zlib 封装 RFC 1950 和原始 RFC 1951 deflate）通过流式引擎增量解压，无需强制全缓冲积攒。gzip member 边界和 trailer 会跨分块校验；Brotli 在 0.9.1 中仍走有界全缓冲路径。
- **全缓冲拷贝减少**：内部优化（默认开启，无配置项），通过将连续缓冲区直接传递给解压器并通过指针赋值交换输出，消除全缓冲压缩路径中冗余的 memcpy。
- **`markdown_auto_decompress` 指令**：现已正式注册为可配置指令（默认开启）。此前仅为内部字段，无法通过 `nginx.conf` 设置。
- **性能证据门禁**：模块级基准测试工具（`tools/perf/run_module_benchmark.sh`）与自动化发布门禁（`make release-gates-check-091`）在发布前强制验证延迟、TTFB、内存斜率和回退率阈值。
- **Doctor 诊断工具**：`python3 tools/perf/doctor_advice.py` 分析运行时指标，为运维人员提供可操作的调优建议。
- **新增 ADR**：[0020](docs/architecture/ADR/0020-091-hybrid-zero-copy-pool-cleanup.md)、[0021](docs/architecture/ADR/0021-091-gzip-deflate-streaming-decompression-routing.md)、[0022](docs/architecture/ADR/0022-091-performance-evidence-release-gate.md)。

关于更早版本的完整变更记录（包括 v0.9.0 引入的突破性配置改动），请参阅 [CHANGELOG.md](CHANGELOG.md)。

## 未来规划

v0.9.1 发布后迈向 v1.0.0 正式版的演进方向：

- **可观测性扩展**：在 NGINX C 模块过滤链路中引入原生的 OpenTelemetry 链路追踪 (Tracing) 支持。
- **分发渠道拓宽**：将 APT 与 YUM 打包发布整合至标准的 Linux 发行版包索引中，降低安装门槛。
- **诊断系统增强**：扩展 `nginx-markdown-doctor` CLI 工具与监控指标，提供转换劣化及偏移的实时诊断。

## 许可证

BSD 2-Clause "Simplified" License。详见 [LICENSE](LICENSE)。

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-17 | Kang | 优化 README 文档组织，移除旧版本的 What's New 日志，合并核心功能特性表，并梳理文档导航结构以适配 v0.9.1。 |
| 0.9.0 | 2026-07-02 | Kang | 文档审查：新增 v0.9.0 新特性段落、MIGRATION-0.9 链接、reason code 数量修正、CHANGELOG 同步分支提交 |
| 0.8.3 | 2026-06-26 | Kang | v0.8.3 收口：流式状态机修复、ExitMany 批量解上下文、解压缓冲区内存安全、快照容量提升、FFI Box::into_raw 修复、完整发布门禁验证 |
| 0.8.2 | 2026-06-25 | Kang | v0.8.2 发布：流式解压加固、FFI panic 安全、隐式闭合正确性、解压预算强制执行、安全扫描范围限定、版本线文档收口 |
| 0.8.0 | 2026-06-16 | Codex | 同步中英文 README 结构、Quick Start 示例、本地测试命令、平台支持标题和 v0.8.0 路线说明 |
| 0.8.0 | 2026-06-16 | Kang | 0.8.0 正式发布文档就绪：双引擎流式转换（auto 默认）、有界内存增量处理、提交前安全回退、旧阈值指令兼容、新流式指令、可观测性与 release-gates-check-080 |
| 0.7.0 | 2026-06-03 | Kang | P0 正确性修复、Rust-first 架构、独立解压预算、Accept 协商、解析超时/预算、DEB/RPM 包分发、K8s 示例、运行时诊断、dynconf dry-run/回滚 |
| 0.6.3 | 2026-05-14 | Kang | 版本号更新至 0.6.3，并补充 release matrix 与发布前最终加固说明 |
| 0.6.2 | 2026-05-08 | Kang | Version bump to 0.6.2 for release |
| 0.5.0 | 2026-04-21 | docs-standardization | 同步中英文 README 快速上手步骤；新增更新追踪段落 |
