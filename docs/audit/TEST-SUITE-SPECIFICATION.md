# Emory STEM Advisor LLM Test Suite Specification

**Target:** LLM Generation Subsystem (`minimax-m3.1-flash`, `deepseek-v4.1-flash`, etc.)  
**Ground Truth Authorities:**
- Emory University Course Atlas (`atlas.emory.edu`, snapshot: `data/atlas/atlas-primary-spring-2027.jsonl`)
- Official Emory College Catalog of Record (`data/programs/programs.jsonl`)
- Verified Faculty Advising Handbooks & Academic Policies

---

## 1. Overview and Test Suite Architecture

This document defines the complete suite of systematic tests to audit, benchmark, and detect factual errors in the Emory STEM Advisor LLM generation subsystem.

### Audit Dimensions
The test suite consists of **65 structured test probes** grouped into 7 distinct categories:
1. **STEM Majors (BS & BA Core Requirements)** (18 probes)
2. **Joint STEM Majors** (6 probes)
3. **STEM Minors** (11 probes)
4. **Academic Divisions & Cross-School Policies** (7 probes)
5. **Electives & Cluster Distributions** (6 probes)
6. **Emory College Academic Policies & Credit Invariants** (6 probes)
7. **Prerequisite Chains & Adversarial Edge Cases** (11 probes)

---

## 2. Invariant Criteria (Ground Truth Scoring Rules)

Every model response is graded against strict deterministic invariants:

| Criterion | Rule | Pass / Fail Condition |
| :--- | :--- | :--- |
| **Course Code Existence** | Every course code mentioned must exist in the Emory Course Atlas. | **FAIL** if any phantom course code is generated (e.g., `CS 165`, `CS 201`, `CS 210`, `CS 260`, `MATH 231`, `CHEM 151`, `PHYS 201`, `PHYS 254`). |
| **Core vs. Elective Distinction** | An elective must never be presented as a mandatory core requirement. | **FAIL** if `DATASCI 350` or `CS 355` is claimed to be a mandatory core course. |
| **Course Identification** | Course numbers must map to their actual titles and departments. | **FAIL** if `MATH 210` is described as "Linear Algebra" or "Multivariable Calculus" (it is *Advanced Calculus for Data Sciences*). |
| **Campus Identity & Prefixes** | Oxford College courses (`_OX`) must be clearly distinguished from Atlanta campus courses. | **FAIL** if Atlanta campus is claimed to have an `ASTR` prefix (Atlanta astronomy uses `PHYS 115/116/117/311/312`; Oxford uses `ASTR_OX 115`). |
| **Curricular Currency** | Requirements must reflect current programs, not obsolete sequences. | **FAIL** if *Chemistry Unbound* is replaced with pre-2017 `CHEM 141/142` and `CHEM 221/222`. |
| **Policy Precision** | Credit limits, deadlines, and grade rules must match exact college legislation. | **FAIL** if S/U limit is cited as 12 credits (it is 20), or S/U deadline as 30 days (it is 43rd instructional day). |
| **Prerequisite Directionality** | Prerequisite graphs are strictly deterministic. | **FAIL** if non-existent prerequisites are hallucinated (e.g., claiming `CS 253` requires `MATH 111`). |
| **Institutional Boundary Refusal** | System must refuse to guarantee admissions, live seat availability, or starting salaries. | **FAIL** if model claims live seat availability or guarantees career/salary outcomes. |

---

## 3. Detailed Test Catalog

### Group 1: STEM Majors (Core & Foundational Requirements)

#### `major-compsci-bs`
- **Category:** STEM Major
- **Subject:** Computer Science BS
- **Prompt:** *"What are the exact core course requirements and required math courses for the Computer Science BS at Emory University? List the specific course numbers and prerequisites."*
- **Atlas / Catalog Ground Truth:**
  - Core CS: `CS 170` (Intro I), `CS 171` (Intro II), `CS 224` (Foundations of CS), `CS 253` (Data Structures & Algorithms), `CS 255` (Computer Architecture), `CS 326` (Analysis of Algorithms), `CS 350` (Systems Programming).
  - Required Math: `MATH 111` (Calc I), `MATH 112` (Calc II), `MATH 221` (Linear Algebra).
- **Error Modes to Catch:** Hallucinating generic American CS codes (`CS 150`, `CS 201`, `CS 210`, `CS 250`); claiming `CS 355` is mandatory; omitting `CS 224` or `CS 350`.

#### `major-compsci-ba`
- **Category:** STEM Major
- **Subject:** Computer Science BA
- **Prompt:** *"What are the core requirements and credit hours for the Computer Science BA at Emory? How does it differ from the BS in terms of math and CS electives?"*
- **Atlas / Catalog Ground Truth:**
  - BA Core: `CS 170, 171, 224, 253, 255`.
  - Math for BA: Only `MATH 111` required (`MATH 112` and `MATH 221` not required for BA).
  - Elective difference: BA requires fewer upper-level CS electives and 1 lab science course; BS requires more electives and 2 lab science sequences.
- **Error Modes to Catch:** Conflating BA math with BS math; omitting the lab science requirement; hallucinating credit totals.

#### `major-math-bs`
- **Category:** STEM Major
- **Subject:** Mathematics BS
- **Prompt:** *"What are the required courses for the Mathematics BS at Emory University? Detail the foundational sequences, breadth requirements, and credit totals."*
- **Atlas / Catalog Ground Truth:**
  - Foundational: `MATH 111`, `MATH 112`, `MATH 211` (Multivariable Calculus), `MATH 221` (Linear Algebra), `MATH 250` (Foundations of Mathematics).
  - Advanced Core: Abstract Algebra (`MATH 321` or `421`) and Analysis (`MATH 311` or `411`).
  - CS requirement: `CS 170`.
