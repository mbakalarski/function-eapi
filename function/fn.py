"""A Crossplane composition function."""

import grpc
from crossplane.function import logging, resource, response
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1


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

        command_paths = walk_cmds(cmds)
        log.info("Generated command paths", count=len(command_paths))

        for path in command_paths:
            name = name_prefix + "-" + name_from_path(path)

            resource.update(
                rsp.desired.resources[name],
                {
                    "apiVersion": "http.crossplane.io/v1alpha2",
                    "kind": "Request",
                    "spec": {
                        "endpoint": endpoint,
                        "command": path,
                    },
                },
            )

        return rsp


def walk_cmds(cmds: dict, path: list[str] | None = None) -> list[list[str]]:
    if path is None:
        path = []

    results: list[list[str]] = []

    # NOTE: keys are sorted to ensure deterministic reconciliation
    for cmd in sorted(cmds.keys()):
        subtree = cmds[cmd]
        next_path = path + [cmd]

        if subtree == {}:
            # Leaf → emit array
            results.append(next_path)
        else:
            # Recurse
            results.extend(walk_cmds(subtree, next_path))

    return results


import hashlib

def name_from_path(path: list[str]) -> str:
    joined = "|".join(path)
    return hashlib.sha1(joined.encode()).hexdigest()[:10]
