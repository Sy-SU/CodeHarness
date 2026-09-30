"""Schema-checked tool registry with correlated, redacted trace events."""

from __future__ import annotations

import enum
import inspect
import math
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Type

from agent.oj_client.client import OJClient, OJClientError
from agent.workspace.task import TaskWorkspace


ToolHandler = Callable[..., Any]


class ToolValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ToolParameter:
    name: str
    types: Tuple[Type[Any], ...] = ()
    required: bool = True
    allow_empty: bool = True
    minimum: Optional[float] = None

    def validate(self, value: Any) -> None:
        if self.types:
            if isinstance(value, bool) and bool not in self.types:
                raise ToolValidationError(f"Tool argument {self.name} has the wrong type")
            if not isinstance(value, self.types):
                expected = ", ".join(item.__name__ for item in self.types)
                raise ToolValidationError(
                    f"Tool argument {self.name} must be one of: {expected}"
                )
        if isinstance(value, str) and not self.allow_empty and not value:
            raise ToolValidationError(f"Tool argument {self.name} must not be empty")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                raise ToolValidationError(f"Tool argument {self.name} must be finite")
        if self.minimum is not None and value < self.minimum:
            raise ToolValidationError(
                f"Tool argument {self.name} must be at least {self.minimum}"
            )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Tuple[ToolParameter, ...]

    @classmethod
    def from_handler(cls, name: str, handler: ToolHandler) -> "ToolSpec":
        parameters = []
        for parameter in inspect.signature(handler).parameters.values():
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            parameters.append(
                ToolParameter(
                    parameter.name,
                    required=parameter.default is inspect.Parameter.empty,
                )
            )
        return cls(name=name, description="", parameters=tuple(parameters))

    def validate(self, arguments: Mapping[str, Any]) -> None:
        definitions = {parameter.name: parameter for parameter in self.parameters}
        unknown = sorted(set(arguments) - set(definitions))
        if unknown:
            raise ToolValidationError(
                f"Unknown arguments for {self.name}: {', '.join(unknown)}"
            )
        missing = [
            parameter.name
            for parameter in self.parameters
            if parameter.required and parameter.name not in arguments
        ]
        if missing:
            raise ToolValidationError(
                f"Missing arguments for {self.name}: {', '.join(missing)}"
            )
        for name, value in arguments.items():
            definitions[name].validate(value)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [
                {
                    "name": parameter.name,
                    "types": [item.__name__ for item in parameter.types],
                    "required": parameter.required,
                    "allow_empty": parameter.allow_empty,
                    "minimum": parameter.minimum,
                }
                for parameter in self.parameters
            ],
        }


def _trace_value(value: Any) -> Any:
    if is_dataclass(value):
        return _trace_value(asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _trace_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trace_value(item) for item in value]
    return value