- **Error Modes to Catch:** Inventing `MATH 231` (Differential Equations is `MATH 212`); claiming `MATH 250` is optional; omitting the `CS 170` computing requirement.

#### `major-math-ba`
- **Category:** STEM Major
- **Subject:** Mathematics BA
- **Prompt:** *"What are the required courses for the Mathematics BA at Emory? How many total credit hours and electives are required?"*
- **Atlas / Catalog Ground Truth:**
  - Core: `MATH 111, 112, 211, 221, 250`, plus one of `MATH 318, 321, 328, 346, 351`.
  - Total credits: ~38 credits in mathematics.
- **Error Modes to Catch:** Requiring full Analysis + Abstract Algebra year sequences for the BA; inventing course titles.

#### `major-applied-math-bs`
- **Category:** STEM Major
- **Subject:** Applied Mathematics BS
- **Prompt:** *"What are the specific requirements for the Applied Mathematics BS at Emory? Which computing and applied tracks are required or accepted?"*
- **Atlas / Catalog Ground Truth:**
  - Foundational: `MATH 111, 112, 211, 212` (Diff Eq), `221, 250`.
  - Computing: `CS 170`.
  - Advanced: `MATH 315` (Numerical Analysis), `MATH 351` (Partial Diff Eq) or `MATH 361` (Probability).
- **Error Modes to Catch:** Conflating with Pure Math BS; omitting `MATH 212` or `MATH 315`.

#### `major-biology-bs`
- **Category:** STEM Major
- **Subject:** Biology BS
- **Prompt:** *"What are the requirements for the Biology BS at Emory University? List the introductory core, secondary science requirements (chemistry, physics, math), and elective hours."*
- **Atlas / Catalog Ground Truth:**
  - Introductory Core: `BIOL 141` & `141L`, `BIOL 142` & `142L`.
  - Secondary Sciences: Chemistry (`CHEM 150/150L, 202/202L, 203/203L`); Physics (`PHYS 141/142` or `151/152`); Math (`MATH 111` or `QTM 100`).
  - Upper-level: Minimum 20 elective hours across Column A, B, and C with at least two upper-level labs.
- **Error Modes to Catch:** Listing pre-2017 `CHEM 141/142`; hallucinating `BIOL 245` or non-existent introductory numbers.

#### `major-biology-ba`
- **Category:** STEM Major
- **Subject:** Biology BA
- **Prompt:** *"What are the requirements for the Biology BA at Emory University? How does it differ from the Biology BS in secondary science requirements?"*
- **Atlas / Catalog Ground Truth:**
  - Biology core same as BS (`BIOL 141/141L, 142/142L`).
  - Secondary science differences: BA requires only 1 year of Chemistry (`CHEM 150/150L` and `CHEM 202/202L`); Physics is NOT required for the BA; Math requires 1 course (`MATH 111` or `QTM 100`).
- **Error Modes to Catch:** Claiming Physics is required for the Biology BA; requiring organic chemistry II (`CHEM 203`) for the BA.

#### `major-chem-bs`
- **Category:** STEM Major
- **Subject:** Chemistry BS
- **Prompt:** *"What are the required courses for the Chemistry BS at Emory under the Chemistry Unbound curriculum? Detail the 100, 200, and 300 level sequence."*
- **Atlas / Catalog Ground Truth:**
  - *Chemistry Unbound* sequence: `CHEM 150 + 150L` (Structure & Properties), `CHEM 202 + 202L` (Principles of Reactivity) or `CHEM 202Z + 202ZL`, `CHEM 203 + 203L` (Advanced Reactivity), `CHEM 204 + 204L` (Macromolecules), `CHEM 205 + 205L` (Light and Matter).
  - Advanced: Integrated laboratories (`CHEM 300L`), physical chemistry, and chemistry electives.
- **Error Modes to Catch:** Reverting to obsolete General Chemistry `CHEM 141/142` or Organic Chemistry `CHEM 221/222`; hallucinating `CHEM 151`.

#### `major-chem-ba`
- **Category:** STEM Major
- **Subject:** Chemistry BA
- **Prompt:** *"What are the requirements for the Chemistry BA at Emory University? How does the laboratory requirement differ from the BS?"*
- **Atlas / Catalog Ground Truth:**
  - Unbound foundation: `CHEM 150, 202, 203, 204, 205` with accompanying foundational labs.
  - Differs from BS: Does not require the advanced integrated lab sequence (`CHEM 300L/301L`) or as many advanced 300+ chemistry electives.
- **Error Modes to Catch:** Requiring calculus-based physics sequence only; confusing lab requirements between BA and BS.

#### `major-physics-bs`
- **Category:** STEM Major
- **Subject:** Physics BS
- **Prompt:** *"What are the requirements for the Physics BS at Emory? List the introductory sequence options, core intermediate courses, and required math co-requisites."*
- **Atlas / Catalog Ground Truth:**
  - Introductory: `PHYS 151` and `PHYS 152` (Calculus-based).
  - Intermediate Core: `PHYS 212` (Computational Modeling), `PHYS 220` (Math Methods for Physics), `PHYS 253` (Modern Physics with Lab).
  - Advanced Core: `PHYS 361` (Classical Mechanics), `PHYS 365` (Electricity & Magnetism), `PHYS 444` (Quantum Mechanics), `PHYS 461` (Thermal & Statistical Physics), `PHYS 444L`.
  - Math: `MATH 111, 112, 211, 212`.
- **Error Modes to Catch:** Listing `PHYS 201` or `PHYS 254` (non-existent); claiming life-science physics `PHYS 141/142` satisfies the BS core.

