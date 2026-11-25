import { useState } from 'react';

import { Body, Button, LinkButton, Screen, TextField, Title } from '../../components/ui';
import { t } from '../../i18n';

const MAX_NAME = 120;

/**
 * Create a chama by name. The creator becomes its chair and it becomes the active chama. The
 * constitution (contributions, fines, loan rules) is set up next, in the Governance tab.
 */
export function CreateChamaScreen({
  onCreate,
  onCancel,
}: {
  /** Resolves to an error message, or null on success. */
  onCreate: (name: string) => Promise<string | null>;
  /** Absent when the user has no chama yet. */
  onCancel?: () => void;
}) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trimmed = name.trim();

  async function submit(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      setError(await onCreate(trimmed));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen testID="create-chama">
      <Title>{t('chamas.create.title')}</Title>
      <Body tone="muted">{t('chamas.create.explain')}</Body>
      <TextField
        label={t('chamas.create.name')}
        hint={t('chamas.create.nameHint')}
        value={name}
        onChangeText={setName}
        maxLength={MAX_NAME}
        testID="chama-name"
      />
      {error !== null ? <Body tone="danger">{error}</Body> : null}
      <Button
        label={t('chamas.create.submit')}
        busy={busy}
        disabled={trimmed === ''}
        onPress={() => void submit()}
      />
      {onCancel !== undefined ? <LinkButton label={t('common.cancel')} onPress={onCancel} /> : null}
    </Screen>
  );
}
