"""
Turns a Record into the natural-language task instruction that browser-use
actually executes. Kept separate from runner.py on purpose — this is the file
you'll be iterating on constantly during testing, and it shouldn't be mixed
in with execution logic.
"""

from config import TARGET_FORM_URL
from data.models import Record


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
