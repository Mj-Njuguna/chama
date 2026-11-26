import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Modal, ScrollView, StyleSheet, Text } from 'react-native';

import { Body, Button, LinkButton, Segmented, TextField, Title } from '../../components/ui';
import { t } from '../../i18n';
import { parseKesInput } from '../../lib/money';
import { nairobiToday } from '../../lib/nairobiTime';
import { c } from '../app/theme';
import type { Member } from '../chamas/chamaApi';
import { type Deps, writeFailure } from '../m2/deps';
import { OptionList } from './MemberPicker';

const NO_MEMBER = 'none';
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * An official records money that reached the chama outside the app (a bank slip, or an M-Pesa
 * payment the platform never saw). The server asks for a fresh PIN; the step-up provider prompts
 * and retries once. The server decides where the money goes.
 */
export function ReceiptForm({
  deps,
  members,
  visible,
  onClose,
  nowMs = Date.now,
}: {
  deps: Deps;
  members: readonly Member[];
  visible: boolean;
  onClose: () => void;
  nowMs?: () => number;
}) {
  const queryClient = useQueryClient();
  const [channel, setChannel] = useState<'bank' | 'mpesa'>('bank');
  const [reference, setReference] = useState('');
  const [amount, setAmount] = useState('');
  const [date, setDate] = useState(() => nairobiToday(nowMs()));
  const [member, setMember] = useState<string>(NO_MEMBER);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  async function submit(): Promise<void> {
    const minor = parseKesInput(amount);
    if (minor === null || reference.trim().length < 3 || !DATE.test(date)) {
      setMessage({ text: t('receipt.invalid'), ok: false });
      return;
    }
    setBusy(true);
    setMessage(null);
    const result = await deps.runWithStepUp(
      deps.chamaApi.createReceipt({
        channel,
        reference: reference.trim(),
        amount_minor: minor,
        received_on: date,
        member_id: member === NO_MEMBER ? null : member,
        note: note.trim(),
      }),
      { reason: 'receipt.reason' },
    );
    setBusy(false);
    if (result.ok) {
      setMessage({ text: t('receipt.saved'), ok: true });
      setReference('');
      setAmount('');
      setNote('');
      setMember(NO_MEMBER);
      void queryClient.invalidateQueries();
      return;
    }
    const text = writeFailure(result, (e) => {
      if (e.code === 'state_conflict' && e.reason === 'duplicate_receipt') return 'receipt.duplicate';
      if (e.code === 'state_conflict' && e.reason === 'member_not_active') return 'receipt.memberInactive';
      if (e.code === 'validation_error') return 'receipt.invalid';
      return null;
    });
    if (text !== null) setMessage({ text, ok: false });
  }

  const active = members.filter((m) => m.status === 'active');
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <ScrollView contentContainerStyle={styles.screen} testID="receipt-form">
        <Title>{t('receipt.title')}</Title>
        <Segmented
          options={[
            { key: 'bank', label: t('receipt.channel.bank') },
            { key: 'mpesa', label: t('receipt.channel.mpesa') },
          ]}
          value={channel}
          onChange={setChannel}
        />
        <TextField
          label={t('receipt.reference')}
          value={reference}
          onChangeText={setReference}
          testID="receipt-reference"
          maxLength={60}
        />
        <TextField
          label={t('receipt.amount')}
          value={amount}
          onChangeText={setAmount}
          keyboardType="number-pad"
          testID="receipt-amount"
        />
        <TextField
          label={t('receipt.date')}
          value={date}
          onChangeText={setDate}
          testID="receipt-date"
          maxLength={10}
        />
        <OptionList
          label={t('receipt.member')}
          value={member}
          onChange={setMember}
          options={[
            ...active.map((m) => ({ key: m.membership_id, label: m.name, note: m.member_code })),
            { key: NO_MEMBER, label: t('receipt.noMember') },
          ]}
        />
        <TextField label={t('receipt.note')} value={note} onChangeText={setNote} maxLength={200} />
        {message !== null ? (
          <Text
            style={[styles.message, message.ok ? styles.ok : styles.bad]}
            accessibilityLiveRegion="polite"
            testID="receipt-message"
          >
            {message.text}
          </Text>
        ) : null}
        <Button label={t('receipt.submit')} busy={busy} onPress={() => void submit()} />
        <LinkButton label={t('common.close')} onPress={onClose} />
        <Body tone="muted">{t('receipt.reason')}</Body>
      </ScrollView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  screen: { padding: 24, gap: 16, backgroundColor: c.white },
  message: { fontSize: 15 },
  ok: { color: c.green },
  bad: { color: c.red },
});
