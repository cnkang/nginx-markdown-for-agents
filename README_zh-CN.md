# NGINX Markdown for Agents

[![Latest Release](https://img.shields.io/github/v/release/cnkang/nginx-markdown-for-agents?sort=semver)](https://github.com/cnkang/nginx-markdown-for-agents/releases) [![NGINX](https://img.shields.io/badge/NGINX-%3E%3D1.24.0-009639?logo=nginx&logoColor=white)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/docs/guides/INSTALLATION.md) [![CI](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml) [![Security Scanning](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml) [![License](https://img.shields.io/github/license/cnkang/nginx-markdown-for-agents)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/LICENSE) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=cnkang_nginx-markdown-for-agents&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=cnkang_nginx-markdown-for-agents)

[English](README.md) | 简体中文

让 NGINX 为你已经在提供的 HTML 页面增加一份更适合机器消费的 Markdown 变体。

> HTML 保持原样，Markdown 按需返回——客户端主动请求，或者你指定哪些 bot 自动获得。

客户端发送 `Accept: text/markdown` 时得到 Markdown；浏览器和普通调用方仍然拿到原始 HTML。你也可以通过 NGINX 配置针对特定的 AI 爬虫（如 ClaudeBot、GPTBot）按 User-Agent 自动改写 Accept 头，让这些 bot 即使没有主动请求 Markdown 也能收到转换后的内容。你不需要改造业务应用，不需要额外维护一套抓取器，也不需要单独部署一个转换服务。

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
- 基于标准 HTTP 内容协商，缓存与回源行为仍然容易理解和运维。
- 仍然是 NGINX 模块的部署模型，不需要额外引入一个新的常驻服务。
- 在最靠近应用的反向代理层做转换，对 HTML 来源和转换配置有完整的控制权。
- 为 AI 消费方提供更干净、更省 token 的内容表示，减少生成式回答引用你的站点时出现误解或信息丢失的风险。

## 一眼看清

| 如果你需要…… | 这个项目提供的是…… |
|--------------|----------------------|
| 让现有站点更适合 Agent 消费 | 从当前 HTML 响应协商出 Markdown 变体 |
| 尽量少动应用层 | 在 NGINX 侧按路径、按站点启用 |
| 可控上线 | 失败透传、大小限制、超时限制和跨 worker 聚合指标 |
| 更符合 HTTP 语义的缓存行为 | 变体 `ETag`、`Vary: Accept` 与条件请求支持 |
| 灵活的配置 | 变量驱动的按请求控制、按 User-Agent 定向 bot 以及认证策略 |

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

安装脚本会识别本机 NGINX 版本，下载匹配的模块制品，并为常见官方 NGINX 构建完成基础 `load_module` 集成。

如果你使用自编译 NGINX 或希望从源码构建，请从 [安装指南](docs/guides/INSTALLATION.md) 开始。
如果你希望基于官方 NGINX Docker 镜像进行源码构建，请参考 `examples/docker/Dockerfile.official-nginx-source-build` 以及 [docs/guides/INSTALLATION.md](docs/guides/INSTALLATION.md) 里的 Docker 小节。

### 2. 在一个路由上开启 Markdown

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    upstream backend {
        server 127.0.0.1:8080;
    }

    server {
        listen 80;

        location /docs/ {
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
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/docs/

# HTML 保持原样
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/docs/
```

预期结果：

- `Accept: text/markdown` 返回 `Content-Type: text/markdown; charset=utf-8`
- `Accept: text/html` 仍返回原始 HTML

如果你想直接看更贴近生产环境的配置模式，下一步可以看 [docs/guides/DEPLOYMENT_EXAMPLES.md](docs/guides/DEPLOYMENT_EXAMPLES.md)。

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

## 什么场景最适合

如果你符合下面这些情况，这个项目通常很合适：

- 已经通过 NGINX 提供 HTML 页面，希望最小改动增加 Agent 友好的表示
- 需要给爬虫、内部 Agent、检索系统或助手类产品提供 Markdown 内容
- 想针对特定 AI 爬虫（ClaudeBot、GPTBot 等）主动返回 Markdown，即使它们不会发送 `Accept: text/markdown`
- 希望 AI 系统在引用你的内容时，能基于更干净、语义更准确的表示来工作
- 想把表示层控制和缓存策略继续放在边缘或反向代理层

下面这些情况就不一定是最佳选择：

- 你已经有专门的 Markdown 或 JSON 内容 API
- 你今天就必须支持超大页面的真正流式 Markdown 转换
- 你希望转换逻辑完全脱离请求路径

## 与边缘层转换的对比

Cloudflare 的 [Markdown for Agents](https://blog.cloudflare.com/markdown-for-agents/) 在 CDN 边缘对已经渲染好的 HTML 进行转换。这种方式在降低接入门槛方面很有效——站点运营者不需要改动源站基础设施就能启用。

本项目在更靠近源站的位置提供 `text/markdown` 表示，通常是在 NGINX 作为反向代理部署在应用前面的那一层。实际区别在于：

- NGINX 转换的 HTML 是应用或 CMS 的直接输出。在这一层做转换意味着不依赖页面在后续链路中可能经历的结构变化或内容增补，更容易保留原始内容语义。
- 转换发生在你自己运维的基础设施内，模块版本、配置、失败策略和上线范围都由你控制。
- 这种方式更贴合标准 HTTP 内容协商模型：由源站（或其反向代理）根据客户端的 Accept 头选择资源的最佳表示。

两种方式没有绝对的优劣。边缘层转换适合希望零改动、跨站点快速启用的场景。靠近源站的转换更适合希望精确控制转换对象、转换方式和运行位置的团队。

## 你能得到什么

| 能力 | 说明 |
|------|------|
| 内容协商 | 客户端请求 `text/markdown` 时触发转换，也支持按 User-Agent 针对特定 bot 自动转换 |
| HTML 透传 | 浏览器和普通客户端行为不变 |
| 自动解压 | 支持 gzip、brotli、deflate 上游响应 |
| 缓存友好变体 | 支持 ETag 与条件请求 |
| 失败策略可控 | 可选失败透传或失败拦截 |
| 资源限制 | 可配置大小与超时上限 |
| 安全清洗 | 在转换器中处理 XSS、XXE、SSRF 相关风险 |
| 可选元数据 | 支持 token 估算与 YAML front matter |
| 指标端点 | 提供转换计数等运行指标 |
| 变量驱动配置 | 使用 NGINX 变量实现按请求的转换控制 |
| 认证感知 | 可配置的认证请求策略与缓存控制 |
| 大响应增量处理 | 基于阈值的路由机制，将大型 HTML 响应导向独立的增量转换路径；当前实现仍会在转换前完成响应缓冲 |
| 性能基线门禁 | 自动化回归检测，采用双阈值体系（警告/阻断），覆盖 PR 和 Nightly CI |
| 矩阵驱动的发布自动化 | 自动化发布流水线，支持平台矩阵管理与制品完整性验证 |

## 工作原理

```mermaid
flowchart TD
    client["Agent 或工具<br/>Accept: text/markdown"]
    bot["AI 爬虫（如 ClaudeBot）<br/>NGINX 按 User-Agent 匹配"]

    subgraph edge["NGINX 请求路径"]
        ingress["请求进入 NGINX"]
        rewrite["Accept 头改写<br/>（针对匹配的 User-Agent）"]
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

## 为什么是 C + Rust

这个拆分是沿着真实的问题边界做的。

- C 负责直接接入 NGINX 模块 API、过滤链、缓冲区和请求生命周期。
- Rust 负责解析不可信 HTML、做内容清洗和生成可预测的 Markdown 输出。
- FFI 边界保持得很小，这样 NGINX 侧的 HTTP 逻辑和转换逻辑可以相对独立演进。

如果你想看完整的设计理由，而不只是这里的简版说明，可以继续读 [docs/architecture/SYSTEM_ARCHITECTURE.md](docs/architecture/SYSTEM_ARCHITECTURE.md) 和 [docs/architecture/ADR/0001-use-rust-for-conversion.md](docs/architecture/ADR/0001-use-rust-for-conversion.md)。

如果你想理解某个 directive 到底会改变哪一段运行时行为，可以继续看 [docs/architecture/CONFIG_BEHAVIOR_MAP.md](docs/architecture/CONFIG_BEHAVIOR_MAP.md)。

## 本地试一下

```bash
# 快速构建 + 冒烟测试
make test

# 完整 Rust 测试
make test-rust

# 完整 NGINX 模块单元测试
make test-nginx-unit

# 运行时集成、canonical E2E 与 fuzz 冒烟
make test-nginx-integration
make test-e2e
make test-rust-fuzz-smoke
```

`make test-nginx-integration` 与 `make test-e2e` 需要真实 `nginx` 运行时；如果 `PATH` 中没有 `nginx`，请使用 `NGINX_BIN=/absolute/path/to/nginx`。

更完整的集成测试、E2E 与性能基线说明见 [docs/testing/README.md](docs/testing/README.md)。

## 文档导航

| 目标 | 文档 |
|------|------|
| 安装模块 | [docs/guides/INSTALLATION.md](docs/guides/INSTALLATION.md) |
| 从源码构建 | [docs/guides/BUILD_INSTRUCTIONS.md](docs/guides/BUILD_INSTRUCTIONS.md) |
| 配置指令 | [docs/guides/CONFIGURATION.md](docs/guides/CONFIGURATION.md) |
| 查看部署示例 | [docs/guides/DEPLOYMENT_EXAMPLES.md](docs/guides/DEPLOYMENT_EXAMPLES.md) |
| 运维与排障 | [docs/guides/OPERATIONS.md](docs/guides/OPERATIONS.md) |
| 报告漏洞或查看安全支持范围 | [SECURITY.md](SECURITY.md) |
| 了解架构与设计取舍 | [docs/architecture/README.md](docs/architecture/README.md) |
| 理解配置如何映射到运行时行为 | [docs/architecture/CONFIG_BEHAVIOR_MAP.md](docs/architecture/CONFIG_BEHAVIOR_MAP.md) |
| 深入实现细节 | [docs/features/README.md](docs/features/README.md) |
| 查看测试说明 | [docs/testing/README.md](docs/testing/README.md) |
| 项目状态 | [docs/project/PROJECT_STATUS.md](docs/project/PROJECT_STATUS.md) |
| 参与贡献 | [CONTRIBUTING.md](CONTRIBUTING.md) |

## 阅读路径建议

- 想快速判断是否适合接入：先看本页，再看 [docs/guides/DEPLOYMENT_EXAMPLES.md](docs/guides/DEPLOYMENT_EXAMPLES.md)
- 准备落地安装：看 [docs/guides/INSTALLATION.md](docs/guides/INSTALLATION.md)
- 需要配置和策略细节：看 [docs/guides/CONFIGURATION.md](docs/guides/CONFIGURATION.md)
- 准备上线运维：看 [docs/guides/OPERATIONS.md](docs/guides/OPERATIONS.md)
- 报告漏洞：看 [SECURITY.md](SECURITY.md)
- 想理解系统结构和技术选型：看 [docs/architecture/README.md](docs/architecture/README.md)
- 想理解 directive 会改动哪段运行时路径：看 [docs/architecture/CONFIG_BEHAVIOR_MAP.md](docs/architecture/CONFIG_BEHAVIOR_MAP.md)
- 想深入实现：看 [docs/features/README.md](docs/features/README.md)
- 想了解验证方式：看 [docs/testing/README.md](docs/testing/README.md)

## 仓库结构

```text
components/
  nginx-module/        NGINX 过滤模块与 NGINX 侧测试
  rust-converter/      HTML 到 Markdown 的转换引擎与 FFI 层
docs/                  用户、运维、测试与架构文档
  architecture/        系统设计、ADR 与配置行为映射
  features/            具体功能的实现细节
  guides/              安装、配置、部署与运维指南
  project/             项目状态与路线图
  testing/             测试策略与参考文档
examples/
  docker/              Docker 构建示例与配置
  nginx-configs/       NGINX 配置示例
tests/                 顶层测试语料库与共享测试资源
tools/                 安装脚本、CI 脚本与开发工具
  build_release/       发布构建自动化
  ci/                  持续集成脚本
  corpus/              测试语料生成工具
  docs/                文档工具
  e2e/                 端到端测试工具
.github/workflows/     CI/CD 流水线定义
Makefile               顶层构建与测试入口
```

## 路线方向

当前版本 (0.4.0)：

- 兼容 Prometheus 的指标端点，用于运维监控
- 统一的决策原因码，提升转换透明度
- 灰度发布手册，支持选择性启用和金丝雀模式
- 回滚指南，包含触发条件和可执行流程
- 基准语料库，支持可复现的证据和回归检测
- 解析路径优化：噪声区域剪枝、简单结构快速路径
- 重构安装指南，提供最短成功路径
- 大响应增量处理
- 矩阵驱动的发布自动化流水线
- 性能基线门禁系统
- 变量驱动的配置支持
- 增强的安装工具
- 共享指标聚合与运行时回归覆盖
- 强化的 CI/CD 流水线

近期重点：

- 通过 CI 制品持续记录性能基线
- 多样化环境下的部署验证
- 社区反馈整合

未来探索：

- 大文档的流式转换方案
- 更多 Markdown 风格与输出格式
- 基于内置共享指标的更丰富可观测性集成

## 许可证

BSD 2-Clause "Simplified" License。详见 [LICENSE](LICENSE)。
