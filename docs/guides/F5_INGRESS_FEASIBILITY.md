# F5 NGINX Ingress Controller Feasibility

## Conclusion

**Feasible with limitations.** The F5 NGINX Ingress Controller supports
dynamic module loading via volume mounts, but the approach requires
careful version alignment.

## Injection Method

Mount the compiled `.so` file into the Ingress Controller pod via
a ConfigMap or PersistentVolume, and add `load_module` via the
`main-snippet` ConfigMap key.

```yaml
volumeMounts:
  - name: markdown-module
    mountPath: /etc/nginx/modules
    readOnly: true
```

## Known Limitations

1. **ABI binding**: The `.so` must be compiled against the exact NGINX
   version inside the F5 Controller image.
2. **No custom image**: F5 does not support replacing the Controller
   image with a custom build in managed deployments.
3. **Module updates**: Require rebuilding the `.so` when the Controller
   image is upgraded.

## Alternative: Sidecar Proxy

For environments where Ingress Controller modification is not feasible,
deploy an NGINX sidecar with the markdown module in front of the
application pods.