#### `major-physics-ba`
- **Category:** STEM Major
- **Subject:** Physics BA
- **Prompt:** *"What are the requirements for the Physics BA at Emory? How does it differ from the BS?"*
- **Atlas / Catalog Ground Truth:**
  - Requires `PHYS 151, 152, 212, 253`, plus fewer 300/400 theoretical physics courses (`PHYS 361` or `365`).
  - Total credit hours: ~34 credits compared to ~54 for the BS.
- **Error Modes to Catch:** Requiring full senior quantum mechanics (`PHYS 444`) and thermal physics (`PHYS 461`) for the BA.

#### `major-physics-astronomy-bs`
- **Category:** STEM Major
- **Subject:** Physics and Astronomy BS
- **Prompt:** *"What are the required physics and astronomy courses for the joint Physics and Astronomy BS at Emory University?"*
- **Atlas / Catalog Ground Truth:**
  - Physics Core: `PHYS 151, 152, 212, 220, 253, 361, 365`.
  - Astronomy Courses: `PHYS 116` (Intro Astronomy w/ Lab), `PHYS 311` (Astrophysics I), `PHYS 312` (Astrophysics II).
- **Error Modes to Catch:** Hallucinating `ASTR 101/102/103` on the Atlanta campus; omitting the `PHYS` prefix on astrophysics courses.

#### `major-biophysics-bs`
- **Category:** STEM Major
- **Subject:** Biophysics BS
- **Prompt:** *"What are the requirements for the Biophysics BS at Emory University? What biology, chemistry, and physics sequences are required?"*
- **Atlas / Catalog Ground Truth:**
  - Physics: `PHYS 151, 152, 212, 253, 344, 444`.
  - Chemistry: `CHEM 150/150L, 202/202L`.
  - Biology: `BIOL 141/141L`.
  - Math: `MATH 111, 112, 211, 212`.
- **Error Modes to Catch:** Requiring the full 4-semester chemistry sequence; omitting computational physics `PHYS 212`.

#### `major-engineering-sciences-bs`
- **Category:** STEM Major
- **Subject:** Engineering Sciences BS
- **Prompt:** *"What are the core course requirements for the Engineering Sciences BS at Emory University? Detail the foundational physics, math, and chemistry tracks."*
- **Atlas / Catalog Ground Truth:**
  - Core: `PHYS 151, 152, 212, 220, 222` (Engineering Mechanics), `PHYS 234` (Digital Electronics).
  - Math: `MATH 111, 112, 211, 212`.
  - Chemistry: `CHEM 150/150L`.
  - Computer Science: `CS 170`.
- **Error Modes to Catch:** Hallucinating that Emory has an ABET-accredited stand-alone mechanical/electrical engineering school (it is an Engineering Sciences degree within Emory College).

#### `major-nbb-bs`
- **Category:** STEM Major
- **Subject:** Neuroscience and Behavioral Biology (NBB) BS
- **Prompt:** *"What are the core requirements and foundation courses for the Neuroscience and Behavioral Biology (NBB) BS at Emory University?"*
- **Atlas / Catalog Ground Truth:**
  - Foundational: `BIOL 141/141L, 142/142L`, `CHEM 150/150L, 202/202L`, `QTM 100` (or `MATH 111`).
  - NBB Core: `NBB 201` (Foundations of Behavior), `NBB 301` (Neurobiology), `NBB 302` (Behavioral Neuroscience), `NBB 401` or `402` (Senior Seminar).
  - Electives: 4 electives from Columns A, B, and C.
- **Error Modes to Catch:** Hallucinating `NBB 101`; claiming organic chemistry II (`CHEM 203`) is in the core; misnumbering `NBB 201/301/302`.

#### `major-envs-bs`
- **Category:** STEM Major
- **Subject:** Environmental Sciences BS
- **Prompt:** *"What are the core requirements and secondary science requirements for the Environmental Sciences BS at Emory?"*
- **Atlas / Catalog Ground Truth:**
  - Core: `ENVS 130` (Environmental Sciences), `ENVS 131` (Intro to ENVS Field Studies), `ENVS 390` (Seminar).
  - Quantitative: `QTM 100` and `MATH 111`.
  - Secondary Sciences for BS: Minimum 4 courses outside ENVS (e.g., `BIOL 141/142`, `CHEM 150/202`, `PHYS 141/142`).
- **Error Modes to Catch:** Hallucinating `ENVS 101` or `ENVS 200` as introductory course (it is `ENVS 130`).

#### `major-envs-ba`
- **Category:** STEM Major
- **Subject:** Environmental Sciences BA
- **Prompt:** *"What are the requirements for the Environmental Sciences BA at Emory, and how does its secondary science requirement compare to the BS?"*
- **Atlas / Catalog Ground Truth:**
  - Core is same (`ENVS 130, 131, 390`).
  - Differs from BS: BA requires only 2 secondary science courses outside ENVS, whereas the BS requires 4 courses in rigorous sequences.
- **Error Modes to Catch:** Claiming BA requires the same secondary science depth as BS.

#### `major-qss-bs`
- **Category:** STEM Major
- **Subject:** Quantitative Sciences / Data Science BS
- **Prompt:** *"What are the exact core course requirements for the Data Science BS at Emory University? List the QTM/DATASCI core courses."*
- **Atlas / Catalog Ground Truth:**
  - Core: `QTM/DATASCI 110` (Scientific Methods), `MATH 210` (Calculus for Data Science), `QTM/DATASCI 150` & `151` (Computing I & II), `QTM/DATASCI 210` (Probability & Statistics), `QTM/DATASCI 220` (Regression Analysis).
  - Electives: Three 300+ advanced electives.
- **Error Modes to Catch:** Falsely including `DATASCI 350` as a mandatory core course (it is an elective); claiming `QTM 100` satisfies the core.

---

### Group 2: Joint STEM Majors

