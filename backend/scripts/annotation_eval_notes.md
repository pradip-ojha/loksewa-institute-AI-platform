# Annotation Accuracy Eval Log

Protocol (run after ANY locator/validator/renderer change):

1. Keep 5–10 real reference sheets (Nepali + English handwriting, incl. one multi-page
   answer and one faint/tilted scan) that have already been fully checked once.
2. For each sheet: `POST /api/admin/subjective/sheets/{sheet_id}/reannotate`
   (re-runs ONLY the locator → validator → renderer on stored data — ~6 Gemini calls,
   no re-check), then open `GET /api/admin/subjective/sheets/{sheet_id}/debug-pdf`.
3. Score each sheet:
   - **U%** — underlines whose line actually touches the target handwriting
   - **T%** — ticks sitting beside (not on) the correct line
   - **C%** — comments legible, in margins/blank space, not overlapping writing
   - plus the `locator_plan.summary` numbers (targets_by_status, mean_match_score,
     pixel_fallbacks, retries) from `GET /api/admin/subjective/sheets/{id}/debug`.
4. Log one row per iteration below. Target: **U ≥ 90%** before relaxing any
   confidence threshold in `annotation_geometry.py`.

| Date | Change tested | Sheets | U% | T% | C% | mean match | pixel_fallbacks | Notes |
|------|---------------|--------|----|----|----|------------|-----------------|-------|
|      |               |        |    |    |    |            |                 |       |
