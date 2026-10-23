/** Frame validator output separately from native PDF-library diagnostics. */
export const VALIDATION_PREFIX = 'SCHEMATIC_VALIDATION_RESULT=';

export function decodeValidation(result) {
  if (result.error || result.status !== 0) {
    return ['validation execution failed: ' + String(result.error || result.stderr || result.stdout || `exit ${result.status}`).slice(-1600)];
  }
  const frames = String(result.stdout || '').split(/\r?\n/).filter(line => line.startsWith(VALIDATION_PREFIX));
  try {
    if (frames.length !== 1) throw Error('Expected one framed validator result');
    const errors = JSON.parse(frames[0].slice(VALIDATION_PREFIX.length));
    if (!Array.isArray(errors) || errors.some(error => typeof error !== 'string')) throw Error('Invalid validator result type');
    return errors;
  } catch (error) {
    return ['validation execution failed: ' + String(error.message) + ': ' + String(result.stderr || result.stdout).slice(-1600)];
  }
}
