import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from '../../components/ui';
import { t } from '../../i18n';
import { formatKes } from '../../lib/money';
import { formatDayMonth } from '../../lib/nairobiTime';
import { Card } from '../app/kit';
import { c } from '../app/theme';
import type { Member } from '../chamas/chamaApi';
import { type Deps, writeFailure } from '../m2/deps';
import type { UnallocatedPayment } from '../m2/m2Api';
import { QueryBlock } from '../m2/QueryBlock';
import { useAuthedQuery } from '../shell/useAuthedQuery';
import { OptionList } from './MemberPicker';

/**
 * The unmatched payment resolver: payments the server could not link to a member (till payments,
 * mistyped account numbers). The server's suggestions come first; assigning needs a fresh PIN.
 */
export function UnallocatedList({ deps, members }: { deps: Deps; members: readonly Member[] }) {
  const query = useAuthedQuery(['unallocated', deps.chamaId], deps.m2.unallocated, deps.callAuthed);
  return (
    <QueryBlock
      query={query}
      isEmpty={(d) => d.payments.length === 0}
      emptyText={t('ledger.empty.unallocated')}
    >
      {(data) =>
        data.payments.map((p) => (
          <UnallocatedCard key={p.payment_id} deps={deps} payment={p} members={members} />
        ))
      }
    </QueryBlock>
  );
}

function UnallocatedCard({
  deps,
  payment,
  members,
}: {
  deps: Deps;
  payment: UnallocatedPayment;
  members: readonly Member[];
}) {
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState<string | null>(payment.suggestions[0]?.member_id ?? null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  const suggested = new Set(payment.suggestions.map((s) => s.member_id));
  const active = members.filter((m) => m.status === 'active');
  const ordered = [
    ...active.filter((m) => suggested.has(m.membership_id)),
    ...active.filter((m) => !suggested.has(m.membership_id)),
  ];
  const chosen = active.find((m) => m.membership_id === choice);

  async function assign(): Promise<void> {
    if (chosen === undefined) return;
    setBusy(true);
    const result = await deps.runWithStepUp(deps.m2.allocate(payment.payment_id, chosen.membership_id), {
      reason: 'resolver.reason',
    });
    setBusy(false);
    if (result.ok) {
      setMessage({ text: t('resolver.assigned'), ok: true });
      void queryClient.invalidateQueries();
      return;
    }
    const text = writeFailure(result);
    if (text !== null) setMessage({ text, ok: false });
  }

  return (
    <Card testID={`unallocated-${payment.payment_id}`}>
      <Text style={styles.amount}>{formatKes(payment.amount_minor)}</Text>
      <Text style={styles.detail}>
        {formatDayMonth(payment.received_at)} · {payment.mpesa_receipt}
      </Text>
      {payment.payer_masked !== null ? (
        <Text style={styles.detail}>{t('resolver.payer', { payer: payment.payer_masked })}</Text>
      ) : null}
      {payment.bill_ref !== null && payment.bill_ref !== '' ? (
        <Text style={styles.detail}>{t('resolver.billRef', { ref: payment.bill_ref })}</Text>
      ) : null}
      <View style={styles.pick}>
        <OptionList
          label={t('receipt.member')}
          value={choice}
          onChange={setChoice}
          options={ordered.map((m) => ({
            key: m.membership_id,
            label: m.name,
            ...(suggested.has(m.membership_id) ? { note: t('resolver.suggested') } : { note: m.member_code }),
          }))}
        />
      </View>
      {message !== null ? (
        <Text
          style={[styles.detail, { color: message.ok ? c.green : c.red }]}
          accessibilityLiveRegion="polite"
        >
          {message.text}
        </Text>
      ) : null}
      {chosen !== undefined ? (
        <Button
          label={t('resolver.assign', { name: chosen.name })}
          busy={busy}
          onPress={() => void assign()}
        />
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  amount: { fontSize: 20, fontWeight: '700', color: c.ink },
  detail: { fontSize: 14, color: c.muted },
  pick: { marginVertical: 8 },
});
