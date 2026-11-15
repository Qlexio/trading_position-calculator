# AGENTS.md

## Project

Turn a LibreOffice Calc `.ods` trading-position calculator into a locally hosted
web application.

The reference spreadsheet is `reference.ods` in the repository root. It is the
authority for calculation behavior.

## Scope

- The app is for a single user and runs on the user's computer.
- It is accessed through a local address such as `http://localhost:<port>`.
- Use Python for the initial implementation.
- The app takes manually entered inputs and displays calculated results.
- Keep the webpage simple and easy to read. UX/UI refinement is out of scope
  until the calculations are working reliably.
- The app performs calculations only. Do not add trading, order execution,
  market-data, or other external-service integrations unless requested.

## Calculation behavior

- The existing `.ods` spreadsheet is the reference for calculation behavior.
- “Preserve the formulas” means preserve the spreadsheet's calculated behavior
  and backend processing; it does not mean recreate the spreadsheet's UI.
- Before changing or improving a calculation, establish and verify the baseline
  behavior against the spreadsheet.
- Do not silently change formulas, rounding, units, defaults, or edge-case
  behavior. Record uncertainties and ask the user when the intended behavior
  is unclear.
- Keep calculation logic separate from web presentation where practical.

## Implementation approach

- Keep the initial implementation simple and easy to run locally.
- Manual re-entry of spreadsheet parameters is acceptable for the baseline.
- Do not add recursive or pre-filled dropdowns, spreadsheet import, or other
  convenience features unless requested.
- Avoid unnecessary dependencies and external services.
- Keep configuration and instructions clear enough for the user to run the app
  locally.

## Verification

- Add tests for calculation logic as it is implemented.
- Where possible, compare test cases and results with the spreadsheet.
- Include representative normal, boundary, and invalid-input cases.
- Clearly report any calculation that could not be verified against the
  spreadsheet.

## Working practices

- Inspect the existing project before making changes.
- Make focused changes that fit the agreed scope.
- `reference.ods` is internal documentation. Reading it to establish and
  verify baseline calculation behavior is expected and authorized; do not modify
  the file.
- `reference.ods` is listed in `.gitignore` and must never be committed,
  pushed, or shared outside this machine. Do not copy its contents into
  tracked files, logs, or commits.
- Ask before making significant scope, behavior, or dependency changes.
