# Fix SeriesBible Validation Error

## Goal Description
The `SeriesBible` validation fails because the LLM sometimes returns `physical_description` for the character description field, which is not currently handled by the normalization logic in `src/core/director.py`. This change adds support for `physical_description` to ensure the `description` field is correctly populated.

## Proposed Changes
### Core
#### [MODIFY] [director.py](file:///Volumes/MainDisk/Dovelopment/AudiboundStudio/src/core/director.py)
- Add a check for `physical_description` in the character normalization loop in `create_series_bible`.

## Verification Plan
### Automated Tests
- I will create a reproduction script `reproduce_issue.py` that simulates the `create_series_bible` method with the problematic input (a dictionary with `physical_description`) and asserts that it fails before the fix and passes after the fix.
- Run `python3 reproduce_issue.py`.
