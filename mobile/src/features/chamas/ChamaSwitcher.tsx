import { useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Body, Button, LinkButton, Title } from '../../components/ui';
import { t } from '../../i18n';
import { c } from '../app/theme';
import type { MeMembership } from './chamaApi';

/**
 * Lists the user's chamas (from GET /me) and switches the session's active chama through the
 * server. The server decides; this screen never assumes a switch worked.
 */
export function ChamaSwitcher({
  memberships,
  activeChamaId,
  onSwitch,
  onCreate,
  onClose,
}: {
  memberships: readonly MeMembership[];
  activeChamaId: string | null;
  /** Resolves to an error message key on failure, or null on success. */
  onSwitch: (chamaId: string) => Promise<string | null>;
  onCreate: () => void;
  /** Absent when a chama must be chosen before anything else. */
  onClose?: () => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = memberships.filter((m) => m.status === 'active');

  async function choose(chamaId: string): Promise<void> {
    if (chamaId === activeChamaId) {
      onClose?.();
      return;
    }
    setBusyId(chamaId);
    setError(null);
    try {
      setError(await onSwitch(chamaId));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <SafeAreaView style={styles.screen} testID="chama-switcher">
      <View style={styles.header}>
        <Title>{t('chamas.switcher.title')}</Title>
        {onClose !== undefined ? <LinkButton label={t('common.close')} onPress={onClose} /> : null}
      </View>
      <FlatList
        data={active}
        keyExtractor={(m) => m.chama_id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Body tone="muted">{t('chamas.switcher.empty')}</Body>}
        renderItem={({ item }) => {
          const selected = item.chama_id === activeChamaId;
          return (
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ selected, busy: busyId === item.chama_id }}
              accessibilityLabel={`${item.chama_name}, ${t(`role.${item.role}`)}`}
              disabled={busyId !== null}
              onPress={() => void choose(item.chama_id)}
              style={[styles.item, selected && styles.itemSelected]}
              testID={`chama-${item.chama_id}`}
            >
              <Text style={styles.name}>{item.chama_name}</Text>
              <Text style={styles.role}>{t(`role.${item.role}`)}</Text>
            </Pressable>
          );
        }}
      />
      {error !== null ? <Body tone="danger">{error}</Body> : null}
      <View style={styles.footer}>
        <Button label={t('chamas.create.open')} onPress={onCreate} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: c.white },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 24 },
  list: { paddingHorizontal: 24, gap: 10 },
  item: { borderWidth: 1, borderColor: c.border, borderRadius: 14, padding: 16, minHeight: 56 },
  itemSelected: { borderColor: c.teal, backgroundColor: c.selectedBg },
  name: { fontSize: 17, fontWeight: '600', color: c.ink },
  role: { fontSize: 14, color: c.muted, marginTop: 2 },
  footer: { padding: 24 },
});
