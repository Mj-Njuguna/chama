import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text } from 'react-native';

import { Button, Segmented } from '../../components/ui';
import { t } from '../../i18n';
import { formatKes } from '../../lib/money';
import { formatDate } from '../../lib/nairobiTime';
import { Card, Row, SectionLabel, StatCard } from '../app/kit';
import { c, CONTENT_BOTTOM } from '../app/theme';
import { isOfficial } from '../chamas/chamaApi';
import type { Deps } from '../m2/deps';
import { QueryBlock } from '../m2/QueryBlock';
import { useAuthedQuery } from '../shell/useAuthedQuery';
import { MemberObligations } from './MemberObligations';
import { ReceiptForm } from './ReceiptForm';
import { CashbookRow, LineItem, ObligationRow, PaymentRow, StatementRow } from './rows';
import { UnallocatedList } from './UnallocatedList';

type MemberView = 'obligations' | 'payments' | 'statement';
type OfficialView = 'collection' | 'cashbook' | 'unallocated' | 'mine';

/**
 * Contributions & Ledger tab. Members see what they owe, their payments and their statement.
 * Officials also get the collection list, the cashbook, the unmatched payment resolver and bank
 * slip entry. Every figure comes from the server.
 */
export function ContributionsTab({ deps }: { deps: Deps }) {
  const official = isOfficial(deps.role);
  const [officialView, setOfficialView] = useState<OfficialView>('collection');
  const [memberView, setMemberView] = useState<MemberView>('obligations');
  const [receiptOpen, setReceiptOpen] = useState(false);
  const members = useAuthedQuery(['members', deps.chamaId], deps.chamaApi.members, deps.callAuthed, official);
  const memberList = members.data?.members ?? [];

  const showMine = !official || officialView === 'mine';
  return (
    <ScrollView contentContainerStyle={styles.content} testID="contributions-tab">
      {official ? (
        <>
          <Segmented
            options={[
              { key: 'collection', label: t('ledger.view.collection') },
              { key: 'cashbook', label: t('ledger.view.cashbook') },
              { key: 'unallocated', label: t('ledger.view.unallocated') },
              { key: 'mine', label: t('ledger.view.mine') },
            ]}
            value={officialView}
            onChange={setOfficialView}
          />
          <Button label={t('receipt.open')} onPress={() => setReceiptOpen(true)} />
          <ReceiptForm
            deps={deps}
            members={memberList}
            visible={receiptOpen}
            onClose={() => setReceiptOpen(false)}
          />
        </>
      ) : null}
      {showMine ? (
        <>
          <CycleCard deps={deps} />
          <Segmented
            options={[
              { key: 'obligations', label: t('ledger.view.obligations') },
              { key: 'payments', label: t('ledger.view.payments') },
              { key: 'statement', label: t('ledger.view.statement') },
            ]}
            value={memberView}
            onChange={setMemberView}
          />
          {memberView === 'obligations' ? <MyObligations deps={deps} /> : null}
          {memberView === 'payments' ? <MyPayments deps={deps} /> : null}
          {memberView === 'statement' ? <MyStatement deps={deps} /> : null}
        </>
      ) : null}
      {official && officialView === 'collection' ? <CollectionList deps={deps} /> : null}
      {official && officialView === 'cashbook' ? <CashbookView deps={deps} /> : null}
      {official && officialView === 'unallocated' ? (
        <UnallocatedList deps={deps} members={memberList} />
      ) : null}
    </ScrollView>
  );
}

