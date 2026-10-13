export const TERMINAL_STATES = new Set(['completed', 'failed', 'expired', 'cancelled']);

const STATE_LABELS: Record<string, string> = {
  prepared: 'Submission prepared',
  submitting: 'Submitting batch',
  pending: 'Submission pending',
  validating: 'Validating requests',
  in_progress: 'In progress',
  finalizing: 'Finalizing results',
  completed: 'Completed',
  failed: 'Failed',
  expired: 'Expired',
  cancel_requested: 'Cancellation requested',
  cancelling: 'Cancellation in progress',
  cancelled: 'Cancelled',
  unknown: 'Outcome unknown',
  reserved: 'Reserved',
  received: 'Received',
};

const PUBLICATION_LABELS: Record<string, string> = {
  pending: 'Awaiting publication',
  published: 'Published',
  withheld: 'Withheld',
  failed: 'Publication failed',
};

export const stateLabel = (state: string) => STATE_LABELS[state] ?? state;
export const publicationLabel = (state: string) => PUBLICATION_LABELS[state] ?? state;
