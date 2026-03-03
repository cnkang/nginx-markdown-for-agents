FROM nginx:latest AS BUILD_STAGE

# Install package dependencies for building nginx modules
#
WORKDIR /tmp
RUN apt-get update -qq && apt-get install -y --no-install-recommends wget git gcc make libpcre2-dev zlib1g-dev libclang-dev

# Install Rust toolchain and make it available to the default shell
#
RUN wget --https-only -O - https://sh.rustup.rs | bash -s -- -y && \
    rm -fr /usr/local/bin && \
    ln -s /root/.cargo/bin /usr/local/bin && \
    cargo install cbindgen

# Clone the module repo and build the bindings
#
RUN git clone https://github.com/cnkang/nginx-markdown-for-agents.git && \
    cd nginx-markdown-for-agents && \
    make

# Compile the dynamic module
#
RUN wget -O - http://nginx.org/download/`nginx -v 2>&1 | tr / - | cut -f3 -d' '`.tar.gz | tar xfz - && \
    cd nginx-1* && \
    ./configure --with-compat --add-dynamic-module=/tmp/nginx-markdown-for-agents/components/nginx-module && \
    make modules && \
    strip objs/ngx_http_markdown_filter_module.so -o /tmp/ngx_http_markdown_filter_module.so

# Create final image with the dynamic module already loaded
#
FROM nginx:latest
COPY --from=BUILD_STAGE /tmp/ngx_http_markdown_filter_module.so /etc/nginx/modules
RUN sed -i "s/events/load_module modules\/ngx_http_markdown_filter_module.so;\nevents/" /etc/nginx/nginx.conf