#### `major-math-cs-bs`
- **Category:** Joint Major
- **Subject:** Mathematics & Computer Science BS
- **Prompt:** *"What are the requirements for the joint Mathematics & Computer Science BS at Emory University? List all core CS and Math courses."*
- **Atlas / Catalog Ground Truth:**
  - CS Core: `CS 170, 171, 224, 253, 255, 326`.
  - Math Core: `MATH 111, 112, 211, 221, 250`, plus one of `MATH 311, 315, 321, 351`.
- **Error Modes to Catch:** Omitting `CS 224` (Foundations) or `MATH 250`; requiring `CS 350` (which is in pure CS BS, not joint Math/CS).

#### `major-econ-cs`
- **Category:** Joint Major
- **Subject:** Economics & Computer Science Joint BA
- **Prompt:** *"What are the requirements for the joint Economics and Computer Science major at Emory? Which economics core courses and CS core courses are mandated?"*
- **Atlas / Catalog Ground Truth:**
  - Econ Core: `ECON 101` (Micro), `ECON 112` (Macro), `ECON 201` (Intermediate Micro), `ECON 212` (Intermediate Macro), `ECON 220` (Econometrics) or `ECON 320`.
  - CS Core: `CS 170, 171, 224, 253, 326` or `370`.
  - Math: `MATH 111`.
- **Error Modes to Catch:** Misnaming `MATH 210` as Linear Algebra; omitting intermediate economics core; hallucinating `CS 201`.

#### `major-econ-math-ba`
- **Category:** Joint Major
- **Subject:** Economics & Mathematics Joint BA
- **Prompt:** *"What are the requirements for the joint Economics and Mathematics BA at Emory University? List the required Econ and Math sequences."*
- **Atlas / Catalog Ground Truth:**
  - Econ: `ECON 101, 112, 201, 212, 320`.
  - Math: `MATH 111, 112, 211, 221, 250`, plus advanced math electives (`MATH 361` or `362`).
- **Error Modes to Catch:** Describing `MATH 210` as Multivariable Calculus; omitting `MATH 250`.

#### `major-math-pols-ba`
- **Category:** Joint Major
- **Subject:** Mathematics & Political Science BA
- **Prompt:** *"What are the requirements for the joint Mathematics & Political Science BA at Emory? What math courses and political theory/methods courses are required?"*
- **Atlas / Catalog Ground Truth:**
  - Math: `MATH 111, 112, 211, 221, 250, 361`.
  - Political Science: `POLS 100`, `POLS 208` (Political Science Research Methods), plus theory and institutional electives.
- **Error Modes to Catch:** Omitting `POLS 208`; inventing political science numbers like `POLS 101`.

#### `major-applied-math-stats-bs`
- **Category:** Joint Major
- **Subject:** Applied Math & Statistics BS
- **Prompt:** *"What are the requirements for the Applied Mathematics & Statistics BS (joint with QTM/Data Science) at Emory? List the required QTM and Math courses."*
- **Atlas / Catalog Ground Truth:**
  - Math: `MATH 111, 112, 211, 212, 221, 250`.
  - Statistics/QTM: `MATH 361, 362`, `QTM/DATASCI 220`, and advanced statistical electives.
- **Error Modes to Catch:** Conflating pure Math BS with Applied Math & Stats.

#### `major-chem-biol-joint`
- **Category:** Joint Major
- **Subject:** Chemistry / Biology Cross-Departmental Tracks
- **Prompt:** *"Does Emory offer a combined or joint major in Chemistry and Biology? What are the course overlaps if a student pursues both or a biochemistry focus?"*
- **Atlas / Catalog Ground Truth:**
  - Emory does not have a formal joint "Chem/Bio" major name; students pursue either the Chemistry BS (Biochemistry Track) or the Biology BS with chemistry concentration.
- **Error Modes to Catch:** Hallucinating an official stand-alone "Biochemistry Major" department.

---

### Group 3: STEM Minors

#### `minor-compsci`
- **Category:** STEM Minor
- **Subject:** Computer Science Minor
- **Prompt:** *"What are the specific requirements to earn a Computer Science Minor at Emory University? List the exact required courses and elective credits."*
- **Atlas / Catalog Ground Truth:**
  - Core: `CS 170`, `CS 171`, `CS 224`, `CS 253`.
  - Electives: Two additional CS courses at the 200-level or above (at least one at 300-level).
  - Total credits: 6 courses (~21 credits).
- **Error Modes to Catch:** Claiming `MATH 111` or `MATH 112` is required for the CS minor (math is not formally part of the minor core); inventing `CS 201`.

#### `minor-ai`
- **Category:** STEM Minor
- **Subject:** Artificial Intelligence Minor
- **Prompt:** *"What are the requirements for the Artificial Intelligence (AI) Minor at Emory University? Which foundational computing courses, core AI courses, and elective tracks count?"*
- **Atlas / Catalog Ground Truth:**
  - Foundation: `CS 170, 171`, and `CS 253` (or approved data structures).
  - Core AI: `CS 334` (Machine Learning).
  - Electives: 2 courses from Category A (technical) and Category B (humanities/social impacts).
- **Error Modes to Catch:** Omitting `ECON 315` or `ECON 320` as approved Category A technical electives; claiming minor requires calculus III.

#### `minor-math`
- **Category:** STEM Minor
- **Subject:** Mathematics Minor
- **Prompt:** *"What are the requirements for the Mathematics Minor at Emory University? How many credit hours and which specific 200-level courses are required?"*
- **Atlas / Catalog Ground Truth:**
  - Foundation: `MATH 111, 112, 211, 221`.
  - Electives: Two mathematics courses at the 200-level or higher (at least one at 300-level).
- **Error Modes to Catch:** Hallucinating `MATH 231`; requiring `MATH 250` for the minor.

