import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ScrollView, StyleSheet, Switch, Text, View } from 'react-native';

import { Button, LinkButton } from '../../components/ui';
import { type MessageKey, t } from '../../i18n';
import { formatKes } from '../../lib/money';
import { formatDayMonth } from '../../lib/nairobiTime';
import { Card, SectionLabel } from '../app/kit';
import { c, CONTENT_BOTTOM } from '../app/theme';
import { isOfficial, type Member } from '../chamas/chamaApi';
import { LineItem } from '../contributions/rows';
import { type Deps, writeFailure } from '../m2/deps';
import type { FeedEvent, Waiver } from '../m2/m2Api';
import { QueryBlock } from '../m2/QueryBlock';
import { useAuthedQuery } from '../shell/useAuthedQuery';
import { PaymentLinkingScreen } from './PaymentLinkingScreen';

const FEED_KINDS: Record<string, MessageKey> = {
  payment_confirmed: 'feed.kind.payment_confirmed',
  fine_assessed: 'feed.kind.fine_assessed',
  fine_waived: 'feed.kind.fine_waived',
  cycle_opened: 'feed.kind.cycle_opened',
  member_joined: 'feed.kind.member_joined',
};

/** Governance & Alerts: group activity feed, my settings, and for officials fine waivers and
 * (chair) the M-Pesa account. */
export function GovernanceTab({ deps }: { deps: Deps }) {
  const official = isOfficial(deps.role);
  const [linkingOpen, setLinkingOpen] = useState(false);
  return (
    <ScrollView contentContainerStyle={styles.content} testID="governance-tab">
      {official ? <PendingWaivers deps={deps} /> : null}
      {deps.role === 'chair' ? (
        <>
          <Button label={t('linking.open')} onPress={() => setLinkingOpen(true)} />
          {linkingOpen ? <PaymentLinkingScreen deps={deps} onClose={() => setLinkingOpen(false)} /> : null}
        </>
      ) : null}
      <Settings deps={deps} />
      <Feed deps={deps} />
    </ScrollView>
  );
}

function Settings({ deps }: { deps: Deps }) {
  const queryClient = useQueryClient();
  const key = ['membership', deps.chamaId];
  const query = useAuthedQuery(key, deps.m2.membership, deps.callAuthed);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle(next: boolean): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await deps.callAuthed(deps.m2.setScheduledStk(next));
    setBusy(false);
    if (result.ok) {
      queryClient.setQueryData(key, result.data);
      return;
    }
    const text = writeFailure(result, () => 'settings.saveFailed');
    if (text !== null) setError(text);
  }

  return (
    <Card testID="settings">
      <SectionLabel>{t('settings.title')}</SectionLabel>
      <QueryBlock query={query}>
        {(prefs) => (
          <View style={styles.switchRow}>
            <View style={styles.switchText}>
              <Text style={styles.label}>{t('settings.scheduledStk')}</Text>
              <Text style={styles.note}>{t('settings.scheduledStkHint')}</Text>
            </View>
            <Switch
              accessibilityLabel={t('settings.scheduledStk')}
              value={prefs.scheduled_stk_opt_in}
              disabled={busy}
              onValueChange={(v) => void toggle(v)}
              testID="scheduled-stk-switch"
            />
          </View>
        )}
      </QueryBlock>
      {error !== null ? <Text style={styles.error}>{error}</Text> : null}
    </Card>
  );
}

function PendingWaivers({ deps }: { deps: Deps }) {
  const query = useAuthedQuery(['waivers', deps.chamaId, 'pending'], deps.m2.pendingWaivers, deps.callAuthed);
  const members = useAuthedQuery(['members', deps.chamaId], deps.chamaApi.members, deps.callAuthed);
  const list = members.data?.members ?? [];
  const mine = list.find((m) => m.user_id === deps.myUserId)?.membership_id ?? null;
  return (
    <Card testID="pending-waivers">
      <SectionLabel>{t('waiver.pending')}</SectionLabel>
      <QueryBlock query={query} isEmpty={(d) => d.waivers.length === 0} emptyText={t('waiver.none')}>
        {(data) =>
          data.waivers.map((w) => (
            <WaiverRow key={w.waiver_id} deps={deps} waiver={w} members={list} myMembershipId={mine} />
          ))
        }
      </QueryBlock>
    </Card>
  );
}

