# Weak-model tooling

The generator is replaceable. A stronger model is not the design; the
deterministic scaffolding around it is. The one capability that cannot be
faked by scaffolding is vision: something must be able to look at the rendered
page. That reviewer can be a separate, smaller connection from the writer.

## Implemented

### The reviewer is independent of the writer

`ArtifactHandler._visual_reviewer` selects the vision connection on its own:

- If the generation profile declares `vision`, it reviews.
- With routing enabled, any evaluated profile that declares `vision` can review,
  even when the writer is text-only. The reviewer is chosen for the artifact's
  data scope (`institutional_corpus` or `user_supplied_data`).
- With routing disabled, the statically configured provider reviews only when
  `LLM_VISION=true`.

A missing reviewer is a provenance note (`visual_review=no_vision`), not a
failed artifact. The review remains advisory.

### The reviewer sees the current HTML

The screenshot alone cannot preserve course codes, numbers or list items that
are clipped or unreadable. The review request now includes the exact current
HTML (bounded to 24,000 characters) and asks the reviewer to change only layout,
styling or markup. If the HTML is truncated, the reviewer must return issues
only, never a revision.

### A rejected revision gets one retry

A weak reviewer can return invalid markup. `_visual_review` validates the
revision; on failure it sends the rejection back once, with the same screenshot
and HTML, and accepts only a validated second revision. A second failure keeps
the original artifact and records `visual_review=invalid_revision`.

### A plan does not need a model to outline it

When the outline stage exhausts its bounded calls, a term-plan request falls
back to the matched program's required course lines and the deterministic
planner (`ArtifactPipeline._fallback_plan_outline`). The plan's sections are
already code-built; the per-term note is optional. The artifact records
`outline_fallback: true` in provenance. A text-only model that cannot call a
tool can therefore still publish a correct, ordered term plan.

### Common artifacts start from a base template

`app/generation/artifact_templates.py` defines the base structure for the
common requests: checklist, comparison document, report/guide, HTML page,
credit tracker, comparison sheet and prerequisite sheet. The outline call gets
the template as a hint. If the outline call still exhausts, the template becomes
the outline. Each section prompt carries the template's fill pattern, so a weak
model is told exactly what belongs in each section instead of inventing a
layout. Provenance records `template` and, when the fallback was used,
`template_fallback`.

## Verification

- Full backend suite: 1,755 passed, 2 skipped; `ruff check .` clean.
- Offline artifact benchmark: 70/70.
- Live on `deepseek-v4.1-flash`: A04 checklist, A06 comparison document, A09
  credit tracker, A10 comparison workbook, A11 prerequisite workbook, A13 HTML
  roadmap and A14 printable requirements page all pass with the templates.
- The plan-outline fallback test drives a provider that never calls a tool and
  still publishes a term plan. The checklist fallback test drives a provider
  that only fails the outline and still publishes the three template sections.
  The visual-review tests cover the reviewer selection, the current-HTML request,
  and the rejection retry text.

## Next

- Split a section that repeatedly truncates into smaller stage calls instead of
  only doubling the output allowance.
- Prefill checklist sections from program items, so a model that cannot call a
  tool at all still gets a checklist.
- Render document and workbook previews to images and review them with the same
  vision connection.
- Record per-stage model-call outcomes in the artifact provenance so a weak-model
  regression is visible without reading worker logs.
