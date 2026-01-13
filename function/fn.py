"""A Crossplane composition function."""

import hashlib

import grpc
from crossplane.function import logging, request, resource, response
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1
from jsonrpcclient import request_json


class FunctionRunner(grpcv1.FunctionRunnerService):
    """A FunctionRunner handles gRPC RunFunctionRequests."""

    def __init__(self):
        """Create a new FunctionRunner."""
        self.log = logging.get_logger()

    async def RunFunction(
        self, req: fnv1.RunFunctionRequest, _: grpc.aio.ServicerContext
    ) -> fnv1.RunFunctionResponse:
        """Run the function."""
        log = self.log.bind(tag=req.meta.tag)
        log.info("Running function")

        rsp = response.to(req)

        composite = req.observed.composite.resource
        name_prefix = composite["metadata"]["name"]
        endpoint = composite["spec"]["endpoint"]
        cmds = composite["spec"]["cmds"]

        environment = resource.struct_to_dict(
            req.context["apiextensions.crossplane.io/environment"]
        )
        port, scheme, insecure_skip_tls_verify = get_envs(environment)

        secret = request.get_required_resource(req, "eos-creds")
        basic_auth = secret.get("data", {}).get("basicAuth", "") if secret else ""

        jsonrpc_cfg = {
            "endpoint": endpoint,
            "port": port,
            "scheme": scheme,
            "basicAuth": basic_auth,
            "insecureSkipTLSVerify": insecure_skip_tls_verify,
        }

        jsonrpc_observe_params = {
            "version": 1,
            "format": "json",
            "cmds": ["enable", "show running-config"],
        }
        jsonrpc_observe = request_json("runCmds", params=jsonrpc_observe_params)

        command_paths = walk_cmds(cmds)
        log.info("Generated command paths", count=len(command_paths))

        for path in command_paths:
            name = name_prefix + "-" + name_from_path(path)

            jsonrpc_create_params = {
                "version": 1,
                "format": "json",
                "cmds": ["enable", "configure", *path],
            }
            jsonrpc_create = request_json("runCmds", params=jsonrpc_create_params)

            remove_path = [*path[:-1], f"no {path[-1]}"]
            jsonrpc_remove_params = {
                "version": 1,
                "format": "json",
                "cmds": ["enable", "configure", *remove_path],
            }
            jsonrpc_remove = request_json("runCmds", params=jsonrpc_remove_params)

            expected_logic, removed_logic = create_jq_logic_expressions(path)

            jsonrpc_ops = {
                "create": jsonrpc_create,
                "observe": jsonrpc_observe,
                "remove": jsonrpc_remove,
                "expectedResponseCheck": expected_logic,
                "isRemovedCheck": removed_logic,
            }

            resource_data = construct_resource_request(jsonrpc_ops, jsonrpc_cfg)

            resource.update(
                rsp.desired.resources[name],
                resource_data,
            )

        return rsp


def create_jq_logic_expressions(path: list[str]) -> tuple[str, str]:
    """Create jq logic exp for Request."""
    logic = ".response.body.error == null and ("
    for cmd in path[:-1]:
        logic = logic + f'.response.body.result[1].cmds."{cmd}".cmds'
    logic1 = logic + f' | has("{path[-1]}")' + ")"
    logic2 = logic + f' | has("{path[-1]}")' + " | not)"

    return logic1, logic2


def walk_cmds(cmds: dict, path: list[str] | None = None) -> list[list[str]]:
    """walk_cmds function."""
    if path is None:
        path = []

    results: list[list[str]] = []

    for cmd in sorted(cmds.keys()):
        subtree = cmds[cmd]
        next_path = [*path, cmd]

        if subtree == {}:
            # Leaf → emit array
            results.append(next_path)
        else:
            # Recurse
            results.extend(walk_cmds(subtree, next_path))

    return results


def name_from_path(path: list[str]) -> str:
    """name_from_path function."""
    joined = "|".join(path)
    return hashlib.sha1(joined.encode(), usedforsecurity=False).hexdigest()[:10]


def get_envs(environment: dict) -> tuple[int, str, bool]:
    """Extract jsonrpc configuration from the environment."""
    if not environment:
        return 0, "", True

    protocols_settings = environment.get("jsonrpc", {})

    port = int(protocols_settings.get("port", 0))
    scheme = protocols_settings.get("scheme", "")
    insecure_skip_tls_verify = bool(protocols_settings.get("skipTlsVerify", True))

    return port, scheme, insecure_skip_tls_verify


def construct_resource_request(ops: dict, config: dict) -> dict:
    """Construct the resource request for the given data."""
    return {
        "apiVersion": "http.crossplane.io/v1alpha2",
        "kind": "Request",
        "spec": {
            "forProvider": {
                "insecureSkipTLSVerify": config["insecureSkipTLSVerify"],
                "headers": {
                    "Accept": ["application/json"],
                    "Authorization": [f"Basic {config['basicAuth']}"],
                },
                "payload": {
                    "baseUrl": f"{config['scheme']}://{config['endpoint']}:{config['port']}",
                    "body": ops["create"],
                },
                "mappings": [
                    {
                        "action": "CREATE",
                        "method": "POST",
                        "url": ".payload.baseUrl",
                        "body": ".payload.body",
                    },
                    {
                        "action": "UPDATE",
                        "method": "POST",
                        "url": ".payload.baseUrl",
                        "body": ".payload.body",
                    },
                    {
                        "action": "OBSERVE",
                        "method": "POST",
                        "url": ".payload.baseUrl",
                        "body": ops["observe"],
                    },
                    {
                        "action": "REMOVE",
                        "method": "POST",
                        "url": ".payload.baseUrl",
                        "body": ops["remove"],
                    },
                ],
                "expectedResponseCheck": {
                    "type": "CUSTOM",
                    "logic": ops["expectedResponseCheck"],
                },
                "isRemovedCheck": {
                    "type": "CUSTOM",
                    "logic": ops["isRemovedCheck"],
                },
            },
        },
    }