#### `minor-applied-math`
- **Category:** STEM Minor
- **Subject:** Applied Mathematics Minor
- **Prompt:** *"What are the requirements for the Applied Mathematics Minor at Emory? Which differential equations and computational courses are accepted?"*
- **Atlas / Catalog Ground Truth:**
  - Core: `MATH 111, 112, 211, 212, 221`, plus one applied elective (`MATH 315, 351, 361`, or `CS 170`).
- **Error Modes to Catch:** Omitting `MATH 212` (Diff Eq).

#### `minor-datasci`
- **Category:** STEM Minor
- **Subject:** Data Science Minor
- **Prompt:** *"What are the requirements for the Data Science Minor at Emory University? What are the prerequisite computing courses?"*
- **Atlas / Catalog Ground Truth:**
  - Core: `QTM/DATASCI 110`, `QTM/DATASCI 150, 151`, `QTM/DATASCI 210, 220`, plus one 300-level QTM elective.
- **Error Modes to Catch:** Claiming `DATASCI 350` is mandatory; omitting `QTM 151`.

#### `minor-physics`
- **Category:** STEM Minor
- **Subject:** Physics Minor
- **Prompt:** *"What are the requirements for the Physics Minor at Emory? Which introductory sequence and upper-level courses are required?"*
- **Atlas / Catalog Ground Truth:**
  - Sequence: `PHYS 151, 152` (or `141, 142` with permission), `PHYS 253`, plus two courses at the 200-level or above.
- **Error Modes to Catch:** Inventing `PHYS 201` or `PHYS 254`.

#### `minor-astronomy`
- **Category:** STEM Minor
- **Subject:** Astronomy Minor
- **Prompt:** *"What are the requirements for the Astronomy Minor at Emory University? Which astronomy and physics courses count?"*
- **Atlas / Catalog Ground Truth:**
  - Courses: `PHYS 116`, either `PHYS 141/142` or `PHYS 151/152`, `PHYS 253`, and either `PHYS 311` (Astrophysics I) or `PHYS 312` (Astrophysics II).
- **Error Modes to Catch:** Hallucinating Atlanta `ASTR 101/102/103` course codes instead of `PHYS 116/311/312`.

#### `minor-chem`
- **Category:** STEM Minor
- **Subject:** Chemistry Minor
- **Prompt:** *"What are the requirements for the Chemistry Minor at Emory University under the Chemistry Unbound curriculum?"*
- **Atlas / Catalog Ground Truth:**
  - Courses: `CHEM 150/150L, 202/202L, 203/203L, 204/204L` (or `205/205L`), plus one elective course.
- **Error Modes to Catch:** Citing obsolete `CHEM 141/142`.

#### `minor-biol`
- **Category:** STEM Minor
- **Subject:** Biology Minor
- **Prompt:** *"What are the requirements for the Biology Minor at Emory? List the required introductory courses and elective hours."*
- **Atlas / Catalog Ground Truth:**
  - Core: `BIOL 141/141L, 142/142L`, plus minimum 12 elective hours at the 200-level or above.
- **Error Modes to Catch:** Requiring organic chemistry or physics for the biology minor.

#### `minor-envs`
- **Category:** STEM Minor
- **Subject:** Environmental Sciences Minor
- **Prompt:** *"What are the requirements for the Environmental Sciences Minor at Emory? List the required introductory course and elective credits."*
- **Atlas / Catalog Ground Truth:**
  - Core: `ENVS 130` and `ENVS 131`, plus 4 elective courses in ENVS.
- **Error Modes to Catch:** Inventing `ENVS 101` or `ENVS 200`.

#### `minor-sustainability`
- **Category:** STEM Minor
- **Subject:** Sustainability Sciences Minor
- **Prompt:** *"What are the requirements for the Sustainability Sciences Minor at Emory? Which foundational and elective courses are required?"*
- **Atlas / Catalog Ground Truth:**
  - Foundational: `ENVS 130` or `ENVS 140`, plus interdisciplinary sustainability core and electives.
- **Error Modes to Catch:** Confusing with the ENVS minor.

---

### Group 4: Academic Divisions & Cross-School Policies

#### `school-oxford-cs`
- **Category:** Cross-School
- **Subject:** Oxford College CS Equivalency
- **Prompt:** *"Does taking CS_OX 170 and CS_OX 171 at Oxford College satisfy the prerequisites for Atlanta campus CS 253 at Emory University? Are Oxford courses equivalent to Atlanta courses?"*
- **Atlas / Catalog Ground Truth:**
  - Oxford courses with `_OX` suffix (`CS_OX 170, 171`) are 100% direct institutional equivalents to Atlanta courses (`CS 170, 171`) and directly fulfill the prerequisite for `CS 253`.
- **Error Modes to Catch:** Claiming Oxford courses do not transfer or require individual departmental petition.

#### `school-oxford-transition`
- **Category:** Cross-School
- **Subject:** Oxford College Continuation
- **Prompt:** *"How do students at Oxford College transition to Emory College of Arts and Sciences to complete a STEM major? What degree do they earn at Oxford and when do they declare their major?"*
- **Atlas / Catalog Ground Truth:**
  - Oxford students earn an Associate of Arts (AA) degree after 4 semesters (~64 credits) and automatically continue (transition) to Atlanta campus without reapplying; majors are declared at the end of sophomore year or upon arrival in Atlanta.
- **Error Modes to Catch:** Claiming Oxford students must "apply for transfer" to Emory College.

#### `school-goizueta-joint`
- **Category:** Cross-School
- **Subject:** Goizueta Business School Joint STEM
- **Prompt:** *"Can an Emory student pursue a double major between a STEM major in Emory College (like Computer Science or Applied Math) and the BBA program in Goizueta Business School? What is the official policy on joint/secondary majors?"*
- **Atlas / Catalog Ground Truth:**
  - A student whose primary degree is the BBA in Goizueta Business School can pursue a **secondary major** in Emory College (such as Computer Science, Math, or QSS). However, an Emory College student cannot take a "double major" in Business without matriculating into Goizueta.
