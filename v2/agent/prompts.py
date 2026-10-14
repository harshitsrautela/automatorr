"""Builds the natural-language task prompts that browser-use executes."""

from config import TARGET_FORM_URL
from data.models import Record


# Builds the full prompt that tells the agent to fill and submit the web form for one record.
# Takes a Record and returns the task text, with the record's fields listed as "key = value" lines.
def build_task_prompt(record: Record) -> str:
    fields = record.field_dict()

    field_lines = "\n".join(f"  - {key} = {value}" for key, value in fields.items())

    return f"""You are filling out a web form at {TARGET_FORM_URL}.

Enter the following field values exactly as given, matching each value to the
form field with the closest matching label (fields may not be in this order
on the page):

{field_lines}

Rules:
- Go to {TARGET_FORM_URL} first if you are not already there.
- Some fields (especially dropdowns) may render slowly. Wait for a dropdown's
  options to actually appear before attempting to select a value from it —
  do not assume it is ready immediately after clicking it open.
- Enter every field listed above. Do not skip a field because you are unsure
  which input matches it — pick the closest label match.
- Do not invent, guess, or fill in values that were not given above.
- After all fields are entered, submit the form.
- Do not retry submission repeatedly if it appears to fail — attempt submit
  once, note what happened, and stop.
"""


# Builds a narrow one-step prompt (one field or one action) for the live-agent fallback.
# Takes a field name, value and the old step instruction; returns a fill-field or single-action prompt.
def build_step_prompt(*, field_name: str | None, value: object | None, instruction: str) -> str:
    if field_name is not None:
        return f"""You are on a partially-filled-in web form (other fields may already be filled in correctly — do not touch them).

The last time this workflow ran, the step here was described as: "{instruction}"

Find the single form field for `{field_name}` and enter exactly this value:
  {field_name} = {value}

Rules:
- Only touch this one field. Do not click submit. Do not modify any other field.
- If this field is a dropdown, wait for its options to actually appear before selecting.
- Do not invent or adjust the value — enter it exactly as given above.
- Once this field is set, stop.
"""

    return f"""You are on a partially-filled-in web form (other fields may already be filled in correctly — do not touch them).
  
The last time this workflow ran, the step here was described as: "{instruction}"

Perform exactly that one action (for example: opening a dropdown, or submitting the
form if the instruction describes submission) and nothing else, then stop.

Rules:
- Do not fill in or modify any field.
- Do not retry repeatedly if it appears to fail — attempt it once, note what happened, and stop.
"""


# Builds a prompt to find an already-submitted person and fix one field through the edit form.
# Takes the match key field and value plus the field name and value to set; returns the prompt text.
def build_reconciliation_prompt(*, match_key_field: str, match_key_value: object, field_name: str, value: object) -> str:
    return f"""You are on DataDesk's People page (a list/table of existing people).

Find the existing person whose {match_key_field} is exactly: {match_key_value}
(use the search box if the list is long — search for "{match_key_value}").

Once you have found that person's row, click that row's edit action to open
the edit form for them — it should already be pre-filled with their existing
data. Do not create a new person.

In that edit form, find the field for `{field_name}` and set it to exactly:
  {field_name} = {value}

Rules:
- Only touch this one field in the edit form. Do not modify anything else.
- If this field is a dropdown, wait for its options to actually appear before selecting.
- Save the edit form once this field is set.
- If you cannot find this person, or cannot find an edit action for their row,
  stop and report exactly what you saw — do not create a new person and do not guess.
"""
