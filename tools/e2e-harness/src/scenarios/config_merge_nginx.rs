//! Configuration for the config-merge scenario.

pub const CONFIG: &str = r#"@LOAD@worker_processes 1;
error_log "nginx-error.log" info;
pid "nginx.pid";

events { worker_connections 512; }

http {
    access_log nginx-access.log;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 5;

    # http-level defaults
    markdown_error_policy pass;

    server {
        listen 127.0.0.1:@PORT@;
        server_name localhost;

        # server-level settings
        markdown_accept force;
        markdown_cache_validation off;

        # Case 1: location overrides error_policy to fail_closed
        location /md/reject/ {
            markdown_filter on;
            markdown_error_policy fail_closed;
            markdown_limits conversion_memory=64k parser_budget=64k streaming_buffer=64k conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 1b: location inherits error_policy pass from http level
        location /md/pass/ {
            markdown_filter on;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 3: markdown_filter off disables conversion
        location /nomd/ {
            markdown_filter off;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 4: markdown_accept strict at location
        location /no-wildcard/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 5: cache_validation full at location overrides server off
        location /etag-on/ {
            markdown_filter on;
            markdown_streaming off;
            markdown_cache_validation full;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 6: cache_validation ims_only override at location
        location /cond-ims/ {
            markdown_filter on;
            markdown_cache_validation ims_only;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 7: flavor override at location
        location /flavor/ {
            markdown_filter on;
            markdown_flavor gfm;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }
    }
}
"#;