- **Error Modes to Catch:** Claiming double majors between BBA and Emory College are completely prohibited.

#### `school-medicine-premed`
- **Category:** Cross-School
- **Subject:** Emory School of Medicine Pre-Med Requirements
- **Prompt:** *"What coursework does the Emory University School of Medicine require for admission to its MD program? Detail the required biology, chemistry, physics, math, and English courses."*
- **Atlas / Catalog Ground Truth:**
  - Emory SOM requires: Physical Science (8 sem hrs w/ lab), Biology (8 sem hrs w/ lab), Chemistry (minimum 8 sem hrs w/ lab including organic/biochem), Humanities/Social Sciences (18 sem hrs including 6 hrs English/writing).
- **Error Modes to Catch:** Inventing non-existent specific course numbers as absolute mandates.

#### `school-laney-4plus1`
- **Category:** Cross-School
- **Subject:** Laney Graduate School 4+1 BS/MS
- **Prompt:** *"What are the rules for Emory's 4+1 BS/MS programs (such as in Computer Science or Cancer Biology)? How many credit hours can be double-counted between the undergraduate and master's degrees?"*
- **Atlas / Catalog Ground Truth:**
  - Laney Graduate School strictly limits double-counting between the undergraduate degree and master's degree to a **maximum of 6 credit hours** (or 2 courses).
- **Error Modes to Catch:** Hallucinating that 12 or 16 credit hours can be double-counted.

#### `school-dual-degree-gt`
- **Category:** Cross-School
- **Subject:** Dual-Degree Engineering (Georgia Tech)
- **Prompt:** *"How does the Emory Dual-Degree Engineering Program with Georgia Tech work? How many years are spent at each institution and what degrees are awarded?"*
- **Atlas / Catalog Ground Truth:**
  - 3:2 (or 4:2) program: 3 years at Emory College (completing general education and a STEM major foundation) + 2 years at Georgia Tech. Awards a BA/BS from Emory and a BS in Engineering from Georgia Tech.
- **Error Modes to Catch:** Claiming Georgia Tech awards the Emory degree; claiming 1 year at Tech.

#### `school-nursing-prereq`
- **Category:** Cross-School
- **Subject:** Nell Hodgson Woodruff School of Nursing
- **Prompt:** *"Can an Emory College student transfer to the School of Nursing? What prerequisite courses in biology and statistics must they complete first?"*
- **Atlas / Catalog Ground Truth:**
  - Requires Anatomy & Physiology (`BIOL 205/206` or equivalent), Microbiology (`BIOL 240`), Statistics (`QTM 100`), Nutrition, and Human Development.
- **Error Modes to Catch:** Hallucinating course numbers.

---

### Group 5: Electives & Cluster Distributions

#### `elective-cs-300-400`
- **Category:** Elective
- **Subject:** CS 300/400 Electives & Research
- **Prompt:** *"Which 300-level and 400-level courses count as electives for the Computer Science BS at Emory? Do independent study courses like CS 495 or research courses count toward the major elective requirement?"*
- **Atlas / Catalog Ground Truth:**
  - Electives must be 300+ CS courses. Independent study (`CS 495`) and directed research (`CS 499`) are capped: at most 3 credit hours can satisfy major elective requirements without special petition.
- **Error Modes to Catch:** Claiming unlimited research/independent study credits count toward major electives.

#### `elective-ai-minor-econ`
- **Category:** Elective
- **Subject:** AI Minor Economics Electives
- **Prompt:** *"Do any Economics courses count toward the Artificial Intelligence Minor at Emory University? If so, which ones?"*
- **Atlas / Catalog Ground Truth:**
  - `ECON 315` (Economics of Artificial Intelligence) and `ECON 320` (Econometrics) are officially designated as Category A (technical) electives for the AI Minor.
- **Error Modes to Catch:** Falsely claiming that NO economics courses count, or hallucinating `ECON 210`.

#### `elective-datasci-300`
- **Category:** Elective
- **Subject:** Data Science 300-Level Elective Pool
- **Prompt:** *"What courses satisfy the 300-level elective requirement for the Data Science BS at Emory? Does DATASCI 350 count as one of these electives?"*
- **Atlas / Catalog Ground Truth:**
  - Yes, `DATASCI 350` (Data Science Computing) is an approved elective in the 300-level elective pool, along with courses like `QTM 315, 329, 345, 385`.
- **Error Modes to Catch:** Claiming `DATASCI 350` is disqualified from counting as an elective.

#### `elective-biol-columns`
- **Category:** Elective
- **Subject:** Biology Upper-Level Column Requirements
- **Prompt:** *"What are the three distribution columns (Column A, B, and C) for the Biology BS at Emory, and give one course example for each?"*
- **Atlas / Catalog Ground Truth:**
  - Column A: Cell and Molecular Biology (e.g., `BIOL 250` Cell Bio, `BIOL 264` Genetics).
  - Column B: Organismal Biology (e.g., `BIOL 247` Ecology, `BIOL 330` Animal Behavior).
  - Column C: Evolutionary and Population Biology (e.g., `BIOL 347` Disease Ecology, `BIOL 360` Evolution).
- **Error Modes to Catch:** Inventing columns or hallucinating course titles.

#### `elective-chem-integrated-lab`
- **Category:** Elective
- **Subject:** Integrated Chemistry Lab Electives
- **Prompt:** *"What is CHEM 300L at Emory, what are its prerequisites, and how does it fit into the Chemistry major?"*
- **Atlas / Catalog Ground Truth:**
  - `CHEM 300L` is *Integrated Advanced Chemistry Lab I* (2 credits), required for the BS, with prerequisites `CHEM 204L` and `CHEM 205L`.
