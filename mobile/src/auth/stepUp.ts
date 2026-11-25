import type { StepUpFactor } from '../api/errors';
import type { AuthedResult } from './tokenManager';

/**
 * Generic `step_up_required` handling:
 * run the action; if the server asks for factors, verify each one in the order given, then retry
 * the action ONCE. A second `step_up_required` is a failure the user sees, never another loop.
 */

/** Asks the user to verify one factor with the server. Resolves when done or abandoned. */
export type FactorPrompter = (factor: StepUpFactor) => Promise<'verified' | 'cancelled'>;

export type StepUpResult<T> =
  | AuthedResult<T>
  /** The user cancelled a factor prompt: the action did not run again. */
  | { readonly ok: false; readonly status: 0; readonly stepUp: 'cancelled' }
  /** Still `step_up_required` after verifying (or an unusable factor list). */
  | { readonly ok: false; readonly status: 0; readonly stepUp: 'failed' };

function stepUpFactors<T>(result: AuthedResult<T>): readonly StepUpFactor[] | null {
  if (result.ok || !('error' in result) || result.error.code !== 'step_up_required') return null;
  return result.error.factors ?? [];
}

export async function withStepUp<T>(
  action: () => Promise<AuthedResult<T>>,
  prompt: FactorPrompter,
): Promise<StepUpResult<T>> {
  const first = await action();
  const factors = stepUpFactors(first);
  if (factors === null) return first;
  if (factors.length === 0) return { ok: false, status: 0, stepUp: 'failed' };

  for (const factor of factors) {
    if ((await prompt(factor)) === 'cancelled') return { ok: false, status: 0, stepUp: 'cancelled' };
  }

  const second = await action();
  return stepUpFactors(second) === null ? second : { ok: false, status: 0, stepUp: 'failed' };
}
