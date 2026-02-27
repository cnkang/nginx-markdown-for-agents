# NGINX Configuration Examples

This directory contains ready-to-use NGINX configuration templates for common deployment scenarios.

## Available Templates

1. **01-minimal-reverse-proxy.conf** - Minimal setup for reverse proxy deployments
2. **02-minimal-php-fpm.conf** - Minimal setup for PHP-FPM applications
3. **03-global-with-exceptions.conf** - Global enablement with path-based exceptions
4. **04-production-full.conf** - Production-ready configuration with all features
5. **05-high-performance.conf** - Optimized for maximum throughput

## How to Use

1. Choose the template that best matches your deployment scenario
2. Copy the template to your NGINX configuration directory
3. Customize the following placeholders:
   - `example.com` → your domain name
   - `/path/to/backend` → your backend server address
   - `/usr/local/nginx` → your NGINX installation path
4. Test the configuration: `nginx -t`
5. Reload NGINX: `nginx -s reload`

## Template Selection Guide

| Scenario | Recommended Template | Key Features |
|----------|---------------------|--------------|
| Simple reverse proxy | 01-minimal-reverse-proxy.conf | Basic conversion, single location |
| WordPress/PHP site | 02-minimal-php-fpm.conf | PHP-FPM integration |
| Multi-path site | 03-global-with-exceptions.conf | Global on, selective off |
| Production deployment | 04-production-full.conf | Security, caching, monitoring |
| High-traffic site | 05-high-performance.conf | Aggressive optimization |

## Configuration Checklist

Before deploying to production:

- [ ] Replace all placeholder values
- [ ] Adjust resource limits (`markdown_max_size`, `markdown_timeout`)
- [ ] Configure authentication cookie patterns if needed
- [ ] Set up metrics endpoint with proper access controls
- [ ] Enable caching with appropriate cache keys
- [ ] Add security headers
- [ ] Test with both `Accept: text/markdown` and `Accept: text/html`
- [ ] Verify cache behavior (different variants cached separately)
- [ ] Monitor initial deployment for errors

## Additional Resources

- [Configuration Guide](../../docs/guides/CONFIGURATION.md) - Complete directive reference
- [Installation Guide](../../docs/guides/INSTALLATION.md) - Installation instructions
- [Operations Guide](../../docs/guides/OPERATIONS.md) - Monitoring and troubleshooting

## Support

For issues or questions:
- Check the [troubleshooting guide](../../docs/guides/OPERATIONS.md#troubleshooting)
- Review [common issues](../../README.md#common-issues-quick-reference)
- Consult your repository's issue tracker