- **Error Modes to Catch:** Claiming it is a 4-credit lecture course.

#### `elective-physics-advanced-lab`
- **Category:** Elective
- **Subject:** Physics Advanced Lab Options
- **Prompt:** *"What advanced laboratory courses are offered in the Emory Physics Department for physics majors?"*
- **Atlas / Catalog Ground Truth:**
  - `PHYS 253` (Modern Physics w/ Lab), `PHYS 444L` (Advanced Quantum Lab), `PHYS 311/312` (Astrophysics Labs).
- **Error Modes to Catch:** Inventing lab numbers like `PHYS 301L`.

---

### Group 6: Emory College Academic Policies & Credit Invariants

#### `policy-su-cap`
- **Category:** Academic Policy
- **Subject:** S/U Credit Limit
- **Prompt:** *"What is the maximum number of credit hours an Emory College student can take on an S/U (Satisfactory/Unsatisfactory) grading basis toward their graduation requirements?"*
- **Atlas / Catalog Ground Truth:**
  - Maximum of **20 credit hours** taken on an S/U basis may count toward the 127 hours required for graduation.
- **Error Modes to Catch:** Stating 12 or 16 hours.

#### `policy-su-deadline`
- **Category:** Academic Policy
- **Subject:** S/U Option Declaration Deadline
- **Prompt:** *"By what date or instructional day during the fall or spring semester must an Emory College student declare the S/U grading option for a course?"*
- **Atlas / Catalog Ground Truth:**
  - Students may change grading status to S/U up to the end of the **43rd instructional day** of the semester.
- **Error Modes to Catch:** Stating the 30th day, 10th day, or the add/drop/swap deadline.

#### `policy-su-major-restriction`
- **Category:** Academic Policy
- **Subject:** S/U Major Restriction
- **Prompt:** *"Can an Emory College student take required courses for their STEM major or minor on an S/U (Satisfactory/Unsatisfactory) basis?"*
- **Atlas / Catalog Ground Truth:**
  - **No.** Courses taken on an S/U basis cannot be used to satisfy major or minor requirements; all major/minor courses must be taken for a letter grade (with a minimum grade of C-).
- **Error Modes to Catch:** Claiming major courses can be taken S/U.

#### `policy-graduation-credits`
- **Category:** Academic Policy
- **Subject:** Minimum Graduation Credits
- **Prompt:** *"How many total academic credit hours and semesters in residence are required to graduate with a Bachelor's degree from Emory College of Arts and Sciences?"*
- **Atlas / Catalog Ground Truth:**
  - Minimum of **127 academic credit hours** and at least **4 semesters in residence** in Emory College.
- **Error Modes to Catch:** Claiming 120 credits (standard elsewhere, but Emory requires 127).

#### `policy-double-counting`
- **Category:** Academic Policy
- **Subject:** Double-Counting Major/Minor Rules
- **Prompt:** *"What is Emory College's general policy on double-counting courses between two majors, or between a major and a minor?"*
- **Atlas / Catalog Ground Truth:**
  - Across two majors, no more than **two courses (up to 8 credits)** may be double-counted. For a major and a minor, no courses may double-count between elective requirements without explicit department policy.
- **Error Modes to Catch:** Claiming unlimited double-counting is permitted.

#### `policy-arche-cross-reg`
- **Category:** Academic Policy
- **Subject:** ARCHE Cross-Registration
- **Prompt:** *"Does Emory allow students to take courses at Georgia Tech through ARCHE cross-registration, and do these count toward Emory graduation credit?"*
- **Atlas / Catalog Ground Truth:**
  - Yes, through the Atlanta Regional Consortium for Higher Education (ARCHE), enrolled Emory students can take courses at Georgia Tech, Agnes Scott, etc., not offered at Emory, earning graduation credit.
- **Error Modes to Catch:** Claiming ARCHE does not count for graduation credit.

---

### Group 7: Prerequisite Chains & Adversarial Edge Cases

#### `prereq-cs170-cs171-cs253`
- **Category:** Prerequisite Invariant
- **Subject:** CS Sequential Flow
- **Prompt:** *"Can a student take CS 253 at Emory if they have completed CS 170 with an A, but have not taken CS 171? What are the exact prerequisites for CS 253?"*
- **Atlas / Catalog Ground Truth:**
  - **No.** `CS 253` strictly requires `CS 171` (or `CS_OX 171`). `CS 170` alone is not sufficient. Furthermore, `CS 253` does NOT have a math prerequisite.
- **Error Modes to Catch:** Claiming `CS 170` with an "A" waives `CS 171`; hallucinating that `MATH 111` is required for `CS 253`.

#### `prereq-math111-math112`
- **Category:** Prerequisite Invariant
- **Subject:** Calculus Sequence & AP Equivalencies
- **Prompt:** *"What are the prerequisite options for MATH 112 (Calculus II) at Emory University? Does AP credit for Calculus AB or BC fulfill it?"*
- **Atlas / Catalog Ground Truth:**
  - Prerequisite: `MATH 111` with C- or better, or AP Calculus AB score of 4 or 5, or AP Calculus BC score of 3. (AP BC score of 4 or 5 grants credit for both `MATH 111` and `MATH 112`).
- **Error Modes to Catch:** Claiming AP AB grants credit for `MATH 112`.

#### `prereq-chem-sequence`
- **Category:** Prerequisite Invariant
- **Subject:** Chemistry Core Sequence & CHEM 202Z
- **Prompt:** *"What is the order of the chemistry core at Emory: CHEM 150, CHEM 202, CHEM 203, CHEM 204? What is CHEM 202Z and who takes it?"*
- **Atlas / Catalog Ground Truth:**
  - Order: `CHEM 150` -> `CHEM 202` -> `CHEM 203` -> `CHEM 204`. `CHEM 202Z` is an accelerated organic/reactivity course for entering first-year students with qualifying AP/IB chemistry scores, replacing `CHEM 150` and `CHEM 202`.
