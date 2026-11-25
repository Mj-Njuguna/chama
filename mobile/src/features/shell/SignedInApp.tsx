import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import type { ApiError } from '../../api/errors';
import { LinkButton } from '../../components/ui';
import { type MessageKey, t } from '../../i18n';
import { c } from '../app/theme';
import type { ChamaApi } from '../chamas/chamaApi';
import { ChamaSwitcher } from '../chamas/ChamaSwitcher';
import { CreateChamaScreen } from '../chamas/CreateChamaScreen';
import { useChamaUi } from '../chamas/store';
import { HomeScreen } from '../home/HomeScreen';
import { NoData, StaleNotice } from './QueryStates';
import { TabPlaceholder } from './TabPlaceholder';
import { type CallAuthed, useAuthedQuery } from './useAuthedQuery';

export type Tab = 'home' | 'ledger' | 'loans' | 'governance';

const TABS: readonly { key: Tab; label: MessageKey }[] = [
  { key: 'home', label: 'tabs.home' },
  { key: 'ledger', label: 'tabs.ledger' },
  { key: 'loans', label: 'tabs.loans' },
  { key: 'governance', label: 'tabs.governance' },
];

type Overlay = 'none' | 'switcher' | 'create';

/** Plain message for a failed write; `null` means it worked. */
function writeError(
  result: { ok: boolean } & Partial<{ error: ApiError; offline: true; signedOut: unknown }>,
): string | null {
  if (result.ok) return null;
  if (result.offline === true) return t('common.needsConnection');
  if (result.signedOut !== undefined) return null; // the session provider shows the login screen
  if (result.error?.code === 'rate_limited') return t('chamas.create.rateLimited');
  if (result.error?.code === 'chama_not_available') return t('chamas.switcher.notAvailable');
  return t('common.failed');
}

/**
 * Signed-in app: chama picker and the four tabs of the role-based shell. The active chama is the
 * server's (GET /me, switch-chama); Zustand only mirrors it for the UI.
 */
export function SignedInApp({
  api,
  callAuthed,
  onLogout,
}: {
  api: ChamaApi;
  callAuthed: CallAuthed;
  onLogout: (scope: 'this_device' | 'everywhere') => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const me = useAuthedQuery(['me'], api.me, callAuthed);
  const activeChamaId = useChamaUi((s) => s.activeChamaId);
  const setActiveChama = useChamaUi((s) => s.setActiveChama);
  const [tab, setTab] = useState<Tab>('home');
  const [overlay, setOverlay] = useState<Overlay>('none');

  const serverActive = me.data?.active_chama_id ?? null;
  useEffect(() => {
    if (me.data !== undefined) setActiveChama(serverActive);
  }, [me.data, serverActive, setActiveChama]);

  async function switchTo(chamaId: string): Promise<string | null> {
    const result = await callAuthed(api.switchTo(chamaId));
    const error = writeError(result);
    if (result.ok) {
      setActiveChama(result.data.active_chama_id);
      setTab('home');
      setOverlay('none');
      await queryClient.invalidateQueries({ queryKey: ['me'] });
    }
    return error;
  }

  async function create(name: string): Promise<string | null> {
    const result = await callAuthed(api.create(name));
    const error = writeError(result);
    if (result.ok) {
      setActiveChama(result.data.active_chama_id);
      setTab('governance');
      setOverlay('none');
      await queryClient.invalidateQueries({ queryKey: ['me'] });
    }
    return error;
  }

  if (me.data === undefined) {
    return <NoData loading={me.isPending} error={me.error} onRetry={() => void me.refetch()} />;
  }
  const memberships = me.data.memberships.filter((m) => m.status === 'active');

  if (overlay === 'create' || memberships.length === 0) {
    return (
      <CreateChamaScreen
        onCreate={create}
        {...(memberships.length === 0 ? {} : { onCancel: () => setOverlay('none') })}
      />
    );
  }

  const current = memberships.find((m) => m.chama_id === activeChamaId);
  if (overlay === 'switcher' || current === undefined) {
    return (
      <ChamaSwitcher
        memberships={memberships}
        activeChamaId={current === undefined ? null : current.chama_id}
        onSwitch={switchTo}
        onCreate={() => setOverlay('create')}
        {...(current === undefined ? {} : { onClose: () => setOverlay('none') })}
      />
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={['top', 'left', 'right']} testID="signed-in">
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t('chamas.switcher.open', { name: current.chama_name })}
          onPress={() => setOverlay('switcher')}
          style={styles.chamaButton}
          testID="open-switcher"
        >
          <Text style={styles.chamaName} numberOfLines={1}>
            {current.chama_name}
          </Text>
          <Text style={styles.role}>{t(`role.${current.role}`)}</Text>
        </Pressable>
        <LinkButton label={t('auth.logout')} onPress={() => void onLogout('this_device')} />
      </View>
      <StaleNotice error={me.error} />
      <View style={styles.body}>
        {tab === 'home' ? (
          <HomeScreen api={api} callAuthed={callAuthed} chamaId={current.chama_id} />
        ) : (
          <TabPlaceholder tab={tab} role={current.role} />
        )}
      </View>
      <SafeAreaView edges={['bottom']} style={styles.tabBar}>
        <View style={styles.tabRow} accessibilityRole="tablist">
          {TABS.map((item) => {
            const on = item.key === tab;
            return (
              <Pressable
                key={item.key}
                accessibilityRole="tab"
                accessibilityState={{ selected: on }}
                accessibilityLabel={t(item.label)}
                onPress={() => setTab(item.key)}
                style={styles.tab}
                testID={`tab-${item.key}`}
              >
                <Text style={[styles.tabText, on && styles.tabTextOn]} numberOfLines={2}>
                  {t(item.label)}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </SafeAreaView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: c.surface },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: c.white,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
  },
  chamaButton: { flex: 1, minHeight: 48, justifyContent: 'center' },
  chamaName: { fontSize: 18, fontWeight: '700', color: c.ink },
  role: { fontSize: 13, color: c.muted },
  body: { flex: 1 },
  tabBar: { backgroundColor: c.white, borderTopWidth: 1, borderTopColor: c.border },
  tabRow: { flexDirection: 'row', minHeight: 56 },
  tab: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4, paddingVertical: 8 },
  tabText: { fontSize: 12, color: c.tabIdle, textAlign: 'center', fontWeight: '500' },
  tabTextOn: { color: c.teal, fontWeight: '700' },
});
