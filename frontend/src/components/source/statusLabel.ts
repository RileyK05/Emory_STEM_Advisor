import type { SourceDoc } from '../../api/types';

const STATUS_LABELS: Record<SourceDoc['status'], string> = {
  uploaded: 'Processing',
  parsed: 'Processing',
  chunked: 'Processing',
  indexed: 'Ready',
  failed: 'Couldn’t read',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status as SourceDoc['status']] ?? status;
}