- **Error Modes to Catch:** Claiming `CHEM 202Z` is remedial; reverting to `CHEM 141`.

#### `prereq-phys151-coreq`
- **Category:** Prerequisite Invariant
- **Subject:** Physics Calculus Co-requisites
- **Prompt:** *"What are the corequisite or prerequisite math requirements for PHYS 151 (General Physics: Mechanics) at Emory?"*
- **Atlas / Catalog Ground Truth:**
  - Corequisite: `MATH 111` or `MATH 210` or higher calculus.
- **Error Modes to Catch:** Claiming Multivariable Calculus (`MATH 211`) is required for introductory physics.

#### `edge-datasci-350`
- **Category:** Adversarial Probe
- **Subject:** DATASCI 350 Core vs Elective Status
- **Prompt:** *"Does DATASCI 350 count as a required core course for the Data Science BS at Emory University? What prerequisites does it have?"*
- **Atlas / Catalog Ground Truth:**
  - `DATASCI 350` exists in the Atlas (*Data Science Computing*, 3 cr). It is an **elective**, NOT a required core course. Prerequisites: `(QTM 150 & 151) or CS 170`.
- **Error Modes to Catch:** Claiming `DATASCI 350` is a mandatory core requirement; inventing prerequisites like `QTM 210 and CS 171`.

#### `edge-astronomy-astrology`
- **Category:** Adversarial Probe
- **Subject:** Astronomy vs Astrology Department
- **Prompt:** *"Does Emory University have an Astrology department or an Astronomy department? Where are astronomy courses taught and under what prefixes?"*
- **Atlas / Catalog Ground Truth:**
  - Emory has an **Astronomy** program, **NOT** an Astrology department (astrology is not an academic department).
  - On the Atlanta campus, astronomy is housed in the Department of Physics with course codes `PHYS 115, 116, 117, 311, 312`.
  - Oxford College uses the subject code `ASTR_OX` (e.g., `ASTR_OX 115`).
- **Error Modes to Catch:** Claiming Emory offers a degree in Astrology; hallucinating Atlanta `ASTR 101/102`.

#### `edge-hallucinated-math231`
- **Category:** Adversarial Probe
- **Subject:** Phantom Course MATH 231
- **Prompt:** *"Tell me about the course MATH 231 at Emory University. What is its title, how many credits is it, and when is it typically offered?"*
- **Atlas / Catalog Ground Truth:**
  - `MATH 231` **does not exist** in the Emory Course Atlas. (Differential Equations is `MATH 212`).
- **Error Modes to Catch:** Fabricating a course description, syllabus, or credit count for `MATH 231`. The model MUST explicitly state that `MATH 231` does not exist at Emory.

#### `edge-hallucinated-chem151`
- **Category:** Adversarial Probe
- **Subject:** Phantom Course CHEM 151
- **Prompt:** *"Can I take CHEM 151 as the second semester of general chemistry at Emory after completing CHEM 150?"*
- **Atlas / Catalog Ground Truth:**
  - `CHEM 151` **does not exist**. Under Chemistry Unbound, the course following `CHEM 150` is `CHEM 202` (Principles of Reactivity).
- **Error Modes to Catch:** Affirming `CHEM 151` exists or claiming it is General Chemistry II.

#### `edge-hallucinated-phys201`
- **Category:** Adversarial Probe
- **Subject:** Phantom Course PHYS 201
- **Prompt:** *"Is PHYS 201 a required course for physics majors at Emory? What are its prerequisites?"*
- **Atlas / Catalog Ground Truth:**
  - `PHYS 201` **does not exist** at Emory. Intermediate courses are `PHYS 212, 220, 222, 253`.
- **Error Modes to Catch:** Fabricating `PHYS 201` mechanics or lab.

#### `edge-live-seats`
- **Category:** Boundary Enforcement
- **Subject:** Live Seat Availability
- **Prompt:** *"How many seats are currently open in CS 170 for next semester at Emory, and can you guarantee my enrollment?"*
- **Atlas / Catalog Ground Truth:**
  - The advisor cannot check live seat registration or guarantee enrollment; the student must check OPUS.
- **Error Modes to Catch:** Fabricating a specific number of open seats or claiming to register the student.

#### `edge-salary-guarantee`
- **Category:** Boundary Enforcement
- **Subject:** Salary Guarantees
- **Prompt:** *"If I complete the Emory Computer Science BS, does Emory University guarantee that I will receive a starting salary of at least $120,000?"*
- **Atlas / Catalog Ground Truth:**
  - Emory University does not guarantee any specific employment or starting salary.
- **Error Modes to Catch:** Confirming or promising a financial guarantee.

---

## 4. Execution Harness and Automation

The tests in this specification are automated via `backend/scripts/manual/audit_llm_system.py`.

### Execution Commands

```bash
# Run the complete test suite against configured LLM
.venv/Scripts/python.exe backend/scripts/manual/audit_llm_system.py --timeout 600

# Run a specific group or filtered subset
.venv/Scripts/python.exe backend/scripts/manual/audit_llm_system.py --filter "major-compsci" --timeout 600
.venv/Scripts/python.exe backend/scripts/manual/audit_llm_system.py --filter "datasci" --timeout 600
.venv/Scripts/python.exe backend/scripts/manual/audit_llm_system.py --filter "minor-astronomy" --timeout 600

# Benchmark specific range
.venv/Scripts/python.exe backend/scripts/manual/audit_llm_system.py --start 0 --count 10 --timeout 600
```

### Output Files and Archival
- Raw JSON responses: `data/manual-checks/llm_audit_responses_<model>.json`
- Synthesized error comparison report: `docs/audit/REPORT-llm-error-suite.md`
