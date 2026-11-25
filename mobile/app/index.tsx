import { useCallback } from 'react';

import { useAppServices } from '../src/auth/AppServicesContext';
import { useSession } from '../src/auth/SessionProvider';
import { useStepUp } from '../src/auth/StepUpProvider';
import { signedOutMessageKey } from '../src/auth/signedOut';
import type { AuthedCall } from '../src/auth/tokenManager';
import { Loading } from '../src/components/ui';
import { LoginFlow } from '../src/features/login/LoginFlow';
import { SetPinScreen } from '../src/features/pin/SetPinScreen';
import { SignedInApp } from '../src/features/shell/SignedInApp';
import { NoScreenLockScreen } from '../src/features/unlock/NoScreenLockScreen';
import { UnlockScreen } from '../src/features/unlock/UnlockScreen';
import { t } from '../src/i18n';

/** Renders exactly one screen per auth state; signed-in content only in `signed_in`. */
export default function Index() {
  const { state, session, completeLogin, unlock, logout, pinSet } = useSession();
  const { loginApi, devReadCode, chamaApi, stepUpApi } = useAppServices();
  const runWithStepUp = useStepUp();
  const callAuthed = useCallback(<T,>(call: AuthedCall<T>) => session.tokens.callAuthed(call), [session]);

  switch (state.status) {
    case 'starting':
      return <Loading label={t('common.checking')} />;
    case 'locked':
      return <UnlockScreen cancelled={state.cancelled} onUnlock={unlock} />;
    case 'no_screen_lock':
      return <NoScreenLockScreen onRetry={unlock} />;
    case 'signed_in':
      // First-time setup: the server PIN must exist before the app is used.
      if (state.pin === 'checking') return <Loading label={t('common.checking')} />;
      if (state.pin === 'missing') {
        return <SetPinScreen setPin={stepUpApi.setPin} runWithStepUp={runWithStepUp} onDone={pinSet} />;
      }
      return <SignedInApp api={chamaApi} callAuthed={callAuthed} onLogout={logout} />;
    case 'signed_out': {
      const noticeKey = state.reason === null ? null : signedOutMessageKey(state.reason);
      return (
        <LoginFlow
          api={loginApi}
          installationId={() => session.installationId()}
          completeLogin={completeLogin}
          {...(noticeKey === null ? {} : { notice: t(noticeKey) })}
          {...(devReadCode === undefined ? {} : { devReadCode })}
        />
      );
    }
  }
}
