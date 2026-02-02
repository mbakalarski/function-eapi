# eAPI Function

This Crossplane composition function generates resources for interacting with Arista EOS devices via JSON-RPC.<br>
It is intended for use in Crossplane Configuration packages to manage device state automatically.


```shell
# Run the code in development mode, for crossplane render
hatch run development
```

```shell
# Lint and format the code - see pyproject.toml
hatch fmt
```

```shell
# Run unit tests - see tests/test_fn.py
hatch test
```

```shell
# Build the function's runtime image - see Dockerfile
docker build . --tag=runtime
```

```shell
# Build a function package - see package/crossplane.yaml
crossplane xpkg build -f package --embed-runtime-image=runtime
```

## License

Copyright 2026-present Michal Bakalarski and Netclab Contributors

The project is published under [Apache 2.0 License](LICENSE)
