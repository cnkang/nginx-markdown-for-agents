//! Configuration for the streaming-failure-cache scenario.

pub const CONFIG: &str = r#"@LOAD@worker_processes 1;
error_log nginx-error.log debug;
pid nginx.pid;

events { worker_connections 512; }

http {
    access_log nginx-access.log;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 5;

    server {
        listen 127.0.0.1:@PORT@;
        server_name localhost;

        # 10.1: Streaming success + cache_validation ims_only
        location /t01/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.2: Pre-commit failure + pass
        location /t02/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.3: Pre-commit failure + fail_closed
        location /t03/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy fail_closed;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.4: Post-commit failure (upstream aborts mid-stream)
        location /t04/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.5: full validation selects the full-buffer path in auto mode
        location /t05/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation full;
            markdown_streaming auto;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.6: cache_validation ims_only + streaming on
        location /t06/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.7: Streaming response headers (same as t01)
        location /t07/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.8: markdown_streaming off + cache_validation full
        location /t08/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation full;
            markdown_streaming off;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.9a: error_policy pass (streaming pre-commit failure → fail-open)
        location /t09a/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.9b: error_policy fail_closed (streaming pre-commit failure → reject)
        location /t09b/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy fail_closed;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.10: HEAD request should bypass streaming body processing
        # Uses ims_only so streaming is eligible for GET,
        # but HEAD must still skip the streaming body path entirely.
        location /t10/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.11: 304 should bypass streaming path selection
        # Full validation with auto mode generates an ETag for revalidation and
        # keeps this request out of the streaming path.
        location /t11/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation full;
            markdown_streaming auto;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.12: Authenticated fail-open still applies Cache-Control policy
        # Uses auth_policy allow so the request enters conversion/streaming,
        # then streaming_buffer=64k triggers pre-commit failure for the
        # 12MB fixture -> fail-open.
        # With auth_policy allow + auth_cookies "session*" + Cookie header,
        # the final header path must rewrite public caching to private even
        # when streaming has failed before conversion can commit.
        location /t12/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*";
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.13: no-cache="public" quoted directive not treated as bare public
        location /t13/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_limits conversion_memory=@MEMORY@ parser_budget=@MEMORY@ streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*";
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }
    }
}
"#;
