"""Records the live agent's cacheable click, input and select steps into the cache."""

import logging
from datetime import datetime, timezone
from typing import Any

from browser_use.agent.views import AgentHistory

from cache.fingerprint import compute_fingerprint
from cache.store import CachedStep, CacheStore
from evaluation.normalizers import FIELD_TYPE_NORMALIZERS, infer_field_type

logger = logging.getLogger(__name__)

# Only these actions have a reusable DOM locator and a Playwright equivalent
_CACHEABLE_VALUE_ACTIONS = ("input", "input_text")
_CACHEABLE_SELECT_ACTIONS = ("select_dropdown",)
_CACHEABLE_CLICK_ACTIONS = ("click",)


# Cuts long agent text down so one step event stays small.
# Takes a value and returns up to 600 characters of it as a string, or None if empty.
def _clip(value: Any, limit: int = 600) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Turns the agent's actions for one step into short readable strings like "click(index=12)".
# Takes the action list and returns a list of strings (empty if there are none).
def _describe_actions(actions: Any) -> list[str]:
    described: list[str] = []
    for action in actions or []:
        try:
            data = action.model_dump(exclude_unset=True)
        except Exception:
            continue
        for name, params in data.items():
            if isinstance(params, dict) and params:
                args = ", ".join(f"{k}={_clip(v, 80)}" for k, v in params.items())
                described.append(f"{name}({args})")
            else:
                described.append(name)
    return described


# Captures cacheable agent steps for one record run via its `callback` method.
# Matches typed values back to record fields and saves each replayable step to the CacheStore.
class StepRecorder:
    # Stores the recorder settings and initializes capture state.
    # Takes the CacheStore, workflow name, path variant, record fields and optional persist/event args.
    def __init__(
        self,
        store: CacheStore,
        *,
        workflow_name: str,
        path_variant: str,
        record_fields: dict[str, Any],
        auto_persist: bool = True,
        record_id: str | None = None,
        on_event=None,
    ):
        self.store = store
        self.workflow_name = workflow_name
        self.path_variant = path_variant
        self.record_fields = record_fields
        # False for the single-step fallback, where the orchestrator persists captures itself
        self.auto_persist = auto_persist
        self.record_id = record_id
        # Optional progress callback; its exceptions are swallowed
        self.on_event = on_event
        self.captured: list[CachedStep] = []
        self.steps_seen = 0
        # Typed or chosen values that were not copies of a record field (they were read from a page or worked out)
        self.unmatched = 0
        # Fields already matched this run, so repeated values map to the next field
        self.matched_fields: set[str] = set()

    # Handles one agent step: emits a progress event and caches any replayable actions.
    # Takes the browser state summary, the agent output and the step number; returns nothing.
    def callback(self, browser_state_summary, agent_output, step_num: int) -> None:
        self.steps_seen = max(self.steps_seen, step_num)

        instruction = getattr(agent_output, "next_goal", None) or getattr(agent_output, "memory", None) or ""
        if self.on_event is not None:
            try:
                self.on_event(
                    {
                        "type": "step",
                        "record_id": self.record_id,
                        "step_index": step_num,
                        "instruction": instruction,
                        "evaluation": _clip(getattr(agent_output, "evaluation_previous_goal", None)),
                        "memory": _clip(getattr(agent_output, "memory", None)),
                        "thinking": _clip(getattr(agent_output, "thinking", None)),
                        "actions": _describe_actions(getattr(agent_output, "action", None)),
                    }
                )
            except Exception:
                logger.exception("on_event callback raised - ignoring, must not break the run")

        selector_map = getattr(browser_state_summary.dom_state, "selector_map", None)
        if selector_map is None:
            return

        try:
            elements = AgentHistory.get_interacted_element(agent_output, selector_map)
        except Exception:
            logger.exception("Failed to derive interacted elements for step %s", step_num)
            return

        actions = getattr(agent_output, "action", None) or []

        for offset, (action, element) in enumerate(zip(actions, elements)):
            if element is None:
                continue  # non-interactive action (wait/scroll/done/extract_content/...) - not replayable

            try:
                action_data = action.model_dump(exclude_unset=True)
            except Exception:
                continue
            if not action_data:
                continue
            action_name = next(iter(action_data))
            params = action_data[action_name] or {}
            if not isinstance(params, dict):
                params = {}

            attributes = element.attributes or {}
            field_name = None
            resolved_action_type = action_name

            if action_name in _CACHEABLE_VALUE_ACTIONS or action_name in _CACHEABLE_SELECT_ACTIONS:
                field_name = self._match_field(params.get("text"))
                if field_name is None:
                    self.unmatched += 1
                    logger.debug(
                        "Step %s.%s: couldn't map typed/selected value back to a record field — not caching.",
                        step_num,
                        offset,
                    )
                    continue
                resolved_action_type = "input" if action_name in _CACHEABLE_VALUE_ACTIONS else action_name
            elif action_name in _CACHEABLE_CLICK_ACTIONS:
                if (attributes.get("type") or "").lower() == "checkbox":
                    field_name = self._match_checkbox_field(attributes)
                    resolved_action_type = "checkbox" if field_name is not None else "click"
                else:
                    resolved_action_type = "click"
            else:
                continue  # not a cacheable action type

            step = CachedStep(
                workflow_name=self.workflow_name,
                path_variant=self.path_variant,
                step_index=step_num,
                action_offset=offset,
                action_type=resolved_action_type,
                field_name=field_name,
                instruction=instruction,
                x_path=element.x_path,
                node_name=element.node_name or "",
                attributes=attributes,
                stable_hash=(str(element.stable_hash) if element.stable_hash is not None else None),
                fingerprint=compute_fingerprint(element.node_name, attributes),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            if self.auto_persist:
                self.store.upsert_step(step)
            self.captured.append(step)

    # Finds the unclaimed record field whose normalized value equals the typed value.
    # Takes the typed value and returns the field name, or None if nothing matches.
    def _match_field(self, typed_value: Any) -> str | None:
        if typed_value is None or typed_value == "":
            return None
        for field_name, expected in self.record_fields.items():
            if expected is None or field_name in self.matched_fields:
                continue
            field_type = infer_field_type(field_name)
            normalizer = FIELD_TYPE_NORMALIZERS[field_type]
            try:
                if normalizer(expected) == normalizer(typed_value):
                    self.matched_fields.add(field_name)
                    return field_name
            except Exception:
                continue
        return None

    # Matches a checkbox to a record field by its name or id attribute, ignoring a "field-" prefix.
    # Takes the element attributes and returns the record field name, or None if none matches.
    def _match_checkbox_field(self, attributes: dict[str, str]) -> str | None:
        candidates = [attributes.get("name"), attributes.get("id")]
        field_names_lower = {fn.lower(): fn for fn in self.record_fields}
        for candidate in candidates:
            if not candidate:
                continue
            normalized = candidate.lower()
            if normalized.startswith("field-"):
                normalized = normalized[len("field-") :]
            if normalized in field_names_lower:
                return field_names_lower[normalized]
        return None
