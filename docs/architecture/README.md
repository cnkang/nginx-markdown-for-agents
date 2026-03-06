# Architecture Documentation

This section explains how the system is put together and why the main technical decisions were made.

Use it when you need more than deployment guidance but less than source-level implementation detail.

## Start Here

| If you want to understand... | Read |
|------------------------------|------|
| The runtime architecture and component boundaries | [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) |
| The request flow through header/body filters and failure paths | [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md) |
| How directives map to runtime branches and implementation areas | [CONFIG_BEHAVIOR_MAP.md](CONFIG_BEHAVIOR_MAP.md) |
| The repository layout and where code lives | [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md) |
| Why conversion lives in Rust | [ADR/0001-use-rust-for-conversion.md](ADR/0001-use-rust-for-conversion.md) |
| Why v1 uses full buffering | [ADR/0002-full-buffering-approach.md](ADR/0002-full-buffering-approach.md) |
| The full ADR index | [ADR/README.md](ADR/README.md) |

## Scope

- `SYSTEM_ARCHITECTURE.md` explains the runtime design in product and engineering terms.
- `REQUEST_LIFECYCLE.md` explains the dynamic request path and control flow.
- `CONFIG_BEHAVIOR_MAP.md` maps public directives to runtime behavior.
- `REPOSITORY_STRUCTURE.md` explains how the repository is organized.
- `ADR/` records specific architectural decisions and their consequences.
