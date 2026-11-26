import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button, Segmented } from '../../components/ui';
import { t } from '../../i18n';
import { formatKes } from '../../lib/money';
import { formatDate } from '../../lib/nairobiTime';
import { Badge, Card, ProgressBar, Row, SectionLabel, StatCard } from '../app/kit';
import { c, CONTENT_BOTTOM } from '../app/theme';
import { type ChamaApi, type ChamaHome, isOfficial } from '../chamas/chamaApi';
import { useChamaUi } from '../chamas/store';
import type { M2Api } from '../m2/m2Api';
import { NoData, StaleNotice } from '../shell/QueryStates';
import { type CallAuthed, useAuthedQuery } from '../shell/useAuthedQuery';

/** "Not available yet" for values the server does not compute until a later phase. */
function money(minor: number | null): string {
  return minor === null ? t('home.notYet') : formatKes(minor);
}

function count(value: number | null): string {
  return value === null ? t('home.notYet') : String(value);
}

/**
 * Home tab. Members see their own position; officials can switch to the group view. Every figure
 * comes from GET /chama/home: the app formats, it never computes balances or rates.
 */
export function HomeScreen({
  api,
  m2,
  callAuthed,
  chamaId,
  onPay,
}: {
  api: ChamaApi;
  m2: M2Api;
  callAuthed: CallAuthed;
  chamaId: string;
  /** Opens the M-Pesa payment modal, prefilled with the server's "still to pay" figure. */
  onPay: (prefillMinor: number | null) => void;
}) {
  const query = useAuthedQuery(['chama-home', chamaId], api.home, callAuthed);
  const viewMode = useChamaUi((s) => s.viewMode);
  const setViewMode = useChamaUi((s) => s.setViewMode);

  if (query.data === undefined) {
    return (
      <NoData
        loading={query.isPending && query.fetchStatus !== 'idle'}
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const home = query.data;
  const official = isOfficial(home.role) && home.official !== null;
  const showOfficial = official && viewMode === 'official';

  return (
    <ScrollView
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={query.isFetching} onRefresh={() => void query.refetch()} />}
      testID="home"
    >
      <StaleNotice error={query.error} />
      {official ? (
        <Segmented
          options={[
            { key: 'official', label: t('home.view.official') },
            { key: 'member', label: t('home.view.member') },
          ]}
          value={viewMode}
          onChange={setViewMode}
        />
      ) : null}
      {showOfficial && home.official !== null ? (
        <OfficialHome data={home.official} m2={m2} callAuthed={callAuthed} chamaId={chamaId} />
      ) : (
        <MemberHome data={home.member} onPay={onPay} />
      )}
    </ScrollView>
  );
}

function MemberHome({
  data,
  onPay,
}: {
  data: ChamaHome['member'];
  onPay: (prefillMinor: number | null) => void;
}) {
  if (data === null) {
    return <Text style={styles.empty}>{t('home.member.empty')}</Text>;
  }
  return (
    <View style={styles.stack} testID="member-home">
      <StatCard label={t('home.member.savings')} value={money(data.savings_minor)} testID="member-savings" />
      <Card>
        <SectionLabel>{t('home.member.cycle')}</SectionLabel>
        <Row
          label={t('home.member.cycleStatus')}
          value={data.cycle_status === null ? t('home.notYet') : t(`cycle.${data.cycle_status}`)}
          tone={data.cycle_status === 'overdue' ? 'alert' : 'normal'}
        />
        {data.cycle_due_date !== null ? (
          <Row label={t('home.member.dueDate')} value={formatDate(data.cycle_due_date)} />
        ) : null}
        {data.cycle_balance_minor !== null ? (
          <Row label={t('home.member.cycleBalance')} value={formatKes(data.cycle_balance_minor)} />
        ) : null}
      </Card>
      <Card>
        <SectionLabel>{t('home.member.loan')}</SectionLabel>
        <Row label={t('home.member.loanBalance')} value={money(data.active_loan_balance_minor)} />
      </Card>
      <Button label={t('home.member.pay')} onPress={() => onPay(data.cycle_balance_minor)} />
    </View>
  );
}

function OfficialHome({
  data,
  m2,
  callAuthed,
  chamaId,
}: {
  data: NonNullable<ChamaHome['official']>;
  m2: M2Api;
  callAuthed: CallAuthed;
  chamaId: string;
}) {
  return (
    <View style={styles.stack} testID="official-home">
      <StatCard
        label={t('home.official.liquidity')}
        value={money(data.liquidity_minor)}
        testID="official-liquidity"
      />
      <Card>
        <SectionLabel>{t('home.official.collection')}</SectionLabel>
        {data.collection_rate_percent === null ? (
          <Text style={styles.note}>{t('home.notYet')}</Text>
        ) : (
          <>
            <ProgressBar
              percent={data.collection_rate_percent}
              label={t('home.official.collectionRate', { percent: data.collection_rate_percent })}
            />
            <Text style={styles.note}>
              {t('home.official.collectionRate', { percent: data.collection_rate_percent })}
            </Text>
          </>
        )}
      </Card>
      <Card>
        <View style={styles.rowWithBadge}>
          <Text style={styles.cardTitle}>{t('home.official.approvals')}</Text>
          {data.pending_approvals !== null && data.pending_approvals > 0 ? (
            <Badge
              count={data.pending_approvals}
              label={t('home.official.approvalsBadge', { count: data.pending_approvals })}
            />
          ) : (
            <Text style={styles.note}>{count(data.pending_approvals)}</Text>
          )}
        </View>
        <Row
          label={t('home.official.overdue')}
          value={count(data.overdue_count)}
          tone={data.overdue_count !== null && data.overdue_count > 0 ? 'alert' : 'normal'}
        />
      </Card>
      <OverdueList m2={m2} callAuthed={callAuthed} chamaId={chamaId} />
    </View>
  );
}

/** Members the server marks overdue in the current cycle (from GET /chama/collection). */
function OverdueList({ m2, callAuthed, chamaId }: { m2: M2Api; callAuthed: CallAuthed; chamaId: string }) {
  const query = useAuthedQuery(['collection', chamaId], m2.collection, callAuthed);
  if (query.data === undefined) return null;
  const overdue = query.data.members.filter((m) => m.status === 'overdue');
  return (
    <Card testID="overdue-list">
      <SectionLabel>{t('home.official.overdueList')}</SectionLabel>
      {overdue.length === 0 ? (
        <Text style={styles.note}>{t('home.official.noOverdue')}</Text>
      ) : (
        overdue.map((m) => (
          <Row key={m.member_id} label={m.name} value={formatKes(m.balance_minor)} tone="alert" />
        ))
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 12, paddingBottom: CONTENT_BOTTOM },
  stack: { gap: 12 },
  empty: { fontSize: 15, color: c.muted, textAlign: 'center', marginTop: 32 },
  note: { fontSize: 13, color: c.muted },
  cardTitle: { fontSize: 15, fontWeight: '600', color: c.ink },
  rowWithBadge: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
});
