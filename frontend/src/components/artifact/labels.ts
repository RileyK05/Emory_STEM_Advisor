import type { ArtifactRunEvent } from '../../api/types';

type Stage = NonNullable<ArtifactRunEvent['stage']>;

const STAGE_LABELS: Record<Stage, string> = {
  planning: 'Planning the output',
  retrieving: 'Retrieving context',
  routing: 'Selecting a model',
  generating: 'Generating',
  repairing: 'Repairing output',
  assembling: 'Assembling artifact',
  validating: 'Checking output',
  validated: 'Output checked',
  rendering: 'Rendering artifact',
  recalculating: 'Recalculating workbook',
  publishing: 'Saving artifact',
  tool_started: 'Using a tool',
  tool_completed: 'Tool finished',
  tool_reused: 'Reused a saved tool result',
  tool_replayed: 'Recovered a saved tool result',
  completed: 'Completed',
  reused: 'Reused saved result',
  replayed: 'Recovered saved result',
};

export function readableStage(stage: ArtifactRunEvent['stage']) {
  return stage ? STAGE_LABELS[stage] : null;
}
