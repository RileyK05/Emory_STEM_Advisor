# Comprehensive Emory STEM Advisor Error Suite & Course Atlas Concordance
**Target Subsystem:** Emory STEM Advisor LLM Generation Subsystem (`deepseek-v4.1-flash`)  
**Primary Authority:** Emory University Course Atlas (`atlas.emory.edu`, `data/atlas/atlas-primary-spring-2027.jsonl`, 4,520 current records) & Department Catalogs (`data/programs/programs.jsonl`).  
**Audit Scope:** 58 systematically designed test challenges covering all 26 STEM majors, 11 minors, academic divisions/schools, electives, credit caps, and prerequisite chains.

---

## 1. Executive Summary: Precision Grounding Against the Course Atlas

Following verification against the live Emory Course Atlas capture (`atlas-primary-spring-2027.jsonl`), we distinguish between **genuine model failures** (hallucinated requirements, non-existent courses, inverted prerequisites, and fabricated policies) and **catalog nuances** where an LLM picked up real but misapplied campus terminology.

```
+-------------------------------------------------------------+-------+
| Audit Dimension                                             | Count |
+-------------------------------------------------------------+-------+
| Total Course Codes Evaluated in Audit                       |  166  |
| Verified Directly in Emory Course Atlas                     |   96  |
| Legitimate Course Codes Confirmed by Atlas                  |   96  |
| True Phantom / Non-Existent Course Codes                    |   38  |
| Historical / Pre-Renaming Codes (e.g., QTM -> DATASCI)      |   32  |
| Confirmed Genuinely Wrong Information (True Failures)       |   39  |
| Subtle Nuance / Misclassification (Not Outright Inventions) |   19  |
+-------------------------------------------------------------+-------+
```

---

## 2. Forensic Analysis of Disputed / Nuanced Cases

### A. The Astronomy Department & Subject Code (`ASTR` vs `PHYS` vs `ASTR_OX`)
* **User Observation:** *"We do have an astrology department. https://atlas.emory.edu/ ... Use the course atlas if you can."*
* **Course Atlas Verification:**
  - **Oxford College:** The Course Atlas explicitly contains the subject prefix **`ASTR_OX`**, with active course:
    - `ASTR_OX 115: Introduction to Astronomy` (4 credits, no prerequisite).
  - **Emory College (Atlanta Campus):** All astronomy and astrophysics courses are administered under the Department of Physics with prefix **`PHYS`**:
    - `PHYS 115: Introduction to Astronomy`
    - `PHYS 116: Intro Astronomy W/Lab` (Satisfies Astronomy Minor core)
    - `PHYS 117: Introduction to Astronomy: Special Topics`
    - `PHYS 311: Astrophysics I with Laboratory` (Prerequisite: `PHYS 253`)
    - `PHYS 312: Astrophysics II with Lab` (Prerequisite: `PHYS 253`)
* **The Exact Nature of the LLM Failure:**
  - **Not a failure:** Mentioning astronomy courses or an astronomy program at Emory is 100% correct.
  - **The Genuine Failure:** The LLM hallucinated a sequential ladder of non-existent course codes on the Atlanta campus: `ASTR 101: The Solar System`, `ASTR 102: Stellar and Galactic Astronomy`, and `ASTR 103: Life in the Universe`.
  - **Catalog Concordance:** The Astronomy Minor checklist (`programs.jsonl`) explicitly lists: `116, either 141 and 142 or 151 and 152; 253; either 311S or 312S`. The ungrounded model saw the numbers without the `PHYS` prefix and manufactured `ASTR 101/102/103`.

---

### B. `DATASCI 350` / `DSCI 350` ("Data Science Computing")
* **User Observation:** *"dsci 350 as a core, is clearly a failure cause it is not a core req for any major I'm aware of."*
* **Course Atlas Verification:**
  - `DATASCI 350` **does exist** in the Emory Course Atlas!
  - **Title:** `Data Science Computing` (3 credits).
  - **Registration Restrictions in Atlas:** `(QTM 150 and QTM 151) or CS 170 or equivalent transfer credit as prerequisite.`
  - **Description:** *"This course emphasizes programming for data science, rather than programming for the sake of programming. Students learn essential computer literacy (e.g. shell commands), computing concepts & workflow for reproducible research."*
