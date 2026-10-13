export function makeId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}
