import type { SourceDoc } from './types';

export interface MockChunk {
  chunkId: string;
  sourceId: string;
  sourceName: string;
  locatorLabel: string;
  text: string;
}

export const MOCK_SOURCES: SourceDoc[] = [
  { id: 'src-onboarding', name: 'Onboarding Guide', locatorType: 'page', status: 'indexed', chunkCount: 14 },
  { id: 'src-linalg', name: 'Linear Algebra Primer', locatorType: 'section', status: 'indexed', chunkCount: 22 },
  { id: 'src-safety', name: 'Lab Safety Manual', locatorType: 'page', status: 'indexed', chunkCount: 9 },
  { id: 'src-syllabus', name: 'STEM Program Syllabus', locatorType: 'section', status: 'parsed', chunkCount: 0 },
];

export const MOCK_CHUNKS: Record<string, MockChunk> = {
  'chk-ob-03': {
    chunkId: 'chk-ob-03',
    sourceId: 'src-onboarding',
    sourceName: 'Onboarding Guide',
    locatorLabel: 'page 3',
    text: 'New researchers are assigned a faculty advisor during the first week of the program. The advisor matching form is due on the Friday of week two; late submissions are pooled and matched in the following cycle. Your advisor is the first point of contact for course selection and lab placement.',
  },
  'chk-ob-04': {
    chunkId: 'chk-ob-04',
    sourceId: 'src-onboarding',
    sourceName: 'Onboarding Guide',
    locatorLabel: 'page 4',
    text: 'Lab access is granted only after completing the safety orientation and passing the checkout quiz with a score of 90% or higher. Orientation sessions run every Tuesday and Thursday at 4pm in the seminar room.',
  },
  'chk-la-02': {
    chunkId: 'chk-la-02',
    sourceId: 'src-linalg',
    sourceName: 'Linear Algebra Primer',
    locatorLabel: 'section 2.1',
    text: 'A vector space over a field F is a set closed under addition and scalar multiplication, satisfying the eight axioms: associativity, commutativity, existence of an additive identity and inverses, and distributivity of scalar multiplication over both field and vector addition.',
  },
  'chk-la-03': {
    chunkId: 'chk-la-03',
    sourceId: 'src-linalg',
    sourceName: 'Linear Algebra Primer',
    locatorLabel: 'section 2.3',
    text: 'The span of a set of vectors is the set of all their linear combinations. A set of vectors is linearly independent if no vector in the set is a linear combination of the others; otherwise it is dependent. A basis is an independent set that spans the space.',
  },
  'chk-la-05': {
    chunkId: 'chk-la-05',
    sourceId: 'src-linalg',
    sourceName: 'Linear Algebra Primer',
    locatorLabel: 'section 3.2',
    text: 'An inner product induces a norm, and a norm induces a metric. Two vectors are orthogonal when their inner product is zero. The Gram–Schmidt procedure converts an arbitrary basis into an orthonormal one by successively removing the projection of each new vector onto the ones already chosen.',
  },
  'chk-la-07': {
    chunkId: 'chk-la-07',
    sourceId: 'src-linalg',
    sourceName: 'Linear Algebra Primer',
    locatorLabel: 'section 4.1',
    text: 'Eigenvectors are the non-zero vectors whose direction is unchanged by a linear map; each carries an eigenvalue equal to the scaling factor. Spectral decomposition applies to diagonalizable matrices and factors them into PDP⁻¹ where the columns of P are eigenvectors.',
  },
  'chk-safe-01': {
    chunkId: 'chk-safe-01',
    sourceId: 'src-safety',
    sourceName: 'Lab Safety Manual',
    locatorLabel: 'page 1',
    text: 'Eye protection is required at all times in the wet lab, including during setup and cleanup. Prescription glasses alone do not qualify; wear goggles over them or use certified safety glasses with side shields.',
  },
  'chk-safe-02': {
    chunkId: 'chk-safe-02',
    sourceId: 'src-safety',
    sourceName: 'Lab Safety Manual',
    locatorLabel: 'page 2',
    text: 'Chemical spills of any size must be reported to the lab manager immediately, even if cleaned up by the researcher. The spill log is maintained at the front desk and reviewed monthly by the safety committee.',
  },
  'chk-syl-01': {
    chunkId: 'chk-syl-01',
    sourceId: 'src-syllabus',
    sourceName: 'STEM Program Syllabus',
    locatorLabel: 'section 1',
    text: 'The STEM program requires 36 credit hours: 24 in the core sequence, 9 in electives, and a 3-credit capstone. Electives must include at least one course from outside the student\'s home department.',
  },
};

export const chunk = (id: string): MockChunk => MOCK_CHUNKS[id];
