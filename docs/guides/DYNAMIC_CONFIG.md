# Dynamic Configuration (removed in 0.9.2)

The 0.9.2 pre-LTS convergence removed the runtime dynconf subsystem. The
following directives and their JSON file watcher no longer exist:

- `markdown_dynamic_config`
- `markdown_dynamic_config_path`
- `markdown_dynconf_dry_run`

The names are no longer registered, so `nginx -t` reports NGINX's standard
`unknown directive` error; `MIGRATION-0.9.2.md` names the replacement for each.
The module does not read a runtime configuration file,
maintain a last-known-good dynconf snapshot, or expose a dynconf metrics
family. Configure the static directives in
[CONFIGURATION.md](CONFIGURATION.md), run `nginx -t`, and use a controlled
reload or restart to apply a change.

For the removal table and upgrade examples, see
[MIGRATION-0.9.2.md](MIGRATION-0.9.2.md). For rollback, restore a matching
configuration and module together as described in
[VERSION_ROLLBACK-0.9.2.md](VERSION_ROLLBACK-0.9.2.md).

This page remains as a stable link for older deployments and historical
references. Its former examples describe the removed implementation. Do not
use them with 0.9.2 or later.
