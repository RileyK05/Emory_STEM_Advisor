# Course data needing personal review

Updated 2026-10-21. **Course Atlas is the primary source for course requirements**, following the user's decision. Captured registration restrictions take precedence over conflicting catalog fields and narrative section notes. The live database now uses `data/atlas/atlas-primary-spring-2027.jsonl`, prepared deterministically from the saved Spring 2027 Atlas captures. Original sources and citation chunks are retained as history.

## Corrections applied to the live database

| Course | Applied Atlas requirement |
|---|---|
| CS 171 | CS 170 or CS_OX 170 or equivalent transfer credit. The incorrect MUS 221 catalog rule is superseded. |
| MATH 412 | MATH 411 or equivalent transfer credit. The conflicting MATH 211/MATH_OX 211 catalog rule is superseded. |
| BIOL 325 | PSYC 110, PSYC_OX 110, BIOL 142, BIOL_OX 142, or equivalent transfer credit. |
| PSYC 325 | PSYC 110, PSYC_OX 110, BIOL 142, BIOL_OX 142, or equivalent transfer credit. |

All four earlier source-conflict holds were released in the same transaction as the corrected publication. Their evidence and release history are preserved. These four courses are no longer pending manual rule selection.

## Oxford and catalog coverage

Oxford is a separate campus in the same public Atlas, not a separate missing course collection. The published master contains **4,520 exact course identities**, including **185 Oxford identities**. **2,059 records use captured Atlas facts directly**; **2,461 records remain explicitly labeled catalog fallbacks** because matching details were absent from the captured term. Term absence does not establish that a course does not exist or is invalid.

Atlas's explicit Atlanta/Oxford alternatives are retained in prerequisite trees. Campus codes and suffixes remain distinguishable in search, follow-up chat, transcript normalization, and program rules. No undocumented credit equivalence is inferred. For example, Atlas references BIOL_OX 142 while the observed offering is BIOL_OX 142WE; the base/suffix credit relationship has not yet been encoded and must not be mistaken for evidence that the base course is invalid.

Forty-eight previously unresolved target identities now have records, covering 364 references in the old master. The rebuilt topic graph covers all 4,520 records. Historical catalog citations remain readable; current retrieval uses the Atlas-primary revision.

## Remaining work: engineering and evidence coverage

The generated section below is a concise inventory, not a request to manually inspect the whole catalog. Detailed diagnostic rows are available with `python -m scripts.manual.review_atlas_cleanup --details` from `backend/`.

- **Unparsed / ambiguous:** prerequisite wording is saved, but the deterministic parser cannot fully represent it. The new, broader master has 586 unparsed and 52 ambiguous records. This requires parser or source-interpretation work; it does not mean the course was absent in Spring or that its stated rule is wrong.
- **Unresolved exact targets:** 738 prerequisite/corequisite mentions refer to 216 distinct codes absent from this combined master. These counts include additional school records that were not in the original College import. Historical codes, uncaptured terms, and base/suffix relationships remain coverage tasks, not declarations of nonexistent courses.
- **Section differences:** HPM 590R and OAM 499R have different captured restrictions by class. Their exact conditions appear below. They require choosing the relevant section for enrollment advice, not choosing which Atlas page is truthful.
- **Additional terms:** complete Fall captures remain to be acquired and published for year-round coverage. Spring captures do not constitute a full academic-year schedule.

## Documented QTM rename

The [department history](https://datascience.emory.edu/about/index.html) confirms that QTM became Data and Decision Sciences in July 2025. The official [Data Science track checklist](https://datascience.emory.edu/documents/checklists-2025/data-science-track-checklist-fillable.pdf) explicitly identifies QTM versions of the listed courses as equivalent to DATASCI versions, including 110 and 210. This is documented historical-code evidence, not an unexplained missing-course finding. A dedicated evidence-linked historical-credit mapping still needs implementation; no blanket Oxford or cross-school equivalence is asserted.

## Targeted Fall 2026 follow-up

These public Atlas searches were manually checked on 2026-10-21. They are a targeted follow-up, not a full Fall import.

| Search | Finding |
|---|---|
| DATASCI 110 | Opened class 1880, Introduction to Scientific Methods, 3 credits; the detail page states Fall and Spring. |
| DATASCI 210 | Opened class 1888, Probability and Statistics, 4 credits, with lab 4386; the detail page states Fall and Spring. |
| QTM 110 / QTM 210 | Historical-code searches returned no matches; use the documented code transition above. |
| PE 230 / PE 232 | Exact keyword searches returned no matches in Fall 2026 or Spring 2027; retain as coverage questions. |
| Law | Fall 2026 search announced 126 courses; Spring 2027 had no results. |
| Theology | Fall 2026 search announced 161 courses; Spring 2027 had only placeholders. |

## Verified backup and validation

`data/backups/after-atlas-authority-20261021T122116Z.zip` contains the published database and all 2,793 referenced files. Every dump/file SHA-256 passed verification, including 2,409 capture references and all four released-hold proofs. The verified pre-change backup is `data/backups/before-atlas-authority-20261021T120633Z.zip`.

Validation: the full backend run passed 1,293 tests with two platform skips; final code-identity changes passed 102 targeted tests. Live checks verified the four corrected rules, Oxford retrieval, complete embeddings, retained historical chunks, released holds, and a current topic graph. Ruff passed.

<!-- BEGIN GENERATED ATLAS CLEANUP QUEUE -->
## Generated Atlas cleanup queue
Generated deterministically from the local course master and saved Spring 2027 captures. This is a review queue, not a verified replacement curriculum or enrollment decision.

### Master coverage
Parser states and unresolved target counts describe engineering coverage for this snapshot; they are not a student homework list. Oxford codes retain the `_OX` campus identity.

| Coverage measure | Count |
|---|---:|
| Courses with parsed prerequisite text | 603 |
| Records with parser status `none` | 3279 |
| Ambiguous parse cases | 52 |
| Unparsed cases | 586 |
| Distinct prerequisite/corequisite target codes absent from this master | 216 |
| Oxford campus course codes (`_OX`) | 185 |
| Other campus course codes | 4335 |

### Saved capture inventory
Counts cover saved JSON envelopes; grouped result cards are normal Atlas records.

| Capture coverage measure | Count |
|---|---:|
| Saved capture files | 2505 |
| Valid capture files | 2503 |
| Invalid final captures held from use | 0 |
| Superseded invalid index captures | 2 |
| Unusual exact code shapes | 0 |
| Grouped or section-variation cards | 429 |

### Atlas section restriction variants
These course records have distinct restriction text across cited Spring 2027 sections.

| Course | Career and section scope | Exact captured restriction |
|---|---|---|
| `HPM 590R` | Public Health / class 3105 | (no restriction text captured) |
| `HPM 590R` | Public Health / class 3525 | HPMMPH or HPMMPHOL students only. |
| `OAM 499R` | Undergraduate Business / class 4618 | This section is reserved for students in the BBA program. |
| `OAM 499R` | Undergraduate Business / class 4910 | (no restriction text captured) |
<!-- END GENERATED ATLAS CLEANUP QUEUE -->
