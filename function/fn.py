# Copyright 2025-present Michal Bakalarski and Netclab Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""A Crossplane composition function."""

import hashlib
import json

import grpc
from crossplane.function import logging, request, resource, response
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1
from jsonrpcclient import request_json

JSONRPC_BASE = {"version": 1, "format": "json"}


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

        observed_xr = resource.struct_to_dict(req.observed.composite.resource)
        observed_xr_name = observed_xr.get("metadata").get("name")
        fqdn = observed_xr["spec"].get("endpoint")
        cmds = observed_xr["spec"].get("cmds")
        remove_container = observed_xr["spec"].get("removeContainer")

        environment = resource.struct_to_dict(
            req.context["apiextensions.crossplane.io/environment"]
        )
        port, scheme, insecure_skip_tls_verify = get_envs(environment)

        secret = request.get_required_resource(req, "eos-creds")
        basic_auth = secret.get("data", {}).get("basicAuth", "") if secret else ""

        jsonrpc_cfg = {
            "fqdn": fqdn,
            "port": port,
            "scheme": scheme,
            "basicAuth": basic_auth,
            "insecureSkipTLSVerify": insecure_skip_tls_verify,
            "basePath": "/command-api",
        }

        jsonrpc_observe_params = {
            **JSONRPC_BASE,
            "cmds": ["enable", "show running-config"],
        }
        jsonrpc_observe = request_json("runCmds", params=jsonrpc_observe_params)

        command_paths = sorted(walk_cmds(cmds))
        log.info("Generated command paths", count=len(command_paths))

        for path in command_paths:
            name = name_based_on_path(observed_xr_name, path)

            path_log = log.bind(resource=name, path=" | ".join(path))
            path_log.debug("Creating resource")

            jsonrpc_create_params = {
                **JSONRPC_BASE,
                "cmds": ["enable", "configure", *path],
            }
            jsonrpc_create = request_json("runCmds", params=jsonrpc_create_params)

            jsonrpc_remove_params = {
                **JSONRPC_BASE,
                "cmds": [
                    "enable",
                    "configure",
                    *build_remove_path(path, remove_container=remove_container),
                ],
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

            resource_data = construct_request_resource(name, jsonrpc_ops, jsonrpc_cfg)

            resource.update(
                rsp.desired.resources[name],
                resource_data,
            )

        return rsp


def name_based_on_path(observed: str, path: list[str]) -> str:
    """name_based_on_path function."""
    joined = "|".join(path)
    hashed_path = hashlib.sha256(
        joined.encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    full = f"{observed[:14]}-{hashed_path[:48]}"
    return full.rstrip("-")[:63]


def toggle_no(cmd: str) -> str:
    """To clarify intent of build_remove_path()."""
    return cmd.removeprefix("no ") if cmd.startswith("no ") else f"no {cmd}"


def build_remove_path(path: list[str], *, remove_container: bool = False) -> list[str]:
    """Create cmd for remove op."""
    head, *tail = path

    # remove the container
    if remove_container and tail:
        return [f"no {head}"]

    # remove nested items
    if tail:
        return [
            head,
            *(toggle_no(cmd) for cmd in tail),
        ]

    # one line path
    return [toggle_no(head)]


def jq_path(cmds: list[str]) -> str:
    """Create middle part of jq logic expression."""
    return "".join([f".cmds[{json.dumps(c)}]" for c in cmds])


def create_jq_logic_expressions(path: list[str]) -> tuple[str, str]:
    """Compose jq logic expressions."""
    base = ".response.body"
    tree = f"{base}.result[1]{jq_path(path[:-1])}.cmds"
    check = f"has({json.dumps(path[-1])})"

    expected = f"{base}.error == null and ({tree} | {check})"
    removed = f"{base}.error == null and ({tree} | {check} | not)"

    return expected, removed


def walk_cmds(cmds: dict, path: list[str] | None = None) -> list[list[str]]:
    """Make command paths from nested command tree."""
    if path is None:
        path = []

    results: list[list[str]] = []

    for cmd in sorted(cmds.keys()):
        subtree = cmds[cmd]
        next_path = [*path, cmd]

        if subtree == {}:
            # Leaf -> emit array
            results.append(next_path)
        else:
            # Recurse
            results.extend(walk_cmds(subtree, next_path))

    return results


def get_envs(environment: dict) -> tuple[int, str, bool]:
    """Extract jsonrpc configuration from the environment."""
    if not environment:
        return 0, "", True

    protocols_settings = environment.get("jsonrpc", {})

    port = int(protocols_settings.get("port", 0))
    scheme = protocols_settings.get("scheme", "")
    insecure_skip_tls_verify = bool(protocols_settings.get("skipTlsVerify", True))

    return port, scheme, insecure_skip_tls_verify


def construct_request_resource(name: str, ops: dict, config: dict) -> dict:
    """Construct the resource request for the given data."""
    return {
        "apiVersion": "http.crossplane.io/v1alpha2",
        "kind": "Request",
        "metadata": {
            "name": name,
        },
        "spec": {
            "forProvider": {
                "insecureSkipTLSVerify": config["insecureSkipTLSVerify"],
                "headers": {
                    "Accept": ["application/json"],
                    "Authorization": [f"Basic {config['basicAuth']}"],
                },
                "payload": {
                    "baseUrl": f"{config['scheme']}://{config['fqdn']}:{config['port']}{config['basePath']}",
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
