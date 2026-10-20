"""Post-review external LLM agents over the public semantic API only."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from arbiter.adapters.webcraft import PublicSemanticAdapter
from arbiter.llm import GatewayClient, GatewayError


EXTERNAL_METHODS = ("direct_llm", "react", "reflexion", "adam", "xenon")
_ACTIONS = frozenset({"search", "inventory", "mine", "craft_grid", "smelt", "check_name", "submit"})


class ModelOutputError(ValueError):
    """A safe, fixed error code that contains no model-authored text."""


_ARGUMENTS = {
    "search": {"query", "k"}, "inventory": set(), "mine": {"node_type"},
    "craft_grid": {"layout"}, "smelt": {"input_item", "fuel", "n"},
    "check_name": {"identifier"}, "submit": {"feasible"},
}


def public_system_prompt(method: str) -> str:
    """Fixed prompt shared by the post-review external-agent comparison."""
    if method not in EXTERNAL_METHODS:
        raise ValueError(f"unknown external method: {method}")
    memory = {
        "direct_llm": "Use only the latest observation and verified tool results.",
        "react": "Use the retained action-observation trace to choose the next step.",
        "reflexion": "Use the retained trace and a temporary lesson from failed progress.",
        "adam": "Maintain a mechanism dependency sketch; use a low-cost probe only when it resolves an uncertainty blocking progress.",
        "xenon": "Maintain a task dependency sketch and revise it when observations contradict an edge.",
    }[method]
    return "\n".join((
        "You operate a deterministic crafting environment through public tools.",
        "Return exactly one JSON object and no markdown.",
        "Schema: {\"action\": string, \"args\": object, \"intent\": \"commit\"|\"probe\", optional \"thought\": string}.",
        "Allowed actions: search, inventory, mine, craft_grid, smelt, check_name, submit.",
        'Example tool call: {"action":"inventory","args":{},"intent":"commit"}.',
        "search args are {query:string,k:integer}; k must be 1..10.",
        "mine args are {node_type:string}; craft_grid args are {layout:[nine string-or-null cells]}.",
        "smelt args are {input_item:string,fuel:string,n:positive integer}; check_name args are {identifier:string}.",
        "submit args are {feasible:true|false|null}. Do not claim success without an observed result.",
        "Use intent=probe only for a deliberate diagnostic mine or name check; otherwise use commit.",
        memory,
    ))


def _parse(content: str) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ModelOutputError("empty_content")
    text = content.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    try:
        action = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelOutputError("invalid_json") from exc
    required = {"action", "args", "intent"}
    optional = {"thought", "reflection"}
    if (not isinstance(action, dict) or not required <= set(action)
            or not set(action) <= required | optional):
        raise ModelOutputError("invalid_top_level_fields")
    if not isinstance(action["action"], str) or action["action"] not in _ACTIONS:
        raise ModelOutputError("unknown_action")
    if not isinstance(action["args"], dict):
        raise ModelOutputError("arguments_not_object")
    if not isinstance(action["intent"], str) or action["intent"] not in {"commit", "probe"}:
        raise ModelOutputError("invalid_intent")
    if any(key in action and (not isinstance(action[key], str) or len(action[key]) > 4000)
           for key in optional):
        raise ModelOutputError("invalid_trace_text")
    _validate_args(action)
    return action


def validate_external_canary(content: str) -> None:
    """Require a known harmless public-tool response before opening a run."""
    try:
        action = json.loads(content)
    except (TypeError, ValueError):
        raise GatewayError("external_canary_invalid_json") from None
    if action != {"action": "inventory", "args": {}, "intent": "commit"}:
        raise GatewayError("external-agent canary did not produce the inventory action")


def _validate_args(action: dict[str, Any]) -> None:
    name, args = action["action"], action["args"]
    if set(args) != _ARGUMENTS[name]:
        raise ModelOutputError("invalid_argument_fields")
    for key in {"query", "node_type", "input_item", "fuel", "identifier"} & set(args):
        if not isinstance(args[key], str) or not args[key].strip():
            raise ModelOutputError("invalid_string_argument")
    if name == "search" and (type(args["k"]) is not int or not 1 <= args["k"] <= 10):
        raise ModelOutputError("invalid_search_k")
    if name == "smelt" and (type(args["n"]) is not int or args["n"] <= 0):
        raise ModelOutputError("invalid_smelt_count")
    if name == "craft_grid" and (
        not isinstance(args["layout"], list) or len(args["layout"]) != 9
        or any(value is not None and (not isinstance(value, str) or not value)
               for value in args["layout"])
    ):
        raise ModelOutputError("invalid_layout")
    if name == "submit" and args["feasible"] is not None and type(args["feasible"]) is not bool:
        raise ModelOutputError("invalid_feasibility")


def _execute(api: PublicSemanticAdapter, action: dict[str, Any], *, method: str) -> Any:
    name, args = action["action"], action["args"]
    intent = action["intent"] if method == "adam" else "commit"
    if name == "search":
        query, k = args.get("query"), args.get("k")
        if not isinstance(query, str) or not isinstance(k, int) or not 1 <= k <= 10:
            raise GatewayError("invalid public search action")
        return api.search(query, k, intent=intent)
    if name == "inventory":
        return api.inventory(intent=intent)
    if name == "mine":
        value = args.get("node_type")
        if not isinstance(value, str):
            raise GatewayError("invalid mine action")
        return api.mine(value, intent=intent)
    if name == "craft_grid":
        layout = args.get("layout")
        if (not isinstance(layout, list) or len(layout) != 9
                or any(value is not None and not isinstance(value, str) for value in layout)):
            raise GatewayError("invalid craft_grid action")
        return api.craft_grid(layout, intent=intent)
    if name == "smelt":
        values = (args.get("input_item"), args.get("fuel"), args.get("n"))
        if not isinstance(values[0], str) or not isinstance(values[1], str) or not isinstance(values[2], int) or values[2] <= 0:
            raise GatewayError("invalid smelt action")
        return api.smelt(*values, intent=intent)
    if name == "check_name":
        value = args.get("identifier")
        if not isinstance(value, str):
            raise GatewayError("invalid check_name action")
        return api.check_name(value, intent=intent)
    value = args.get("feasible")
    if value not in {True, False, None}:
        raise GatewayError("invalid submit action")
    return api.submit({"feasible": value}, intent=intent)


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal": {"item": str(task["target"]["item"]), "count": int(task["target"]["count"])},
        "initial_inventory": task["standard_items"] + task.get("task_supplements", []),
    }


@dataclass(frozen=True)
class ExternalEpisode:
    submitted: bool
    reported_feasible: bool | None
    model_calls: int
    input_tokens: int
    output_tokens: int
    tool_actions: int
    infrastructure_error: str | None
    model_output_error: str | None
    diagnostics: tuple[dict[str, Any], ...] = ()
    primary_cost: float = 0.0


def run_external_episode(api: PublicSemanticAdapter, client: GatewayClient, *, method: str,
                         task: dict[str, Any], max_calls: int = 40,
                         max_actions: int = 90, thinking: str = "disabled") -> ExternalEpisode:
    """Run one external agent without exposing evaluator-only task fields."""
    if method not in EXTERNAL_METHODS:
        raise ValueError("unregistered external method")
    if max_calls < 1 or max_actions < 1:
        raise ValueError("nonpositive episode limits")
    trace: list[dict[str, Any]] = []
    observation: Any = api.inventory(intent="commit")
    tool_actions, calls, inputs, outputs = 1, 0, 0, 0
    lesson = ""
    invalid_outputs = 0
    diagnostics: list[dict[str, Any]] = []
    primary_cost = 0.0
    for _ in range(max_calls):
        context = trace[-8:] if method != "direct_llm" else trace[-2:]
        request = {"task": _public_task(task), "observation": observation, "trace": context}
        if method == "reflexion" and lesson:
            request["temporary_lesson"] = lesson
        try:
            response = client.chat(
                f"external_{method}",
                [{"role": "system", "content": public_system_prompt(method)},
                 {"role": "user", "content": json.dumps(request, sort_keys=True)}],
                # This gateway accounts for hidden reasoning tokens in the same
                # completion allowance; a short cap can yield an empty visible JSON.
                temperature=0.0, max_tokens=4096,
                json_object=True, thinking=thinking,
            )
            calls += 1
            usage = response.get("usage", {})
            inputs += int(usage.get("prompt_tokens", 0))
            outputs += int(usage.get("completion_tokens", 0))
            diagnostic = {
                "call": calls, "model": response.get("model"),
                "finish_reason": response.get("finish_reason"),
                "visible_characters": len(response["content"]) if isinstance(response.get("content"), str) else 0,
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            }
            diagnostics.append(diagnostic)
            if response.get("finish_reason") == "length":
                raise ModelOutputError("completion_truncated")
            action = _parse(response["content"])
            next_cost = (action["args"]["n"] if action["action"] == "smelt" else
                         int(action["action"] in {"mine", "craft_grid", "check_name"}))
            if primary_cost + next_cost > max_actions:
                diagnostic["error_code"] = "action_budget_exceeded"
                break
            observation = _execute(api, action, method=method)
        except GatewayError:
            # Transport, budget, model identity and canary failures stop the run.
            raise
        except ModelOutputError as exc:
            diagnostic["error_code"] = str(exc)
            invalid_outputs += 1
            observation = {"tool_error": str(exc), "retry": "Return one complete action object using the documented fields."}
            trace.append({"invalid_model_output": True, "observation": observation})
            if invalid_outputs >= 3:
                return ExternalEpisode(False, None, calls, inputs, outputs, tool_actions, None, str(exc), tuple(diagnostics), primary_cost)
            continue
        invalid_outputs = 0
        primary_cost += next_cost
        tool_actions += 1
        trace.append({"action": action, "observation": observation})
        if method == "reflexion" and isinstance(observation, dict) and observation.get("success") is False:
            lesson = "The previous action did not complete; choose a different prerequisite or verify it first."
        if action["action"] == "submit":
            return ExternalEpisode(True, action["args"]["feasible"], calls, inputs, outputs, tool_actions, None, None, tuple(diagnostics), primary_cost)
        if primary_cost >= max_actions:
            break
    api.submit({"feasible": None}, intent="commit")
    return ExternalEpisode(True, None, calls, inputs, outputs, tool_actions, None, None, tuple(diagnostics), primary_cost)