function WaiverRow({
  deps,
  waiver,
  members,
  myMembershipId,
}: {
  deps: Deps;
  waiver: Waiver;
  members: readonly Member[];
  myMembershipId: string | null;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const name = members.find((m) => m.membership_id === waiver.member_id)?.name ?? '';
  // Maker-checker: the official who asked may not approve. The server enforces this too.
  const requestedByMe = myMembershipId !== null && waiver.requested_by_member_id === myMembershipId;

  async function approve(): Promise<void> {
    setBusy(true);
    const result = await deps.runWithStepUp(deps.m2.approveWaiver(waiver.waiver_id), {
      reason: 'waiver.reasonStepUp',
    });
    setBusy(false);
    if (result.ok) {
      setMessage({ text: t('waiver.approved'), ok: true });
      void queryClient.invalidateQueries();
      return;
    }
    const text = writeFailure(result);
    if (text !== null) setMessage({ text, ok: false });
  }

  return (
    <View style={styles.waiver} testID={`waiver-${waiver.waiver_id}`}>
      <LineItem title={name} detail={waiver.reason} amount={formatKes(waiver.amount_minor)} />
      {message !== null ? (
        <Text style={{ color: message.ok ? c.green : c.red }} accessibilityLiveRegion="polite">
          {message.text}
        </Text>
      ) : requestedByMe ? (
        <Text style={styles.note}>{t('waiver.yours')}</Text>
      ) : (
        <Button label={t('waiver.approve')} busy={busy} onPress={() => void approve()} />
      )}
    </View>
  );
}

function Feed({ deps }: { deps: Deps }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [older, setOlder] = useState<readonly FeedEvent[]>([]);
  const first = useAuthedQuery(['feed', deps.chamaId], deps.m2.feed(null), deps.callAuthed);
  const [busy, setBusy] = useState(false);

  async function more(next: string): Promise<void> {
    setBusy(true);
    const result = await deps.callAuthed(deps.m2.feed(next));
    setBusy(false);
    if (result.ok) {
      setOlder((prev) => [...prev, ...result.data.events]);
      setCursor(result.data.next_cursor);
    }
  }

  return (
    <Card testID="feed">
      <SectionLabel>{t('feed.title')}</SectionLabel>
      <QueryBlock query={first} isEmpty={(d) => d.events.length === 0} emptyText={t('feed.empty')}>
        {(page) => {
          const nextCursor = older.length === 0 ? page.next_cursor : cursor;
          return (
            <>
              {[...page.events, ...older].map((e) => (
                <FeedRow key={e.event_id} event={e} />
              ))}
              {nextCursor !== null ? (
                <LinkButton label={t('feed.more')} disabled={busy} onPress={() => void more(nextCursor)} />
              ) : null}
            </>
          );
        }}
      </QueryBlock>
    </Card>
  );
}

function FeedRow({ event }: { event: FeedEvent }) {
  const amount = typeof event.payload.amount_minor === 'number' ? event.payload.amount_minor : null;
  const who = typeof event.payload.member_name === 'string' ? event.payload.member_name : undefined;
  return (
    <LineItem
      title={t(FEED_KINDS[event.kind] ?? 'feed.kind.other')}
      detail={
        who === undefined ? formatDayMonth(event.created_at) : `${who} · ${formatDayMonth(event.created_at)}`
      }
      amount={amount !== null && Number.isSafeInteger(amount) ? formatKes(amount) : ''}
    />
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 12, paddingBottom: CONTENT_BOTTOM },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  switchText: { flex: 1, gap: 2 },
  label: { fontSize: 15, color: c.ink },
  note: { fontSize: 13, color: c.muted },
  error: { fontSize: 14, color: c.red },
  waiver: { gap: 8, paddingBottom: 8 },
});