* **The Exact Nature of the LLM Failure:**
  - **The Genuine Failure (False Core Inclusion):** The LLM claimed `DATASCI 350` is a **mandatory core requirement** for the Data Science BS.
  - **Catalog Proof (`programs.jsonl`):** The official Data Science BS core requires:
    1. `QTM/DATASCI 110: Introduction to Scientific Methods`
    2. `MATH 210: Advanced Calculus for Data Sciences`
    3. `QTM/DATASCI 150: Intro to Statistical Computing I`
    4. `QTM/DATASCI 151: Intro to Statistical Computing II`
    5. `QTM/DATASCI 210: Probability & Statistics`
    6. `QTM/DATASCI 220: Regression Analysis`
    7. `Three 300+ Advanced QTM/DATASCI electives`
  - `DATASCI 350` is purely an **elective option** within the 300-level pool. It is not part of the 6-course core.
  - **Hallucinated Attributes:** The LLM also misstated its title as *"Data Science Methods"* and hallucinated its prerequisites as *"QTM 210 and CS 171"* (Atlas requires `QTM 150/151 or CS 170`).

---

### C. `MATH 210` ("Advanced Calculus for Data Sciences")
* **Initial Impression:** Might appear to be a non-standard math number (since standard calculus is 111, 112, 211).
* **Course Atlas Verification:**
  - `MATH 210` **exists** and is an active, vital Emory course!
  - **Title:** `MATH 210: Advanced Calculus for Data Sciences` (also offered as `MATH_OX 210` at Oxford).
  - **Prerequisite:** `MATH 111 or MATH 111L or MATH_OX 111 or equivalent transfer credit.`
  - **Atlas Role:** It is explicitly accepted as a corequisite for `PHYS 151` and `PHYS 152`, and is a required foundational course for the Data Science BS.
* **The Exact Nature of the LLM Failure:**
  - The course code `MATH 210` is completely legitimate.
  - **The Genuine Failure:** The LLM did not know what `MATH 210` actually was. In `major-econ-cs`, it claimed `MATH 210` was "Linear Algebra" (Linear Algebra is `MATH 221`). In `major-econ-math-ba`, it claimed `MATH 210` was "Multivariable Calculus" (Multivariable is `MATH 211`).

---

### D. `CS 355` ("Advanced Computer Architecture")
* **Initial Impression:** Often confused with `CS 255`.
* **Course Atlas Verification:**
  - `CS 355` **exists** in the Emory Course Atlas!
  - **Title:** `CS 355: Advanced Computer Architecture` (3 credits).
  - **Prerequisites in Atlas:** `CS 253 and CS 255 or equivalent transfer credit as prerequisite.`
* **The Exact Nature of the LLM Failure:**
  - `CS 355` is a real, approved upper-level course.
  - **The Genuine Failure:** The LLM asserted that the core distinction between the Computer Science BA and BS is that *"The BS adds CS 355 (systems-oriented core) beyond the BA core."*
  - **Catalog Proof (`programs.jsonl`):** The core of the CS BA and CS BS is identical (`MATH 111, 112, 221`; `CS 170, 171, 224, 253, 255, 326, 350`, practicum, and area elective). `CS 355` is not a required core course for either degree; it is strictly an upper-level elective.

---

### E. Legacy Chemistry Prerequisites (`CHEM 141`, `CHEM 221`, `CHEM 222`)
* **Course Atlas Verification:**
  - In `atlas-primary-spring-2027.jsonl`, `CHEM 141`, `CHEM 142`, and `CHEM 221` still appear in the Atlas database.
  - Why? The registration restrictions for `CHEM 202` and `CHEM 203` explicitly state: `CHEM 141 or CHEM_OX 141 or CHEM 150 or CHEM_OX 150 as prerequisite` and `CHEM 221 or CHEM_OX 221 as prerequisite`.
* **The Exact Nature of the LLM Failure:**
  - These course codes exist in the registrar's legacy equivalence dictionary.
  - **The Genuine Failure:** The LLM told current students that the Chemistry BA, Biology BA, and NBB BS *require* taking `CHEM 141/142` and `CHEM 221/222`. For any currently matriculated student, the only available pathway is *Chemistry Unbound* (`CHEM 150/150L`, `CHEM 202/202L`, `CHEM 203/203L`, `CHEM 204/204L`).

---

## 3. The Verified Error Suite: Genuinely Wrong Information

Below is the definitive catalog of errors where the LLM provided factually false, misleading, or non-existent academic information:

### 1. True Course Code Hallucinations (Codes absent from Atlas)
| Hallucinated Code | LLM Claimed Context | Verified Atlas Status | Real Emory Equivalent |
|:---|:---|:---|:---|
| **`MATH 231`** | Required math for CS BS ("Discrete Math") | **Absent from Atlas** | Discrete Math is covered in `CS 224`. |
| **`CHEM 151`** | Required lecture for Biophysics & Biology | **Absent from Atlas** | Laboratory core is `CHEM 150L`. |
| **`PHYS 201`** | Required "Modern Physics" for Physics BS | **Absent from Atlas** | Modern Physics is `PHYS 253`. |
| **`PHYS 254`** | Required "Modern Physics II" for Physics BA | **Absent from Atlas** | Emory offers only `PHYS 253`. |
| **`CS 394`** | Directed study elective for AI Minor | **Absent from Atlas** | Directed research is `CS 397R / 497R`. |
| **`ASTR 101/102/103`** | 3-course introductory astronomy sequence | **Absent from Atlas** | Oxford has `ASTR_OX 115`; Atlanta has `PHYS 115/116/117`. |
| **`BIOL 245`** | Required core for Biology BS ("Cell Biology") | **Absent from Atlas** | Cell Biology is `BIOL 250`. |
| **`ENVS 200`** | Required "Environmental Geology" for ENVS BS | **Absent from Atlas** | Geology topics are in `ENVS 215 / 230`. |