function CycleCard({ deps }: { deps: Deps }) {
  const query = useAuthedQuery(['cycle', deps.chamaId], deps.m2.currentCycle, deps.callAuthed);
  if (query.data === undefined) {
    // No cycle yet answers 404: say so rather than showing an error.
    if (query.error !== null && !query.isFetching) {
      return (
        <Card>
          <Text style={styles.note}>{t('ledger.cycle.none')}</Text>
        </Card>
      );
    }
    return null;
  }
  const cycle = query.data;
  return (
    <Card testID="cycle-card">
      <SectionLabel>{t('ledger.cycle.title')}</SectionLabel>
      <Row label={t('ledger.cycle.amount')} value={formatKes(cycle.amount_minor)} />
      <Row label={t('home.member.dueDate')} value={formatDate(cycle.due_date)} />
    </Card>
  );
}

function MyObligations({ deps }: { deps: Deps }) {
  const query = useAuthedQuery(
    ['obligations', deps.chamaId, 'me'],
    deps.m2.obligations('me'),
    deps.callAuthed,
  );
  return (
    <QueryBlock
      query={query}
      isEmpty={(d) => d.obligations.length === 0}
      emptyText={t('ledger.empty.obligations')}
    >
      {(data) => data.obligations.map((o) => <ObligationRow key={o.obligation_id} item={o} />)}
    </QueryBlock>
  );
}

function MyPayments({ deps }: { deps: Deps }) {
  const query = useAuthedQuery(['payments', deps.chamaId, 'mine'], deps.m2.payments(true), deps.callAuthed);
  return (
    <QueryBlock query={query} isEmpty={(d) => d.payments.length === 0} emptyText={t('ledger.empty.payments')}>
      {(data) => data.payments.map((p) => <PaymentRow key={p.payment_id} item={p} />)}
    </QueryBlock>
  );
}

function MyStatement({ deps }: { deps: Deps }) {
  const query = useAuthedQuery(['statement', deps.chamaId], deps.m2.statement, deps.callAuthed);
  return (
    <QueryBlock query={query} isEmpty={(d) => d.entries.length === 0} emptyText={t('ledger.empty.statement')}>
      {(data) => data.entries.map((e, i) => <StatementRow key={`${e.transaction_id}-${i}`} item={e} />)}
    </QueryBlock>
  );
}

function CollectionList({ deps }: { deps: Deps }) {
  const query = useAuthedQuery(['collection', deps.chamaId], deps.m2.collection, deps.callAuthed);
  const [open, setOpen] = useState<{ id: string; name: string } | null>(null);
  return (
    <>
      <QueryBlock
        query={query}
        isEmpty={(d) => d.members.length === 0}
        emptyText={t('ledger.empty.collection')}
      >
        {(data) =>
          data.members.map((m) => (
            <Pressable
              key={m.member_id}
              accessibilityRole="button"
              onPress={() => setOpen({ id: m.member_id, name: m.name })}
              testID={`collection-${m.member_id}`}
            >
              <LineItem
                title={m.name}
                detail={t(`collection.status.${m.status}`)}
                amount={formatKes(m.balance_minor)}
                tone={m.status === 'overdue' ? 'alert' : m.status === 'paid' ? 'good' : 'normal'}
              />
            </Pressable>
          ))
        }
      </QueryBlock>
      {open !== null ? (
        <MemberObligations deps={deps} memberId={open.id} name={open.name} onClose={() => setOpen(null)} />
      ) : null}
    </>
  );
}

function CashbookView({ deps }: { deps: Deps }) {
  const query = useAuthedQuery(['cashbook', deps.chamaId], deps.m2.cashbook, deps.callAuthed);
  return (
    <QueryBlock query={query}>
      {(data) => (
        <>
          <StatCard
            label={t('cashbook.balance')}
            value={formatKes(data.balance_minor)}
            testID="cashbook-balance"
          />
          {data.transactions.length === 0 ? (
            <Text style={styles.note}>{t('ledger.empty.cashbook')}</Text>
          ) : (
            data.transactions.map((tx) => <CashbookRow key={tx.transaction_id} item={tx} />)
          )}
        </>
      )}
    </QueryBlock>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 12, paddingBottom: CONTENT_BOTTOM },
  note: { fontSize: 15, color: c.muted },
});
