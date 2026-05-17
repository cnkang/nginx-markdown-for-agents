# Kubernetes Deployment Guide

## Overview

This guide provides reference examples for deploying the NGINX Markdown
Filter Module in Kubernetes environments with NGINX Ingress Controllers.

**Important:** These are reference examples, not officially supported
configurations. Each Ingress Controller implementation may require
different customization approaches.

## Verified Ingress Controllers

| Controller | Version | Module Injection | Status |
|-----------|---------|-----------------|--------|
| ingress-nginx (community) | 1.10+ | Custom image build | Verified |
| F5 NGINX Ingress Controller | Latest | Dynamic module volume | Experimental |

## Enable/Verify/Disable/Rollback

### Enable

1. Build custom Ingress Controller image with module included
2. Update Deployment to use custom image
3. Add `load_module` and `markdown_filter` via ConfigMap snippet

### Verify

```bash
kubectl exec -n ingress-nginx <pod> -- nginx -V 2>&1 | grep markdown
```

### Disable

Remove `markdown_filter on` from ConfigMap; module remains loaded but inactive.

### Rollback

Revert Deployment image tag to upstream Ingress Controller image.
