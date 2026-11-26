import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Modal, ScrollView, StyleSheet, Text } from 'react-native';

import { Body, Button, LinkButton, Segmented, TextField, Title } from '../../components/ui';
import { t } from '../../i18n';
import { Card, Row } from '../app/kit';
import { c } from '../app/theme';
import { type Deps, writeFailure } from '../m2/deps';
import { QueryBlock } from '../m2/QueryBlock';
import { useAuthedQuery } from '../shell/useAuthedQuery';

type Channel = 'paybill' | 'till';
type Environment = 'sandbox' | 'production';

/**
 * The chair connects the chama's OWN paybill or till (money never passes through the platform).
 * The Daraja keys are write-only: the server stores them encrypted and never returns them, and
 * the form clears them after every attempt. The server asks for a fresh code (step-up).
 */
export function PaymentLinkingScreen({ deps, onClose }: { deps: Deps; onClose: () => void }) {
  const queryClient = useQueryClient();
  const query = useAuthedQuery(['payment-linking', deps.chamaId], deps.m2.paymentLinking, deps.callAuthed);
  const [channel, setChannel] = useState<Channel>('paybill');
  const [environment, setEnvironment] = useState<Environment>('sandbox');
  const [shortcode, setShortcode] = useState('');
  const [consumerKey, setConsumerKey] = useState('');
  const [consumerSecret, setConsumerSecret] = useState('');
  const [passkey, setPasskey] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  function clearSecrets(): void {
    setConsumerKey('');
    setConsumerSecret('');
    setPasskey('');
  }

  async function save(): Promise<void> {
    const fields = [shortcode, consumerKey, consumerSecret, passkey].map((v) => v.trim());
    if (fields.some((v) => v === '')) {
      setMessage({ text: t('linking.invalid'), ok: false });
      return;
    }
    const [code, key, secret, pass] = fields as [string, string, string, string];
    setBusy(true);
    setMessage(null);
    const result = await deps.runWithStepUp(
      deps.m2.updatePaymentLinking({
        channel,
        shortcode: code,
        consumer_key: key,
        consumer_secret: secret,
        passkey: pass,
        environment,
      }),
      { reason: 'linking.reason' },
    );
    setBusy(false);
    clearSecrets();
    if (result.ok) {
      setMessage({ text: t('linking.saved'), ok: true });
      queryClient.setQueryData(['payment-linking', deps.chamaId], result.data);
      return;
    }
    const text = writeFailure(result, (e) => (e.code === 'validation_error' ? 'linking.invalid' : null));
    if (text !== null) setMessage({ text, ok: false });
  }

  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <ScrollView contentContainerStyle={styles.screen} testID="payment-linking">
        <Title>{t('linking.title')}</Title>
        <QueryBlock query={query}>
          {(linking) => (
            <Card>
              <Row label={t('linking.title')} value={t(`linking.status.${linking.status}`)} />
              {linking.shortcode !== null ? (
                <Row label={t('linking.shortcode')} value={linking.shortcode} />
              ) : null}
              {linking.credentials_set ? <Row label={t('linking.credentialsSet')} value="✓" /> : null}
            </Card>
          )}
        </QueryBlock>
        <Segmented
          options={[
            { key: 'paybill', label: t('linking.channel.paybill') },
            { key: 'till', label: t('linking.channel.till') },
          ]}
          value={channel}
          onChange={setChannel}
        />
        {channel === 'till' ? <Body tone="muted">{t('linking.tillNote')}</Body> : null}
        <TextField
          label={t('linking.shortcode')}
          value={shortcode}
          onChangeText={setShortcode}
          keyboardType="number-pad"
          maxLength={10}
          testID="linking-shortcode"
        />
        <TextField
          label={t('linking.consumerKey')}
          value={consumerKey}
          onChangeText={setConsumerKey}
          secure
          autoComplete="off"
        />
        <TextField
          label={t('linking.consumerSecret')}
          value={consumerSecret}
          onChangeText={setConsumerSecret}
          secure
          autoComplete="off"
        />
        <TextField
          label={t('linking.passkey')}
          value={passkey}
          onChangeText={setPasskey}
          secure
          autoComplete="off"
        />
        <Segmented
          options={[
            { key: 'sandbox', label: t('linking.environment.sandbox') },
            { key: 'production', label: t('linking.environment.production') },
          ]}
          value={environment}
          onChange={setEnvironment}
        />
        <Body tone="muted">{t('linking.secretsNote')}</Body>
        {message !== null ? (
          <Text
            style={{ color: message.ok ? c.green : c.red }}
            accessibilityLiveRegion="polite"
            testID="linking-message"
          >
            {message.text}
          </Text>
        ) : null}
        <Button label={t('linking.save')} busy={busy} onPress={() => void save()} />
        <LinkButton label={t('common.close')} onPress={onClose} />
      </ScrollView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  screen: { padding: 24, gap: 16, backgroundColor: c.white },
});
