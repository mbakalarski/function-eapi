# Example manifests

You can run your function locally and test it using `crossplane beta render`
with these example manifests.

```shell
# Run the function locally
hatch run development
```

```shell
# Then, in another terminal, call it with these example manifests
crossplane render xr.yaml composition.yaml functions.yaml \
  --required-resources secret.yaml --extra-resources environment.yaml -r
```

```
---
apiVersion: eos.netclab.dev/v1alpha1
kind: EosCommand
metadata:
  name: eoscommand-1
spec:
  endpoint: ceos01.default.svc.cluster.local
  cmds:
    ip prefix-list PL-Loopback0:
      seq 10 permit 10.0.0.1/32 eq 32: {}
      seq 20 permit 10.0.0.2/32 eq 32: {}
---
<...>
```