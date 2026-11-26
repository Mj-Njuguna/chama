import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Modal, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button, LinkButton, TextField, Title } from '../../components/ui';
import { t } from '../../i18n';
import { c } from '../app/theme';
import { type Deps, writeFailure } from '../m2/deps';
import type { Obligation } from '../m2/m2Api';
import { QueryBlock } from '../m2/QueryBlock';
import { useAuthedQuery } from '../shell/useAuthedQuery';
import { ObligationRow } from './rows';

/** An official's view of one member's obligations, with a waiver request on open fines. */
export function MemberObligations({
  deps,
  memberId,
  name,
  onClose,
}: {
  deps: Deps;
  memberId: string;
  name: string;
  onClose: () => void;
}) {
  const query = useAuthedQuery(
    ['obligations', deps.chamaId, memberId],
    deps.m2.obligations(memberId),
    deps.callAuthed,
  );
  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <ScrollView contentContainerStyle={styles.screen} testID="member-obligations">
        <Title>{t('member.obligations', { name })}</Title>
        <QueryBlock
          query={query}
          isEmpty={(d) => d.obligations.length === 0}
          emptyText={t('ledger.empty.obligations')}
        >
          {(data) =>
            data.obligations.map((o) => (
              <View key={o.obligation_id}>
                <ObligationRow item={o} />
                {o.kind === 'fine' && o.status === 'open' ? <WaiverRequest deps={deps} fine={o} /> : null}
              </View>
            ))
          }
        </QueryBlock>
        <LinkButton label={t('common.close')} onPress={onClose} />
      </ScrollView>
    </Modal>
  );
}

function WaiverRequest({ deps, fine }: { deps: Deps; fine: Obligation }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  async function send(): Promise<void> {
    if (reason.trim() === '') return;
    setBusy(true);
    const result = await deps.runWithStepUp(deps.m2.requestWaiver(fine.obligation_id, reason.trim()), {
      reason: 'waiver.reasonStepUp',
    });
    setBusy(false);
    if (result.ok) {
      setMessage({ text: t('waiver.sent'), ok: true });
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['waivers'] });
      return;
    }
    const text = writeFailure(result);
    if (text !== null) setMessage({ text, ok: false });
  }

  return (
    <View style={styles.waiver}>
      {message !== null ? (
        <Text style={{ color: message.ok ? c.green : c.red }} accessibilityLiveRegion="polite">
          {message.text}
        </Text>
      ) : null}
      {open ? (
        <>
          <TextField
            label={t('waiver.reasonLabel')}
            value={reason}
            onChangeText={setReason}
            maxLength={200}
          />
          <Button
            label={t('waiver.send')}
            busy={busy}
            disabled={reason.trim() === ''}
            onPress={() => void send()}
          />
        </>
      ) : message?.ok === true ? null : (
        <LinkButton label={t('waiver.request')} onPress={() => setOpen(true)} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { padding: 24, gap: 12, backgroundColor: c.white },
  waiver: { gap: 8, paddingBottom: 8 },
});