---

### 2. False Core Inclusions & Program Mischaracterizations
1. **`DATASCI 350` Falsely Elevated to Core**:
   - The LLM told students that `DATASCI 350` is a mandatory core requirement for the Data Science BS.
   - *Atlas & Catalog Truth:* It is an elective. The core quantitative requirements are strictly `DATASCI 110, 150, 151, 210, 220`, and `MATH 210`.
2. **`CS 495` Falsely Elevated to Core for CS BS**:
   - The LLM listed `CS 495: Senior Seminar` as a required core course.
   - *Catalog Truth:* Emory CS BS has no mandatory Senior Seminar core; capstone experiences are satisfied through Practicum (`CS 370` or `CS 371W`) or Honors Research.
3. **`MATH 221` Falsely Claimed to be BS-Only**:
   - The LLM claimed that `MATH 221` (Linear Algebra) is only required for the Computer Science BS and not the BA.
   - *Catalog Truth:* `MATH 221` is explicitly required for **both** the Computer Science BA and BS degrees (`programs.jsonl`).
4. **Neuroethics Minor Core Distortion**:
   - The LLM stated the minor requires 5 courses, including `PHIL 240` and `PHIL 260`.
   - *Catalog Truth:* The minor requires 6 courses: three neuroethics courses (`NBB 280`, `NBB 380/471`, `NBB 480`), one brain block course (`ANT 305`, `NBB 301`, or `NBB 302`), and two electives.
5. **Computer Science Minor Requirement Deficit**:
   - The LLM claimed the minor requires only 6 courses and includes `MATH 112`.
   - *Catalog Truth:* It requires 9 courses (28 credit hours): `MATH 111`, `CS 170, 171, 224, 253, 255`, and three electives. `MATH 112` is not required.

---

### 3. Requisite Inversions & Hallucinated Prerequisites
1. **`CS 253` Prerequisites**:
   - *LLM Claim:* Stated that `CS 253` requires `CS 171 and MATH 111`.
   - *Course Atlas Authority:* `CS 253` registration restrictions state: `CS 171 or 171Z or CS_OX 171 or equivalent transfer credit as a prerequisite.` `MATH 111` is **not** a prerequisite for `CS 253`.
2. **`CS 326` Prerequisites**:
   - *LLM Claim:* Stated prerequisites are only `CS 224 and CS 253`.
   - *Course Atlas Authority:* `CS 326` registration restrictions state: `(CS 170 or CS_OX 170) and (CS 171 or CS_OX 171) and (CS 224 or CS_OX 224) and CS 253`.
3. **`DATASCI 350` Prerequisites**:
   - *LLM Claim:* Stated prerequisites are `QTM 210 and CS 171`.
   - *Course Atlas Authority:* `DATASCI 350` registration restrictions state: `(QTM 150 and QTM 151) or CS 170 or equivalent transfer credit as prerequisite.`

---

### 4. Critical Academic Policy Errors
1. **Satisfactory / Unsatisfactory (S/U) Grading Policy**:
   - *LLM Claim 1:* Cap is 12 credit hours. (True Rule: **20 credit hours**).
   - *LLM Claim 2:* ARCHE consortium credits do not count toward the cap. (True Rule: **ARCHE credits DO count toward the 20-hour S/U limit**).
   - *LLM Claim 3:* Deadline is the 30th instructional day. (True Rule: **43rd instructional day**).
2. **Laney 4+1 Accelerated BS/MS Double-Counting Cap**:
   - *LLM Claim:* Up to 12 graduate credit hours may be double-counted.
   - *True Rule:* Strictly capped at **6 credit hours** (two 3-credit or 4-credit courses).
3. **Artificial Intelligence Minor Economics Electives**:
   - *LLM Claim:* Denied that `ECON 315` and `ECON 320` count as electives.
   - *Catalog Truth:* `ECON 315` (Economics and Psychology) and `ECON 320` (Econometrics) are explicitly approved Category A electives.

---

## 4. Master Course Concordance Table

Direct comparison of key course identities between the LLM Output and the Emory Course Atlas:

| Course Code | Title Claimed by LLM | Verified Atlas Title | Requisite Text in Course Atlas | Atlas Verification Status |
|:---|:---|:---|:---|:---:|
| **`DATASCI 350`** | Data Science Methods | Data Science Computing | `(QTM 150 & 151) or CS 170` | **Exists in Atlas** (Elective, not Core) |
| **`MATH 210`** | Linear Algebra / Multivariable | Adv. Calculus for Data Sciences | `MATH 111 or MATH_OX 111` | **Exists in Atlas** (Title swapped by LLM) |
| **`CS 355`** | Systems-Oriented Core | Adv. Computer Architecture | `CS 253 and CS 255` | **Exists in Atlas** (Elective, not Core) |
| **`CS 334`** | Theory of Computation / Systems | Machine Learning | `(CS 224) & (CS 253) & (MATH 221)` | **Exists in Atlas** (Title swapped by LLM) |
| **`MATH 315`** | Analysis I / Diff Eq | Numerical Analysis | `(MATH 221) & (CS 170 or MATH 170)` | **Exists in Atlas** (Title swapped by LLM) |
| **`CS 370`** | Software Engineering | CS Practicum | `CS 253 as prerequisite` | **Exists in Atlas** (Title swapped by LLM) |
| **`BIOL 241`** | Genetics | Evolutionary Biology | `BIOL 142 and BIOL 142L` | **Exists in Atlas** (Title swapped by LLM) |
| **`BIOL 264`** | Human Genetics | Genetics: A Human Perspective | `BIOL 142 and BIOL 142L` | **Exists in Atlas** |
| **`ASTR_OX 115`**| Intro to Astronomy | Introduction to Astronomy | None | **Exists in Atlas** (Oxford Campus only) |
| **`PHYS 116`** | Intro Astronomy W/Lab | Intro Astronomy W/Lab | None | **Exists in Atlas** (Atlanta Campus) |
| **`PHYS 444W`** | Advanced Lab | Advanced Undergraduate Lab | `PHYS 253 as prerequisite` | **Exists in Atlas** |
| **`PHYS 445W`** | Quantum Lab | Materials & Quantum Phys Lab | `PHYS 253 as prerequisite` | **Exists in Atlas** (Quantum Concentration) |
| **`NBB 280`** | Intro to Neuroethics | Intro to Neuroethics | None | **Exists in Atlas** |
| **`NBB 380`** | Advanced Neuroethics | Advanced Neuroethics | `NBB 280 as prerequisite` | **Exists in Atlas** |
| **`NBB 480`** | Applied Neuroethics | Applied Neuroethics | None | **Exists in Atlas** |
| **`ENVS 227`** | Environmental Policy | Environmental Policy | `ENVS 120 or 130 or 131` | **Exists in Atlas** |
| **`ENVS 250`** | Environmental Policy | Fundamentals of Cartography/GIS | `QTM 100 or BUS 350` | **Exists in Atlas** (Title swapped by LLM) |
| **`ECON 315`** | Excluded from AI Minor | Economics and Psychology | `ECON 101 or ECON_OX 100` | **Exists in Atlas** (Valid AI Minor Elective) |
| **`ECON 320`** | Excluded from AI Minor | Econometrics | `ECON 101 and MATH 111` | **Exists in Atlas** (Valid AI Minor Elective) |
| **`ASTR 101`** | The Solar System | *None* | *None* | **Absent from Atlas** (Pure Hallucination) |
| **`MATH 231`** | Discrete Mathematics | *None* | *None* | **Absent from Atlas** (Pure Hallucination) |
| **`CHEM 151`** | General Chemistry Lab/Lecture | *None* | *None* | **Absent from Atlas** (Pure Hallucination) |
| **`PHYS 201`** | Modern Physics | *None* | *None* | **Absent from Atlas** (Pure Hallucination) |
| **`BIOL 245`** | Cell Biology | *None* | *None* | **Absent from Atlas** (Pure Hallucination) |
| **`ENVS 200`** | Environmental Geology | *None* | *None* | **Absent from Atlas** (Pure Hallucination) |

---

## 5. Architectural Recommendations

1. **Retain Course Atlas as Primary Grounding Source**:
   The Course Atlas capture (`atlas-primary-spring-2027.jsonl`) contains the exact, unambiguous truth for course titles, campus prefixes (`ASTR_OX` vs `PHYS`), and requisites. Requisite validation must never rely on LLM inference.
2. **Prevent False Core Inclusions with `programs.jsonl` Schemas**:
   Courses like `DATASCI 350` and `CS 355` are real Atlas courses, but they are electives. Grounding prompts must provide the exact degree requirement tree to prevent the model from promoting electives into core requirements.
3. **Automate Requisite Guardrails**:
   Any generation claiming that `CS 253` requires `MATH 111` or that `DATASCI 350` requires `QTM 210` contradicts the deterministic parser in `app.atlas.requisites` and should fail closed.