def _safe_arguments(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    summarized = {"code", "content", "stdin"}
    return {
        key: (f"<{len(value)} chars>" if key in summarized and isinstance(value, str) else value)
        for key, value in arguments.items()
    }


class ToolRuntime:
    def __init__(self, workspace: TaskWorkspace):
        self.workspace = workspace
        self.handlers: Dict[str, ToolHandler] = {}
        self.specs: Dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        handler: ToolHandler,
        *,
        spec: Optional[ToolSpec] = None,
    ) -> None:
        if name in self.handlers:
            raise ValueError(f"Tool already registered: {name}")
        resolved_spec = spec or ToolSpec.from_handler(name, handler)
        if resolved_spec.name != name:
            raise ValueError("Tool spec name must match the registered name")
        self.handlers[name] = handler
        self.specs[name] = resolved_spec

    def describe(self) -> Dict[str, Dict[str, Any]]:
        return {name: self.specs[name].as_dict() for name in sorted(self.specs)}

    def call(self, name: str, **arguments: Any) -> Any:
        if name not in self.handlers:
            raise KeyError(f"Unknown tool: {name}")
        call_id = f"tool_{uuid.uuid4().hex}"
        self.workspace.trace.append(
            "TOOL_CALL",
            {"tool": name, "arguments": _safe_arguments(arguments)},
            correlation_id=call_id,
        )
        try:
            self.specs[name].validate(arguments)
            result = self.handlers[name](**arguments)
        except Exception as exc:
            error_payload: Dict[str, Any] = {
                "tool": name,
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            if isinstance(exc, OJClientError):
                error_payload.update(
                    {
                        "error_kind": exc.kind.value,
                        "http_status": exc.http_status,
                        "request_id": exc.request_id,
                        "submission_state_unknown": exc.submission_state_unknown,
                    }
                )
            self.workspace.trace.append(
                "TOOL_RESULT",
                error_payload,
                correlation_id=call_id,
            )
            raise
        self.workspace.trace.append(
            "TOOL_RESULT",
            {"tool": name, "ok": True, "result": _trace_value(result)},
            correlation_id=call_id,
        )
        return result


def _parameter(
    name: str,
    *types: Type[Any],
    required: bool = True,
    allow_empty: bool = True,
    minimum: Optional[float] = None,
) -> ToolParameter:
    return ToolParameter(
        name=name,
        types=tuple(types),
        required=required,
        allow_empty=allow_empty,
        minimum=minimum,
    )


def _spec(name: str, description: str, *parameters: ToolParameter) -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters=tuple(parameters))


def build_default_tools(client: OJClient, workspace: TaskWorkspace) -> ToolRuntime:
    runtime = ToolRuntime(workspace)
    runtime.register(
        "get_problem",
        client.get_problem,
        spec=_spec(
            "get_problem",
            "Fetch the sanitized problem over MiniOJ HTTP.",
            _parameter("problem_id", str, allow_empty=False),
        ),
    )
    runtime.register(
        "run_code",
        client.run_code,
        spec=_spec(
            "run_code",
            "Run source with caller-provided stdin on MiniOJ.",
            _parameter("code", str, allow_empty=False),
            _parameter("stdin", str),
        ),
    )
    runtime.register(
        "submit_solution",
        client.submit_solution,
        spec=_spec(
            "submit_solution",
            "Create one formal MiniOJ submission.",
            _parameter("problem_id", str, allow_empty=False),
            _parameter("code", str, allow_empty=False),
        ),
    )
    runtime.register(
        "get_submission",
        client.get_submission,
        spec=_spec(
            "get_submission",
            "Fetch one MiniOJ submission state.",
            _parameter("submission_id", str, allow_empty=False),
        ),
    )
    runtime.register(
        "get_feedback",
        client.get_feedback,
        spec=_spec(
            "get_feedback",
            "Fetch allowed structured MiniOJ feedback.",
            _parameter("submission_id", str, allow_empty=False),
        ),
    )
    runtime.register(
        "wait_for_submission",
        client.wait_for_submission,
        spec=_spec(
            "wait_for_submission",
            "Poll through OJClient until a machine-final result or deadline.",
            _parameter("submission_id", str, allow_empty=False),
            _parameter("timeout_seconds", int, float, minimum=0),
            _parameter("poll_interval_seconds", int, float, minimum=0),
        ),
    )
    runtime.register(
        "read_file",
        workspace.read_text,
        spec=_spec(
            "read_file",
            "Read a UTF-8 file from this task workspace.",
            _parameter("relative", str, allow_empty=False),
        ),
    )
    runtime.register(
        "write_file",
        workspace.write_tool_text,
        spec=_spec(
            "write_file",
            "Atomically write a UTF-8 task file; overwrite must be explicit.",
            _parameter("relative", str, allow_empty=False),
            _parameter("content", str),
            _parameter("overwrite", bool, required=False),
        ),
    )
    runtime.register(
        "list_files",
        workspace.list_files,
        spec=_spec(
            "list_files",
            "List files below a task workspace directory.",
            _parameter("relative", str, required=False, allow_empty=False),
        ),
    )
    return runtime
